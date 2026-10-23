import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
import tempfile
from unittest.mock import patch


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepare = load('prepare-products-qa')
verify = load('verify-products-isolation')
route = load('route-products-qa')


class MemoryRedis:
    def __init__(self, database):
        self.connection_pool = SimpleNamespace(connection_kwargs={'db': database})
        self.values = {'business-cache': 'unchanged'}

    def set(self, key, value, ex, nx=False):
        assert ex <= 60
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def get(self, key):
        return self.values.get(key)

    def delete(self, key):
        self.values.pop(key, None)

    def exists(self, key):
        return key in self.values


class ProductsIsolationTests(unittest.TestCase):
    def test_preparation_overrides_all_dev_source_and_cache_settings(self):
        products = dict(db_name='ConcordDb_V5', redis_db=2, cache_namespace='products',
                        internal_api_key='dev-key', warmers_enabled=True, api_port=8005,
                        source_history_start_date='2025-01-01', velocity_window_days=180)
        readonly = dict(db_host='sql-fixture', db_user='readonly', db_password='fixture')
        result = prepare.qa_settings(products, readonly, 'qa-key')
        self.assertEqual(('ConcordDb_V5_QA', '12', 'products-qa', 'qa', 'false', '8015'),
                         tuple(result[k] for k in ('DB_NAME', 'REDIS_DB', 'CACHE_NAMESPACE',
                               'SOURCE_ENVIRONMENT', 'WARMERS_ENABLED', 'API_PORT')))
        self.assertEqual('180', result['VELOCITY_WINDOW_DAYS'])
        for bad in ('', 'dev-key'):
            with self.assertRaises(ValueError):
                prepare.qa_settings(products, readonly, bad)
        with self.assertRaises(ValueError):
            prepare.qa_settings(products, readonly | {'db_user': 'sa'}, 'qa-key')

    def test_proxy_verifier_rejects_dev_route_missing_lineage_and_reused_or_obsolete_key(self):
        good = dict(ProductsApi__Url='http://host.docker.internal:8015',
                    ProductsApi__SourceEnvironment='qa', ProductsApi__ApiKey='qa-key')
        self.assertTrue(all(verify.validate_proxy_environment(good, 'qa', 8015, 'qa-key').values()))
        for change in ({'ProductsApi__Url': 'http://host.docker.internal:8005'},
                       {'ProductsApi__SourceEnvironment': 'dev'}, {'ProductsApi__SourceEnvironment': ''},
                       {'ProductsApi__ApiKey': 'dev-key'}, {'ProductsApi__Key': 'qa-key'}):
            with self.assertRaises(ValueError):
                verify.validate_proxy_environment(good | change, 'qa', 8015, 'qa-key')

    def test_route_only_changes_qa_products_fields(self):
        document = {'services': {'data-concord-qa': {'environment': {
            'ProductsApi__Url': 'http://host.docker.internal:8005', 'Other__Key': 'preserved'}}}}
        routing = dict(ProductsApi__Url='http://host.docker.internal:8015',
                       ProductsApi__SourceEnvironment='qa', ProductsApi__ApiKey='qa-key')
        updated = route.apply_routing(document, routing)
        self.assertEqual('preserved', updated['services']['data-concord-qa']['environment']['Other__Key'])
        with self.assertRaises(ValueError):
            route.apply_routing(document, routing | {'ProductsApi__SourceEnvironment': 'dev'})

    def test_runtime_settings_decodes_structured_process_overrides(self):
        helper = verify.runtime_helper()
        class FixtureSettings:
            model_fields = {'cors_allow_origins': SimpleNamespace(annotation=list[str]),
                            'source_environment': SimpleNamespace(annotation=str)}
            def __init__(self, **values):
                self.values = values
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = root / '123'
            process.mkdir()
            (process / 'cwd').mkdir()
            (process / 'cmdline').write_bytes(b'python\0-m\0uvicorn\0--port\08015\0')
            (process / 'environ').write_bytes(b'CORS_ALLOW_ORIGINS=[]\0SOURCE_ENVIRONMENT=qa\0')
            with patch.object(helper.subprocess, 'check_output', return_value='123'), patch.object(
                helper, 'Path', side_effect=lambda value: root if value == '/proc' else Path(value)
            ):
                pid, config = helper.runtime_settings('fixture.service', 8015, FixtureSettings)
            self.assertEqual(123, pid)
            self.assertEqual([], config.values['cors_allow_origins'])
            self.assertEqual('qa', config.values['source_environment'])

    def test_cache_probe_is_independent_and_removes_only_its_fixture(self):
        dev, qa = MemoryRedis(2), MemoryRedis(12)
        self.assertTrue(all(verify.redis_probe(dev, qa).values()))
        self.assertEqual({'business-cache': 'unchanged'}, dev.values)
        self.assertEqual({'business-cache': 'unchanged'}, qa.values)
        with self.assertRaises(ValueError):
            verify.redis_probe(dev, dev)


if __name__ == '__main__':
    unittest.main()
