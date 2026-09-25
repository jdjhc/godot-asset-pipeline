import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons/asset_pipeline/backend"))
from asset_pipeline.service import PipelineService
from asset_pipeline.tripo import SubmissionUncertain, ProviderUnavailable


class FakeProvider:
    def __init__(self):
        self.submissions = 0
        self.polls = 0
        self.fail_poll_once = False
        self.uncertain = False
        self.release = threading.Event()
        self.release.set()

    def preview(self, plan):
        return {"kind": plan["kind"], "cost": None}

    def available(self):
        return {"available": True}

    def submit(self, plan):
        self.submissions += 1
        if self.uncertain:
            raise SubmissionUncertain("Response lost")
        return {"task_id": "00000000-0000-4000-8000-000000000001"}

    def poll(self, task_id):
        self.polls += 1
        self.release.wait(2)
        if self.fail_poll_once:
            self.fail_poll_once = False
            raise RuntimeError("Temporary read failure")
        return {"status": "success", "progress": 100, "outputs": [], "credits_consumed": 10}

    def download(self, result, directory):
        file = Path(directory) / "result.png"
        file.write_bytes(b"test-artifact")
        return [{"path": str(file), "role": "image"}]


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.provider = FakeProvider()
        self.service = PipelineService(self.temp.name, self.provider, poll_interval=0.005)
        self.node = self.service.dispatch("node.create", {"node": {"kind": "concept", "label": "Concept", "prompt": "scene"}})

    def tearDown(self):
        self.provider.release.set()
        self.service.stopping.set()
        for worker, _ in self.service.workers.values():
            worker.join(3)
        self.temp.cleanup()

    def wait(self, job, status):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            current = self.service.store.get_job(job["id"])
            if current["status"] == status:
                return current
            time.sleep(0.005)
        self.fail("Job did not reach %s: %s" % (status, current))

    def test_only_selected_stage_runs(self):
        child = self.service.store.create_node({"label": "Child", "kind": "subject", "inputs": [{"node_id": self.node["id"], "role": "reference"}]})
        job = self.service.run(self.node["id"])
        self.wait(job, "completed")
        self.assertEqual(self.provider.submissions, 1)
        self.assertIsNone(self.service.store.get_node(child["id"])["current_version"])
        self.assertEqual(len(self.service.store.snapshot()["jobs"]), 1)

    def test_resume_after_network_error_never_submits_again(self):
        self.provider.fail_poll_once = True
        job = self.service.run(self.node["id"])
        self.wait(job, "paused")
        self.service.workers[job["id"]][0].join()
        self.service.resume(job["id"])
        self.wait(job, "completed")
        self.assertEqual(self.provider.submissions, 1)
        self.assertEqual(self.provider.polls, 2)

    def test_ambiguous_submission_prevents_accidental_duplicate(self):
        self.provider.uncertain = True
        job = self.service.run(self.node["id"])
        self.wait(job, "submission_uncertain")
        with self.assertRaises(ValueError):
            self.service.run(self.node["id"])
        with self.assertRaises(ValueError):
            self.service.resume(job["id"])
        self.assertEqual(self.provider.submissions, 1)

    def test_two_clicks_while_in_flight_do_not_double_submit(self):
        self.provider.release.clear()
        job = self.service.run(self.node["id"])
        with self.assertRaises(ValueError):
            self.service.run(self.node["id"])
        self.provider.release.set()
        self.wait(job, "completed")
        self.assertEqual(self.provider.submissions, 1)

    def test_old_result_cannot_replace_edited_recipe(self):
        self.provider.release.clear()
        job = self.service.run(self.node["id"])
        self.service.store.update_node(self.node["id"], {"prompt": "different scene"})
        self.provider.release.set()
        completed = self.wait(job, "completed")
        node = self.service.store.get_node(self.node["id"])
        self.assertFalse(completed["promoted"])
        self.assertIsNone(node["current_version"])
        self.assertEqual(len(node["versions"]), 1)

    def test_preview_and_template_never_submit(self):
        self.service.dispatch("node.plan", {"node_id": self.node["id"]})
        result = self.service.template("Prop")
        self.assertEqual(len(result["nodes"]), 6)
        self.assertEqual(self.provider.submissions, 0)

    def test_local_configuration_failure_can_be_fixed_without_unknown_task(self):
        with patch.object(self.provider, "submit", side_effect=ProviderUnavailable("CLI not installed")):
            job = self.service.run(self.node["id"])
            self.wait(job, "failed")
            self.service.workers[job["id"]][0].join()
        fixed = self.service.run(self.node["id"])
        self.wait(fixed, "completed")
        self.assertEqual(self.provider.submissions, 1)

    def test_restart_after_artifact_commit_does_not_duplicate_version(self):
        plan = self.service.store.plan(self.node["id"])
        job = self.service.store.create_job(self.node["id"], plan)
        task_id = "00000000-0000-4000-8000-000000000001"
        self.service.store.update_job(job["id"], {"status": "downloading", "task_id": task_id})
        file = Path(self.temp.name) / "completed.png"
        file.write_bytes(b"saved")
        version = self.service.store.import_version(self.node["id"], [{"path": str(file), "role": "image"}],
            metadata={"task_id": task_id}, plan=plan)
        restarted = PipelineService(self.temp.name, self.provider)
        restarted.resume(job["id"])
        restarted.workers[job["id"]][0].join(3)
        self.assertEqual(restarted.store.get_job(job["id"])["version_id"], version["id"])
        self.assertEqual(len(restarted.store.get_node(self.node["id"])["versions"]), 1)
        self.assertEqual(self.provider.polls, 0)

    def test_restart_retains_id_for_resume(self):
        plan = self.service.store.plan(self.node["id"])
        job = self.service.store.create_job(self.node["id"], plan)
        self.service.store.update_job(job["id"], {"status": "running", "task_id": "00000000-0000-4000-8000-000000000001"})
        restarted = PipelineService(self.temp.name, self.provider, poll_interval=0.005)
        self.assertEqual(restarted.store.get_job(job["id"])["status"], "paused")
        restarted.resume(job["id"])
        restarted.workers[job["id"]][0].join(3)
        self.assertEqual(restarted.store.get_job(job["id"])["status"], "completed")
        self.assertEqual(self.provider.submissions, 0)


if __name__ == "__main__":
    unittest.main()
