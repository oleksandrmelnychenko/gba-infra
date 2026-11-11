from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "local-dev.sh"

REPOSITORIES = (
    "gba-server",
    "gba-ecommerce-api",
    "gba_client",
    "gba_console",
    "gba_ecommerce",
)

SECRETS = (
    "Anthropic__ApiKey",
    "ConnectionStrings__AmgOneCConnectionString",
    "ConnectionStrings__FenixOneCConnectionString",
    "ConnectionStrings__LocalConnectionString",
    "ConnectionStrings__LocalConnectionStringDataAnalitic",
    "ConnectionStrings__LocalIdentityConnectionString",
    "ConnectionStrings__RemoteConnectionString",
    "ConnectionStrings__RemoteIdentityConnectionString",
    "ConnectionStrings__VehicleRegistryConnectionString",
    "ConnectionStrings__VehicleRegistryMigrationConnectionString",
    "EcommerceInternalAuth__ApiKey",
    "Elasticsearch__AppPassword",
    "Elasticsearch__Password",
    "ForecastApi__ApiKey",
    "GbaNbaApi__ApiKey",
    "ProcureCostInternalAuth__ApiKey",
    "PricingApi__ApiKey",
    "ProcurementApi__ApiKey",
    "ProductsApi__ApiKey",
    "RecommendationApi__ApiKey",
    "Security__JwtKey",
    "Security__JwtPrivateKey",
    "Security__JwtPublicKey",
    "ShopAuth__JwtKey",
    "Security__PriceEncryptionIV",
    "Security__PriceEncryptionKey",
    "SolvencyApi__ApiKey",
    "Storefront__PriceTokenSecret",
)


