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
    if service == 'data-concord':
        result['BackgroundWriters__Enabled'] = 'true'
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
        service_secrets = []
        for secret in source.get('secrets', []):
            target = secret['target']
            name = secret['source']
            if 'onec' in target.lower() or 'onec' in name.lower():
                continue
            dev_file = Path(dev_secrets[name]['file'])
            if target in LOCAL_CONNECTION_TARGETS:
                qa_file = SECRETS_DIR / name
                qa_file.write_text(rewrite_connection_string(dev_file.read_text()))
                os.chmod(qa_file, 0o600)
                secrets[name] = {'file': str(qa_file), 'name': f'gba-qa_{name}'}
            else:
                secrets[name] = {'file': str(dev_file), 'name': f'gba-qa_{name}'}
            service_secrets.append({'source': name, 'target': target})

        volumes = [{'type': 'volume', 'source': 'qa-dataprotection-keys', 'target': '/root/.aspnet/DataProtection-Keys'}]
        for volume in source.get('volumes', []):
            target = volume.get('target')
            if target in DATA_BIND_TARGETS:
                folder = STATE_DIR / 'concord' / Path(target).name
                folder.mkdir(parents=True, exist_ok=True)
                volumes.append({'type': 'bind', 'source': str(folder), 'target': target})
            elif volume.get('type') == 'bind' and volume.get('read_only') and target.startswith('/run/gba/'):
                volumes.append({'type': 'bind', 'source': volume['source'], 'target': target, 'read_only': True})

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
