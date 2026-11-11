from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from board import keys


def entry(name: str, priority: int = 60, rpm: int = 0) -> dict:
    return {"name": name, "base_url": "https://example.invalid/v1", "api_key": "secret",
            "models": [], "rpm": rpm, "stream": True, "priority": priority, "source": "test"}


class KeyPoolContractTests(unittest.TestCase):
    def test_load_entries_filters_invalid_and_disabled_rows_without_real_key_access(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "keys.json"
            path.write_text(json.dumps({"keys": [
                {"name": "valid", "base_url": "https://a.test/v1/", "api_key": "x", "models": "m", "rpm": 4},
                {"name": "off", "base_url": "https://b.test", "api_key": "y", "enabled": False},
                {"name": "missing", "base_url": "https://c.test"},
            ]}))
            with patch.object(keys, "KEYS_PATH", path):
                loaded = keys.load_entries()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["name"], "valid")
        self.assertEqual(loaded[0]["base_url"], "https://a.test/v1")
        self.assertEqual(loaded[0]["models"], ["m"])
        self.assertEqual(loaded[0]["rpm"], 4)
        self.assertEqual(loaded[0]["source"], "keys.json")

    def test_pool_mixes_file_and_host_entries_then_sorts_by_priority(self):
        file_rows = [entry("overflow", 60), entry("lead", 10)]
        host_rows = [entry("host", 40)]
        with patch.object(keys, "load_entries", return_value=file_rows), \
             patch.object(keys, "hermes_entries", return_value=host_rows):
            pool = keys.pool()
        self.assertEqual([row["name"] for row in pool.entries], ["lead", "host", "overflow"])
        self.assertEqual(len(pool.entries), 3)

    def test_pool_deduplicates_host_entry_when_file_uses_the_same_name(self):
        with patch.object(keys, "load_entries", return_value=[entry("same", 10)]), \
             patch.object(keys, "hermes_entries", return_value=[entry("same", 40)]):
            pool = keys.pool()
        self.assertEqual([row["name"] for row in pool.entries], ["same"])

    def test_rpm_window_blocks_at_budget_and_recovers_after_sixty_seconds(self):
        pool = keys.KeyPool([entry("limited", rpm=2)])
        with patch("board.keys.time.time", return_value=100.0):
            pool.note_hit("limited")
            pool.note_hit("limited")
            self.assertEqual(pool.candidates(), [])
            self.assertIn("rpm budget", pool.state("limited")["last_error"])
        with patch("board.keys.time.time", return_value=161.0):
            self.assertEqual([row["name"] for row in pool.candidates()], ["limited"])
            self.assertEqual(pool.state("limited")["used_last_minute"], 0)

    def test_failure_cools_key_then_success_clears_error_and_cooldown(self):
        pool = keys.KeyPool([entry("flaky")])
        with patch("board.keys.time.time", return_value=200.0):
            pool.note_failure("flaky", "HTTP 429")
            state = pool.state("flaky")
            self.assertFalse(state["ready"])
            self.assertEqual(state["cooling_for"], keys.COOLDOWN_SECONDS)
            self.assertEqual(pool.candidates(), [])
        with patch("board.keys.time.time", return_value=210.0):
            pool.note_success("flaky")
            state = pool.state("flaky")
            self.assertTrue(state["ready"])
            self.assertEqual(state["last_error"], "")
            self.assertEqual([row["name"] for row in pool.candidates()], ["flaky"])

    def test_candidates_returns_at_most_three_ready_routes(self):
        pool = keys.KeyPool([entry(f"k{i}") for i in range(5)])
        self.assertEqual(len(pool.candidates()), 3)
        self.assertEqual([row["name"] for row in pool.candidates()], ["k1", "k2", "k3"])


if __name__ == "__main__":
    unittest.main()
