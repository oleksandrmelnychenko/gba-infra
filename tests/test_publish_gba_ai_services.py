from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


SERVICES = (
    "gba-nba",
    "gba-reco",
    "gba-procure",
    "gba-solvency",
    "gba-pricing",
    "gba-products",
    "gba-forecast",
)
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY_ROOT / "scripts" / "publish_gba_ai_services.sh"


def _run(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=check,
        text=True,
        capture_output=True,
    )


def _init_repository(path: Path) -> None:
    path.mkdir(parents=True)
    _run("git", "init", "-q", "-b", "main", cwd=path)
    _run("git", "config", "user.name", "Sync Test", cwd=path)
    _run("git", "config", "user.email", "sync-test@example.invalid", cwd=path)


def _commit_all(path: Path, message: str) -> None:
    _run("git", "add", "-A", cwd=path)
    _run("git", "commit", "-q", "-m", message, cwd=path)


class PublishScriptSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "projects"
        self.monorepo = self.root / "gba-ai-services"
        _init_repository(self.monorepo)
        (self.monorepo / ".gitignore").write_text(
            ".env\n.venv/\ndata/\n", encoding="utf-8"
        )
        (self.monorepo / "README.md").write_text("root sentinel\n", encoding="utf-8")

        for service in SERVICES:
            standalone = self.root / service
            _init_repository(standalone)
            (standalone / ".gitignore").write_text(
                ".env\n.venv/\ndata/\n", encoding="utf-8"
            )
            (standalone / "combined.txt").write_text(
                f"standalone source: {service}\n", encoding="utf-8"
            )
            _commit_all(standalone, "standalone baseline")

            monorepo_service = self.monorepo / service
            monorepo_service.mkdir()
            (monorepo_service / "stale.txt").write_text(
                f"stale monorepo copy: {service}\n", encoding="utf-8"
            )

        _commit_all(self.monorepo, "monorepo baseline")
        self.env = {
            **os.environ,
            "GBA_PROJECTS_ROOT": str(self.root),
            "GBA_AI_SERVICES_DEST": str(self.monorepo),
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ("bash", str(SCRIPT), *args),
            cwd=REPOSITORY_ROOT,
            env=self.env,
            check=False,
            text=True,
            capture_output=True,
        )

    def test_script_contains_no_checkout_recreation_or_forced_push(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")

        self.assertNotIn('rm -rf "$MONOREPO"', text)
        self.assertIsNone(re.search(r"\bgit\s+init\b", text))
        self.assertIsNone(re.search(r"\bgit\b[^\n]*\bpush\b[^\n]*--force", text))

    def test_default_is_a_read_only_preview(self) -> None:
        git_dir_before = (self.monorepo / ".git").stat().st_ino

        result = self.run_script()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("DRY RUN", result.stdout)
        self.assertEqual(
            "stale monorepo copy: gba-reco\n",
            (self.monorepo / "gba-reco" / "stale.txt").read_text(encoding="utf-8"),
        )
        self.assertEqual(git_dir_before, (self.monorepo / ".git").stat().st_ino)
        self.assertEqual("", _run("git", "status", "--porcelain", cwd=self.monorepo).stdout)

    def test_apply_refuses_a_dirty_monorepo(self) -> None:
        (self.monorepo / "README.md").write_text("uncommitted work\n", encoding="utf-8")

        result = self.run_script("--to-monorepo", "--apply")

        self.assertNotEqual(0, result.returncode)
        self.assertIn("monorepo is dirty", result.stderr)
        self.assertTrue((self.monorepo / "gba-reco" / "stale.txt").exists())

    def test_apply_deletes_only_service_content_and_preserves_runtime_files(self) -> None:
        source_repository = self.root / "gba-reco" / "app" / "data" / "repository.py"
        source_repository.parent.mkdir(parents=True)
        source_repository.write_text(
            "SOURCE_HISTORY_START = '2025-01-01'\n", encoding="utf-8"
        )
        _run(
            "git",
            "add",
            "-f",
            "app/data/repository.py",
            cwd=self.root / "gba-reco",
        )
        _run(
            "git",
            "commit",
            "-q",
            "-m",
            "track application data repository",
            cwd=self.root / "gba-reco",
        )
        runtime_env = self.monorepo / "gba-reco" / ".env"
        runtime_cache = self.monorepo / "gba-reco" / "data" / "cache.bin"
        runtime_env.write_text("SECRET=runtime-only\n", encoding="utf-8")
        runtime_cache.parent.mkdir()
        runtime_cache.write_text("runtime cache\n", encoding="utf-8")
        git_dir_before = (self.monorepo / ".git").stat().st_ino

        result = self.run_script("--to-monorepo", "--apply")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse((self.monorepo / "gba-reco" / "stale.txt").exists())
        self.assertEqual(
            "standalone source: gba-reco\n",
            (self.monorepo / "gba-reco" / "combined.txt").read_text(
                encoding="utf-8"
            ),
        )
        self.assertEqual("root sentinel\n", (self.monorepo / "README.md").read_text())
        self.assertEqual(
            "SOURCE_HISTORY_START = '2025-01-01'\n",
            (
                self.monorepo
                / "gba-reco"
                / "app"
                / "data"
                / "repository.py"
            ).read_text(encoding="utf-8"),
        )
        self.assertEqual("SECRET=runtime-only\n", runtime_env.read_text())
        self.assertEqual("runtime cache\n", runtime_cache.read_text())
        self.assertEqual(git_dir_before, (self.monorepo / ".git").stat().st_ino)

    def test_reverse_apply_refuses_dirty_standalone_destination(self) -> None:
        dirty = self.root / "gba-procure" / "combined.txt"
        dirty.write_text("uncommitted standalone work\n", encoding="utf-8")

        result = self.run_script("--to-standalone", "--apply")

        self.assertNotEqual(0, result.returncode)
        self.assertIn("standalone destination is dirty", result.stderr)
        self.assertEqual("uncommitted standalone work\n", dirty.read_text())

    def test_reverse_apply_updates_host_copy_without_removing_runtime_state(self) -> None:
        target = self.root / "gba-procure"
        runtime_env = target / ".env"
        runtime_cache = target / "data" / "cache.bin"
        runtime_env.write_text("SECRET=runtime-only\n", encoding="utf-8")
        runtime_cache.parent.mkdir()
        runtime_cache.write_text("runtime cache\n", encoding="utf-8")
        standalone_only = target / "standalone-only.txt"
        standalone_only.write_text("remove after reviewed reverse sync\n", encoding="utf-8")
        _commit_all(target, "standalone-only tracked file")
        git_dir_before = (target / ".git").stat().st_ino
        for service in SERVICES:
            service_file = self.monorepo / service / "combined.txt"
            service_file.write_text(f"combined monorepo: {service}\n", encoding="utf-8")
        _commit_all(self.monorepo, "combined service trees")

        result = self.run_script("--to-standalone", "--apply")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            "combined monorepo: gba-procure\n",
            (target / "combined.txt").read_text(encoding="utf-8"),
        )
        self.assertFalse(standalone_only.exists())
        self.assertEqual("SECRET=runtime-only\n", runtime_env.read_text())
        self.assertEqual("runtime cache\n", runtime_cache.read_text())
        self.assertEqual(git_dir_before, (target / ".git").stat().st_ino)

    def test_credential_scan_aborts_before_sync(self) -> None:
        credential_file = self.root / "gba-forecast" / "leaked-key.txt"
        credential_file.write_text(
            "-----BEGIN PRIVATE KEY-----\nnot-a-real-key\n",
            encoding="utf-8",
        )
        _commit_all(self.root / "gba-forecast", "leak")

        result = self.run_script("--to-monorepo", "--apply")

        self.assertNotEqual(0, result.returncode)
        self.assertIn("credential scan failed before sync", result.stderr)
        self.assertTrue((self.monorepo / "gba-forecast" / "stale.txt").exists())

    def test_products_is_never_touched_without_the_explicit_flag(self) -> None:
        forward = self.run_script("--to-monorepo", "--apply")

        self.assertEqual(0, forward.returncode, forward.stderr)
        self.assertIn("gba-products: skipped", forward.stdout)
        self.assertEqual(
            "stale monorepo copy: gba-products\n",
            (self.monorepo / "gba-products" / "stale.txt").read_text(encoding="utf-8"),
        )
        self.assertFalse((self.monorepo / "gba-products" / "combined.txt").exists())
        self.assertFalse((self.monorepo / "gba-reco" / "stale.txt").exists())

        _commit_all(self.monorepo, "forward sync")
        reverse = self.run_script("--to-standalone", "--apply")

        self.assertEqual(0, reverse.returncode, reverse.stderr)
        self.assertFalse((self.root / "gba-products" / "stale.txt").exists())
        self.assertEqual(
            "standalone source: gba-products\n",
            (self.root / "gba-products" / "combined.txt").read_text(encoding="utf-8"),
        )

    def test_only_products_requires_the_explicit_flag(self) -> None:
        result = self.run_script("--only", "gba-products", "--apply")

        self.assertNotEqual(0, result.returncode)
        self.assertIn("--include-products", result.stderr)
        self.assertTrue((self.monorepo / "gba-products" / "stale.txt").exists())

    def test_include_products_flag_syncs_products(self) -> None:
        result = self.run_script("--include-products", "--only", "gba-products", "--apply")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse((self.monorepo / "gba-products" / "stale.txt").exists())
        self.assertTrue((self.monorepo / "gba-products" / "combined.txt").exists())
        self.assertTrue((self.monorepo / "gba-reco" / "stale.txt").exists())

    def test_only_limits_the_run_and_rejects_unknown_names(self) -> None:
        result = self.run_script("--only", "gba-solvency,gba-pricing", "--apply")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse((self.monorepo / "gba-solvency" / "stale.txt").exists())
        self.assertFalse((self.monorepo / "gba-pricing" / "stale.txt").exists())
        self.assertTrue((self.monorepo / "gba-reco" / "stale.txt").exists())

        unknown = self.run_script("--only", "gba-unknown")

        self.assertNotEqual(0, unknown.returncode)
        self.assertIn("unknown service", unknown.stderr)

    def test_dry_run_lists_changes_and_changes_nothing(self) -> None:
        result = self.run_script("--dry-run")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("combined.txt", result.stdout)
        self.assertIn("*deleting", result.stdout)
        self.assertIn("gba-solvency: +2 new, ~0 changed, -1 deleted", result.stdout)
        self.assertNotIn("gba-products: +", result.stdout)
        for service in SERVICES:
            self.assertTrue((self.monorepo / service / "stale.txt").exists())
            self.assertFalse((self.monorepo / service / "combined.txt").exists())

    def test_apply_refuses_tracked_changes_outside_the_allow_list(self) -> None:
        dirty = self.root / "gba-pricing" / "combined.txt"
        dirty.write_text("uncommitted code\n", encoding="utf-8")

        preview = self.run_script("--dry-run")

        self.assertEqual(0, preview.returncode, preview.stderr)
        self.assertIn("gba-pricing: REFUSED", preview.stdout)
        self.assertIn("REFUSED-ON-APPLY", preview.stdout)

        result = self.run_script("--apply")

        self.assertNotEqual(0, result.returncode)
        self.assertIn("standalone source is dirty: gba-pricing", result.stderr)
        for service in SERVICES:
            self.assertTrue((self.monorepo / service / "stale.txt").exists())

    def test_allow_listed_changes_are_tolerated_but_head_is_published(self) -> None:
        service = self.root / "gba-solvency"
        (service / "Makefile").write_text("committed\n", encoding="utf-8")
        (service / "docs").mkdir()
        (service / "docs" / "RUNBOOK.md").write_text("committed\n", encoding="utf-8")
        _commit_all(service, "allow-listed files")
        (service / "Makefile").write_text("local host tweak\n", encoding="utf-8")
        (service / "docs" / "RUNBOOK.md").write_text("local note\n", encoding="utf-8")

        result = self.run_script("--apply")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("allow-listed local modifications", result.stdout)
        self.assertEqual(
            "committed\n", (self.monorepo / "gba-solvency" / "Makefile").read_text()
        )
        self.assertEqual(
            "committed\n",
            (self.monorepo / "gba-solvency" / "docs" / "RUNBOOK.md").read_text(),
        )

    def test_ignored_and_untracked_files_are_never_copied(self) -> None:
        service = self.root / "gba-procure"
        (service / ".gitignore").write_text(
            ".env\n.venv*/\ndata/\n__pycache__/\n*.joblib\n", encoding="utf-8"
        )
        _commit_all(service, "ignore rules")
        for relative in (
            ".venv/bin/python",
            ".venv-old/bin/python",
            "__pycache__/module.pyc",
            "app/risk/artifacts/model.joblib",
        ):
            path = service / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("binary\n", encoding="utf-8")
        (service / ".env").write_text("SECRET=local\n", encoding="utf-8")
        (service / "scratch.txt").write_text("untracked\n", encoding="utf-8")

        result = self.run_script("--apply")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("untracked files are not published", result.stdout)
        target = self.monorepo / "gba-procure"
        self.assertTrue((target / "combined.txt").exists())
        for relative in (
            ".venv",
            ".venv-old",
            "__pycache__",
            "app/risk/artifacts/model.joblib",
            ".env",
            "scratch.txt",
        ):
            self.assertFalse((target / relative).exists(), relative)

    def test_committed_env_file_aborts(self) -> None:
        service = self.root / "gba-nba"
        (service / ".env.dev").write_text("SECRET=value\n", encoding="utf-8")
        _commit_all(service, "env committed by mistake")

        result = self.run_script("--apply")

        self.assertNotEqual(0, result.returncode)
        self.assertIn("non-template env file is committed", result.stderr)
        self.assertTrue((self.monorepo / "gba-nba" / "stale.txt").exists())

    def test_missing_monorepo_folder_is_created_on_apply(self) -> None:
        _run("git", "rm", "-rq", "gba-nba", cwd=self.monorepo)
        _run("git", "commit", "-q", "-m", "drop nba copy", cwd=self.monorepo)

        preview = self.run_script("--dry-run")

        self.assertEqual(0, preview.returncode, preview.stderr)
        self.assertIn("(new folder)", preview.stdout)
        self.assertFalse((self.monorepo / "gba-nba").exists())

        result = self.run_script("--apply")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue((self.monorepo / "gba-nba" / "combined.txt").exists())


if __name__ == "__main__":
    unittest.main()
