"""Provider contract tests. No test submits a real task, uploads or spends credits."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons/asset_pipeline/backend"))

from asset_pipeline.tripo import (
    InvalidPlan, ProviderUnavailable, SubmissionUncertain, SubmissionRejected, TripoError,
    TripoProvider, discover_executable,
)


class DownloadResponse(io.BytesIO):
    def __init__(self, data, content_length=None):
        super().__init__(data)
        self.headers = {"Content-Length": str(content_length if content_length is not None else len(data))}


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        (self.project / "assets").mkdir()
        self.provider = TripoProvider(self.project, executable="/nonexistent/tripo")

    def tearDown(self):
        self.temp.cleanup()

    def file(self, name, role="image"):
        target = self.project / "assets" / name
        target.write_bytes(b"fixture-image")
        return {"path": f"res://assets/{name}", "role": role}

    def plan(self, kind="model", params=None, prompt=""):
        files = [self.file("wrong-right.png", "front"), self.file("second.png", "back"), self.file("third.png", "right")]
        return {"kind": kind, "prompt": prompt, "params": params or {},
                "inputs": [{"node_id": "views", "version_id": "v1", "files": files}]}

    def concept(self):
        return {"kind": "concept", "prompt": "--model is literal text, not a command", "params": {}, "inputs": []}

    def mock_helper(self):
        return patch.object(self.provider, "_helper_command", return_value=["node", "transport.mjs", "official-package"])

    def mock_layout(self):
        return patch.object(self.provider, "_layout", return_value=(Path("/node"), Path("/official"), "0.5.1"))

    def test_preview_is_local_pure_and_role_based(self):
        plan = self.plan()
        before = sorted(str(path) for path in self.project.rglob("*"))
        with patch("asset_pipeline.tripo.subprocess.run") as run, patch("asset_pipeline.tripo.urlopen") as download:
            preview = self.provider.preview(plan)
        run.assert_not_called()
        download.assert_not_called()
        self.assertEqual(before, sorted(str(path) for path in self.project.rglob("*")))
        self.assertEqual([next(iter(item)) for item in preview["api_payload"]["inputs"]], ["front", "back", "right"])
        self.assertIn("wrong-right.png", preview["api_payload"]["inputs"][0]["front"])
        self.assertEqual(preview["api_payload"]["model"], "P2-20260801")
        self.assertEqual(preview["cost"], "unknown")

    def test_known_failure_phases_do_not_become_ambiguous_submissions(self):
        for phase, exception in [("pre_submit", ProviderUnavailable), ("rejected", SubmissionRejected), ("uncertain", SubmissionUncertain)]:
            response = subprocess.CompletedProcess([], 1, json.dumps({"failure_phase": phase, "http_status": 401}), "")
            with self.subTest(phase=phase), self.mock_helper(), patch("asset_pipeline.tripo.subprocess.run", return_value=response):
                with self.assertRaises(exception):
                    self.provider.submit(self.concept())

    def test_rejection_preserves_only_safe_diagnostics_and_static_hint(self):
        request_id = "01234567-89ab-cdef-0123-456789abcdef"
        payload = {"failure_phase": "rejected", "http_status": 429, "api_code": 2000,
                   "request_id": request_id, "message": "secret echoed input",
                   "suggestion": "secret credential", "headers": {"Authorization": "secret"}}
        response = subprocess.CompletedProcess([], 1, json.dumps(payload), "secret stderr")
        with self.mock_helper(), patch("asset_pipeline.tripo.subprocess.run", return_value=response) as run:
            with self.assertRaises(SubmissionRejected) as caught:
                self.provider.submit(self.concept())
        self.assertEqual(run.call_count, 1)
        self.assertEqual(caught.exception.diagnostics,
                         {"http_status": 429, "api_code": 2000, "request_id": request_id})
        self.assertIn("generation concurrency limit", str(caught.exception))
        self.assertIn(request_id, str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))

    def test_invalid_failure_metadata_cannot_leak_into_errors(self):
        for invalid in (
            {"http_status": "401 secret", "api_code": "2000 secret", "request_id": "Bearer secret"},
            {"http_status": True, "api_code": False, "request_id": "tcli_secret0123456789"},
            {"http_status": 999, "api_code": -1, "request_id": "req_" + "a" * 65},
            {"http_status": 400.5, "api_code": 1000000, "request_id": "req_valid001\nsecret"},
        ):
            with self.subTest(invalid=invalid):
                response = subprocess.CompletedProcess([], 1, json.dumps({"failure_phase": "rejected", **invalid}), "secret stderr")
                with self.mock_helper(), patch("asset_pipeline.tripo.subprocess.run", return_value=response):
                    with self.assertRaises(SubmissionRejected) as caught:
                        self.provider.submit(self.concept())
                self.assertEqual(caught.exception.diagnostics, {})
                self.assertNotIn("secret", str(caught.exception))

    def test_pre_submit_and_uncertain_keep_diagnostics_without_changing_retry_safety(self):
        for phase, error_type in (("pre_submit", ProviderUnavailable), ("uncertain", SubmissionUncertain)):
            with self.subTest(phase=phase):
                response = subprocess.CompletedProcess([], 1, json.dumps({"failure_phase": phase,
                    "http_status": 500, "api_code": 1000, "request_id": "req_test01234567"}), "")
                with self.mock_helper(), patch("asset_pipeline.tripo.subprocess.run", return_value=response) as run:
                    with self.assertRaises(error_type) as caught:
                        self.provider.submit(self.concept())
                self.assertEqual(run.call_count, 1)
                self.assertEqual(caught.exception.diagnostics["api_code"], 1000)
                self.assertIn("server error", str(caught.exception))

    def test_unknown_numeric_api_code_is_retained_without_raw_message(self):
        response = subprocess.CompletedProcess([], 1, json.dumps({"failure_phase": "rejected",
            "http_status": 400, "api_code": 90909, "message": "secret"}), "")
        with self.mock_helper(), patch("asset_pipeline.tripo.subprocess.run", return_value=response):
            with self.assertRaises(SubmissionRejected) as caught:
                self.provider.submit(self.concept())
        self.assertEqual(caught.exception.diagnostics, {"http_status": 400, "api_code": 90909})
        self.assertIn("API code 90909", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))

    def test_front_left_back_right_order_with_shuffled_files(self):
        plan = self.plan()
        plan["inputs"][0]["files"].append(self.file("fourth.png", "left"))
        plan["inputs"][0]["files"].reverse()
        inputs = self.provider.preview(plan)["api_payload"]["inputs"]
        self.assertEqual([next(iter(item)) for item in inputs], ["front", "left", "back", "right"])

    def test_input_edge_role_and_rear_alias(self):
        front = self.file("a.png")
        back = self.file("b.png")
        plan = {"kind": "model", "inputs": [{"role": "front", "files": [front]}, {"role": "rear", "files": [back]}]}
        self.assertEqual([list(item)[0] for item in self.provider.preview(plan)["api_payload"]["inputs"]], ["front", "back"])

    def test_rejects_missing_ambiguous_and_duplicate_views(self):
        for roles in (["back", "right"], ["front", "front"], ["image", "right"], ["front"]):
            with self.subTest(roles=roles):
                plan = {"kind": "model", "inputs": [{"files": [self.file(f"{i}.png", role) for i, role in enumerate(roles)]}]}
                with self.assertRaises(InvalidPlan):
                    self.provider.preview(plan)

    def test_quad_limit_and_wire_model(self):
        preview = self.provider.preview(self.plan(params={"model": "P2-20260801", "quad": True, "face_limit": 25000}))
        self.assertEqual(preview["api_payload"]["face_limit"], 25000)
        self.assertIn("FBX", preview["warnings"][0])
        for params in ({"quad": True, "face_limit": 25001}, {"face_limit": "1000"}, {"quad": "false"}, {"face_limit": 47}, {"texture": False}, {"geometry_quality": "detailed"}):
            with self.subTest(params=params), self.assertRaises(InvalidPlan):
                self.provider.preview(self.plan(params=params))
        self.provider.preview(self.plan(params={"texture": False, "pbr": False}))

    def test_image_stages_have_image_models(self):
        self.assertEqual(self.provider.preview(self.concept())["api_payload"]["model"], "seedream_v4")
        subject = {"kind": "subject", "prompt": "Isolate the kiosk", "inputs": [{"files": [self.file("subject.png")]}]}
        self.assertEqual(self.provider.preview(subject)["api_payload"]["model"], "seedream_v5")
        subject["params"] = {"model": "P2-20260801"}
        with self.assertRaises(InvalidPlan):
            self.provider.preview(subject)

    def test_image_prompts_longer_than_1024_are_preserved_verbatim(self):
        prompt = "Keep every visible newsstand detail and proportion.\n" * 60 + "末尾必须完整保留。"
        self.assertGreater(len(prompt), 1024)
        for kind in ("concept", "subject", "view"):
            with self.subTest(kind=kind):
                plan = {"kind": kind, "prompt": prompt, "inputs": []}
                if kind != "concept":
                    plan["inputs"] = [{"files": [self.file("source.png")]}]
                self.assertEqual(self.provider.preview(plan)["api_payload"]["prompt"], prompt)
                self.assertEqual(self.provider._compile(plan)["prompt"], prompt)

    def test_prompt_type_check_is_retained(self):
        with self.assertRaisesRegex(InvalidPlan, "must be text"):
            self.provider.preview({**self.concept(), "prompt": ["not", "text"]})

    def test_image_edit_preserves_multiple_references_and_order(self):
        plan = {"kind": "view", "prompt": "Use subject image[1] and proportions image[2] for the right view",
                "inputs": [{"role": "subject", "files": [self.file("a.png")]},
                           {"role": "front", "files": [self.file("b.png")]}]}
        preview = self.provider.preview(plan)
        self.assertNotIn("input", preview["api_payload"])
        self.assertEqual(len(preview["api_payload"]["inputs"]), 2)
        self.assertIn("a.png", preview["api_payload"]["inputs"][0])
        self.assertIn("b.png", preview["api_payload"]["inputs"][1])
        self.assertTrue(preview["warnings"])

    def test_image_edit_respects_provider_reference_limits(self):
        files = [self.file(f"reference_{index}.png") for index in range(5)]
        plan = {"kind": "subject", "prompt": "Combine references", "inputs": [{"files": files}]}
        with self.assertRaisesRegex(InvalidPlan, "at most 4"):
            self.provider.preview(plan)
        plan["params"] = {"model": "banana2"}
        self.assertEqual(len(self.provider.preview(plan)["api_payload"]["inputs"]), 5)

    def test_model_package_keeps_model_textures_and_archive(self):
        outputs = self.provider._outputs({"model_url": "https://cdn.example/model.fbx",
                                          "textures": {"albedo_url": "https://cdn.example/albedo.png"},
                                          "package_url": "https://cdn.example/model.zip"})
        self.assertEqual([item["role"] for item in outputs], ["model", "texture", "archive"])
        status = {"status": "success", "outputs": outputs}
        with patch("asset_pipeline.tripo.urlopen", side_effect=lambda *args, **kwargs: DownloadResponse(b"artifact")) as download:
            files = self.provider.download(status, self.project / "bundle")
        self.assertEqual(download.call_count, 3)
        self.assertEqual(len(files), 3)

    def test_multiview_endpoint_rejects_ignored_parameters(self):
        plan = {"kind": "multiview", "inputs": [{"files": [self.file("source.png")]}]}
        self.assertEqual(set(self.provider.preview(plan)["api_payload"]), {"input"})
        for update in ({"prompt": "left"}, {"params": {"model": "P2-20260801"}}, {"params": {"face_limit": 1000}}):
            with self.subTest(update=update), self.assertRaises(InvalidPlan):
                self.provider.preview({**plan, **update})

    def test_paths_cannot_escape_project(self):
        plan = {"kind": "subject", "prompt": "Isolate", "inputs": [{"files": [{"path": "../secret.png", "role": "image"}]}]}
        with self.assertRaises(InvalidPlan):
            self.provider.preview(plan)

    def test_sensitive_and_override_params_rejected(self):
        for key in ("api_key", "token", "authorization", "input", "inputs", "prompt", "profile"):
            with self.subTest(key=key), self.assertRaises(InvalidPlan):
                self.provider.preview({**self.concept(), "params": {key: "hidden-value"}})

    def test_submit_is_one_nonblocking_request_and_returns_id(self):
        done = subprocess.CompletedProcess([], 0, json.dumps({"task_id": "task_confirmed", "type": "text_to_image"}), "")
        with self.mock_helper(), patch("asset_pipeline.tripo.subprocess.run", return_value=done) as run:
            result = self.provider.submit(self.concept())
        self.assertEqual(result["task_id"], "task_confirmed")
        self.assertEqual(run.call_count, 1)
        spec = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(spec["operation"], "submit")
        self.assertEqual(spec["prompt"], self.concept()["prompt"])
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_submit_timeout_never_retries(self):
        with self.mock_helper(), patch("asset_pipeline.tripo.subprocess.run", side_effect=subprocess.TimeoutExpired("tripo", 180)) as run:
            with self.assertRaises(SubmissionUncertain):
                self.provider.submit(self.concept())
        self.assertEqual(run.call_count, 1)

    def test_submit_unknown_result_does_not_leak_cli_output(self):
        done = subprocess.CompletedProcess([], 1, "not-json", "secret-api-key must never leave the child")
        with self.mock_helper(), patch("asset_pipeline.tripo.subprocess.run", return_value=done) as run:
            with self.assertRaises(SubmissionUncertain) as caught:
                self.provider.submit(self.concept())
        self.assertNotIn("secret", str(caught.exception))
        self.assertEqual(run.call_count, 1)

    def test_confirmed_id_survives_later_process_failure(self):
        done = subprocess.CompletedProcess([], 1, '{"task_id":"task_saved"}', "cleanup error")
        with self.mock_helper(), patch("asset_pipeline.tripo.subprocess.run", return_value=done):
            self.assertEqual(self.provider.submit(self.concept())["task_id"], "task_saved")

    def test_unavailable_transport_is_not_submission_uncertainty(self):
        with patch.object(self.provider, "_helper_command", side_effect=ProviderUnavailable("not installed")):
            with self.assertRaises(ProviderUnavailable):
                self.provider.submit(self.concept())

    def test_poll_only_accepts_exact_ids(self):
        for value in ("@last", "@kiosk", "", "task_../../other", "--download"):
            with self.subTest(value=value), patch("asset_pipeline.tripo.subprocess.run") as run:
                with self.assertRaises(InvalidPlan):
                    self.provider.poll(value)
                run.assert_not_called()

    def test_poll_normalizes_nested_outputs_and_credits(self):
        payload = {"task_id": "task_saved", "status": "success", "progress": 100, "credits_consumed": 110,
                   "output": {"front_view_url": "https://cdn.example/front.png?signature=secret", "back_view_url": "https://cdn.example/back.png",
                              "model": {"url": "https://cdn.example/object.glb"}, "rendered_image_url": "https://cdn.example/view.webp"}}
        done = subprocess.CompletedProcess([], 0, json.dumps(payload), "")
        with self.mock_layout(), patch("asset_pipeline.tripo.subprocess.run", return_value=done) as run:
            result = self.provider.poll("task_saved")
        self.assertEqual([out["role"] for out in result["outputs"]], ["front", "back", "model", "preview"])
        self.assertEqual(result["outputs"][0]["filename"], "front.png")
        self.assertEqual(result["credits_consumed"], 110)
        self.assertEqual(run.call_args.args[0][-3:], ["task", "get", "task_saved"])

    def test_poll_rejects_wrong_id_and_redacts_errors(self):
        responses = [subprocess.CompletedProcess([], 1, "", "private-secret"),
                     subprocess.CompletedProcess([], 0, '{"task_id":"task_other","status":"success"}', "")]
        for response in responses:
            with self.subTest(response=response.returncode), self.mock_layout(), patch("asset_pipeline.tripo.subprocess.run", return_value=response):
                with self.assertRaises(TripoError) as caught:
                    self.provider.poll("task_saved")
                self.assertNotIn("private-secret", str(caught.exception))

    def test_download_is_atomic_and_returns_roles_for_store(self):
        status = {"status": "success", "outputs": [{"url": "https://cdn.example/a.png", "role": "front", "filename": "front.png"}]}
        with tempfile.TemporaryDirectory() as directory, patch("asset_pipeline.tripo.urlopen", return_value=DownloadResponse(b"image-data")):
            result = self.provider.download(status, Path(directory))
            self.assertTrue(Path(result[0]["path"]).is_absolute())
            self.assertEqual(Path(result[0]["path"]).read_bytes(), b"image-data")
            self.assertEqual(result[0]["role"], "front")
            self.assertEqual(len(result[0]["sha256"]), 64)
            self.assertFalse(list(Path(directory).glob("*.part")))

    def test_partial_download_never_replaces_valid_output(self):
        folder = self.project / "downloads"
        folder.mkdir()
        (folder / "model.glb").write_bytes(b"previous-valid-version")
        status = {"status": "success", "outputs": [{"url": "https://cdn.example/model.glb", "role": "model", "filename": "model.glb"}]}
        with patch("asset_pipeline.tripo.urlopen", return_value=DownloadResponse(b"partial", content_length=100)):
            with self.assertRaises(TripoError):
                self.provider.download(status, folder)
        self.assertEqual((folder / "model.glb").read_bytes(), b"previous-valid-version")
        self.assertEqual([path.name for path in folder.iterdir()], ["model.glb"])

    def test_download_rejects_path_traversal(self):
        status = {"status": "success", "outputs": [{"url": "https://cdn.example/a", "role": "image", "filename": "../outside.png"}]}
        with self.assertRaises(TripoError), patch("asset_pipeline.tripo.urlopen") as download:
            self.provider.download(status, self.project / "downloads")
            download.assert_not_called()

    def test_available_never_queries_identity(self):
        with self.mock_layout(), patch("asset_pipeline.tripo.subprocess.run") as run:
            status = self.provider.available()
        self.assertTrue(status["available"])
        self.assertIsNone(status["authenticated"])
        run.assert_not_called()

    def test_discovery_honors_explicit_missing_path_without_fallback(self):
        self.assertIsNone(discover_executable("/definitely-missing/tripo"))


class InstalledCliCompatibilityTests(unittest.TestCase):
    """Pure request-builder / mocked-client checks against the installed CLI."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        self.provider = TripoProvider(self.project)
        if not self.provider.available().get("available"):
            self.temp.cleanup()
            self.skipTest("Verified official CLI 0.5.1 is not installed")
        for name in ("front.png", "back.png", "right.png"):
            (self.project / name).write_bytes(b"never uploaded")

    def tearDown(self):
        self.temp.cleanup()

    def test_official_builders_agree_with_all_stage_previews_without_network(self):
        file = {"path": "front.png", "role": "image"}
        plans = [
            {"kind": "concept", "prompt": "A city street", "params": {}},
            {"kind": "subject", "prompt": "Isolate the kiosk", "inputs": [{"files": [file]}]},
            {"kind": "view", "prompt": "Orthographic right side", "inputs": [{"files": [file]}]},
            {"kind": "view", "prompt": "Use image[1] subject and image[2] proportions for a side view",
             "inputs": [{"role": "subject", "files": [file]}, {"role": "front", "files": [{"path": "back.png", "role": "image"}]}]},
            {"kind": "multiview", "inputs": [{"files": [file]}]},
            {"kind": "model", "params": {"model": "P2-20260801", "quad": True, "face_limit": 12000},
             "inputs": [{"files": [{"path": f"{role}.png", "role": role} for role in ("right", "front", "back")]}]},
        ]
        for plan in plans:
            with self.subTest(kind=plan["kind"]):
                spec = self.provider._compile(plan)
                spec["operation"] = "validate"
                result = subprocess.run(self.provider._helper_command(), input=json.dumps(spec), text=True,
                                        capture_output=True, timeout=10, check=False)
                self.assertEqual(result.returncode, 0, "Offline official CLI validation failed")
                data = json.loads(result.stdout)
                self.assertTrue(data["valid"])
                self.assertEqual(data["request"]["payload"], spec["expected_payload"])

    def test_official_client_makes_one_attempt_on_network_failure(self):
        node, root, _ = self.provider._layout()
        script = """
          const { TripoClient } = await import(process.argv[1]);
          const { NetworkError } = await import(process.argv[2]);
          const client = Object.create(TripoClient.prototype);
          client.maxRetries = 0;
          let attempts = 0;
          client.sendOnce = async () => { attempts++; throw new NetworkError('offline fixture'); };
          try { await client.createTask('/v3/generation/text-to-image', { prompt: 'fixture' }); } catch {}
          process.stdout.write(JSON.stringify({ attempts }));
        """
        result = subprocess.run([str(node), "--input-type=module", "-e", script,
                                 (root / "dist/core/client.js").as_uri(), (root / "dist/core/errors.js").as_uri()],
                                capture_output=True, text=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["attempts"], 1)

    def test_transport_failure_metadata_is_allowlisted_without_credentials_or_network(self):
        node, _, _ = self.provider._layout()
        helper = Path(__file__).resolve().parents[1] / "addons/asset_pipeline/backend/asset_pipeline/tripo_transport.mjs"
        fake = self.project / "fake-package"
        core = fake / "dist/core"
        core.mkdir(parents=True)
        (fake / "package.json").write_text(json.dumps({"name": "tripo-cli", "version": "0.5.1", "type": "module"}))
        (core / "task-service.js").write_text("export async function resolveInputValue() { throw Error('uploads forbidden'); }")
        (core / "proxy.js").write_text("export async function installProxyFromEnv() {}")
        (core / "requests.js").write_text("""
          export function buildTextToImage(prompt, params) {
            return {request: {endpoint: '/never-sent', payload: {...params, prompt}}};
          }
          export const buildImageToImage = buildTextToImage;
          export const buildImageToMultiview = buildTextToImage;
          export const buildMultiviewToModel = buildTextToImage;
        """)
        plan = {"kind": "concept", "prompt": "local fixture"}
        spec = {**self.provider._compile(plan), "operation": "submit"}
        request_id = "0123456789abcdef0123456789abcdef"
        for metadata, expected in (
            ({"httpStatus": 429, "apiCode": 2000, "requestId": request_id},
             {"failure_phase": "rejected", "http_status": 429, "api_code": 2000, "request_id": request_id}),
            ({"httpStatus": 400, "apiCode": 2009, "requestId": "req_fixture001"},
             {"failure_phase": "rejected", "http_status": 400, "api_code": 2009, "request_id": "req_fixture001"}),
            ({"httpStatus": "401 secret", "apiCode": "2000 secret", "requestId": "tcli_secret001"},
             {"failure_phase": "uncertain"}),
            ({"httpStatus": 999, "apiCode": -5, "requestId": "req_fixture001\nsecret"},
             {"failure_phase": "uncertain"}),
            ({"httpStatus": 400, "apiCode": 1004, "requestId": "req_fixture001\n"},
             {"failure_phase": "rejected", "http_status": 400, "api_code": 1004}),
        ):
            with self.subTest(metadata=metadata):
                (core / "client.js").write_text("""
                  export class TripoClient {
                    constructor(options) { this.maxRetries = options.maxRetries; }
                    sendOnce() { throw Error('network forbidden'); }
                    async createTask() { throw Object.assign(new Error('secret remote message'), %s); }
                  }
                """ % json.dumps({**metadata, "suggestion": "secret", "headers": {"Authorization": "secret"}}))
                result = subprocess.run([str(node), str(helper), str(fake)], input=json.dumps(spec),
                    capture_output=True, text=True, timeout=10, check=False)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(json.loads(result.stdout), expected)
                self.assertNotIn("secret", result.stdout + result.stderr)

    def test_official_image_builders_preserve_long_prompts_without_network(self):
        prompt = "Preserve the reference geometry, signs, roof and surface materials.\n" * 50
        for kind in ("concept", "subject", "view"):
            with self.subTest(kind=kind):
                plan = {"kind": kind, "prompt": prompt}
                if kind != "concept":
                    plan["inputs"] = [{"files": [{"path": "front.png", "role": "image"}]}]
                spec = self.provider._compile(plan)
                spec["operation"] = "validate"
                result = subprocess.run(self.provider._helper_command(), input=json.dumps(spec), text=True,
                                        capture_output=True, timeout=10, check=False)
                self.assertEqual(result.returncode, 0, "Offline official image builder rejected the long prompt")
                self.assertEqual(json.loads(result.stdout)["request"]["payload"]["prompt"], prompt)


if __name__ == "__main__":
    unittest.main()