def make_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class LocalDevSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.projects = Path(self.temp.name) / "projects"
        self.infra = self.projects / "gba-infra"
        self.scripts = self.infra / "scripts"
        self.secrets = self.infra / "secrets" / "dev"
        self.fake_bin = Path(self.temp.name) / "bin"
        self.docker_log = Path(self.temp.name) / "docker.log"

        self.scripts.mkdir(parents=True)
        self.secrets.mkdir(parents=True)
        self.fake_bin.mkdir()
        shutil.copy2(SCRIPT, self.scripts / "local-dev.sh")
        shutil.copy2(ROOT / ".env.dev.example", self.infra / ".env.dev.example")
        shutil.copytree(
            ROOT / "secrets" / "dev.example",
            self.infra / "secrets" / "dev.example",
        )
        make_executable(
            self.scripts / "verify-secure-exposure.sh",
            "#!/bin/sh\nexit 0\n",
        )

        for repository in REPOSITORIES:
            (self.projects / repository / ".git").mkdir(parents=True)

        env_file = self.infra / ".env.dev"
        env_file.write_text("SAFE_VALUE=configured\n", encoding="utf-8")
        env_file.chmod(0o600)
        for secret in SECRETS:
            value = "public-key\n" if secret == "Security__JwtPublicKey" else "secret\n"
            path = self.secrets / secret
            path.write_text(value, encoding="utf-8")
            path.chmod(0o600)

        make_executable(
            self.fake_bin / "docker",
            '#!/bin/sh\nprintf "%s\\n" "$*" >> "$FAKE_DOCKER_LOG"\nexit 0\n',
        )
        make_executable(
            self.fake_bin / "openssl",
            "#!/bin/sh\n"
            "output=\n"
            "previous=\n"
            "for argument in \"$@\"; do\n"
            "  if [ \"$previous\" = -out ]; then output=$argument; fi\n"
            "  previous=$argument\n"
            "done\n"
            "if [ -n \"$output\" ]; then\n"
            "  printf 'public-key\\n' > \"$output\"\n"
            "else\n"
            "  printf 'public-key\\n'\n"
            "fi\n",
        )

        self.environment = {
            **os.environ,
            "FAKE_DOCKER_LOG": str(self.docker_log),
            "PATH": f"{self.fake_bin}:/usr/bin:/bin",
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def fake_placeholder_scan(self, exit_code: int) -> None:
        make_executable(
            self.fake_bin / "grep",
            "#!/bin/sh\n"
            "for argument in \"$@\"; do\n"
            "  if [ \"$argument\" = CHANGE_ME ]; then\n"
            f"    exit {exit_code}\n"
            "  fi\n"
            "done\n"
            "exec /usr/bin/grep \"$@\"\n",
        )

    def run_script(self, command: str = "check") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            (str(self.scripts / "local-dev.sh"), command),
            check=False,
            capture_output=True,
            env=self.environment,
            text=True,
        )

    def docker_calls(self) -> str:
        if not self.docker_log.exists():
            return ""
        return self.docker_log.read_text(encoding="utf-8")

    def test_placeholder_match_still_blocks_before_compose_config(self) -> None:
        self.fake_placeholder_scan(0)

        result = self.run_script()

        self.assertNotEqual(0, result.returncode)
        self.assertIn("Placeholder values remain", result.stderr)
        self.assertNotIn("config --quiet", self.docker_calls())

    def test_clean_placeholder_scan_preserves_successful_check(self) -> None:
        self.fake_placeholder_scan(1)
        (self.secrets / "ShopAuth__JwtKey").chmod(0o640)

        result = self.run_script()

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("configuration is complete", result.stdout)
        self.assertIn("config --quiet", self.docker_calls())

    def test_placeholder_scan_error_blocks_up_before_compose(self) -> None:
        self.fake_placeholder_scan(2)

        result = self.run_script("up")

        self.assertNotEqual(0, result.returncode)
        self.assertIn("Unable to scan", result.stderr)
        self.assertNotIn("config --quiet", self.docker_calls())
        self.assertNotIn("up -d", self.docker_calls())

    def test_permission_scan_error_blocks_before_compose_config(self) -> None:
        make_executable(
            self.fake_bin / "find",
            "#!/bin/sh\n"
            "for argument in \"$@\"; do\n"
            "  if [ \"$argument\" = -perm ]; then\n"
            "    echo 'simulated traversal failure' >&2\n"
            "    exit 2\n"
            "  fi\n"
            "done\n"
            "exec /usr/bin/find \"$@\"\n",
        )

        result = self.run_script()

        self.assertNotEqual(0, result.returncode)
        self.assertIn("Unable to inspect secret file permissions", result.stderr)
        self.assertNotIn("config --quiet", self.docker_calls())

    def test_world_readable_env_file_is_rejected(self) -> None:
        (self.infra / ".env.dev").chmod(0o644)

        result = self.run_script()

        self.assertNotEqual(0, result.returncode)
        self.assertIn("world-accessible", result.stderr)
        self.assertNotIn("config --quiet", self.docker_calls())

    def test_missing_procure_cost_secret_blocks_before_compose_config(self) -> None:
        (self.secrets / "ProcureCostInternalAuth__ApiKey").unlink()

        result = self.run_script()

        self.assertNotEqual(0, result.returncode)
        self.assertIn("ProcureCostInternalAuth__ApiKey", result.stderr)
        self.assertNotIn("config --quiet", self.docker_calls())

    def test_init_adds_a_missing_required_template_without_overwriting_existing_files(self) -> None:
        (self.secrets / "ProcureCostInternalAuth__ApiKey").unlink()
        existing = self.secrets / "ShopAuth__JwtKey"
        original = existing.read_text(encoding="utf-8")

        result = self.run_script("init")

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn(
            "CHANGE_ME",
            (self.secrets / "ProcureCostInternalAuth__ApiKey").read_text(
                encoding="utf-8"
            ),
        )
        self.assertEqual(original, existing.read_text(encoding="utf-8"))

    def test_group_executable_secret_is_rejected(self) -> None:
        (self.secrets / "ShopAuth__JwtKey").chmod(0o650)

        result = self.run_script()

        self.assertNotEqual(0, result.returncode)
        self.assertIn("group-writable/executable", result.stderr)
        self.assertNotIn("config --quiet", self.docker_calls())

    def test_required_secret_symlink_is_rejected(self) -> None:
        target = Path(self.temp.name) / "external-secret"
        target.write_text("secret\n", encoding="utf-8")
        target.chmod(0o600)
        secret = self.secrets / "Anthropic__ApiKey"
        secret.unlink()
        secret.symlink_to(target)

        result = self.run_script()

        self.assertNotEqual(0, result.returncode)
        self.assertIn("only regular files", result.stderr)
        self.assertNotIn("config --quiet", self.docker_calls())

    def test_required_secret_directory_is_rejected(self) -> None:
        secret = self.secrets / "Anthropic__ApiKey"
        secret.unlink()
        secret.mkdir()

        result = self.run_script()

        self.assertNotEqual(0, result.returncode)
        self.assertIn("only regular files", result.stderr)
        self.assertNotIn("config --quiet", self.docker_calls())

    def test_unlisted_symlink_is_rejected_before_recursive_scan(self) -> None:
        target = Path(self.temp.name) / "external-secret"
        target.write_text("secret\n", encoding="utf-8")
        (self.secrets / "retired-secret").symlink_to(target)

        result = self.run_script()

        self.assertNotEqual(0, result.returncode)
        self.assertIn("only regular files", result.stderr)
        self.assertNotIn("config --quiet", self.docker_calls())

    def test_symlinked_env_file_is_rejected_by_check_and_init(self) -> None:
        target = Path(self.temp.name) / "external.env"
        target.write_text("SAFE_VALUE=configured\n", encoding="utf-8")
        original_mode = target.stat().st_mode
        (self.infra / ".env.dev").unlink()
        (self.infra / ".env.dev").symlink_to(target)

        check_result = self.run_script("check")
        init_result = self.run_script("init")

        self.assertNotEqual(0, check_result.returncode)
        self.assertNotEqual(0, init_result.returncode)
        self.assertIn("not a symbolic link", check_result.stderr)
        self.assertEqual(original_mode, target.stat().st_mode)
        self.assertNotIn("config --quiet", self.docker_calls())

    def test_symlinked_secret_directory_is_rejected_by_check_and_init(self) -> None:
        real_secrets = self.infra / "secrets" / "external-dev"
        self.secrets.rename(real_secrets)
        self.secrets.symlink_to(real_secrets, target_is_directory=True)

        check_result = self.run_script("check")
        init_result = self.run_script("init")

        self.assertNotEqual(0, check_result.returncode)
        self.assertNotEqual(0, init_result.returncode)
        self.assertIn("not a symbolic link", check_result.stderr)
        self.assertNotIn("config --quiet", self.docker_calls())

    def test_clean_init_creates_the_procure_cost_secret_template(self) -> None:
        shutil.rmtree(self.secrets)
        (self.infra / ".env.dev").unlink()

        result = self.run_script("init")

        procure_secret = self.secrets / "ProcureCostInternalAuth__ApiKey"
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue(procure_secret.is_file())
        self.assertIn("CHANGE_ME", procure_secret.read_text(encoding="utf-8"))

        for secret in (
            "RecommendationApi__ApiKey",
            "ProcurementApi__ApiKey",
            "GbaNbaApi__ApiKey",
            "SolvencyApi__ApiKey",
            "PricingApi__ApiKey",
            "ProductsApi__ApiKey",
            "ForecastApi__ApiKey",
        ):
            self.assertIn(
                "CHANGE_ME",
                (self.secrets / secret).read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
