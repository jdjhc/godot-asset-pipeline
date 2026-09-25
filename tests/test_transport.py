"""Real local-server/stdio integration. No Tripo account or network is used.

A deliberately unusable Tripo executable is injected, so a transport regression
cannot spend API credits. HTTP runs on an isolated project and is shut down by
its own authenticated connection in tearDownClass.
"""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "addons/asset_pipeline/backend/server.py"


class TransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="asset-pipeline-transport-")
        cls.project = Path(cls.temporary.name)
        (cls.project / "project.godot").write_text('config_version=5\n[application]\nconfig/name="Transport test"\n')
        cls.source = cls.project / "source.png"
        cls.source.write_bytes(b"local provenance artifact; no image quality gate")
        cls.env = os.environ.copy()
        cls.fake_cli = cls.project / "fake-tripo"
        cls.fake_cli.write_text("#!/bin/sh\nexit 99\n")
        cls.fake_cli.chmod(0o700)
        cls.env["TRIPO_CLI"] = str(cls.fake_cli)
        cls.process = subprocess.Popen([sys.executable, str(SERVER), "--project", str(cls.project)],
                                       env=cls.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 10
        connection_file = cls.project / ".godot/asset_pipeline_connection.json"
        while time.monotonic() < deadline:
            if connection_file.is_file():
                try:
                    cls.connection = json.loads(connection_file.read_text())
                    status, response = cls.http("ping")
                    if status == 200 and response.get("ok"):
                        break
                except (OSError, ValueError):
                    pass
            if cls.process.poll() is not None:
                _, stderr = cls.process.communicate()
                cls.temporary.cleanup()
                raise RuntimeError("Local backend exited during startup: " + stderr)
            time.sleep(0.02)
        else:
            cls.process.terminate()
            cls.process.communicate(timeout=5)
            cls.temporary.cleanup()
            raise RuntimeError("Local backend did not become ready")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.http("shutdown")
            cls.process.communicate(timeout=5)
        except Exception:
            cls.process.terminate()
            try:
                cls.process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                cls.process.kill()
                cls.process.communicate(timeout=5)
        finally:
            cls.temporary.cleanup()

    @classmethod
    def http(cls, method, params=None, auth=True, origin=None, path="/rpc", token=None):
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = "Bearer " + (token if token is not None else cls.connection["token"])
        if origin is not None:
            headers["Origin"] = origin
        request = urllib.request.Request("http://127.0.0.1:%d%s" % (cls.connection["port"], path),
            data=json.dumps({"method": method, "params": params or {}}).encode(), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.load(error)

    def call(self, method, params=None):
        status, response = self.http(method, params)
        self.assertEqual(status, 200)
        self.assertTrue(response.get("ok"), response.get("error", "RPC failed"))
        return response["result"]

    def mcp(self, requests):
        payload = "".join(json.dumps(request) + "\n" for request in requests)
        process = subprocess.run([sys.executable, str(SERVER), "--project", str(self.project), "--mcp"],
                                 input=payload, text=True, capture_output=True, timeout=10, env=self.env)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(process.stderr, "")
        return [json.loads(line) for line in process.stdout.splitlines() if line.strip()]

    def test_authentication_and_browser_origin_cannot_submit(self):
        self.call("node.create", {"node": {"id": "auth_concept", "kind": "concept", "label": "Auth concept", "prompt": "A kiosk"}})
        before = self.call("snapshot")
        for auth, origin, token, expected in (
            (False, None, None, 401),
            (True, None, "incorrect", 401),
            (True, "https://example.com", None, 403),
            (True, "null", None, 403),
        ):
            status, result = self.http("node.run", {"node_id": "auth_concept"}, auth=auth, origin=origin, token=token)
            self.assertEqual(status, expected)
            self.assertFalse(result["ok"])
        after = self.call("snapshot")
        self.assertEqual(after["revision"], before["revision"])
        self.assertEqual(after["jobs"], before["jobs"])

    def test_connection_credentials_stay_local_and_private(self):
        connection = self.project / ".godot/asset_pipeline_connection.json"
        self.assertEqual(stat.S_IMODE(connection.stat().st_mode), 0o600)
        self.assertEqual(self.call("ping")["pid"], self.process.pid)
        snapshot = self.call("snapshot")
        self.assertNotIn(self.connection["token"], json.dumps(snapshot))
        status, response = self.http("ping", path="/not-an-endpoint")
        self.assertEqual(status, 404)
        self.assertFalse(response["ok"])

    def test_preview_is_read_only_and_does_not_submit(self):
        node = self.call("node.create", {"node": {"id": "preview_concept", "kind": "concept", "label": "Preview", "prompt": "A kiosk"}})
        before = self.call("snapshot")
        plan = self.call("node.plan", {"node_id": node["id"]})
        self.assertEqual(plan["will_generate"], [node["id"]])
        self.assertIn("fingerprint", plan)
        self.assertEqual(self.call("snapshot"), before)
        self.assertFalse(self.call("provider.status")["available"])

    def test_http_create_import_and_persist_provenance(self):
        root = self.call("node.create", {"node": {"id": "http_root", "kind": "reference", "label": "Imported concept"}})
        root_version = self.call("artifact.import", {"node_id": root["id"], "path": str(self.source), "role": "image"})
        child = self.call("node.create", {"node": {"id": "http_subject", "kind": "subject", "label": "Subject", "prompt": "isolate kiosk",
                                                   "inputs": [{"node_id": root["id"], "role": "reference"}]}})
        version = self.call("artifact.import", {"node_id": child["id"], "path": "res://source.png", "role": "image"})
        self.assertEqual(version["input_versions"], {root["id"]: root_version["id"]})
        self.assertEqual(version["prompt"], "isolate kiosk")
        self.assertEqual((self.project / version["files"][0]["path"]).read_bytes(), self.source.read_bytes())
        self.call("instance.record", {"instance": {"id": "http_instance", "asset_node_id": child["id"], "version_id": version["id"],
                                                   "scene": "res://street.tscn", "node_path": "Street/Kiosk"}})
        # A separately launched diagnostic client reads the same durable project data.
        client = subprocess.run([sys.executable, str(SERVER), "--project", str(self.project), "--call", "snapshot"],
                                capture_output=True, text=True, timeout=10, env=self.env)
        self.assertEqual(client.returncode, 0, client.stderr)
        snapshot = json.loads(client.stdout)
        persisted = next(node for node in snapshot["nodes"] if node["id"] == child["id"])
        self.assertEqual(persisted["current_version"], version["id"])
        self.assertEqual(persisted["versions"][0]["input_versions"], {root["id"]: root_version["id"]})
        self.assertTrue(any(instance["id"] == "http_instance" for instance in snapshot["instances"]))

    def test_mcp_initialize_tools_and_snapshot(self):
        responses = self.mcp([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "unittest", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "asset_pipeline_snapshot", "arguments": {}}},
        ])
        self.assertEqual([response["id"] for response in responses], [1, 2, 3])
        self.assertEqual(responses[0]["result"]["serverInfo"]["name"], "godot-asset-pipeline")
        names = {tool["name"] for tool in responses[1]["result"]["tools"]}
        self.assertIn("asset_pipeline_import", names)
        self.assertIn("asset_pipeline_plan", names)
        self.assertTrue({"asset_pipeline_create_batch", "asset_pipeline_plan_batch", "asset_pipeline_run_batch",
                         "asset_pipeline_pause_batch", "asset_pipeline_resume_batch"}.issubset(names))
        result = responses[2]["result"]
        self.assertFalse(result.get("isError", False))
        snapshot = json.loads(result["content"][0]["text"])
        self.assertIn("nodes", snapshot)
        self.assertIn("jobs", snapshot)
        self.assertIn("instances", snapshot)

    def test_mcp_create_import_and_plan_roundtrip(self):
        responses = self.mcp([
            {"jsonrpc": "2.0", "id": "create", "method": "tools/call", "params": {"name": "asset_pipeline_create_node", "arguments": {
                "node": {"id": "mcp_ref", "kind": "reference", "label": "MCP imported", "prompt": "Recorded prompt"}}}},
            {"jsonrpc": "2.0", "id": "import", "method": "tools/call", "params": {"name": "asset_pipeline_import", "arguments": {
                "node_id": "mcp_ref", "path": str(self.source), "role": "image"}}},
            {"jsonrpc": "2.0", "id": "plan", "method": "tools/call", "params": {"name": "asset_pipeline_plan", "arguments": {"node_id": "mcp_ref"}}},
        ])
        self.assertEqual([response["id"] for response in responses], ["create", "import", "plan"])
        for response in responses:
            self.assertFalse(response["result"].get("isError", False))
        imported = json.loads(responses[1]["result"]["content"][0]["text"])
        self.assertEqual(imported["prompt"], "Recorded prompt")
        self.assertTrue(imported["promoted"])
        plan = json.loads(responses[2]["result"]["content"][0]["text"])
        self.assertEqual(plan["node_id"], "mcp_ref")
        self.assertEqual(plan["missing_inputs"], [])

    def test_mcp_invalid_arguments_preserve_request_correlation(self):
        responses = self.mcp([
            {"jsonrpc": "2.0", "id": 71, "method": "tools/call", "params": {"name": "not_a_real_tool", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 72, "method": "tools/call", "params": {"name": "asset_pipeline_snapshot", "arguments": {"unexpected": True}}},
            {"jsonrpc": "2.0", "id": 73, "method": "tools/call", "params": {"name": "asset_pipeline_update_node", "arguments": {}}},
        ])
        self.assertEqual([response["id"] for response in responses], [71, 72, 73])
        for response in responses:
            self.assertTrue("error" in response or response.get("result", {}).get("isError"))

    def test_starting_second_server_reuses_owned_process(self):
        second = subprocess.run([sys.executable, str(SERVER), "--project", str(self.project)],
                                capture_output=True, text=True, timeout=10, env=self.env)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.call("ping")["pid"], self.process.pid)

    def test_diagnostic_params_file_preserves_literal_prompt(self):
        prompt = "A storefront\nKeep literal $(not_a_command) and `not_a_command` in its sign."
        parameters = self.project / "node-parameters.json"
        parameters.write_text(json.dumps({"node": {"id": "file_params", "kind": "concept", "label": "File input",
                                                   "prompt": prompt}}), encoding="utf-8")
        client = subprocess.run([sys.executable, str(SERVER), "--project", str(self.project), "--call", "node.create",
                                 "--params-file", str(parameters)], capture_output=True, text=True, timeout=10, env=self.env)
        self.assertEqual(client.returncode, 0, client.stderr)
        self.assertEqual(json.loads(client.stdout)["prompt"], prompt)
        self.assertEqual(self.call("snapshot")["jobs"], [])


if __name__ == "__main__":
    unittest.main()
