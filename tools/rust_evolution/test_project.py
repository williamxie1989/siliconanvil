# SPDX-License-Identifier: Apache-2.0
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).with_name("project.py")


class ProjectTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.checkout = self.init_checkout("checkout")
        self.command = [
            sys.executable,
            str(PROJECT),
            "--db",
            str(self.directory / "progress.sqlite3"),
            "--repo",
            str(self.checkout),
            "--json",
        ]
        self.evidence = self.directory / "evidence.json"
        self.evidence.write_text("{}")

    def call(self, *args, ok=True):
        result = subprocess.run(
            [*self.command, *args],
            cwd=self.directory,
            capture_output=True,
            check=False,
            text=True,
        )
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertEqual(result.returncode, 2, result.stderr)
        return result.stderr

    def test_new_process_recovers_active_task_and_searchable_memory(self):
        self.assertEqual(self.call("resume")["focus"]["id"], "EV-001")
        self.call("start", "EV-001", "--summary", "开始审计")
        self.call(
            "note",
            "EV-001",
            "--summary",
            "residency 实验",
            "--evidence",
            str(self.evidence),
            "--next",
            "核对版本",
        )
        self.call("note", "EV-001", "--summary", "补充路径")
        resumed = self.call("resume")
        self.assertEqual(resumed["focus"]["state"], "active")
        self.assertEqual(resumed["focus"]["next"], "核对版本")
        (hit,) = self.call("history", "--query", "residency")
        self.assertEqual(hit["evidence"], [str(self.evidence.resolve())])
        self.assertEqual(len(hit["head"]), 40)

    def test_transitions_gate_dependencies_and_require_evidence(self):
        self.assertIn("EV-001", self.call("start", "EV-002", ok=False))
        self.call("start", "EV-001")
        self.call("block", "EV-001", "--summary", "缺原始数据")
        self.assertEqual(self.call("resume")["focus"]["state"], "blocked")
        self.call("start", "EV-001")
        self.assertIn(
            "evidence", self.call("finish", "EV-001", "--summary", "完成", ok=False)
        )
        self.call(
            "finish", "EV-001", "--summary", "完成", "--evidence", str(self.evidence)
        )
        self.assertEqual(self.call("resume")["focus"]["id"], "R-001")
        self.assertIn("GATE-P1", self.call("start", "EV-201", ok=False))

    def test_concurrent_start_commits_once(self):
        self.call("status")
        first = subprocess.Popen(
            [*self.command, "start", "EV-001"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        second = subprocess.Popen(
            [*self.command, "start", "EV-001"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.assertEqual(
            sorted([first.wait(timeout=10), second.wait(timeout=10)]), [0, 2]
        )
        self.assertEqual(len(self.call("history", "EV-001")), 1)

    def init_checkout(self, name):
        repo = self.directory / name
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "commit.gpgSign=false",
                "commit",
                "-q",
                "--allow-empty",
                "-m",
                f"test baseline {name}",
            ],
            check=True,
        )
        return repo

    def test_records_head_from_explicit_checkout(self):
        repo = self.init_checkout("other-checkout")
        head = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        self.assertEqual(
            self.call("--repo", str(repo), "start", "EV-001")["head"], head
        )


if __name__ == "__main__":
    unittest.main()
