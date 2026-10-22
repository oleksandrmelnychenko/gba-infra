#!/usr/bin/env python3
"""Verify live DEV/QA routing with disposable, non-business Mongo fixtures only.

Run with the NBA venv after both web services start. No tasks, contacts, sync or
generation endpoints are mutated; only a new __isolation_probe_<uuid> collection
is created, checked and dropped in each namespace.
"""
import argparse
import json
import os
import secrets
from pathlib import Path
import subprocess
import sys
from uuid import uuid4


def mongo_probe(dev_db, qa_db):
    if dev_db.name != 'gba_nba' or qa_db.name != 'gba_nba_qa':
        raise ValueError('Unexpected NBA namespace')
    name = '__isolation_probe_' + uuid4().hex
    created = []
    try:
        for environment, database in (('dev', dev_db), ('qa', qa_db)):
            database.create_collection(name)
            created.append(database)
            database[name].insert_one({'_id': 'same-key', 'environment': environment, 'status': 'open'})
        qa_db[name].update_one({'_id': 'same-key'}, {'$set': {'status': 'done'}})
        dev = dev_db[name].find_one({'_id': 'same-key'})
        qa = qa_db[name].find_one({'_id': 'same-key'})
        if dev != {'_id': 'same-key', 'environment': 'dev', 'status': 'open'} or qa != {
            '_id': 'same-key', 'environment': 'qa', 'status': 'done'
        }:
            raise RuntimeError('Mongo isolation probe did not preserve independent values')
    finally:
        for database in created:
            database.drop_collection(name)
    if any(name in database.list_collection_names() for database in (dev_db, qa_db)):
        raise RuntimeError('Mongo isolation fixture cleanup failed')
    return {'independentValues': True, 'independentMutation': True, 'fixturesRemoved': True}


def runtime_settings(service, port, settings_type):
    pid = int(subprocess.check_output(['systemctl', 'show', service, '--property=MainPID', '--value'], text=True).strip())
    if pid <= 0:
        raise RuntimeError('NBA service is not running')
    process = Path('/proc') / str(pid)
    command = process.joinpath('cmdline').read_bytes().decode().split('\0')
    if '--port' not in command or command[command.index('--port') + 1] != str(port):
        raise RuntimeError('NBA service port does not match its environment')
    environment = dict(item.split('=', 1) for item in process.joinpath('environ').read_bytes().decode().split('\0') if '=' in item)
    # Use exactly the running process overrides and its existing dotenv fallback.
    fields = settings_type.model_fields
    overrides = {key.lower(): value for key, value in environment.items() if key.lower() in fields}
    settings = settings_type(_env_file=process.joinpath('cwd').resolve() / '.env', **overrides)
    return pid, settings


def validate_proxy_environment(environment, expected_environment, port, api_key):
    """Validate the .NET field names actually consumed, never return any key value."""
    if environment.get('GbaNbaApi__Url') != f'http://host.docker.internal:{port}':
        raise ValueError('API NBA URL differs from its isolated runtime')
    if environment.get('GbaNbaApi__SourceEnvironment') != expected_environment:
        raise ValueError('API expected NBA source environment differs from its runtime')
    if 'GbaNbaApi__Key' in environment:
        raise ValueError('Obsolete NBA Key configuration must be removed; .NET consumes ApiKey')
    configured_key = environment.get('GbaNbaApi__ApiKey', '')
    if not configured_key or not secrets.compare_digest(configured_key, api_key):
        raise ValueError('API NBA ApiKey differs from its isolated runtime')
    return {'urlMatches': True, 'sourceEnvironmentMatches': True, 'apiKeyMatches': True}


def proxy_configuration(container, expected_environment, port, api_key):
    values = json.loads(subprocess.check_output(
        ['docker', 'inspect', '--format', '{{json .Config.Env}}', container], text=True))
    environment = dict(value.split('=', 1) for value in values if '=' in value)
    return validate_proxy_environment(environment, expected_environment, port, api_key)


def api_container(environment):
    if environment == 'qa':
        return 'data-concord-qa'
    # Compose-generated DEV container names are not the service key "data-concord".
    matches = subprocess.check_output(['docker', 'ps', '--filter', 'label=com.docker.compose.project=gba-dev',
        '--filter', 'label=com.docker.compose.service=data-concord', '--format', '{{.ID}}'], text=True).split()
    if len(matches) != 1:
        raise RuntimeError('Expected exactly one running DEV API container')
    return matches[0]


