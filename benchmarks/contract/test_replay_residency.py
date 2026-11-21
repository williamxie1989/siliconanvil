# SPDX-License-Identifier: Apache-2.0
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from replay_residency import replay


class ResidencyReplayTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.run = Path(temp.name)
        self.samples = [
            self.sample(60, 100, 10),
            self.sample(61, 99, 12),
            self.sample(62, 99, 12),
            self.sample(290, 100, 12),
            self.sample(310, 6, 12),
            self.sample(311, 5, 15),
            self.sample(312, 6, 15),
        ]
        self.events = [
            {"kind": "idle_anchor", "mono": 0, "t0": 0},
            {"kind": "pressure_start", "label": "warm", "mono": 60.1},
            {"kind": "pressure_end", "label": "warm", "mono": 61.9, "returncode": 0},
            {"kind": "pressure_start", "label": "cold", "mono": 310.1},
            {"kind": "pressure_end", "label": "cold", "mono": 311.9, "returncode": 0},
            {"kind": "sequence_complete", "mono": 315},
            {"kind": "unload_done", "mono": 317, "result": {"status": "ok"}},
            {"kind": "experiment_end", "mono": 319, "aborted": False},
        ]

    @staticmethod
    def sample(second, wired_gib, swapouts):
        return {
            "mono": second,
            "page": 16384,
            "vm": {
                "Pages wired down": int(wired_gib * 2**30 / 16384),
                "Swapouts": swapouts,
            },
        }

    def write_input(self):
        for name, rows in (("samples", self.samples), ("events", self.events)):
            (self.run / f"{name}.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows)
            )
        (self.run / "final.json").write_text(json.dumps(self.sample(318, 4, 15)))

    def test_replays_time_anchors_and_vm_counters_with_input_digest(self):
        self.write_input()
        result = replay(self.run)
        self.assertEqual(result, replay(self.run))
        self.assertEqual(result["idle"]["290"]["system_wired_gib"], 100)
        self.assertEqual(result["idle"]["310"]["system_wired_gib"], 6)
        self.assertEqual(result["pressure"]["cold"]["system_wired_min_gib"], 5)
        self.assertEqual(result["pressure"]["cold"]["swapouts_delta_pages"], 3)
        self.assertEqual(result["pressure"]["warm"]["swapouts_delta_pages"], 2)
        self.assertEqual(result["after_unload"]["system_wired_gib"], 4)
        self.assertEqual(
            result["input_sha256"]["samples.jsonl"],
            hashlib.sha256((self.run / "samples.jsonl").read_bytes()).hexdigest(),
        )

    def test_rejects_stale_anchor_or_reset_counter(self):
        self.samples.pop(3)
        self.write_input()
        with self.assertRaisesRegex(ValueError, "T\\+290"):
            replay(self.run)
        self.samples.insert(3, self.sample(290, 100, 12))
        self.samples[5] = self.sample(311, 5, 1)
        self.write_input()
        with self.assertRaisesRegex(ValueError, "Swapouts"):
            replay(self.run)

    def test_rejects_incomplete_experiment(self):
        self.events = self.events[:-3]
        self.write_input()
        with self.assertRaisesRegex(ValueError, "sequence_complete"):
            replay(self.run)

    def test_rejects_pressure_window_after_experiment_and_unload(self):
        self.events[-3]["mono"] = 200
        self.events[-2]["mono"] = 210
        self.write_input()
        with self.assertRaisesRegex(ValueError, "ordering"):
            replay(self.run)

    def test_rejects_aborted_experiment_and_failed_unload(self):
        self.events[-1]["aborted"] = True
        self.write_input()
        with self.assertRaisesRegex(ValueError, "aborted"):
            replay(self.run)
        self.events[-1]["aborted"] = False
        self.events[-2]["result"]["status"] = "failed"
        self.write_input()
        with self.assertRaisesRegex(ValueError, "unload"):
            replay(self.run)


if __name__ == "__main__":
    unittest.main()
