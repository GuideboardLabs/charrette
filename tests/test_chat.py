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


class _FakeLlm:
    """Stands in for the host facade. ``on_call`` runs inside the turn, which is where a cancel lands."""

    def __init__(self, reply: str = '{"say": "finished", "ops": []}', on_call=None):
        self.reply = reply
        self.on_call = on_call
        self.calls = 0

    def complete(self, messages, **_kw):
        self.calls += 1
        if self.on_call:
            self.on_call(self.calls)
        class R:
            text = self.reply
        return R()


class BoardChatCancellationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def _chat(self, llm, calls=None):
        handler = lambda args: (calls.append(args) if calls is not None else None) or {"ok": True}
        return BoardChat(self.store, [], {"board_draw": handler}, llm=llm)

    def test_cancel_stops_the_turn_before_the_next_model_call(self):
        """A cancel sets the flag the loop checks between steps, so no second call and no partial message."""
        frames = []
        holder = {}

        def on_call(n):
            if n == 1:                       # the first plan asks for work, then we cancel
                holder["chat"].cancel("b1")
        llm = _FakeLlm('{"say": "drawing", "ops": [{"tool": "board_draw", "args": {}}]}', on_call=on_call)
        calls = []
        chat = self._chat(llm, calls)
        holder["chat"] = chat

        events = _events(list(chat.stream("b1", "draw something")))
        kinds = [e["type"] for e in events]

        self.assertEqual(kinds.count("stopped"), 1)
        self.assertNotIn("done", kinds)
        self.assertEqual(llm.calls, 1, "the run kept calling the model after cancel")
        self.assertEqual(calls, [], "a cancelled plan still executed an op")
        self.assertEqual([m["role"] for m in chat.history("b1")], ["user"])

    def test_cancel_is_scoped_to_one_board_and_reports_inactive_runs(self):
        llm = _FakeLlm('{"say": "finished", "ops": []}')
        chat = self._chat(llm)

        other = _events(list(chat.stream("b2", "hello")))
        self.assertEqual(other[-1]["type"], "done")
        self.assertEqual(other[-1]["text"], "finished")
        self.assertFalse(chat.cancel("missing"), "an inactive board reported a live run")
        self.assertEqual([m["role"] for m in chat.history("b2")], ["user", "assistant"])

    def test_no_session_means_a_plain_error_not_a_guess(self):
        chat = self._chat(None)
        events = _events(list(chat.stream("b1", "hello")))
        self.assertEqual(events[-1]["type"], "error")
        self.assertIn("no model seam", events[-1]["error"])


if __name__ == "__main__":
    unittest.main()
