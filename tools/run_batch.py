#!/usr/bin/env python3
"""Submit one asset brief to the project backend and optionally watch the batch.

The backend owns expansion, scheduling, downloads and persistence. This client
does not generate images itself or ask an agent to advance individual stages.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--spec", type=Path, help="One asset-brief JSON object, not a shell-escaped RPC string")
    target.add_argument("--batch", help="An existing batch ID")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--run", action="store_true", help="Start all authorized remaining stages; consumes Tripo API credits")
    action.add_argument("--resume", action="store_true", help="Continue a saved batch without recreating completed work")
    parser.add_argument("--wait", action="store_true", help="Report changes until the backend completes or needs attention")
    args = parser.parse_args()
    project = args.project.expanduser().resolve()
    server = project / "addons/asset_pipeline/backend/server.py"
    spec = importlib.util.spec_from_file_location("asset_pipeline_rpc", server)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rpc = lambda method, params=None: module.rpc(project, method, params or {})
    snapshot = rpc("snapshot")
    if snapshot.get("project") and Path(snapshot["project"]).resolve() != project:
        raise ValueError("Backend belongs to a different Godot project")
    if args.spec:
        brief = json.loads(args.spec.read_text(encoding="utf-8"))
        if not isinstance(brief, dict):
            raise ValueError("Asset brief must be a JSON object")
        batch = rpc("batch.create", {"spec": brief})
        batch_id = batch["id"]
    else:
        batch_id = args.batch
    plan = rpc("batch.plan", {"batch_id": batch_id})
    print(json.dumps({"batch_id": batch_id, "plan": plan}, ensure_ascii=False), flush=True)
    if args.run or args.resume:
        result = rpc("batch.resume" if args.resume else "batch.run", {"batch_id": batch_id})
        print(json.dumps({"batch_id": batch_id, "started": result}, ensure_ascii=False), flush=True)
    if not args.wait:
        return 0
    previous = None
    while True:
        snapshot = rpc("snapshot")
        batch = next((item for item in snapshot.get("batches", []) if item["id"] == batch_id), None)
        if batch is None:
            raise ValueError("Batch disappeared from the project snapshot")
        # Emit just batch progress; never dump credentials, full prompts or URLs.
        status = {key: batch.get(key) for key in (
            "id", "label", "status", "progress", "completed_nodes", "total_nodes",
            "active_node_ids", "blocked_node_ids", "error") if key in batch}
        encoded = json.dumps(status, ensure_ascii=False, sort_keys=True)
        if encoded != previous:
            print(encoded, flush=True)
            previous = encoded
        if batch.get("status") in {"completed", "succeeded"}:
            return 0
        if batch.get("status") in {"failed", "blocked", "paused", "interrupted", "needs_attention", "partial_failure", "planned"}:
            return 2
        time.sleep(2)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
