import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HARDEN = ROOT / "scripts" / "harden-docker-firewall.sh"
VERIFY = ROOT / "scripts" / "verify-ai-firewall.sh"


def executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class AiFirewallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.fake_bin = Path(self.temp.name)
        self.log = self.fake_bin / "calls.log"
        self.ipv6_state = self.fake_bin / "disable_ipv6"
        self.ipv6_state.write_text("0\n", encoding="utf-8")
        executable(
            self.fake_bin / "iptables",
            "#!/bin/sh\n"
            "printf 'iptables %s\\n' \"$*\" >> \"$FIREWALL_LOG\"\n"
            "if [ \"${1:-}\" = -S ]; then\n"
            "  printf '%s\\n' \"${IPV4_RULES:-}\"\n"
            "  exit 0\n"
            "fi\n"
            "[ \"${1:-}\" != -C ]\n",
        )
        executable(
            self.fake_bin / "ip6tables",
            "#!/bin/sh\n"
            "printf 'ip6tables %s\\n' \"$*\" >> \"$FIREWALL_LOG\"\n"
            "if [ \"${1:-}\" = -S ]; then\n"
            "  printf '%s\\n' \"${IPV6_RULES:-}\"\n"
            "  exit 0\n"
            "fi\n"
            "[ \"${1:-}\" != -C ]\n",
        )
        self.env = {
            **os.environ,
            "FIREWALL_LOG": str(self.log),
            "GBA_IPV6_DISABLE_STATE_FILE": str(self.ipv6_state),
            "PATH": f"{self.fake_bin}:/usr/bin:/bin",
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_hardening_installs_ipv4_and_ipv6_ai_drop_rules(self):
        result = subprocess.run(
            (str(HARDEN), "eth-test"),
            check=False,
            capture_output=True,
            env=self.env,
            text=True,
        )

        calls = self.log.read_text(encoding="utf-8")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("iptables -I INPUT 1 -i eth-test -p tcp -m multiport --dports 8000:8006,8012,8015 -j DROP", calls)
        self.assertIn("ip6tables -I INPUT 1 -i eth-test -p tcp -m multiport --dports 8000:8006,8012,8015 -j DROP", calls)

    def test_verifier_fails_when_drop_rule_is_missing(self):
        result = subprocess.run(
            (str(VERIFY), "eth-test"),
            check=False,
            capture_output=True,
            env=self.env,
            text=True,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("missing or shadowed", result.stderr)

    def test_verifier_accepts_only_first_position_drop_rules(self):
        self.env["IPV4_RULES"] = (
            "-A INPUT -i eth-test -p tcp -m multiport --dports 8000:8006,8012,8015 -j DROP"
        )
        self.env["IPV6_RULES"] = (
            "-A INPUT -i eth-test -p tcp -m multiport --dports 8000:8006,8012,8015 -j DROP"
        )

        result = subprocess.run(
            (str(VERIFY), "eth-test"),
            check=False,
            capture_output=True,
            env=self.env,
            text=True,
        )

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_verifier_rejects_drop_rule_shadowed_by_accept(self):
        self.env["IPV4_RULES"] = "\n".join((
            "-A INPUT -i eth-test -p tcp -j ACCEPT",
            "-A INPUT -i eth-test -p tcp -m multiport --dports 8000:8006,8012,8015 -j DROP",
        ))

        result = subprocess.run(
            (str(VERIFY), "eth-test"),
            check=False,
            capture_output=True,
            env=self.env,
            text=True,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("missing or shadowed", result.stderr)

    def test_verifier_rejects_legacy_rule_without_qa_twin_ports(self):
        legacy = "-A INPUT -i eth-test -p tcp -m tcp --dport 8000:8006 -j DROP"
        self.env["IPV4_RULES"] = legacy
        self.env["IPV6_RULES"] = legacy

        result = subprocess.run(
            (str(VERIFY), "eth-test"),
            check=False,
            capture_output=True,
            env=self.env,
            text=True,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("missing or shadowed", result.stderr)

    def test_hardening_removes_legacy_rule_only_after_new_rule_is_in_place(self):
        (self.fake_bin / "iptables").write_text(
            "#!/bin/sh\n"
            "printf 'iptables %s\\n' \"$*\" >> \"$FIREWALL_LOG\"\n"
            "case \"$*\" in\n"
            "  '-C INPUT -i eth-test -p tcp --dport 8000:8006 -j DROP')\n"
            "    [ ! -e \"$FIREWALL_LOG.legacy-removed\" ]; exit $? ;;\n"
            "  '-D INPUT -i eth-test -p tcp --dport 8000:8006 -j DROP')\n"
            "    : > \"$FIREWALL_LOG.legacy-removed\"; exit 0 ;;\n"
            "esac\n"
            "[ \"${1:-}\" != -C ]\n",
            encoding="utf-8",
        )

        result = subprocess.run(
            (str(HARDEN), "eth-test"),
            check=False,
            capture_output=True,
            env=self.env,
            text=True,
        )

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        calls = [
            line for line in self.log.read_text(encoding="utf-8").splitlines()
            if line.startswith("iptables ") and " INPUT " in line
        ]
        insert = calls.index(
            "iptables -I INPUT 1 -i eth-test -p tcp -m multiport "
            "--dports 8000:8006,8012,8015 -j DROP"
        )
        delete = calls.index("iptables -D INPUT -i eth-test -p tcp --dport 8000:8006 -j DROP")
        self.assertLess(insert, delete)

    def test_scripts_share_one_port_list(self):
        for script in (HARDEN, VERIFY):
            self.assertIn(
                "ai_ports='8000:8006,8012,8015'", script.read_text(encoding="utf-8")
            )

    def test_persistent_unit_runs_hardening_and_verification(self):
        unit = (ROOT / "systemd" / "gba-docker-firewall.service").read_text(
            encoding="utf-8"
        )
        self.assertIn("scripts/harden-docker-firewall.sh", unit)
        self.assertIn("scripts/verify-ai-firewall.sh", unit)
        self.assertIn("WantedBy=multi-user.target", unit)
        for service in (
            "gba-reco.service",
            "gba-procure.service",
            "gba-nba.service",
            "gba-solvency.service",
            "gba-pricing.service",
            "gba-products.service",
            "gba-forecast.service",
        ):
            self.assertIn(service, unit)

        drop_in = (ROOT / "systemd" / "gba-ai-require-firewall.conf").read_text(
            encoding="utf-8"
        )
        self.assertIn("Requires=gba-docker-firewall.service", drop_in)
        self.assertIn("After=gba-docker-firewall.service", drop_in)

    def test_verifier_fails_closed_when_ipv6_tool_is_unavailable(self):
        (self.fake_bin / "ip6tables").unlink()
        self.env["IPV4_RULES"] = (
            "-A INPUT -i eth-test -p tcp -m multiport --dports 8000:8006,8012,8015 -j DROP"
        )

        result = subprocess.run(
            (str(VERIFY), "eth-test"),
            check=False,
            capture_output=True,
            env=self.env,
            text=True,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("IPv6 is enabled but ip6tables is not installed", result.stderr)


if __name__ == "__main__":
    unittest.main()
