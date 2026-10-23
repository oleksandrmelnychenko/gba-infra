#!/usr/bin/env python3
import json
import os
import re
import sys
from pathlib import Path

DEV_COMPOSE = Path(os.environ.get('QA_DEV_COMPOSE', '/private/claude-deploy-reopen-20261014/candidate-compose.private.json'))
STATE_DIR = Path(os.environ.get('QA_STATE_DIR', '/private/gba-qa'))
SECRETS_DIR = STATE_DIR / 'secrets'
OUTPUT = STATE_DIR / 'qa-compose.private.json'

DATABASE_RENAMES = {
    'ConcordDb_V5': 'ConcordDb_V5_QA',
    'ConcordDb_Data': 'ConcordDb_Data_QA',
    'ConcordIdentityDb': 'ConcordIdentityDb_QA',
    'GbaVehicleRegistry': 'GbaVehicleRegistry_QA',
}
LOCAL_CONNECTION_TARGETS = {
    'ConnectionStrings:LocalConnectionString',
    'ConnectionStrings:LocalConnectionStringDataAnalitic',
    'ConnectionStrings:LocalIdentityConnectionString',
    'ConnectionStrings:VehicleRegistryConnectionString',
}
SERVICES = {
    'data-concord': ('data-concord-qa', '35996', 35981, '6g'),
    'data-analytics': ('data-analytics-qa', '35997', 35982, '3g'),
}
DATA_BIND_TARGETS = {'/app/Data', '/app/Images', '/app/Logs'}
HISTORY_READONLY_TARGETS = {
    'ConnectionStrings:FenixOneCReadOnlyConnectionString',
    'ConnectionStrings:AmgOneCReadOnlyConnectionString',
}
SOURCE_PACING_LOCK = '/private/bug1274-settlement-source-paced.lock'
SOURCE_FLAG = re.compile(r'(?i)(sync|capture|refresh|replay|fenix|amg|onec|import|export)')
FORCED_ENVIRONMENT = {
    'Maintenance__Enabled': 'false',
    'Maintenance__QaEnabled': 'false',
    'SchedulerTasks__SchedulingEnabled': 'false',
    'GBA_CONTROL_DATABASE_NAME': 'ConcordDb_V5_QA',
}


def rewrite_connection_string(text: str) -> str:
    def replace(match: re.Match) -> str:
        database = match.group(2)
        if database not in DATABASE_RENAMES:
            raise SystemExit(f'Unexpected database in a local connection string: {database}')
        return f'{match.group(1)}={DATABASE_RENAMES[database]}{match.group(3)}'

    rewritten, count = re.subn(r'(?i)(Database|Initial Catalog)=([^;]+)(;|$)', replace, text)
    if count != 1:
        raise SystemExit('Expected exactly one database name in a local connection string')
    return rewritten


def qa_conversion_flags(service: str, source: dict) -> dict:
    existing = {}
    if OUTPUT.is_file():
        document = json.loads(OUTPUT.read_text())
        existing = document.get('services', {}).get(SERVICES[service][0], {}).get('environment', {})
    result = {}
    for flag in ('GBA_LIBREOFFICE_POOL_ENABLED', 'GBA_DOCUMENT_CONVERSION_TIMINGS'):
        value = str(os.environ.get(flag, existing.get(flag, source.get(flag, 'false')))).lower()
        if value not in ('true', 'false'):
            raise SystemExit(f'{flag} must be true or false')
        result[flag] = value
    return result


def qa_environment(environment: dict, service: str) -> dict:
    result = {}
    for key, value in environment.items():
        value = '' if value is None else str(value)
        lowered = key.lower()
        if lowered.endswith('enabled') and key != 'BackgroundWriters__Enabled' and SOURCE_FLAG.search(key):
            value = 'false'
        if key.startswith('GBA_DATASYNC_') or key.endswith('_LOAD_GUARD'):
            value = 'false'
        result[key] = value
    result.update(FORCED_ENVIRONMENT)
    result.update(qa_conversion_flags(service, environment))
    if service == 'data-concord':
        result.pop('GbaNbaApi__Key', None)
        result['BackgroundWriters__Enabled'] = 'true'
        if result.get('GbaNbaApi__Url'):
            key_file = STATE_DIR / 'nba' / 'api-key'
            if not key_file.is_file() or not key_file.read_text().strip():
                raise SystemExit('Prepare isolated QA NBA credentials before regenerating QA compose')
            result['GbaNbaApi__Url'] = 'http://host.docker.internal:8012'
            result['GbaNbaApi__SourceEnvironment'] = 'qa'
            result['GbaNbaApi__ApiKey'] = key_file.read_text().strip()
        result.pop('ProductsApi__Key', None)
        result['ProductsApi__SourceEnvironment'] = 'qa'
        if result.get('ProductsApi__Url'):
            key_file = STATE_DIR / 'products' / 'api-key'
            if not key_file.is_file() or not key_file.read_text().strip():
                raise SystemExit('Prepare isolated QA products credentials before regenerating QA compose')
            key = key_file.read_text().strip()
            if key == result.get('ProductsApi__ApiKey'):
                raise SystemExit('QA products must use a distinct API key')
            result['ProductsApi__Url'] = 'http://host.docker.internal:8015'
            result['ProductsApi__SourceEnvironment'] = 'qa'
            result['ProductsApi__ApiKey'] = key
        else:
            # Empty values explicitly override appsettings.Development defaults.
            result['ProductsApi__Url'] = ''
            result['ProductsApi__ApiKey'] = ''
        if str(result.get('ProductSourceHistory__Enabled', '')).lower() == 'true':
            database = result.get('ProductSourceHistory__OwnExpectedDatabase')
            if database not in DATABASE_RENAMES:
                raise SystemExit('Product history requires a known DEV database before mapping its QA target')
            result['ProductSourceHistory__OwnExpectedDatabase'] = DATABASE_RENAMES[database]
    else:
        result['ProductSourceHistory__Enabled'] = 'false'
    return result


