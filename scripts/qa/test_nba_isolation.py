import importlib.util
from pathlib import Path
import unittest

import mongomock


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepare = load('prepare-nba-qa')
verify = load('verify-nba-isolation')


class NbaIsolationTests(unittest.TestCase):
    def test_proxy_verification_rejects_unused_key_wrong_key_route_and_environment(self):
        correct = {'GbaNbaApi__Url': 'http://host.docker.internal:8012',
            'GbaNbaApi__SourceEnvironment': 'qa', 'GbaNbaApi__ApiKey': 'qa-fixture-secret'}
        result = verify.validate_proxy_environment(correct, 'qa', 8012, 'qa-fixture-secret')
        self.assertTrue(all(result.values()))
        self.assertNotIn('qa-fixture-secret', str(result))
        for change in (
            {'GbaNbaApi__Url': 'http://host.docker.internal:8002'},
            {'GbaNbaApi__SourceEnvironment': 'dev'},
            {'GbaNbaApi__ApiKey': 'dev-fixture-secret'},
            {'GbaNbaApi__ApiKey': '', 'GbaNbaApi__Key': 'qa-fixture-secret'},
        ):
            with self.subTest(change=tuple(change)):
                with self.assertRaises(ValueError) as error:
                    verify.validate_proxy_environment({**correct, **change}, 'qa', 8012, 'qa-fixture-secret')
                self.assertNotIn('fixture-secret', str(error.exception))

    def test_preparation_cannot_inherit_dev_business_namespaces_or_downstream_routes(self):
        settings = prepare.qa_settings({'DB_HOST': 'fixture', 'DB_USER': 'readonly', 'DB_PASSWORD': 'fixture',
            'MONGO_URI': 'mongodb://fixture', 'DB_NAME': 'ConcordDb_V5', 'MONGO_DB': 'gba_nba',
            'GENERATION_ENABLED': 'true', 'RECO_URL': 'http://dev:8000'}, 'qa-key')
        self.assertEqual('ConcordDb_V5_QA', settings['DB_NAME'])
        self.assertEqual('gba_nba_qa', settings['MONGO_DB'])
        self.assertEqual('false', settings['GENERATION_ENABLED'])
        self.assertEqual('false', settings['WARMERS_ENABLED'])
        self.assertEqual('http://127.0.0.1:9', settings['RECO_URL'])
        self.assertEqual('http://127.0.0.1:9', settings['PROCURE_URL'])
        self.assertEqual('qa-key', settings['INTERNAL_API_KEY'])

    def test_runtime_probe_uses_only_temporary_collections_and_cleans_them(self):
        client = mongomock.MongoClient()
        for database in (client.gba_nba, client.gba_nba_qa):
            database.tasks.insert_one({'_id': 'untouched', 'status': 'open'})
        result = verify.mongo_probe(client.gba_nba, client.gba_nba_qa)
        self.assertTrue(all(result.values()))
        for database in (client.gba_nba, client.gba_nba_qa):
            self.assertEqual(['tasks'], database.list_collection_names())
            self.assertEqual('open', database.tasks.find_one({})['status'])

    def test_probe_refuses_shared_namespace_before_writing(self):
        client = mongomock.MongoClient()
        with self.assertRaises(ValueError):
            verify.mongo_probe(client.gba_nba, client.gba_nba)
        self.assertEqual([], client.gba_nba.list_collection_names())


if __name__ == '__main__':
    unittest.main()
