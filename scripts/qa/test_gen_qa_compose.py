import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location('qa_generator', Path(__file__).with_name('gen-qa-compose.py'))
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


class ProductHistoryQaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        generator.DEV_COMPOSE = root / 'dev.json'
        generator.STATE_DIR = root / 'qa'
        generator.SECRETS_DIR = generator.STATE_DIR / 'secrets'
        generator.OUTPUT = generator.STATE_DIR / 'qa-compose.private.json'
        source_file = root / 'source.txt'
        source_file.write_text('synthetic-readonly-value')
        self.dev = {'secrets': {}, 'services': {}}
        targets = sorted(generator.HISTORY_READONLY_TARGETS) + ['ConnectionStrings:FenixOneCConnectionString']
        secrets = []
        for index, target in enumerate(targets):
            name = f'onec-{index}'
            self.dev['secrets'][name] = {'file': str(source_file)}
            secrets.append({'source': name, 'target': target})
        for name in generator.SERVICES:
            self.dev['services'][name] = {'image': 'synthetic:1', 'environment': {
                'ProductSourceHistory__Enabled': 'true',
                'ProductSourceHistory__OwnExpectedDatabase': 'ConcordDb_V5',
                'ProductSourceHistory__OwnExpectedServer': 'dev-sql',
                'DataSync__Enabled': 'true',
            }, 'secrets': secrets.copy(), 'volumes': [{'type': 'bind',
                'source': generator.SOURCE_PACING_LOCK, 'target': generator.SOURCE_PACING_LOCK,
                'read_only': False}]}

    def generate(self):
        generator.DEV_COMPOSE.write_text(json.dumps(self.dev))
        generator.main()
        return json.loads(generator.OUTPUT.read_text())

    def test_nba_refresh_preserves_qa_route_and_separate_key(self):
        source = self.dev['services']['data-concord']['environment']
        source.update({'GbaNbaApi__Url': 'http://host.docker.internal:8002', 'GbaNbaApi__Key': 'dev-secret'})
        folder = generator.STATE_DIR / 'nba'
        folder.mkdir(parents=True)
        (folder / 'api-key').write_text('qa-fixture-secret')
        api = self.generate()['services']['data-concord-qa']['environment']
        self.assertEqual('http://host.docker.internal:8012', api['GbaNbaApi__Url'])
        self.assertEqual('qa-fixture-secret', api['GbaNbaApi__Key'])
        self.assertEqual('qa', api['GbaNbaApi__SourceEnvironment'])

    def test_nba_refresh_refuses_silent_shared_dev_fallback(self):
        self.dev['services']['data-concord']['environment']['GbaNbaApi__Url'] = 'http://host.docker.internal:8002'
        with self.assertRaisesRegex(SystemExit, 'isolated QA NBA'):
            self.generate()

    def test_conversion_flags_default_off_and_preserve_existing_qa_selection(self):
        result = self.generate()
        api = result['services']['data-concord-qa']['environment']
        self.assertEqual('false', api['GBA_LIBREOFFICE_POOL_ENABLED'])
        self.assertEqual('false', api['GBA_DOCUMENT_CONVERSION_TIMINGS'])
        api['GBA_LIBREOFFICE_POOL_ENABLED'] = 'true'
        api['GBA_DOCUMENT_CONVERSION_TIMINGS'] = 'true'
        generator.OUTPUT.write_text(json.dumps(result))
        refreshed = self.generate()['services']['data-concord-qa']['environment']
        self.assertEqual('true', refreshed['GBA_LIBREOFFICE_POOL_ENABLED'])
        self.assertEqual('true', refreshed['GBA_DOCUMENT_CONVERSION_TIMINGS'])

    def test_history_gets_only_readonly_sources_and_shared_pacing_with_qa_database(self):
        result = self.generate()
        api = result['services']['data-concord-qa']
        self.assertEqual(generator.HISTORY_READONLY_TARGETS, {x['target'] for x in api['secrets']})
        self.assertEqual('ConcordDb_V5_QA', api['environment']['ProductSourceHistory__OwnExpectedDatabase'])
        self.assertEqual('false', api['environment']['DataSync__Enabled'])
        self.assertEqual(1, sum(x.get('target') == generator.SOURCE_PACING_LOCK for x in api['volumes']))
        analytics = result['services']['data-analytics-qa']
        self.assertEqual([], analytics['secrets'])
        self.assertEqual('false', analytics['environment']['ProductSourceHistory__Enabled'])

    def test_disabled_history_preserves_no_source_access(self):
        self.dev['services']['data-concord']['environment']['ProductSourceHistory__Enabled'] = 'false'
        api = self.generate()['services']['data-concord-qa']
        self.assertEqual([], api['secrets'])
        self.assertFalse(any(x.get('target') == generator.SOURCE_PACING_LOCK for x in api['volumes']))

    def test_missing_readonly_source_is_refused(self):
        self.dev['services']['data-concord']['secrets'].pop(0)
        with self.assertRaisesRegex(SystemExit, 'readonly'):
            self.generate()

    def test_missing_shared_pacing_is_refused(self):
        self.dev['services']['data-concord']['volumes'] = []
        with self.assertRaisesRegex(SystemExit, 'pacing'):
            self.generate()

    def test_unknown_local_target_cannot_be_carried_to_qa(self):
        self.dev['services']['data-concord']['environment']['ProductSourceHistory__OwnExpectedDatabase'] = 'unexpected'
        with self.assertRaisesRegex(SystemExit, 'known DEV database'):
            self.generate()


if __name__ == '__main__':
    unittest.main()
