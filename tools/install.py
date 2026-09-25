#!/usr/bin/env python3
"""Copy this editor addon into a Godot project; preserve unrelated addons/settings."""
import argparse
from pathlib import Path
import shutil
import sys
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    project = Path(args.project).expanduser().resolve()
    if not (project / "project.godot").is_file():
        raise SystemExit("Not a Godot project: " + str(project))
    source = ROOT / "addons/asset_pipeline"
    destination = project / "addons/asset_pipeline"
    if source.resolve() != destination.resolve():
        shutil.copytree(source, destination, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
    # Refresh an idle persistent daemon after copying new code. Never interrupt
    # generation or resubmit any provider task as part of an installation.
    sys.path.insert(0, str(destination / "backend"))
    from server import rpc
    try:
        rpc(project, "ping")
        snapshot = rpc(project, "snapshot")
        busy = any(j.get("status") in {"queued", "submitting", "running", "downloading", "pausing"}
                   for j in snapshot.get("jobs", [])) or any(
                   b.get("status") == "running" for b in snapshot.get("batches", []))
        if busy:
            print("Backend not restarted: generation is active. Run installer again when idle.")
        else:
            rpc(project, "shutdown")
            for _ in range(60):
                if not (project / ".godot/asset_pipeline_connection.json").exists():
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError("Backend did not stop; retry installation when it has stopped.")
            subprocess.Popen([sys.executable, str(destination / "backend/server.py"), "--project", str(project)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            print("Idle backend restarted with updated code.")
    except (OSError, ValueError, KeyError):
        print("No reachable backend; Godot will start it on connection.")
    print("Installed: " + str(destination))
    print("Godot: Project Settings > Plugins > Asset Pipeline > Enable")