def main() -> None:
    dev = json.loads(DEV_COMPOSE.read_text())
    dev_secrets = dev.get('secrets', {})
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(SECRETS_DIR, 0o700)

    secrets = {}
    services = {}
    for dev_name, (qa_name, host_port, container_port, memory) in SERVICES.items():
        source = dev['services'][dev_name]
        history_enabled = dev_name == 'data-concord' and str(
            source.get('environment', {}).get('ProductSourceHistory__Enabled', '')).lower() == 'true'
        service_secrets = []
        history_secrets = set()
        for secret in source.get('secrets', []):
            target = secret['target']
            name = secret['source']
            if 'onec' in target.lower() or 'onec' in name.lower():
                if not history_enabled or target not in HISTORY_READONLY_TARGETS:
                    continue
                history_secrets.add(target)
            dev_file = Path(dev_secrets[name]['file'])
            if target in LOCAL_CONNECTION_TARGETS:
                qa_file = SECRETS_DIR / name
                qa_file.write_text(rewrite_connection_string(dev_file.read_text()))
                os.chmod(qa_file, 0o600)
                secrets[name] = {'file': str(qa_file), 'name': f'gba-qa_{name}'}
            else:
                secrets[name] = {'file': str(dev_file), 'name': f'gba-qa_{name}'}
            service_secrets.append({'source': name, 'target': target})
        if history_enabled and history_secrets != HISTORY_READONLY_TARGETS:
            raise SystemExit('Product history requires both dedicated readonly source secrets')

        volumes = [{'type': 'volume', 'source': 'qa-dataprotection-keys', 'target': '/root/.aspnet/DataProtection-Keys'}]
        history_pacing = False
        for volume in source.get('volumes', []):
            target = volume.get('target')
            if target in DATA_BIND_TARGETS:
                folder = STATE_DIR / 'concord' / Path(target).name
                folder.mkdir(parents=True, exist_ok=True)
                volumes.append({'type': 'bind', 'source': str(folder), 'target': target, 'bind': {'create_host_path': False}})
            elif volume.get('type') == 'bind' and volume.get('read_only') and target.startswith('/run/gba/'):
                volumes.append({'type': 'bind', 'source': volume['source'], 'target': target, 'read_only': True,
                                'bind': {'create_host_path': False}})
            elif history_enabled and volume.get('type') == 'bind' and target == SOURCE_PACING_LOCK:
                if volume.get('read_only') or volume['source'] != SOURCE_PACING_LOCK:
                    raise SystemExit('Product history must share the existing host source pacing lock')
                volumes.append({'type': 'bind', 'source': volume['source'], 'target': target, 'read_only': False,
                                'bind': {'create_host_path': False}})
                history_pacing = True
        if history_enabled and not history_pacing:
            raise SystemExit('Product history source pacing lock is missing')

        service = {
            'image': source['image'],
            'container_name': qa_name,
            'environment': qa_environment(source.get('environment', {}), dev_name),
            'secrets': service_secrets,
            'volumes': volumes,
            'ports': [{'host_ip': '127.0.0.1', 'published': host_port, 'target': container_port, 'protocol': 'tcp'}],
            'networks': ['gba-dev-net'],
            'mem_limit': memory,
            'restart': 'unless-stopped',
            'labels': {**source.get('labels', {}), 'gba.stand': 'qa'},
        }
        for key in ('healthcheck', 'extra_hosts', 'logging', 'entrypoint', 'command'):
            if source.get(key) is not None:
                service[key] = source[key]
        services[qa_name] = service

    compose = {
        'name': 'gba-qa',
        'services': services,
        'secrets': secrets,
        'volumes': {'qa-dataprotection-keys': {'name': 'gba-qa_dataprotection-keys'}},
        'networks': {'gba-dev-net': {'external': True, 'name': 'gba-dev_default'}},
    }
    OUTPUT.write_text(json.dumps(compose, indent=2, ensure_ascii=False))
    os.chmod(OUTPUT, 0o600)
    print(OUTPUT)


if __name__ == '__main__':
    sys.exit(main())
