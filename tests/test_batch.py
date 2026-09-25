"""Batch scheduling regressions with real SQLite and a thread-safe fake provider.

The provider never launches CLI/network operations, so these tests cannot spend
credits. Fake task IDs survive service replacement to exercise resume semantics.
"""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons/asset_pipeline/backend"))
from asset_pipeline.service import PipelineService
from asset_pipeline.tripo import SubmissionUncertain, TripoProvider


class BatchProvider:
    def __init__(self):
        self.lock = threading.RLock()
        self.release = threading.Event()
        self.release.set()
        self.submissions = []
        self.polls = []
        self.tasks = {}
        self.failed_nodes = set()
        self.uncertain_nodes = set()
        self.fail_poll_once_nodes = set()
        self.active = set()
        self.max_active = 0
        self.max_active_images = 0

    def available(self):
        return {"available": True, "authentication": "fake_no_network"}

    def preview(self, plan):
        if plan.get("missing_inputs"):
            raise ValueError("Fake provider refuses missing actual input versions")
        return {"kind": plan["kind"], "inputs": copy.deepcopy(plan["inputs"]), "cost": 1}

    def submit(self, plan):
        with self.lock:
            captured = copy.deepcopy(plan)
            self.submissions.append(captured)
            task_id = str(uuid.uuid4())
            self.tasks[task_id] = captured
            self.active.add(task_id)
            self.max_active = max(self.max_active, len(self.active))
            self.max_active_images = max(self.max_active_images,
                sum(PipelineService._is_image_plan(self.tasks[task]) for task in self.active))
            if plan["node_id"] in self.uncertain_nodes:
                raise SubmissionUncertain("Simulated response loss after remote acceptance")
            return {"task_id": task_id, "status": "queued"}

    def poll(self, task_id):
        with self.lock:
            self.polls.append(task_id)
            plan = self.tasks[task_id]
            node_id = plan["node_id"]
            if node_id in self.fail_poll_once_nodes:
                self.fail_poll_once_nodes.remove(node_id)
                raise RuntimeError("Simulated transient query failure")
            if not self.release.is_set():
                return {"task_id": task_id, "status": "running", "progress": 25, "outputs": []}
            self.active.discard(task_id)
            if node_id in self.failed_nodes:
                return {"task_id": task_id, "status": "failed", "progress": 25, "error": "Simulated generation failure"}
            return {"task_id": task_id, "status": "success", "progress": 100, "credits_consumed": 1,
                    "outputs": [{"fake_task_id": task_id}]}

    def download(self, result, directory):
        task_id = result["task_id"]
        with self.lock:
            plan = self.tasks[task_id]
        model = plan["kind"] == "model"
        file = Path(directory) / ("artifact.glb" if model else "artifact.png")
        file.write_bytes((plan["node_id"] + ":" + task_id).encode())
        return [{"path": str(file), "role": "model" if model else "image"}]

    def submitted_ids(self):
        with self.lock:
            return [plan["node_id"] for plan in self.submissions]


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="asset-pipeline-batch-")
        self.project = Path(self.temp.name)
        self.provider = BatchProvider()
        self.service = PipelineService(self.project, self.provider, poll_interval=0.005)
        self.services = [self.service]
        self.source = self.project / "reference.png"
        self.source.write_bytes(b"existing origin reference, no remote generation")
        self.service.store.create_node({"id": "origin", "label": "Original concept", "kind": "reference"})
        self.service.store.import_version("origin", [{"path": str(self.source), "role": "image"}])

    def stop_service(self, service):
        service.stopping.set()
        # Both scheduler and individual job workers must stop before temp cleanup.
        for collection_name in ("batch_workers", "workers"):
            collection = getattr(service, collection_name, {})
            values = list(collection.values()) if isinstance(collection, dict) else []
            for value in values:
                worker = value[0] if isinstance(value, tuple) else value
                if isinstance(worker, threading.Thread):
                    worker.join(3)

    def tearDown(self):
        self.provider.release.set()
        for service in self.services:
            self.stop_service(service)
        self.temp.cleanup()

    def eventually(self, predicate, description="condition", timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.005)
        snapshot = self.service.store.snapshot()
        diagnostic = {"batches": snapshot.get("batches", []), "jobs": [
            {key: job.get(key) for key in ("node_id", "status", "error")} for job in snapshot["jobs"]],
            "submitted": self.provider.submitted_ids()}
        self.fail("Timed out waiting for " + description + ": " + repr(diagnostic))

    def spec(self, count=1, concurrency=2, batch_id="batch_test"):
        return {"id": batch_id, "label": "Fake batch", "reference_node_id": "origin", "concurrency": concurrency,
                "assets": [{"id": "asset%d" % index, "name": "Asset %d" % index,
                            "prompts": {stage: "%s for asset %d" % (stage, index) for stage in ("subject", "front", "right", "back")},
                            "image_params": {"model": "seedream_v5"},
                            "model_params": {"model": "P2-20260801", "quad": False, "face_limit": 16000,
                                             "texture_quality": "standard", "texture": True, "pbr": True}}
                           for index in range(count)]}

    def create(self, spec=None):
        value = self.service.dispatch("batch.create", {"spec": spec or self.spec()})
        return value.get("batch", value)

    def batch(self, batch_id):
        return next(batch for batch in self.service.dispatch("snapshot", {})["batches"] if batch["id"] == batch_id)

    def stage_ids(self, batch, asset_index=0):
        return batch["assets"][asset_index]["node_ids"]

    def run_batch(self, batch):
        return self.service.dispatch("batch.run", {"batch_id": batch["id"]})

    def wait_batch(self, batch, statuses=("completed",)):
        return self.eventually(lambda: (current if current["status"] in statuses else None)
            if (current := self.batch(batch["id"])) else None, "batch state " + str(statuses))

    def count_submitted(self, node_id):
        return self.provider.submitted_ids().count(node_id)

    def test_create_materializes_complete_dependency_graph_without_generation(self):
        batch = self.create(self.spec(2))
        snapshot = self.service.dispatch("snapshot", {})
        self.assertEqual(len(snapshot["nodes"]), 11)
        self.assertEqual(len(batch["node_ids"]), 10)
        self.assertEqual(batch["progress"], {"completed": 0, "total": 10})
        self.assertEqual(snapshot["jobs"], [])
        self.assertEqual(self.provider.submitted_ids(), [])
        nodes = {node["id"]: node for node in snapshot["nodes"]}
        for asset in batch["assets"]:
            ids = asset["node_ids"]
            self.assertEqual(set(ids), {"subject", "front", "right", "back", "model"})
            self.assertEqual(nodes[ids["subject"]]["inputs"], [{"node_id": "origin", "role": "reference"}])
            for stage in ("right", "back"):
                self.assertEqual({edge["node_id"] for edge in nodes[ids[stage]]["inputs"]}, {ids["subject"], ids["front"]})
            self.assertEqual({(edge["node_id"], edge["role"]) for edge in nodes[ids["model"]]["inputs"]},
                             {(ids[role], role) for role in ("front", "right", "back")})
            self.assertEqual(nodes[ids["model"]]["prompt"], "")
            self.assertFalse(nodes[ids["model"]]["params"]["quad"])
            self.assertIsNone(nodes[ids["model"]]["current_version"])

    def test_batch_plan_and_repeated_create_do_not_submit_or_duplicate_nodes(self):
        spec = self.spec(2)
        batch = self.create(spec)
        before = self.service.store.snapshot()
        self.service.dispatch("batch.plan", {"batch_id": batch["id"]})
        again = self.create(spec)
        after = self.service.store.snapshot()
        self.assertEqual(again["id"], batch["id"])
        self.assertEqual({node["id"] for node in before["nodes"]}, {node["id"] for node in after["nodes"]})
        self.assertEqual(after["jobs"], [])
        self.assertEqual(self.provider.submitted_ids(), [])

    def test_full_batch_pins_actual_upstream_versions_and_progress(self):
        batch = self.create(self.spec(2))
        self.run_batch(batch)
        finished = self.wait_batch(batch)
        self.assertEqual(finished["progress"], {"completed": 10, "total": 10})
        self.assertEqual(finished["completed_nodes"], finished["total_nodes"])
        self.assertEqual(finished["active_node_ids"], [])
        self.assertEqual(len(self.provider.submissions), 10)
        self.assertEqual(len(set(self.provider.submitted_ids())), 10)
        nodes = {node["id"]: node for node in self.service.store.snapshot()["nodes"]}
        for plan in self.provider.submissions:
            node = nodes[plan["node_id"]]
            self.assertFalse(plan["missing_inputs"])
            version = next(v for v in node["versions"] if v["id"] == node["current_version"])
            for edge in plan["inputs"]:
                self.assertIsNotNone(edge["version_id"])
                self.assertTrue(edge["files"])
                self.assertEqual(version["input_versions"][edge["node_id"]], edge["version_id"])
                self.assertEqual(edge["version_id"], nodes[edge["node_id"]]["current_version"])
        for index in range(2):
            ids = self.stage_ids(batch, index)
            positions = {stage: self.provider.submitted_ids().index(node_id) for stage, node_id in ids.items()}
            self.assertLess(positions["subject"], positions["front"])
            self.assertLess(positions["front"], positions["right"])
            self.assertLess(positions["front"], positions["back"])
            self.assertGreater(positions["model"], max(positions["right"], positions["back"]))

    def test_shared_image_references_reach_submissions_and_pin_versions_without_model_input(self):
        spec = self.spec(2)
        layout_image = self.project / "layout.png"
        layout_image.write_bytes(b"Distinct final frame of the concept video")
        spec["references"] = [{"id": "layout", "path": str(layout_image)}]
        spec["image_reference_inputs"] = [{"node_id": "origin", "role": "overall_concept"},
                                           {"node_id": "layout", "role": "layout_reference"},
                                           {"node_id": "layout", "role": "duplicate_role"}]
        batch = self.create(spec)
        self.assertEqual(self.provider.submissions, [])
        layout_version = self.service.store.get_node("layout")["current_version"]
        self.assertEqual(batch["external_versions"]["layout"], layout_version)
        self.run_batch(batch)
        self.wait_batch(batch)
        plans = {plan["node_id"]: plan for plan in self.provider.submissions}
        tripo = TripoProvider(self.project, executable="/nonexistent/tripo")
        for asset in batch["assets"]:
            ids = asset["node_ids"]
            expected = {"subject": ["origin", "layout"],
                        "front": [ids["subject"], "origin", "layout"],
                        "right": [ids["subject"], ids["front"], "origin", "layout"],
                        "back": [ids["subject"], ids["front"], "origin", "layout"]}
            for stage, input_ids in expected.items():
                plan = plans[ids[stage]]
                self.assertEqual([edge["node_id"] for edge in plan["inputs"]], input_ids)
                layout_edge = plan["inputs"][-1]
                self.assertEqual(layout_edge["role"], "layout_reference")
                self.assertEqual(layout_edge["version_id"], layout_version)
                node = self.service.store.get_node(ids[stage])
                version = next(v for v in node["versions"] if v["id"] == node["current_version"])
                self.assertEqual(version["input_versions"]["layout"], layout_version)
                # Exercise the real provider's submit serialization, but replace the
                # subprocess so neither CLI, upload nor paid generation can execute.
                response = subprocess.CompletedProcess([], 0, json.dumps({"task_id": "task_test"}), "")
                with patch.object(tripo, "_helper_command", return_value=["fake_transport"]), \
                        patch("asset_pipeline.tripo.subprocess.run", return_value=response) as run, \
                        patch("asset_pipeline.tripo.urlopen") as network:
                    tripo.submit(plan)
                network.assert_not_called()
                submitted = json.loads(run.call_args.kwargs["input"])
                self.assertEqual(submitted["operation"], "submit")
                self.assertEqual(len(submitted["files"]), len(input_ids))
                self.assertEqual(submitted["expected_payload"]["inputs"],
                                 ["<upload:%s>" % file["path"] for file in submitted["files"]])
                self.assertEqual(Path(submitted["files"][-1]["path"]).read_bytes(), layout_image.read_bytes())
            model = plans[ids["model"]]
            self.assertEqual([(edge["node_id"], edge["role"]) for edge in model["inputs"]],
                             [(ids[view], view) for view in ("front", "right", "back")])
            self.assertEqual(set(next(iter(edge)) for edge in tripo.preview(model)["api_payload"]["inputs"]),
                             {"front", "right", "back"})
        subject_plan = plans[self.stage_ids(batch)["subject"]]
        self.assertEqual(subject_plan["inputs"][0]["role"], "reference")
        history = {node_id: self.service.store.get_node(node_id)["versions"] for node_id in batch["node_ids"]}
        self.service.store.import_version("layout", [{"path": str(self.source), "role": "image"}])
        for node_id, versions in history.items():
            node = self.service.store.get_node(node_id)
            self.assertEqual(node["versions"], versions)
            self.assertTrue(node["stale"])

    def test_shared_image_references_append_after_custom_order_and_keep_first_role(self):
        spec = self.spec()
        spec["references"] = [{"id": "layout", "path": str(self.source)}]
        spec["image_reference_inputs"] = [{"node_id": "layout"}, {"node_id": "origin", "role": "concept"}]
        spec["assets"][0]["stage_inputs"] = {
            "back": [{"stage": "front", "role": "primary"}, {"node_id": "origin", "role": "detail"},
                     {"stage": "subject", "role": "subject"}, {"node_id": "origin", "role": "duplicate"}]}
        batch = self.create(spec)
        ids = self.stage_ids(batch)
        expected = [{"node_id": ids["front"], "role": "primary"}, {"node_id": "origin", "role": "detail"},
                    {"node_id": ids["subject"], "role": "subject"}, {"node_id": "layout", "role": "reference"}]
        self.assertEqual(self.service.store.get_node(ids["back"])["inputs"], expected)
        self.assertEqual(self.create(spec)["id"], batch["id"])
        self.assertEqual(self.provider.submitted_ids(), [])

    def test_invalid_shared_image_references_roll_back_imported_files_and_graph(self):
        invalid = [None, {}, "layout", ["layout"], [{"node_id": "missing"}], [{"node_id": ["origin"]}],
                   [{"node_id": "origin", "role": ""}], [{"node_id": "origin", "role": " "}],
                   [{"node_id": "origin", "role": 3}], [{"node_id": "origin", "role": "x" * 101}],
                   [{"node_id": "origin"}, {"node_id": "origin", "role": None}]]
        before = self.service.store.snapshot()
        artifacts = set((self.project / "assets/asset_pipeline").rglob("*"))
        for value in invalid:
            with self.subTest(value=value):
                spec = self.spec()
                spec["references"] = [{"id": "layout", "path": str(self.source)}]
                spec["image_reference_inputs"] = value
                with self.assertRaises(ValueError):
                    self.create(spec)
                self.assertEqual(self.service.store.snapshot(), before)
                new_paths = set((self.project / "assets/asset_pipeline").rglob("*")) - artifacts
                self.assertFalse([path for path in new_paths if path.is_file() or any(path.iterdir())])
        self.assertEqual(self.provider.submitted_ids(), [])

    def test_shared_image_reference_changes_block_batch_before_submission(self):
        spec = self.spec()
        spec["references"] = [{"id": "layout", "path": str(self.source)}]
        spec["image_reference_inputs"] = [{"node_id": "layout", "role": "layout_reference"}]
        batch = self.create(spec)
        replacement = self.project / "replacement_layout.png"
        replacement.write_bytes(b"New layout after the batch was planned")
        self.service.store.import_version("layout", [{"path": str(replacement), "role": "image"}])
        self.run_batch(batch)
        self.wait_batch(batch, ("blocked", "failed", "needs_attention"))
        self.assertEqual(self.provider.submitted_ids(), [])

    def test_image_concurrency_is_one_despite_two_batch_worker_slots(self):
        self.provider.release.clear()
        batch = self.create(self.spec(3, concurrency=2))
        self.run_batch(batch)
        self.eventually(lambda: len(self.provider.submissions) == 1, "one remote image despite two workers")
        submitted = set(self.provider.submitted_ids())
        subject_ids = {self.stage_ids(batch, i)["subject"] for i in range(3)}
        self.assertTrue(submitted <= subject_ids)
        self.assertEqual(self.provider.max_active, 1)
        time.sleep(0.05)
        self.assertEqual(len(self.provider.submissions), 1)
        self.provider.release.set()
        self.wait_batch(batch)
        self.assertLessEqual(self.provider.max_active, 2)
        self.assertEqual(len(self.provider.submissions), 15)
        self.assertEqual(self.provider.max_active_images, 1)

    def test_manual_and_other_batch_wait_for_same_remote_image_slot(self):
        self.provider.release.clear()
        self.service.store.create_node({"id": "manual_image", "kind": "subject", "prompt": "Manual image",
            "inputs": [{"node_id": "origin", "role": "reference"}]})
        manual = self.service.run("manual_image")
        self.eventually(lambda: self.service.store.get_job(manual["id"]).get("task_id"), "manual remote task")
        first = self.create(self.spec(batch_id="first"))
        second = self.create(self.spec(batch_id="second"))
        self.run_batch(first)
        self.run_batch(second)
        self.eventually(lambda: len(self.service.store.snapshot()["jobs"]) == 3, "two queued batches")
        time.sleep(0.05)
        self.assertEqual(self.provider.submitted_ids(), ["manual_image"])
        self.assertEqual(self.batch(first["id"])["status"], "running")
        self.assertEqual(self.batch(second["id"])["status"], "running")
        self.provider.release.set()
        self.wait_batch(first)
        self.wait_batch(second)
        self.assertEqual(self.provider.max_active_images, 1)
        self.assertEqual(len(self.provider.submissions), 11)

    def test_paused_remote_image_reserves_slot_across_restart_until_polled_complete(self):
        self.provider.release.clear()
        first = self.create(self.spec(batch_id="first"))
        self.run_batch(first)
        held = self.eventually(lambda: next((job for job in self.service.store.snapshot()["jobs"] if job.get("task_id")), None), "held remote image")
        self.stop_service(self.service)
        self.service = PipelineService(self.project, self.provider, poll_interval=0.005)
        self.services.append(self.service)
        self.assertEqual(self.service.store.get_job(held["id"])["status"], "paused")
        second = self.create(self.spec(batch_id="second"))
        self.run_batch(second)
        time.sleep(0.05)
        self.assertEqual(len(self.provider.submissions), 1)
        self.assertEqual(self.batch(second["id"])["status"], "running")
        self.service.dispatch("job.resume", {"job_id": held["id"]})
        self.eventually(lambda: self.provider.polls.count(held["task_id"]) > 1, "existing ID can still poll")
        self.provider.release.set()
        self.wait_batch(second)
        self.assertEqual(self.count_submitted(held["node_id"]), 1)
        self.assertEqual(self.provider.max_active_images, 1)

    def test_uncertain_submit_keeps_other_images_queued_without_resubmit(self):
        first = self.create(self.spec(batch_id="first"))
        subject = self.stage_ids(first)["subject"]
        self.provider.uncertain_nodes.add(subject)
        self.run_batch(first)
        self.wait_batch(first, ("blocked",))
        second = self.create(self.spec(batch_id="second"))
        self.run_batch(second)
        time.sleep(0.05)
        self.assertEqual(self.provider.submitted_ids(), [subject])
        self.assertEqual(self.batch(second["id"])["status"], "running")

    def rejected_job(self, batch, node_id, status=429, structured=True):
        job = self.service.store.create_job(node_id, self.service.store.plan(node_id))
        patch = {"status": "failed", "error": "Tripo rejected this request (HTTP %d). Check API balance, authorization and parameters before trying again." % status}
        if structured:
            patch["submission_rejection"] = {"http_status": status, "api_code": 1005, "request_id": "fake-safe-id"}
        self.service.store.update_job(job["id"], patch)
        attempts = dict(self.batch(batch["id"])["attempts"])
        attempts[node_id] = job["id"]
        self.service.store.update_batch(batch["id"], {"attempts": attempts, "status": "blocked"})
        return self.service.store.get_job(job["id"])

    def test_explicit_429_retry_preserves_old_job_and_does_not_submit_until_run(self):
        batch = self.create()
        subject = self.stage_ids(batch)["subject"]
        old = self.rejected_job(batch, subject)
        result = self.service.dispatch("batch.retry_rejected", {"batch_id": batch["id"],
            "node_ids": [subject], "reason": "Image concurrency has been corrected to one"})
        retry = result["jobs"][0]
        self.assertEqual(retry["retry_of"], old["id"])
        self.assertEqual(retry["plan"]["fingerprint"], old["plan"]["fingerprint"])
        self.assertEqual(self.service.store.get_job(old["id"]), old)
        self.assertEqual(self.provider.submissions, [])
        self.assertEqual(self.batch(batch["id"])["attempts"][subject], retry["id"])
        self.run_batch(batch)
        self.wait_batch(batch)
        self.assertEqual(self.count_submitted(subject), 1)
        self.assertEqual(self.service.store.get_job(old["id"]), old)

    def test_400_retry_requires_changed_fingerprint_and_recovery_batch_recipe(self):
        batch = self.create()
        ids = self.stage_ids(batch)
        old = self.rejected_job(batch, ids["subject"], status=400)
        request = {"batch_id": batch["id"], "node_ids": [ids["subject"]], "reason": "Correct rejected parameters"}
        before = self.service.store.snapshot()
        with self.assertRaisesRegex(ValueError, "不可原样重试"):
            self.service.dispatch("batch.retry_rejected", request)
        self.assertEqual(self.service.store.snapshot(), before)
        self.service.store.update_node(ids["subject"], {"params": {"model": "seedream_v5", "size": "2K"}})
        with self.assertRaisesRegex(ValueError, "恢复批次"):
            self.service.dispatch("batch.retry_rejected", request)
        spec = self.spec(batch_id="recovery")
        spec["assets"][0]["existing_node_ids"] = ids
        recovery = self.create(spec)
        request["batch_id"] = recovery["id"]
        retry = self.service.dispatch("batch.retry_rejected", request)["jobs"][0]
        self.assertNotEqual(retry["plan"]["fingerprint"], old["plan"]["fingerprint"])
        self.assertEqual(retry["retry_of"], old["id"])
        self.assertEqual(self.batch(batch["id"])["attempts"][ids["subject"]], old["id"])
        self.run_batch(recovery)
        self.wait_batch(recovery)
        self.assertEqual(self.count_submitted(ids["subject"]), 1)

    def test_rejected_retry_validates_all_stages_atomically(self):
        batch = self.create(self.spec(2))
        first, second = (self.stage_ids(batch, index)["subject"] for index in range(2))
        self.rejected_job(batch, first)
        old = self.rejected_job(batch, second)
        self.service.store.update_job(old["id"], {"status": "submission_uncertain"})
        before = self.service.store.snapshot()
        with self.assertRaises(ValueError):
            self.service.dispatch("batch.retry_rejected", {"batch_id": batch["id"], "node_ids": [first, second], "reason": "Explicit retry"})
        self.assertEqual(self.service.store.snapshot(), before)

    def test_rejected_retry_refuses_remote_id_uncertainty_generic_errors_and_running_batch(self):
        cases = [
            {"task_id": str(uuid.uuid4())},
            {"remote_task_id": str(uuid.uuid4())},
            {"status": "submission_uncertain"},
            {"submission_rejection": {}, "error": "HTTP 429 network failure"},
            {"submission_rejection": {}, "error": "prefix Tripo rejected this request (HTTP 429). failure"},
            {"submission_rejection": {"http_status": 500}},
        ]
        for index, patch_job in enumerate(cases):
            with self.subTest(patch=patch_job):
                batch = self.create(self.spec(batch_id="reject_%d" % index))
                node = self.stage_ids(batch)["subject"]
                old = self.rejected_job(batch, node)
                self.service.store.update_job(old["id"], patch_job)
                before = self.service.store.snapshot()
                with self.assertRaises(ValueError):
                    self.service.dispatch("batch.retry_rejected", {"batch_id": batch["id"], "node_ids": [node], "reason": "Explicit retry"})
                self.assertEqual(self.service.store.snapshot(), before)
        batch = self.create(self.spec(batch_id="running_reject"))
        node = self.stage_ids(batch)["subject"]
        self.rejected_job(batch, node)
        self.service.store.update_batch(batch["id"], {"status": "running"})
        with self.assertRaises(ValueError):
            self.service.dispatch("batch.retry_rejected", {"batch_id": batch["id"], "node_ids": [node], "reason": "Explicit retry"})

    def test_legacy_explicit_rejection_is_supported_but_retry_cannot_be_duplicated(self):
        batch = self.create()
        node = self.stage_ids(batch)["subject"]
        old = self.rejected_job(batch, node, structured=False)
        args = {"batch_id": batch["id"], "node_ids": [node], "reason": "Known concurrency rejection"}
        result = self.service.dispatch("batch.retry_rejected", args)
        self.assertEqual(result["jobs"][0]["retry_of"], old["id"])
        before = self.service.store.snapshot()
        with self.assertRaises(ValueError):
            self.service.dispatch("batch.retry_rejected", args)
        self.assertEqual(self.service.store.snapshot(), before)

    def test_repeated_start_cannot_double_charge_active_or_completed_batch(self):
        self.provider.release.clear()
        batch = self.create(self.spec(2))
        self.run_batch(batch)
        self.eventually(lambda: len(self.provider.submissions) == 1, "active initial image")
        for _ in range(3):
            try:
                self.run_batch(batch)
            except ValueError:
                pass
        self.assertEqual(len(self.provider.submissions), 1)
        self.provider.release.set()
        self.wait_batch(batch)
        try:
            self.run_batch(batch)
        except ValueError:
            pass
        time.sleep(0.05)
        self.assertEqual(len(self.provider.submissions), 10)
        self.assertEqual(len(set(self.provider.submitted_ids())), 10)

    def test_failed_generation_never_automatically_retries_or_runs_dependents(self):
        batch = self.create()
        ids = self.stage_ids(batch)
        self.provider.failed_nodes.add(ids["front"])
        self.run_batch(batch)
        blocked = self.wait_batch(batch, ("blocked", "failed", "needs_attention"))
        self.assertEqual(self.count_submitted(ids["front"]), 1)
        self.assertTrue(blocked.get("error") or blocked.get("blocked_node_ids"))
        self.assertEqual(self.count_submitted(ids["right"]), 0)
        self.assertEqual(self.count_submitted(ids["back"]), 0)
        self.assertEqual(self.count_submitted(ids["model"]), 0)
        count = len(self.provider.submissions)
        self.provider.failed_nodes.clear()
        try:
            self.service.dispatch("batch.resume", {"batch_id": batch["id"]})
        except ValueError:
            pass
        time.sleep(0.1)
        self.assertEqual(len(self.provider.submissions), count, "resuming a batch is not authorization to buy a failed stage again")

    def test_ambiguous_submission_is_not_resubmitted_on_batch_resume(self):
        batch = self.create()
        subject = self.stage_ids(batch)["subject"]
        self.provider.uncertain_nodes.add(subject)
        self.run_batch(batch)
        self.wait_batch(batch, ("blocked", "failed", "needs_attention"))
        self.assertEqual(self.count_submitted(subject), 1)
        try:
            self.service.dispatch("batch.resume", {"batch_id": batch["id"]})
        except ValueError:
            pass
        time.sleep(0.1)
        self.assertEqual(self.count_submitted(subject), 1)
        self.assertEqual(len(self.provider.submissions), 1)

    def test_existing_images_are_adopted_without_new_generation(self):
        spec = self.spec()
        paths = {}
        for stage in ("subject", "front", "right", "back"):
            file = self.project / (stage + ".png")
            file.write_bytes(("already generated " + stage).encode())
            paths[stage] = str(file)
        spec["assets"][0]["existing_outputs"] = paths
        batch = self.create(spec)
        self.assertEqual(batch["progress"], {"completed": 4, "total": 5})
        self.run_batch(batch)
        self.wait_batch(batch)
        ids = self.stage_ids(batch)
        self.assertEqual(self.provider.submitted_ids(), [ids["model"]])
        nodes = {n["id"]: n for n in self.service.store.snapshot()["nodes"]}
        for stage in ("right", "back"):
            version = nodes[ids[stage]]["versions"][0]
            self.assertEqual(set(version["input_versions"]), {ids["subject"], ids["front"]})
            self.assertTrue(all(version_id for version_id in version["input_versions"].values()))

    def test_adopts_existing_active_job_and_does_not_submit_it_twice(self):
        batch = self.create()
        ids = self.stage_ids(batch)
        self.provider.release.clear()
        existing_job = self.service.run(ids["subject"])
        self.eventually(lambda: self.service.store.get_job(existing_job["id"]).get("task_id"), "saved original remote ID")
        self.run_batch(batch)
        time.sleep(0.05)
        self.assertEqual(self.count_submitted(ids["subject"]), 1)
        self.provider.release.set()
        self.wait_batch(batch)
        self.assertEqual(len(self.provider.submissions), 5)
        self.assertEqual(self.count_submitted(ids["subject"]), 1)

    def test_restart_resumes_saved_task_by_polling_without_resubmission(self):
        self.provider.release.clear()
        batch = self.create()
        ids = self.stage_ids(batch)
        self.run_batch(batch)
        self.eventually(lambda: len(self.provider.submissions) == 1, "initial submit")
        original_job = self.eventually(lambda: next((j for j in self.service.store.snapshot()["jobs"] if j.get("task_id")), None), "persisted task ID")
        original_task = original_job["task_id"]
        self.stop_service(self.service)
        restarted = PipelineService(self.project, self.provider, poll_interval=0.005)
        self.services.append(restarted)
        self.service = restarted
        recovered = self.batch(batch["id"])
        self.assertIn(recovered["status"], ("paused", "interrupted"))
        self.assertEqual(len(self.provider.submissions), 1)
        self.provider.release.set()
        self.service.dispatch("batch.resume", {"batch_id": batch["id"]})
        self.wait_batch(batch)
        self.assertEqual(self.count_submitted(ids["subject"]), 1)
        self.assertEqual(len(self.provider.submissions), 5)
        self.assertIn(original_task, self.provider.polls)
        saved = self.service.store.get_job(original_job["id"])
        self.assertEqual(saved["task_id"], original_task)
        self.assertEqual(saved["status"], "completed")

    def test_existing_node_mapping_preserves_original_recipe(self):
        existing = self.service.store.create_node({"id": "existing_subject", "kind": "subject", "label": "Original subject",
            "prompt": "Actual original prompt", "params": {"model": "seedream_v5"},
            "inputs": [{"node_id": "origin", "role": "reference"}], "position": [400, 500]})
        self.service.store.import_version(existing["id"], [{"path": str(self.source), "role": "image"}])
        before = self.service.store.get_node(existing["id"])
        spec = self.spec()
        spec["references"] = [{"id": "layout", "path": str(self.source)}]
        spec["image_reference_inputs"] = [{"node_id": "layout", "role": "layout_reference"}]
        spec["assets"][0]["existing_node_ids"] = {"subject": existing["id"]}
        batch = self.create(spec)
        self.assertEqual(self.stage_ids(batch)["subject"], existing["id"])
        self.assertEqual(self.service.store.get_node(existing["id"]), before)
        self.run_batch(batch)
        self.wait_batch(batch)
        self.assertNotIn(existing["id"], self.provider.submitted_ids())
        self.assertEqual(len(self.provider.submissions), 4)
        self.assertEqual(self.service.store.get_node(existing["id"]), before)
        for stage in ("front", "right", "back"):
            plan = next(p for p in self.provider.submissions if p["node_id"] == self.stage_ids(batch)[stage])
            self.assertEqual(plan["inputs"][-1]["node_id"], "layout")

    def test_custom_actual_view_references_are_retained(self):
        spec = self.spec()
        spec["assets"][0]["stage_inputs"] = {
            "back": [{"stage": "subject", "role": "reference"}, {"stage": "front", "role": "reference"},
                     {"node_id": "origin", "role": "reference"}]}
        batch = self.create(spec)
        ids = self.stage_ids(batch)
        back = self.service.store.get_node(ids["back"])
        self.assertEqual({e["node_id"] for e in back["inputs"]}, {ids["subject"], ids["front"], "origin"})
        self.run_batch(batch)
        self.wait_batch(batch)
        actual = next(p for p in self.provider.submissions if p["node_id"] == ids["back"])
        self.assertEqual({e["node_id"] for e in actual["inputs"]}, {ids["subject"], ids["front"], "origin"})
        self.assertTrue(all(e["version_id"] for e in actual["inputs"]))

    def test_pause_stops_new_stages_and_resume_continues_without_rebuying(self):
        self.provider.release.clear()
        batch = self.create()
        self.run_batch(batch)
        self.eventually(lambda: len(self.provider.submissions) == 1, "initial stage before pause")
        self.service.dispatch("batch.pause", {"batch_id": batch["id"]})
        self.provider.release.set()
        self.wait_batch(batch, ("paused",))
        time.sleep(0.05)
        self.assertEqual(len(self.provider.submissions), 1)
        self.service.dispatch("batch.resume", {"batch_id": batch["id"]})
        self.wait_batch(batch)
        self.assertEqual(len(self.provider.submissions), 5)
        self.assertEqual(len(set(self.provider.submitted_ids())), 5)

    def test_recipe_edit_during_generation_blocks_batch_without_auto_repurchase(self):
        self.provider.release.clear()
        batch = self.create()
        subject = self.stage_ids(batch)["subject"]
        self.run_batch(batch)
        self.eventually(lambda: self.count_submitted(subject) == 1, "initial subject before edit")
        self.service.store.update_node(subject, {"prompt": "A deliberate new recipe while old task runs"})
        self.provider.release.set()
        self.wait_batch(batch, ("blocked", "failed", "needs_attention"))
        node = self.service.store.get_node(subject)
        self.assertIsNone(node["current_version"])
        self.assertEqual(len(node["versions"]), 1)
        self.assertFalse(node["versions"][0]["promoted"])
        try:
            self.service.dispatch("batch.resume", {"batch_id": batch["id"]})
        except ValueError:
            pass
        time.sleep(0.1)
        self.assertEqual(self.provider.submitted_ids(), [subject])

    def test_invalid_later_asset_does_not_leave_half_created_batch(self):
        spec = self.spec(2)
        spec["assets"][1]["existing_node_ids"] = {"subject": "missing_existing_subject"}
        before = self.service.store.snapshot()
        with self.assertRaises(ValueError):
            self.create(spec)
        after = self.service.store.snapshot()
        self.assertEqual(after["nodes"], before["nodes"])
        self.assertEqual(after["jobs"], before["jobs"])
        self.assertEqual(after.get("batches", []), before.get("batches", []))
        self.assertEqual(self.provider.submitted_ids(), [])

    def test_static_references_import_in_order_and_pin_actual_versions(self):
        spec = self.spec()
        draft = self.project / "layout.png"
        draft.write_bytes(b"Human supplied street layout")
        annotated = self.project / "annotated.png"
        annotated.write_bytes(b"Known subject bounds drawn over layout")
        spec["references"] = [
            {"id": "layout", "label": "Street layout", "kind": "concept", "path": str(draft),
             "prompt": "Original layout prompt", "inputs": [{"node_id": "origin", "role": "reference"}]},
            {"id": "annotated", "label": "Subject sheet", "path": str(annotated),
             "inputs": [{"node_id": "layout", "role": "reference"}], "metadata": {"human_annotated": True}},
        ]
        spec["assets"][0]["subject_reference_node_ids"] = ["origin", "annotated"]
        batch = self.create(spec)
        origin_version = self.service.store.get_node("origin")["current_version"]
        layout = self.service.store.get_node("layout")
        sheet = self.service.store.get_node("annotated")
        self.assertEqual(layout["versions"][0]["input_versions"], {"origin": origin_version})
        self.assertEqual(sheet["versions"][0]["input_versions"], {"layout": layout["current_version"]})
        self.assertEqual(batch["progress"], {"completed": 0, "total": 5})
        self.assertEqual(self.provider.submitted_ids(), [])
        self.run_batch(batch)
        self.wait_batch(batch)
        subject_plan = next(plan for plan in self.provider.submissions if plan["node_id"] == self.stage_ids(batch)["subject"])
        self.assertEqual([entry["node_id"] for entry in subject_plan["inputs"]], ["origin", "annotated"])
        self.assertEqual(subject_plan["inputs"][1]["version_id"], sheet["current_version"])

    def test_subject_front_alias_reuses_one_node_and_preserves_unique_view_inputs(self):
        self.service.store.create_node({"id": "shared_front", "kind": "subject", "prompt": "Already frontal subject",
                                       "inputs": [{"node_id": "origin", "role": "reference"}]})
        self.service.store.import_version("shared_front", [{"path": str(self.source), "role": "image"}])
        spec = self.spec()
        spec["assets"][0]["existing_node_ids"] = {"subject": "shared_front", "front": "shared_front"}
        batch = self.create(spec)
        self.assertEqual(len(batch["node_ids"]), 4)
        self.assertEqual(batch["progress"], {"completed": 1, "total": 4})
        ids = self.stage_ids(batch)
        for side in ("right", "back"):
            self.assertEqual(self.service.store.get_node(ids[side])["inputs"], [{"node_id": "shared_front", "role": "reference"}])
        self.run_batch(batch)
        finished = self.wait_batch(batch)
        self.assertEqual(finished["progress"], {"completed": 4, "total": 4})
        self.assertEqual(set(self.provider.submitted_ids()), {ids["right"], ids["back"], ids["model"]})
        self.assertEqual(len(self.provider.submitted_ids()), 3)
        model_plan = next(plan for plan in self.provider.submissions if plan["node_id"] == ids["model"])
        self.assertEqual(next(edge["node_id"] for edge in model_plan["inputs"] if edge["role"] == "front"), "shared_front")

    def test_failed_asset_does_not_prevent_independent_asset_completion(self):
        batch = self.create(self.spec(2))
        failed = self.stage_ids(batch)
        good = self.stage_ids(batch, 1)
        self.provider.failed_nodes.add(failed["front"])
        self.run_batch(batch)
        finished = self.wait_batch(batch, ("blocked", "failed", "needs_attention"))
        self.assertEqual(finished["progress"], {"completed": 6, "total": 10})
        self.assertTrue(self.service.store.get_node(good["model"])["current_version"])
        self.assertEqual({node_id: self.count_submitted(node_id) for node_id in good.values()},
                         {node_id: 1 for node_id in good.values()})
        self.assertEqual(self.count_submitted(failed["front"]), 1)
        for stage in ("right", "back", "model"):
            self.assertEqual(self.count_submitted(failed[stage]), 0)

    def test_external_reference_change_blocks_unstarted_batch_without_submitting(self):
        batch = self.create()
        replacement = self.project / "replacement.png"
        replacement.write_bytes(b"User chose a different concept after batch creation")
        self.service.store.import_version("origin", [{"path": str(replacement), "role": "image"}])
        self.run_batch(batch)
        finished = self.wait_batch(batch, ("blocked", "failed", "needs_attention"))
        self.assertEqual(finished["progress"], {"completed": 0, "total": 5})
        self.assertIn(self.stage_ids(batch)["subject"], finished["blocked_node_ids"])
        self.assertEqual(self.provider.submitted_ids(), [])

    def test_missing_reference_is_visible_in_plan_and_never_submitted(self):
        self.service.store.create_node({"id": "empty_reference", "kind": "reference"})
        spec = self.spec()
        spec["reference_node_id"] = "empty_reference"
        batch = self.create(spec)
        planned = self.service.dispatch("batch.plan", {"batch_id": batch["id"]})
        subject_plan = next(plan for plan in planned["plans"] if plan["node_id"] == self.stage_ids(batch)["subject"])
        self.assertTrue(subject_plan["missing_inputs"])
        self.assertEqual(self.provider.submitted_ids(), [])
        self.assertEqual(self.service.store.snapshot()["jobs"], [])
        self.run_batch(batch)
        self.wait_batch(batch, ("blocked", "failed", "needs_attention"))
        self.assertEqual(self.provider.submitted_ids(), [])

    def test_failed_reference_import_rolls_back_graph_and_copied_artifacts(self):
        spec = self.spec()
        spec["references"] = [
            {"id": "first_reference", "path": str(self.source)},
            {"id": "bad_reference", "path": str(self.project / "absent.png")},
        ]
        before = self.service.store.snapshot()
        artifacts = set((self.project / "assets/asset_pipeline").rglob("*"))
        with self.assertRaises(ValueError):
            self.create(spec)
        after = self.service.store.snapshot()
        self.assertEqual(after, before)
        # Empty node parent directories are harmless; no orphan version or file survives.
        new_paths = set((self.project / "assets/asset_pipeline").rglob("*")) - artifacts
        self.assertFalse([path for path in new_paths if path.is_file() or any(path.iterdir())])
        self.assertEqual(self.provider.submitted_ids(), [])

    def test_query_failure_requires_explicit_resume_and_reuses_saved_task(self):
        batch = self.create()
        subject = self.stage_ids(batch)["subject"]
        self.provider.fail_poll_once_nodes.add(subject)
        self.run_batch(batch)
        self.wait_batch(batch, ("blocked", "failed", "needs_attention"))
        job = next(job for job in self.service.store.snapshot()["jobs"] if job["node_id"] == subject)
        self.assertTrue(job["task_id"])
        self.assertEqual(self.provider.polls, [job["task_id"]])
        self.assertEqual(self.provider.submitted_ids(), [subject])
        self.service.dispatch("batch.resume", {"batch_id": batch["id"]})
        self.wait_batch(batch)
        self.assertEqual(self.count_submitted(subject), 1)
        self.assertGreaterEqual(self.provider.polls.count(job["task_id"]), 2)
        self.assertEqual(len(self.provider.submitted_ids()), 5)

    def test_simultaneous_run_requests_create_one_scheduler_and_one_job_per_stage(self):
        self.provider.release.clear()
        batch = self.create(self.spec(2))
        gate = threading.Barrier(8)
        errors = []
        def start():
            try:
                gate.wait(timeout=3)
                self.run_batch(batch)
            except Exception as error:
                errors.append(error)
        callers = [threading.Thread(target=start) for _ in range(8)]
        for caller in callers:
            caller.start()
        for caller in callers:
            caller.join(3)
        self.assertFalse([caller for caller in callers if caller.is_alive()])
        self.assertEqual(errors, [])
        self.eventually(lambda: len(self.provider.submitted_ids()) == 1, "one remote image after eight callers")
        self.assertEqual(len(self.service.store.snapshot()["jobs"]), 2)
        self.provider.release.set()
        self.wait_batch(batch)
        submitted = self.provider.submitted_ids()
        self.assertEqual(len(submitted), 10)
        self.assertEqual(len(set(submitted)), 10)

    def test_restart_after_ambiguous_submit_never_purchases_a_replacement(self):
        batch = self.create()
        subject = self.stage_ids(batch)["subject"]
        self.provider.uncertain_nodes.add(subject)
        self.run_batch(batch)
        self.wait_batch(batch, ("blocked", "failed", "needs_attention"))
        self.stop_service(self.service)
        self.service = PipelineService(self.project, self.provider, poll_interval=0.005)
        self.services.append(self.service)
        self.provider.uncertain_nodes.clear()
        self.service.dispatch("batch.resume", {"batch_id": batch["id"]})
        self.wait_batch(batch, ("blocked", "failed", "needs_attention"))
        self.assertEqual(self.provider.submitted_ids(), [subject])
        self.assertEqual(self.provider.polls, [])
        self.assertEqual(len(self.service.store.snapshot()["jobs"]), 1)


if __name__ == "__main__":
    unittest.main()
