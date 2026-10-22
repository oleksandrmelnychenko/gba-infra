#!/usr/bin/env python3
"""Update private compose configs only, after the isolated NBA services are verified."""
import json
import os
from pathlib import Path
import tempfile


def write_json(path, value):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.nba-routing-')
    try:
        with os.fdopen(fd, 'w') as file:
            json.dump(value, file, indent=2)
            file.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    dev_path = Path('/private/claude-deploy-reopen-20261014/candidate-compose.private.json')
    qa_path = Path('/private/gba-qa/qa-compose.private.json')
    routing = json.loads(Path('/private/gba-qa/nba/api-routing.json').read_text())
    if routing.get('GbaNbaApi__SourceEnvironment') != 'qa' or routing.get('GbaNbaApi__Url') != 'http://host.docker.internal:8012':
        raise SystemExit('QA NBA routing must target the isolated service')
    documents = [(dev_path, json.loads(dev_path.read_text()), 'data-concord'),
                 (qa_path, json.loads(qa_path.read_text()), 'data-concord-qa')]
    for _, value, service in documents:
        current = value['services'][service]['environment'].get('GbaNbaApi__Url')
        allowed = ('http://host.docker.internal:8002',) if service == 'data-concord' else (
            'http://host.docker.internal:8002', 'http://host.docker.internal:8012')
        if current not in allowed:
            raise SystemExit('Unexpected current NBA URL; review the runtime before rerouting')
    documents[0][1]['services']['data-concord']['environment']['GbaNbaApi__SourceEnvironment'] = 'dev'
    documents[1][1]['services']['data-concord-qa']['environment'].update(routing)
    for path, value, _ in documents:
        write_json(path, value)
    print('Prepared DEV expected environment and isolated QA NBA routing; API containers were not restarted.')


if __name__ == '__main__':
    main()
