import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "scripts" / "verify-secure-exposure.sh"


class SecureExposureTests(unittest.TestCase):
    def run_check(
        self,
        *,
        ss_output: str = "",
        docker_output: str = "",
        gateway: str | None = "172.17.0.1",
        env_file_gateway: str | None = None,
        validate_config_only: bool = False,
        ss_exit: int = 0,
        docker_exit: int = 0,
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            fake_bin = Path(directory)
            for name, variable, exit_code in (
                ("ss", "FAKE_SS_OUTPUT", ss_exit),
                ("docker", "FAKE_DOCKER_OUTPUT", docker_exit),
            ):
                executable = fake_bin / name
                executable.write_text(
                    f'#!/bin/sh\nprintf "%s\\n" "${{{variable}:-}}"\nexit {exit_code}\n',
                    encoding="utf-8",
                )
                executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

            environment = os.environ.copy()
            environment.update({
                "FAKE_DOCKER_OUTPUT": docker_output,
                "FAKE_SS_OUTPUT": ss_output,
                "PATH": f"{fake_bin}:/usr/bin:/bin",
            })
            if gateway is None:
                environment.pop("GBA_DOCKER_HOST_GATEWAY_IP", None)
            else:
                environment["GBA_DOCKER_HOST_GATEWAY_IP"] = gateway

            arguments = [str(CHECK)]
            if env_file_gateway is not None:
                env_file = fake_bin / ".env.dev"
                env_file.write_text(
                    f"GBA_DOCKER_HOST_GATEWAY_IP={env_file_gateway}\n",
                    encoding="utf-8",
                )
                arguments.extend(("--env-file", str(env_file)))
            if validate_config_only:
                arguments.append("--validate-config-only")

            return subprocess.run(
                arguments,
                check=False,
                capture_output=True,
                env=environment,
                text=True,
            )

    def test_allows_loopback_and_the_private_caddy_gateway(self):
        result = self.run_check(
            ss_output="\n".join((
                "LISTEN 0 4096 127.0.0.1:8081 0.0.0.0:*",
                "LISTEN 0 4096 172.17.0.1:8081 0.0.0.0:*",
                "LISTEN 0 4096 172.17.0.1:8083 0.0.0.0:*",
                "LISTEN 0 4096 0.0.0.0:443 0.0.0.0:*",
            )),
            docker_output=(
                "gba-dev-gba-ecommerce 127.0.0.1:8081->3000/tcp, "
                "172.17.0.1:8081->3000/tcp"
            ),
        )

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_rejects_a_wildcard_console_listener(self):
        result = self.run_check(
            ss_output="LISTEN 0 4096 0.0.0.0:8083 0.0.0.0:*",
            docker_output="gba-dev-gba-console 0.0.0.0:8083->80/tcp",
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("Unsafe public listeners", result.stdout)

    def test_rejects_a_public_address(self):
        result = self.run_check(
            ss_output="LISTEN 0 4096 203.0.113.10:8081 0.0.0.0:*",
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("203.0.113.10:8081", result.stdout)

    def test_rejects_a_non_private_configured_gateway(self):
        result = self.run_check(gateway="203.0.113.10")

        self.assertNotEqual(0, result.returncode)
        self.assertIn("Unsafe GBA_DOCKER_HOST_GATEWAY_IP", result.stderr)

    def test_rejects_an_address_specific_public_docker_mapping(self):
        result = self.run_check(
            docker_output="gba-dev-gba-ecommerce 203.0.113.10:8081->3000/tcp",
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("Unsafe Docker published ports", result.stdout)

    def test_reads_a_private_gateway_from_the_compose_env_file(self):
        result = self.run_check(
            gateway=None,
            env_file_gateway="10.20.30.40",
            ss_output="LISTEN 0 4096 10.20.30.40:8081 0.0.0.0:*",
            docker_output="gba-dev-gba-ecommerce 10.20.30.40:8081->3000/tcp",
        )

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_preflight_rejects_a_public_gateway_from_the_compose_env_file(self):
        result = self.run_check(
            gateway=None,
            env_file_gateway="203.0.113.10",
            validate_config_only=True,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("Unsafe GBA_DOCKER_HOST_GATEWAY_IP", result.stderr)

    def test_fails_closed_when_host_listeners_cannot_be_read(self):
        result = self.run_check(ss_exit=1)

        self.assertNotEqual(0, result.returncode)
        self.assertIn("ss failed", result.stderr)
        self.assertNotIn("OK:", result.stdout)

    def test_fails_closed_when_docker_mappings_cannot_be_read(self):
        result = self.run_check(docker_exit=1)

        self.assertNotEqual(0, result.returncode)
        self.assertIn("docker ps failed", result.stderr)
        self.assertNotIn("OK:", result.stdout)


if __name__ == "__main__":
    unittest.main()
