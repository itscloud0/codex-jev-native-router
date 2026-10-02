import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import manage
from metrics import measurement_status


class MeasurementStatusTests(unittest.TestCase):
    now = 1_000_000

    def usage(self, **values):
        return {"event": "usage", "ts": self.now, **values}

    def test_recent_generations_have_clear_states(self):
        valid_full = self.usage(schema_version=3, usage_scope="turn_total",
                                usage_coverage_reason="cumulative_delta", input_tokens=10,
                                output_tokens=2, cache_observed=True, cached_input_tokens=3)
        cases = {
            "no_recent_usage": ([], 0),
            "legacy_only": ([self.usage(schema_version=2)], 0),
            "current_partial_only": ([self.usage(schema_version=3, usage_scope="model_call")], 0),
            "full_turns_observed": ([valid_full], 1),
            "mixed_generations": ([self.usage(schema_version=2), valid_full], 1),
        }

        for expected_state, (rows, complete_turns) in cases.items():
            with self.subTest(expected_state=expected_state):
                status = measurement_status(rows, now=self.now)
                self.assertEqual(status["state"], expected_state)
                self.assertEqual(status["usage_records"], len(rows))
                self.assertEqual(status["complete_turn_records"], complete_turns)

    def test_full_turn_requires_valid_input_output_and_coherent_cache(self):
        rows = [
            self.usage(schema_version=3, usage_scope="turn_total",
                       usage_coverage_reason="cumulative_delta", input_tokens=10,
                       output_tokens=2, cache_observed=True, cached_input_tokens=3),
            self.usage(schema_version=3, usage_scope="turn_total",
                       usage_coverage_reason="cumulative_delta", input_tokens=10,
                       output_tokens=2),
            self.usage(schema_version=3, usage_scope="turn_total",
                       usage_coverage_reason="cumulative_delta", input_tokens=10,
                       output_tokens=2, cache_observed=True, cached_input_tokens=11),
            self.usage(schema_version=3, usage_scope="turn_total",
                       usage_coverage_reason="cumulative_delta", input_tokens=1.5,
                       output_tokens=2, cache_observed=True, cached_input_tokens=1),
            self.usage(schema_version=3, usage_scope="turn_total",
                       usage_coverage_reason="cumulative_delta", input_tokens=10,
                       output_tokens=True, cache_observed=True, cached_input_tokens="3"),
        ]

        status = measurement_status(rows, now=self.now)
        self.assertEqual(status["state"], "full_turns_observed")
        self.assertEqual(status["current_records"], 5)
        self.assertEqual(status["complete_turn_records"], 1)

    def test_unsafe_reasons_and_out_of_window_rows_are_ignored_or_redacted(self):
        secret_reason = "private operator detail"
        rows = [
            self.usage(schema_version=3, usage_coverage_reason=secret_reason),
            {"event": "usage", "ts": self.now - 901, "schema_version": 3},
            {"event": "usage", "ts": self.now + 6, "schema_version": 3},
            {"event": "usage", "ts": float("nan"), "schema_version": 3},
            {"event": "usage", "ts": 10**400, "schema_version": 3},
        ]

        status = measurement_status(rows, now=self.now)
        self.assertEqual(status["usage_records"], 1)
        self.assertEqual(status["coverage_reasons"], {"unknown": 1})
        self.assertNotIn(secret_reason, json.dumps(status))


class MeasurementHealthTests(unittest.TestCase):
    now = 1_000_000

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.state.mkdir()

    def write_lines(self, name, lines):
        (self.state / name).write_text("".join(
            line if isinstance(line, str) else json.dumps(line) + "\n" for line in lines))

    def health(self):
        with mock.patch("metrics.time.time", return_value=self.now):
            return manage.measurement_health(self.root)

    def test_health_ignores_malformed_non_dict_and_out_of_window_log_rows(self):
        self.write_lines("telemetry.jsonl", [
            "not json\n",
            ["not", "a", "record"],
            {"event": "usage", "ts": self.now - 901, "schema_version": 3},
            {"event": "usage", "ts": self.now + 6, "schema_version": 3},
            {"event": "usage", "ts": self.now, "schema_version": 2},
        ])

        health = self.health()
        self.assertEqual(health["state"], "legacy_only")
        self.assertEqual(health["usage_records"], 1)
        self.assertTrue(health["bounded_tail_scan"])
        self.assertFalse(health["truncated"])

    def test_health_reads_only_last_two_files_and_marks_truncated_tail(self):
        self.write_lines("telemetry.001.jsonl", [
            {"event": "usage", "ts": self.now, "schema_version": 2},
        ])
        tail_record = {"event": "usage", "ts": self.now, "schema_version": 3,
                       "usage_scope": "turn_total", "usage_coverage_reason": "cumulative_delta",
                       "input_tokens": 10, "output_tokens": 2,
                       "cache_observed": True, "cached_input_tokens": 2}
        (self.state / "telemetry.002.jsonl").write_bytes(
            b"x" * 1_000_001 + b"\n" + json.dumps(tail_record).encode() + b"\n")
        self.write_lines("telemetry.jsonl", [
            {"event": "usage", "ts": self.now, "schema_version": 3,
             "usage_scope": "model_call"},
        ])

        health = self.health()
        self.assertEqual(health["state"], "full_turns_observed")
        self.assertEqual(health["usage_records"], 2)
        self.assertEqual(health["legacy_records"], 0)
        self.assertEqual(health["complete_turn_records"], 1)
        self.assertTrue(health["truncated"])


if __name__ == "__main__":
    unittest.main()
