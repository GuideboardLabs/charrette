from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from board.store import RevConflict, Store, add_stroke, apply_objects


class StoreContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root)

    def test_write_bumps_revision_and_rejects_a_stale_revision(self):
        self.assertEqual(self.store.read("b1")["rev"], 0)
        first = self.store.write("b1", lambda doc: doc, if_rev=0)
        self.assertEqual(first["rev"], 1)
        second = self.store.write("b1", lambda doc: doc, if_rev=1)
        self.assertEqual(second["rev"], 2)
        with self.assertRaises(RevConflict) as caught:
            self.store.write("b1", lambda doc: doc, if_rev=1)
        self.assertEqual(caught.exception.rev, 2)

    def test_stroke_author_assigns_the_protected_human_and_agent_layers(self):
        human = self.store.write("b1", add_stroke([[0, 1], [4, 5]], actor="human"))
        agent = self.store.write("b1", add_stroke([[8, 9], [12, 13]], actor="agent"))
        self.assertEqual(human["_last"], "obj_1")
        self.assertEqual(agent["_last"], "obj_2")
        persisted = self.store.read("b1")["objects"]
        self.assertEqual(persisted["obj_1"]["actor"], "human")
        self.assertEqual(persisted["obj_1"]["layer"], "human-ink")
        self.assertEqual(persisted["obj_2"]["actor"], "agent")
        self.assertEqual(persisted["obj_2"]["layer"], "agent-diagram")

    def test_camera_write_persists_in_the_document_and_board_index(self):
        camera = {"x": 13.5, "y": -8.0, "z": 0.42}
        self.store.write("b1", lambda doc: {**doc, "camera": camera})
        reopened = Store(self.root)
        self.assertEqual(reopened.read("b1")["camera"], camera)
        row = next(item for item in reopened.index()["boards"] if item["id"] == "b1")
        self.assertEqual(row["camera"], camera)
        self.assertEqual(row["rev"], 1)

    def test_apply_objects_resolves_map_key_and_does_not_persist_address_fields(self):
        doc = self.store.write("b1", apply_objects([{"_key": "shape_7", "kind": "shape", "ink": "red"}]))
        self.assertEqual(doc["_last"], "shape_7")
        obj = self.store.read("b1")["objects"]["shape_7"]
        self.assertEqual(obj["kind"], "shape")
        self.assertEqual(obj["ink"], "red")
        self.assertNotIn("id", obj)
        self.assertNotIn("_key", obj)
        self.assertEqual(obj["rev"], 1)

    def test_new_object_gets_one_creation_stamp_and_updates_keep_it(self):
        self.store.write("b1", apply_objects([{"id": "n1", "kind": "text", "text": "a"}]))
        created = self.store.read("b1")["objects"]["n1"]["created"]
        self.store.write("b1", apply_objects([{"id": "n1", "text": "b"}]))
        updated = self.store.read("b1")["objects"]["n1"]
        self.assertRegex(created, r"^\d{4}-\d{2}-\d{2}T")
        self.assertEqual(updated["created"], created)
        self.assertEqual(updated["text"], "b")
        self.assertEqual(updated["rev"], 2)

    def test_board_index_materialises_default_and_allocates_a_unique_count_based_id(self):
        initial = self.store.index()
        self.assertEqual(initial["active"], "b1")
        self.assertEqual([row["id"] for row in initial["boards"]], ["b1"])
        self.store.ensure_board("b3", "third")
        created = self.store.new_board("next board")
        self.assertEqual(created["active"], "b4")
        self.assertEqual([row["id"] for row in created["boards"]], ["b1", "b3", "b4"])
        names = {row["id"]: row["name"] for row in created["boards"]}
        self.assertEqual(names["b4"], "next board")
        self.assertEqual(names["b3"], "third")

    def test_board_index_tracks_object_count_revision_and_last_object(self):
        self.store.write("b1", add_stroke([[1, 2], [3, 4]], actor="agent"))
        row = next(item for item in self.store.index()["boards"] if item["id"] == "b1")
        self.assertEqual(row["rev"], 1)
        self.assertEqual(row["objects"], 1)
        self.assertEqual(row["last_activity"]["object_id"], "obj_1")
        self.assertEqual(row["last_activity"]["actor"], "agent")
        self.assertEqual(row["last_activity"]["bbox"], [1, 2, 2, 2])


if __name__ == "__main__":
    unittest.main()

    def test_add_stroke_stamps_rev_and_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            written = store.write("b1", add_stroke([[10, 10], [40, 20]], actor="agent"))
            doc = store.read("b1")
            stroke = doc["objects"][written["_last"]]
            self.assertEqual(stroke["rev"], doc["rev"],
                             "a stroke must carry the rev of the write that created it")
            self.assertTrue(stroke.get("created"),
                            "a stroke without a created stamp cannot be ordered by a replay")
            self.assertEqual(stroke["actor"], "agent")

    def test_a_moved_stroke_gets_its_box_recomputed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            wrote = store.write("b1", add_stroke([[0, 0], [100, 0]], actor="agent"))
            oid = wrote["_last"]
            # a client that moves the ink but keeps the old box would leave every hit
            # test aiming at where the stroke used to be
            store.write("b1", apply_objects(upserts=[{"id": oid, "pts": [[0, 50], [100, 50]],
                                                      "bbox": [0, 0, 100, 0]}]))
            obj = store.read("b1")["objects"][oid]
            self.assertEqual(obj["bbox"], [0.0, 50.0, 100.0, 0.0],
                             "the box is derived from the points, not taken from the writer")
