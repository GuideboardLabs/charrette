from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from typing import Any, cast

from board.chat import BoardChat, _normalise_args
from board.store import Store


class NormaliseArgsContractTests(unittest.TestCase):
    def test_dict_points_are_converted_to_numeric_pairs(self):
        out = _normalise_args("board_stroke", {"points": [{"x": "1.5", "y": 2}]})
        self.assertEqual(out["points"], [[1.5, 2.0]])

    def test_pts_alias_becomes_points_and_alias_is_removed(self):
        out = _normalise_args("board_stroke", {"pts": [[1, 2], [3, 4]]})
        self.assertEqual(out["points"], [[1.0, 2.0], [3.0, 4.0]])
        self.assertNotIn("pts", out)

    def test_junk_points_are_dropped_without_losing_valid_points(self):
        out = _normalise_args("board_stroke", {"points": [None, [1], ["bad", 2], {"x": 3, "y": 4}, [5, 6, 7]]})
        self.assertEqual(out["points"], [[3.0, 4.0], [5.0, 6.0]])
        self.assertEqual(len(out["points"]), 2)

    def test_string_width_is_coerced_to_integer(self):
        out = _normalise_args("board_stroke", {"width": "5"})
        self.assertEqual(out["width"], 5)
        self.assertIsInstance(out["width"], int)

    def test_invalid_integer_fields_are_removed_instead_of_raising(self):
        out = _normalise_args("board_draw", {"rows": "many", "cols": "3", "if_rev": None})
        self.assertNotIn("rows", out)
        self.assertNotIn("if_rev", out)
        self.assertEqual(out["cols"], 3)

    def test_non_dictionary_arguments_normalise_to_empty_dictionary(self):
        self.assertEqual(_normalise_args("board_stroke", cast(Any, [1, 2])), {})


class _Pool:
    entries = [{"name": "fake"}]

    def candidates(self):
        return [{"name": "fake", "base_url": "http://unused", "api_key": "test",
                 "models": ["fake-model"], "stream": True}]

    def note_hit(self, _name):
        pass

    def note_failure(self, _name, _reason):
        pass

    def note_success(self, _name):
        pass


class _BlockingResponse:
    def __init__(self):
        self.reading = threading.Event()
        self.closed = threading.Event()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def close(self):
        self.closed.set()

    def __iter__(self):
        yield b'data: {"choices":[{"delta":{"content":"unfinished "}}]}\n'
        yield (b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1",'
               b'"function":{"name":"board_draw","arguments":"{\\"kind\\":\\"diagram\\"}"}}]}}]}\n')
        self.reading.set()
        self.closed.wait(5)
        yield b'data: [DONE]\n'


class _ImmediateResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        pass

    def close(self):
        pass

    def __iter__(self):
        yield b'data: {"choices":[{"delta":{"content":"finished"}}]}\n'
        yield b'data: [DONE]\n'


def _events(frames):
    return [json.loads(frame.decode().split("data: ", 1)[1]) for frame in frames]


class BoardChatCancellationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name))
        self.calls = []
        self.chat = BoardChat(self.store, [], {"board_draw": lambda args: self.calls.append(args) or {"ok": True}})
        self.blocking = _BlockingResponse()

    def tearDown(self):
        self.tmp.cleanup()

    def _post(self, _base, _key, payload):
        user = next((m.get("content", "") for m in reversed(payload["messages"])
                     if m.get("role") == "user"), "")
        return self.blocking if user == "block" else _ImmediateResponse()

    def test_cancel_interrupts_blocked_read_without_dispatching_or_storing_partial_reply(self):
        frames = []
        worker = threading.Thread(target=lambda: frames.extend(self.chat.stream("b1", "block")))
        with patch("board.chat._get_pool", return_value=_Pool()), \
             patch("board.chat._post_stream", side_effect=self._post), \
             patch("board.chat._model_name", return_value="fake-model"):
            worker.start()
            self.assertTrue(self.blocking.reading.wait(1), "fake provider never reached its blocked read")
            self.assertTrue(self.chat.cancel("b1"))
            worker.join(1)

        self.assertFalse(worker.is_alive(), "cancel left the board stream blocked")
        self.assertTrue(self.blocking.closed.wait(1), "cancel did not close the active response")
        self.assertEqual(self.calls, [])
        events = _events(frames)
        self.assertEqual([e["type"] for e in events].count("stopped"), 1)
        self.assertNotIn("done", [e["type"] for e in events])
        self.assertEqual([m["role"] for m in self.chat.history("b1")], ["user"])

    def test_cancel_is_scoped_to_one_board_and_reports_inactive_runs(self):
        blocked_frames = []
        worker = threading.Thread(target=lambda: blocked_frames.extend(self.chat.stream("b1", "block")))
        with patch("board.chat._get_pool", return_value=_Pool()), \
             patch("board.chat._post_stream", side_effect=self._post), \
             patch("board.chat._model_name", return_value="fake-model"):
            worker.start()
            self.assertTrue(self.blocking.reading.wait(1))
            other = _events(list(self.chat.stream("b2", "finish")))
            self.assertEqual(other[-1]["type"], "done")
            self.assertEqual(other[-1]["text"], "finished")
            self.assertFalse(self.chat.cancel("missing"))
            self.assertTrue(self.chat.cancel("b1"))
            worker.join(1)

        self.assertFalse(worker.is_alive())
        self.assertEqual(_events(blocked_frames)[-1]["type"], "stopped")
        self.assertEqual([m["role"] for m in self.chat.history("b2")], ["user", "assistant"])


if __name__ == "__main__":
    unittest.main()
