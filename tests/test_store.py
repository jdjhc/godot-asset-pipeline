import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons/asset_pipeline/backend"))
from asset_pipeline.store import Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        self.source = self.project / "source.png"
        self.source.write_bytes(b"pretend image, deliberately not visually approved")
        self.store = Store(str(self.project))

    def tearDown(self):
        self.tmp.cleanup()

    def node(self, name, *parents, kind="view"):
        return self.store.create_node({"id": name, "label": name, "kind": kind,
            "inputs": [{"node_id": parent, "role": f"input_{index}"} for index, parent in enumerate(parents)]})

    def version(self, name, plan=None):
        return self.store.import_version(name, [{"path": str(self.source), "role": "image"}], plan=plan)

    def diamond(self):
        self.node("concept", kind="concept")
        self.version("concept")
        self.node("front", "concept")
        self.version("front")
        self.node("back", "concept")
        self.version("back")
        self.node("model", "front", "back", kind="model")
        self.version("model")
        self.node("unrelated", kind="reference")
        self.version("unrelated")

    def test_direct_library_import_keeps_recipe_and_copies_artifact(self):
        self.node("concept", kind="concept")
        self.store.update_node("concept", {"prompt": "a blue kiosk", "params": {"seed": 42}})
        version = self.version("concept")
        self.assertTrue(version["promoted"])
        self.assertEqual(version["prompt"], "a blue kiosk")
        self.assertEqual(version["params"], {"seed": 42})
        artifact = self.project / version["files"][0]["path"]
        self.assertEqual(artifact.read_bytes(), self.source.read_bytes())
        self.source.write_bytes(b"source overwritten")
        self.assertNotEqual(artifact.read_bytes(), self.source.read_bytes())
        self.assertFalse(self.store.get_node("concept")["stale"])

    def test_prompt_change_invalidates_only_descendants_without_generating(self):
        self.diamond()
        before = self.store.snapshot()
        previous = {node["id"]: node["current_version"] for node in before["nodes"]}
        self.store.update_node("concept", {"prompt": "red kiosk"})
        after = self.store.snapshot()
        for node in after["nodes"]:
            self.assertEqual(node["current_version"], previous[node["id"]])
            self.assertEqual(node["stale"], node["id"] != "unrelated")
        self.assertEqual(after["jobs"], [])
        self.assertEqual(sum(len(n["versions"]) for n in before["nodes"]), sum(len(n["versions"]) for n in after["nodes"]))

    def test_branch_regeneration_preserves_sibling_and_pins_all_inputs(self):
        self.diamond()
        before_back = self.store.get_node("back")
        self.store.update_node("front", {"prompt": "remove extra rack"})
        front = self.version("front")
        self.assertEqual(self.store.get_node("back"), before_back)
        self.assertTrue(self.store.get_node("model")["stale"])
        model = self.version("model")
        self.assertEqual(model["input_versions"]["front"], front["id"])
        self.assertEqual(model["input_versions"]["back"], before_back["current_version"])
        self.assertFalse(self.store.get_node("model")["stale"])

    def test_obsolete_remote_completion_saved_but_not_promoted(self):
        self.diamond()
        original = self.store.get_node("model")["current_version"]
        plan = self.store.plan("model")
        self.store.update_node("front", {"prompt": "edit while remote job runs"})
        late = self.version("model", plan)
        self.assertFalse(late["promoted"])
        model = self.store.get_node("model")
        self.assertEqual(model["current_version"], original)
        self.assertTrue(model["stale"])
        self.assertEqual(len(model["versions"]), 2)
        self.assertEqual(late["input_versions"], {edge["node_id"]: edge["version_id"] for edge in plan["inputs"]})

    def test_own_recipe_change_prevents_old_completion_promotion(self):
        self.node("model", kind="model")
        plan = self.store.plan("model")
        self.store.update_node("model", {"params": {"face_limit": 5000}})
        version = self.version("model", plan)
        self.assertFalse(version["promoted"])
        self.assertIsNone(self.store.get_node("model")["current_version"])

    def test_cosmetic_changes_do_not_invalidate_plan(self):
        self.node("model", kind="model")
        plan = self.store.plan("model")
        self.store.update_node("model", {"position": [120, 360], "label": "New label"})
        self.assertEqual(plan["fingerprint"], self.store.plan("model")["fingerprint"])
        self.assertTrue(self.version("model", plan)["promoted"])

    def test_cycle_rejected_atomically(self):
        self.diamond()
        before = self.store.snapshot()
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.store.update_node("concept", {"inputs": [{"node_id": "model", "role": "oops"}]})
        self.assertEqual(self.store.snapshot(), before)
        with self.assertRaisesRegex(ValueError, "itself"):
            self.store.update_node("concept", {"inputs": [{"node_id": "concept", "role": "oops"}]})

    def test_missing_inputs_reported_without_provider_call(self):
        self.node("empty")
        self.node("downstream", "empty")
        plan = self.store.plan("downstream")
        self.assertEqual(plan["missing_inputs"], ["empty"])
        self.assertIsNone(plan["inputs"][0]["version_id"])
        self.assertEqual(plan["inputs"][0]["files"], [])

    def test_select_old_version_keeps_immutable_history_and_invalidates_model(self):
        self.diamond()
        old = self.store.get_node("front")["current_version"]
        self.version("front")
        history = self.store.get_node("front")["versions"]
        chosen = self.store.select_version("front", old)
        self.assertEqual(chosen["versions"], history)
        self.assertEqual(chosen["current_version"], old)
        self.assertTrue(self.store.get_node("model")["stale"])

    def test_jobs_restart_and_immutable_recipe(self):
        self.node("model", kind="model")
        plan = self.store.plan("model")
        job = self.store.create_job("model", plan)
        self.store.update_job(job["id"], {"status": "submitted", "remote_task_id": "remote-123", "progress": 40})
        reopened = Store(str(self.project))
        saved = reopened.get_job(job["id"])
        self.assertEqual(saved["remote_task_id"], "remote-123")
        self.assertEqual(saved["plan"], plan)
        with self.assertRaisesRegex(ValueError, "immutable"):
            reopened.update_job(job["id"], {"plan": {}})

    def test_stale_job_plan_cannot_submit(self):
        self.node("model", kind="model")
        plan = self.store.plan("model")
        self.store.update_node("model", {"prompt": "new"})
        with self.assertRaisesRegex(ValueError, "fresh plan"):
            self.store.create_job("model", plan)
        self.assertEqual(self.store.snapshot()["jobs"], [])

    def test_relative_traversal_and_symbolic_link_escape_rejected(self):
        self.node("ref")
        with self.assertRaisesRegex(ValueError, "must not contain"):
            self.store.import_version("ref", [{"path": "../source.png"}])
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / "image.png"
            external.write_bytes(b"outside")
            (self.project / "escape").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "escaped"):
                self.store.import_version("ref", [{"path": "escape/image.png"}])
            # Explicit user-selected external files are valid imports.
            self.assertTrue(self.store.import_version("ref", [{"path": str(external)}])["promoted"])

    def test_managed_output_symlink_rejected(self):
        self.node("ref")
        with tempfile.TemporaryDirectory() as outside:
            (self.project / "assets").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symbolic links"):
                self.version("ref")
            self.assertEqual(list(Path(outside).iterdir()), [])
        self.assertEqual(self.store.get_node("ref")["versions"], [])

    def test_state_directory_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as project, tempfile.TemporaryDirectory() as outside:
            (Path(project) / ".asset_pipeline").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symbolic links"):
                Store(project)

    def test_identifiers_finite_params_and_cross_node_versions_checked(self):
        with self.assertRaises(ValueError):
            self.node("../escape")
        self.node("a")
        self.node("b")
        a = self.version("a")
        with self.assertRaisesRegex(ValueError, "belong"):
            self.store.select_version("b", a["id"])
        with self.assertRaises(ValueError):
            self.store.update_node("a", {"params": {"bad": float("nan")}})
        with self.assertRaises(ValueError):
            self.store.update_node("a", {"position": [0, float("inf")]})

    def test_instances_keep_stable_asset_and_pinned_version(self):
        self.node("model", kind="model")
        v1 = self.version("model")
        one = self.store.record_instance({"id": "instance_1", "asset_node_id": "model", "version_id": v1["id"],
                                          "scene": "res://demo.tscn", "node_path": "Street/Kiosk", "transform": [1, 2, 3]})
        self.version("model")
        self.assertEqual(self.store.snapshot()["instances"], [one])

    def test_parallel_store_connections_do_not_lose_mutations(self):
        errors = []
        def create(index):
            try:
                Store(str(self.project)).create_node({"id": f"n{index}", "label": f"Node {index}", "kind": "custom"})
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=create, args=(index,)) for index in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        snapshot = self.store.snapshot()
        self.assertEqual(len(snapshot["nodes"]), 12)
        self.assertEqual(snapshot["revision"], 12)

    def test_two_parallel_completions_cannot_replace_first_with_outdated_recipe(self):
        self.node("model", kind="model")
        plan = self.store.plan("model")
        first = self.version("model", plan)
        second = self.version("model", plan)
        self.assertTrue(first["promoted"])
        self.assertFalse(second["promoted"])
        self.assertEqual(self.store.get_node("model")["current_version"], first["id"])

    def test_missing_source_leaves_no_version_or_revision_change(self):
        self.node("ref")
        before = self.store.snapshot()
        with self.assertRaisesRegex(ValueError, "does not exist"):
            self.store.import_version("ref", [{"path": "missing.glb"}])
        self.assertEqual(self.store.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
