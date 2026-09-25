#!/usr/bin/env python3
"""Loopback service / diagnostic CLI / MCP stdio. Python 3.9+, standard library."""
import argparse
import hmac
import json
import logging
import os
from pathlib import Path
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_REQUEST = 2 * 1024 * 1024
CONNECTION = ".godot/asset_pipeline_connection.json"


def rpc(project, method, params=None):
    connection = json.loads((Path(project) / CONNECTION).read_text())
    port = int(connection["port"])
    if port < 1 or port > 65535:
        raise ValueError("无效后台端口")
    request = urllib.request.Request("http://127.0.0.1:%d/rpc" % port,
        data=json.dumps({"method": method, "params": params or {}}).encode(),
        headers={"Authorization": "Bearer " + connection["token"], "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)
    if not result.get("ok"):
        raise ValueError(result.get("error", "后台返回错误"))
    return result["result"]


def serve(project):
    from asset_pipeline.service import PipelineService
    project = Path(project).resolve()
    if not (project / "project.godot").is_file():
        raise ValueError("请选择包含 project.godot 的项目文件夹")
    runtime = project / ".godot" / "asset_pipeline"
    runtime.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=str(runtime / "server.log"), level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s")
    # Bind under an atomic per-project startup lock; do not launch two schedulers.
    lock = runtime / "startup.lock"
    try:
        lock.mkdir()
    except FileExistsError:
        if time.time() - lock.stat().st_mtime > 30:
            lock.rmdir()
            lock.mkdir()
        else:
            return
    server = None
    token = secrets.token_urlsafe(32)
    connection_path = project / CONNECTION
    try:
        try:
            rpc(project, "ping")
            return
        except (OSError, ValueError, KeyError):
            pass
        service = PipelineService(project)

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send_json(self, status, value):
                data = json.dumps(value, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                if self.path != "/rpc":
                    self.send_json(404, {"ok": False, "error": "not found"})
                    return
                # This endpoint is not a browser API. Avoid cross-origin requests.
                if self.headers.get("Origin"):
                    self.send_json(403, {"ok": False, "error": "Browser origins are not accepted"})
                    return
                if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                    self.send_json(401, {"ok": False, "error": "本地连接令牌无效，请重新连接"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= MAX_REQUEST:
                        raise ValueError("请求大小超出限制")
                    request = json.loads(self.rfile.read(length))
                    method = request["method"]
                    if method == "ping":
                        result = {"version": "0.4.0", "pid": os.getpid(), "project": str(project)}
                    elif method == "shutdown":
                        service.stopping.set()
                        threading.Thread(target=server.shutdown, daemon=True).start()
                        result = {"stopping": True}
                    else:
                        result = service.dispatch(method, request.get("params", {}))
                    self.send_json(200, {"ok": True, "result": result})
                except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
                    self.send_json(200, {"ok": False, "error": str(error)[:2000]})
                except Exception:
                    logging.exception("Unhandled pipeline operation")
                    self.send_json(200, {"ok": False, "error": "后台操作失败，详见 .godot/asset_pipeline/server.log"})

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        data = {"port": server.server_port, "token": token, "pid": os.getpid()}
        temp = connection_path.with_suffix(".tmp")
        fd = os.open(str(temp), os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump(data, output)
        os.chmod(temp, 0o600)
        os.replace(temp, connection_path)
        logging.info("Asset Pipeline ready, version 0.4.0")
    finally:
        lock.rmdir()
    if server:
        try:
            server.serve_forever(poll_interval=0.25)
        finally:
            service.stopping.set()
            server.server_close()
            try:
                if json.loads(connection_path.read_text()).get("token") == token:
                    connection_path.unlink()
            except (OSError, ValueError):
                pass


def _schema(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


STRING = {"type": "string"}
OBJECT = {"type": "object"}
TOOL_DEFS = [
    ("asset_pipeline_snapshot", "snapshot", "Read all nodes, immutable versions, jobs and scene links.", _schema({})),
    ("asset_pipeline_provider_status", "provider.status", "Check local Tripo transport without generation or balance query.", _schema({})),
    ("asset_pipeline_create_node", "node.create", "Create a production stage without running it. node: label, kind, prompt, params, inputs[{node_id,role}], position.", _schema({"node": OBJECT}, ["node"])),
    ("asset_pipeline_update_node", "node.update", "Edit a stage. Affected outputs become stale but no remote job runs.", _schema({"node_id": STRING, "patch": OBJECT}, ["node_id", "patch"])),
    ("asset_pipeline_plan", "node.plan", "Preview exact selected-stage generation inputs and affected descendants. No upload, submission or charges.", _schema({"node_id": STRING}, ["node_id"])),
    ("asset_pipeline_run", "node.run", "Submit ONE selected stage using Tripo API credits. No downstream auto-runs or automatic resubmissions. Only within user's authorized generation scope.", _schema({"node_id": STRING}, ["node_id"])),
    ("asset_pipeline_import", "artifact.import", "Copy a local output into versioned library directly; no visual quality gate.", _schema({"node_id": STRING, "path": STRING, "role": STRING}, ["node_id", "path"])),
    ("asset_pipeline_select_version", "version.select", "Switch active library version. Scene instances remain pinned until explicit update.", _schema({"node_id": STRING, "version_id": STRING}, ["node_id", "version_id"])),
    ("asset_pipeline_resume", "job.resume", "Resume polling/downloading the same saved Tripo task ID. Does not generate again.", _schema({"job_id": STRING}, ["job_id"])),
    ("asset_pipeline_pause", "job.pause", "Pause local polling; this DOES NOT cancel remote generation or billing.", _schema({"job_id": STRING}, ["job_id"])),
    ("asset_pipeline_attach_task", "job.attach", "Associate a known Tripo task ID after ambiguous submission; no automatic resubmit.", _schema({"job_id": STRING, "task_id": STRING}, ["job_id", "task_id"])),
    ("asset_pipeline_template", "template", "Create concept > subject > front/right/back > model stages without spending credits.", _schema({"name": STRING})),
    ("asset_pipeline_record_instance", "instance.record", "Record asset/version to scene-node provenance after successful editor placement.", _schema({"instance": OBJECT}, ["instance"])),
    ("asset_pipeline_create_batch", "batch.create", "Submit an asset brief once and prebuild all subject, view and model nodes. No paid generation. spec contains reference_node_id and assets.", _schema({"spec": OBJECT}, ["spec"])),
    ("asset_pipeline_plan_batch", "batch.plan", "Inspect the automatically expanded batch, reused outputs and remaining work before generation. No uploads or charges.", _schema({"batch_id": STRING}, ["batch_id"])),
    ("asset_pipeline_run_batch", "batch.run", "Start the entire authorized batch. The backend runs dependency-ready image and model stages and synchronizes outputs to Godot without intermediate agent actions. Uses Tripo API credits.", _schema({"batch_id": STRING}, ["batch_id"])),
    ("asset_pipeline_pause_batch", "batch.pause", "Pause this batch's local scheduling and polling. Does not cancel remote tasks or billing.", _schema({"batch_id": STRING}, ["batch_id"])),
    ("asset_pipeline_resume_batch", "batch.resume", "Continue a paused batch, reuse saved remote task IDs, and schedule remaining unsubmitted stages. Does not blindly retry failed or uncertain submissions.", _schema({"batch_id": STRING}, ["batch_id"])),
    ("asset_pipeline_retry_rejected_batch", "batch.retry_rejected", "Prepare an explicitly requested recovery of confirmed HTTP 400/429 rejected submissions without remote task IDs. HTTP 400 requires corrected inputs or recipe in a new recovery batch. Preserves historical jobs; does not submit until batch.run.", _schema({"batch_id": STRING, "node_ids": {"type": "array", "items": STRING, "minItems": 1}, "reason": STRING}, ["batch_id", "node_ids", "reason"])),
]


def mcp(project):
    """MCP newline-delimited JSON-RPC stdio. No stdout logs and no server spawning."""
    tools = {definition[0]: definition for definition in TOOL_DEFS}
    for line in sys.stdin:
        request_id = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("Request must be an object")
            request_id = request.get("id")
            if "id" not in request:
                continue
            method = request.get("method")
            if method == "initialize":
                result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                          "serverInfo": {"name": "godot-asset-pipeline", "version": "0.2.0"}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [{"name": d[0], "description": d[2], "inputSchema": d[3]} for d in TOOL_DEFS]}
            elif method == "tools/call":
                params = request.get("params", {})
                if params.get("name") not in tools:
                    raise ValueError("Unknown tool")
                definition = tools[params["name"]]
                arguments = params.get("arguments", {})
                if not isinstance(arguments, dict) or set(arguments) - set(definition[3]["properties"]):
                    raise ValueError("Unexpected tool arguments")
                if set(definition[3]["required"]) - set(arguments):
                    raise ValueError("Missing required tool arguments")
                try:
                    value = rpc(project, definition[1], arguments)
                    result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}]}
                except Exception as error:
                    result = {"isError": True, "content": [{"type": "text", "text": str(error)[:2000]}]}
            else:
                sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request["id"],
                    "error": {"code": -32601, "message": "Method not found"}}) + "\n")
                sys.stdout.flush()
                continue
            response = {"jsonrpc": "2.0", "id": request["id"], "result": result}
        except Exception as error:
            response = {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602 if request_id is not None else -32600, "message": str(error)}}
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--mcp", action="store_true")
    parser.add_argument("--call")
    parameters = parser.add_mutually_exclusive_group()
    parameters.add_argument("--params", default=None)
    parameters.add_argument("--params-file", type=Path, help="Read RPC JSON parameters from a file without shell interpolation")
    args = parser.parse_args()
    if args.mcp:
        mcp(args.project)
    elif args.call:
        raw_params = args.params_file.read_text(encoding="utf-8") if args.params_file else (args.params or "{}")
        print(json.dumps(rpc(args.project, args.call, json.loads(raw_params)), ensure_ascii=False, indent=2))
    else:
        serve(args.project)
