import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AuthDefaultsTests(unittest.TestCase):
    def test_all_stacks_reject_legacy_gba_hs256_by_default(self):
        setting = '${GBA_ACCEPT_LEGACY_HS256_TOKENS:-false}'
        for compose_name in (
            "docker-compose.dev.yml",
            "docker-compose.prod.yml",
            "docker-compose.e2e.yml",
        ):
            compose = (ROOT / compose_name).read_text(encoding="utf-8")
            self.assertEqual(2, compose.count(setting), compose_name)
            self.assertNotIn("GBA_ACCEPT_LEGACY_HS256_TOKENS:-true", compose)

    def test_versioned_environment_examples_are_strict(self):
        for example_name in (".env.example", ".env.dev.example"):
            example = (ROOT / example_name).read_text(encoding="utf-8")
            self.assertIn("GBA_ACCEPT_LEGACY_HS256_TOKENS=false", example)

    def test_all_issuers_use_short_lived_access_tokens(self):
        setting = 'Security__AccessTokenLifetimeMinutes: "15"'
        for compose_name in (
            "docker-compose.dev.yml",
            "docker-compose.prod.yml",
            "docker-compose.e2e.yml",
        ):
            compose = (ROOT / compose_name).read_text(encoding="utf-8")
            self.assertEqual(1, compose.count(setting), compose_name)
            self.assertNotIn('Security__AccessTokenLifetimeMinutes: "60"', compose)


if __name__ == "__main__":
    unittest.main()
