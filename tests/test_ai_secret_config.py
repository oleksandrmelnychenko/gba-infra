import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AI_SECRETS = {
    "RecommendationApi__ApiKey": "RecommendationApi:ApiKey",
    "ProcurementApi__ApiKey": "ProcurementApi:ApiKey",
    "GbaNbaApi__ApiKey": "GbaNbaApi:ApiKey",
    "SolvencyApi__ApiKey": "SolvencyApi:ApiKey",
    "PricingApi__ApiKey": "PricingApi:ApiKey",
    "ProductsApi__ApiKey": "ProductsApi:ApiKey",
    "ForecastApi__ApiKey": "ForecastApi:ApiKey",
}


class AiSecretConfigTests(unittest.TestCase):
    def test_dev_and_e2e_mount_ai_keys_as_files_not_environment_values(self):
        for compose_name in ("docker-compose.dev.yml", "docker-compose.e2e.yml"):
            compose = (ROOT / compose_name).read_text(encoding="utf-8")
            environment = compose.split("environment:", 1)[1].split("secrets:", 1)[0]

            for source, target in AI_SECRETS.items():
                self.assertNotIn(source, environment, compose_name)
                self.assertIn(f"source: {source}", compose, compose_name)
                self.assertIn(f"target: {target}", compose, compose_name)
                self.assertIn(
                    f"file: ./secrets/dev/{source}",
                    compose,
                    compose_name,
                )

    def test_tracked_compose_contains_no_dev_internal_key_literals(self):
        for compose_name in ("docker-compose.dev.yml", "docker-compose.e2e.yml"):
            compose = (ROOT / compose_name).read_text(encoding="utf-8")
            self.assertIsNone(
                re.search(r"[a-z]+_dev_internal_[0-9a-f]+", compose),
                compose_name,
            )

    def test_every_ai_secret_has_a_safe_template(self):
        for source in AI_SECRETS:
            value = (ROOT / "secrets" / "dev.example" / source).read_text(
                encoding="utf-8"
            )
            self.assertTrue(value.startswith("CHANGE_ME_"), source)


if __name__ == "__main__":
    unittest.main()
