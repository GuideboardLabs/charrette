from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from board.replay import GAP_MS, build_html, build_timeline, duration_ms, pick_objects, split_sentence


class ReplayContractTests(unittest.TestCase):
    def test_duration_uses_path_length_text_length_and_fixed_kind_values(self):
        self.assertEqual(duration_ms({"kind": "stroke", "pts": [[0, 0], [620, 0]]}), 1000.0)
        self.assertEqual(duration_ms({"kind": "stroke", "pts": [[0, 0], [1, 0]]}), 320.0)
        self.assertAlmostEqual(duration_ms({"kind": "shape", "bbox": [0, 0, 100, 50]}), 300 / 620 * 1000)
        self.assertEqual(duration_ms({"kind": "text", "text": "abcdefghij"}), 450.0)
        self.assertEqual(duration_ms({"kind": "table"}), 1300.0)
        self.assertEqual(duration_ms({"kind": "unknown"}), 700.0)

    def test_timeline_applies_speed_and_preserves_the_gap_between_objects(self):
        objects = [("a", {"kind": "annotation"}), ("b", {"kind": "annotation"})]
        timeline, total = build_timeline(objects, speed=2.0)
        self.assertEqual(timeline[0], {"id": "a", "t0": 0.0, "t1": 300.0})
        self.assertEqual(timeline[1]["t0"], 300.0 + GAP_MS)
        self.assertEqual(timeline[1]["t1"], 860.0)
        self.assertEqual(total, 1120.0)

    def test_pick_objects_orders_creation_stamps_before_legacy_layer_fallbacks(self):
        doc = {"objects": {
            "legacy-human": {"kind": "stroke", "layer": "human-ink", "z": "a1"},
            "newer": {"kind": "stroke", "created": "2026-01-02T00:00:00Z"},
            "older": {"kind": "stroke", "created": "2026-01-01T00:00:00Z"},
            "legacy-agent": {"kind": "stroke", "layer": "agent-diagram", "z": "a9"},
        }}
        self.assertEqual([oid for oid, _ in pick_objects(doc)],
                         ["older", "newer", "legacy-agent", "legacy-human"])

    def test_pick_objects_uses_layer_then_numeric_z_for_legacy_objects(self):
        doc = {"objects": {
            "annotation": {"layer": "agent-annotation", "z": "a1"},
            "human-late": {"layer": "human-ink", "z": "a12"},
            "diagram": {"layer": "agent-diagram", "z": "a99"},
            "human-early": {"layer": "human-ink", "z": "a2"},
        }}
        self.assertEqual([oid for oid, _ in pick_objects(doc)],
                         ["diagram", "human-early", "human-late", "annotation"])

    def test_explicit_ids_preserve_caller_order_and_drop_missing_ids(self):
        doc = {"objects": {"a": {"created": "1"}, "b": {"created": "2"}}}
        self.assertEqual([oid for oid, _ in pick_objects(doc, ids=["b", "missing", "a"])], ["b", "a"])

    def test_scene_order_takes_precedence_over_explicit_ids(self):
        doc = {"objects": {"a": {}, "b": {}, "c": {}}}
        scene = {"objects": ["c", "a"]}
        self.assertEqual([oid for oid, _ in pick_objects(doc, ids=["b"], scene=scene)], ["c", "a"])

    def test_caption_split_never_emits_an_empty_word_segment(self):
        timeline = [
            {"id": "a", "t0": 0, "t1": 100},
            {"id": "b", "t0": 120, "t1": 220},
            {"id": "c", "t0": 240, "t1": 340},
        ]
        segments, words = split_sentence("  one  ", timeline)
        self.assertEqual(words, ["one"])
        self.assertEqual(len(segments), 1)
        self.assertTrue(all(segment["to"] > segment["from"] for segment in segments))
        self.assertEqual(segments[0]["from"], 0)
        self.assertEqual(segments[0]["to"], 1)

    def test_caption_split_returns_words_without_segments_when_timeline_is_empty(self):
        segments, words = split_sentence(" alpha   beta ", [])
        self.assertEqual(segments, [])
        self.assertEqual(words, ["alpha", "beta"])

    def test_generated_html_contains_inlined_engine_timeline_and_seek_surface(self):
        doc = {"schema": "charrette/1", "rev": 3, "layers": [], "objects": {}}
        picked = [("s1", {"kind": "stroke", "pts": [[0, 0], [50, 0]], "bbox": [0, 0, 50, 1]})]
        with tempfile.TemporaryDirectory() as root:
            html, total = build_html(doc, Path(root), picked, "draw this", 1.0)
        self.assertIn("function createBoard", html)
        self.assertIn('const TIMELINE = [{"id": "s1"', html)
        self.assertIn("window.__replay", html)
        self.assertIn("seek(t)", html)
        self.assertNotIn("__ENGINE__", html)
        self.assertNotIn("__TIMELINE__", html)
        self.assertGreater(total, 0)

    def test_generated_replay_has_spaced_card_chrome_and_deterministic_finished_state(self):
        item = {"kind": "stroke", "pts": [[0, 0], [50, 0]], "bbox": [0, 0, 50, 1]}
        with tempfile.TemporaryDirectory() as root:
            html, _ = build_html({"objects": {"s1": item}}, Path(root),
                                 [("s1", item)], "one mark", 1.0)
        self.assertIn('id="state">playing</small>', html)
        self.assertIn("classList.toggle('finished', finished)", html)
        self.assertIn("finished ? 'finished' : 'playing'", html)
        self.assertIn('width:min(960px, 100%)', html)
        self.assertIn('padding:clamp(12px, 3vw, 30px)', html)

    def test_glove_readability_scale_does_not_change_the_liked_pen_marker(self):
        item = {"kind": "table", "bbox": [0, 0, 100, 60]}
        with tempfile.TemporaryDirectory() as root:
            html, _ = build_html({"objects": {"table": item}}, Path(root),
                                 [("table", item)], "place this", 1.0)
        glove = html.split("function bodyGlove", 1)[1].split("function drawHand", 1)[0]
        pen = html.split("function bodyPen", 1)[1].split("function capsule", 1)[0]
        self.assertIn("c.scale(1.16, 1.16)", glove)
        self.assertNotIn("c.scale(1.16, 1.16)", pen)


if __name__ == "__main__":
    unittest.main()