def verify(nba_repo, services_only=False):
    sys.path.insert(0, str(nba_repo))
    from app.core.config import Settings
    import httpx
    from pymongo import MongoClient
    from sqlalchemy import create_engine, text

    result, settings, clients = {}, {}, []
    try:
        for environment, service, port, database in (
            ('dev', 'gba-nba.service', 8002, 'ConcordDb_V5'),
            ('qa', 'gba-nba-qa.service', 8012, 'ConcordDb_V5_QA'),
        ):
            pid, config = runtime_settings(service, port, Settings)
            if config.source_environment != environment or config.db_name != database or not config.internal_api_key:
                raise RuntimeError('Running NBA source configuration does not match the expected environment')
            settings[environment] = config
            headers = {'X-Internal-Api-Key': config.internal_api_key, 'X-GBA-Source-Environment': environment}
            with httpx.Client(base_url=f'http://127.0.0.1:{port}', headers=headers, timeout=40) as api:
                source = api.get('/cockpit/source')
                source.raise_for_status()
                provenance = source.json()
                if provenance['sourceEnvironment'] != environment:
                    raise RuntimeError('Served NBA provenance does not match the running configuration')
                opposite = 'qa' if environment == 'dev' else 'dev'
                rejected = api.post('/cockpit/status', headers={'X-GBA-Source-Environment': opposite,
                    'Content-Type': 'application/json'}, content='{malformed')
                if rejected.status_code != 409 or rejected.json().get('detail') != 'source_environment_mismatch':
                    raise RuntimeError('Environment fence did not reject before mutation body validation')
            engine = create_engine(config.sqlalchemy_url, connect_args={'timeout': 15, 'login_timeout': 10})
            try:
                with engine.connect() as connection:
                    row = connection.execute(text("SELECT DB_NAME() AS db, IS_ROLEMEMBER('db_datareader') AS is_reader, HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'INSERT') AS can_insert")).mappings().one()
                    if row['db'] != database or row['is_reader'] != 1 or row['can_insert'] != 0:
                        raise RuntimeError('Actual SQL database or read-only permissions differ from expected')
                    sql = dict(row)
            finally:
                engine.dispose()
            result[environment] = {'pid': pid, 'port': port, 'sql': sql, 'mongo': config.mongo_db,
                'source': provenance, 'wrongEnvironmentMutationRejected': True}
            if not services_only:
                container = api_container(environment)
                result[environment]['apiConfiguration'] = proxy_configuration(container, environment, port, config.internal_api_key)
        if result['dev']['pid'] == result['qa']['pid']:
            raise RuntimeError('DEV and QA must not share process-local caches or locks')
        if settings['dev'].internal_api_key == settings['qa'].internal_api_key:
            raise RuntimeError('DEV and QA must have distinct internal API keys')
        if settings['qa'].generation_enabled or result['qa']['source']['generationReady']:
            raise RuntimeError('QA must remain uninitialized with generation disabled')
        for environment in ('dev', 'qa'):
            clients.append(MongoClient(settings[environment].mongo_uri, serverSelectionTimeoutMS=5000, socketTimeoutMS=10000))
        result['mongoProbe'] = mongo_probe(clients[0][settings['dev'].mongo_db], clients[1][settings['qa'].mongo_db])
        result['cacheIsolation'] = 'Distinct web process PIDs; module-level caches and threading locks are not shared.'
        result['apiRoutingVerified'] = not services_only
        return result
    finally:
        for client in clients:
            client.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--nba-repo', type=Path, default=Path('/root/projects/gba-nba'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--services-only', action='store_true', help='Pre-routing check only; full API configuration acceptance is explicitly skipped')
    args = parser.parse_args()
    try:
        result = verify(args.nba_repo, services_only=args.services_only)
    except Exception as error:
        # Connection exceptions can contain credentials; do not print their text.
        print(json.dumps({'verified': False, 'errorType': type(error).__name__}))
        return 1
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as file:
        json.dump(result, file, indent=2)
    args.output.chmod(0o600)
    print(json.dumps({'verified': True, 'apiRoutingVerified': result['apiRoutingVerified'], 'evidence': str(args.output), 'probeFixturesRemoved': True}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
