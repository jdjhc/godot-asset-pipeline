"""Persistent asset provenance, version history, and bounded regeneration plans.

The graph describes content dependencies, not an agent reasoning workflow.  Nothing
in this module submits jobs or regenerates descendants.  SQLite transactions and
input-version snapshots keep concurrent editor and worker operations consistent.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("Value must contain finite, JSON-compatible data") from exc


def _clone(value: Any) -> Any:
    return json.loads(_json(value))


def _identifier(value: Any, what: str = "ID") -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", value):
        raise ValueError(f"{what} must use 1–96 letters, digits, underscores, or hyphens")
    return value


class Store:
    """Project-local, restart-safe store. Each operation uses its own connection."""

    _locks: Dict[str, threading.RLock] = {}
    _locks_guard = threading.Lock()

    def __init__(self, project: str):
        self.project = Path(project).expanduser().resolve()
        if not self.project.is_dir():
            raise ValueError("Project directory does not exist")
        state = self._safe_output_dir(Path(".asset_pipeline"))
        self.database = state / "state.sqlite3"
        self.database_path = self.database
        if self.database.is_symlink():
            raise ValueError("The project database must not be a symbolic link")
        with self._locks_guard:
            self._lock = self._locks.setdefault(str(self.database), threading.RLock())
        with self._connection(write=True) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
                INSERT OR IGNORE INTO settings(key,value) VALUES ('revision',0);
                CREATE TABLE IF NOT EXISTS nodes (
                    id TEXT PRIMARY KEY, data TEXT NOT NULL, generation_revision INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS versions (
                    id TEXT PRIMARY KEY, node_id TEXT NOT NULL REFERENCES nodes(id), data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, node_id TEXT NOT NULL REFERENCES nodes(id), data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS instances (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS batches (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS canvases (id TEXT PRIMARY KEY, name TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS canvas_nodes (
                    canvas_id TEXT NOT NULL REFERENCES canvases(id),
                    node_id TEXT NOT NULL REFERENCES nodes(id), position TEXT NOT NULL,
                    PRIMARY KEY(canvas_id,node_id)
                );
                CREATE INDEX IF NOT EXISTS versions_by_node ON versions(node_id);
                CREATE INDEX IF NOT EXISTS jobs_by_node ON jobs(node_id);
            """)

        with self._connection(write=True) as db:
            if not db.execute("SELECT 1 FROM settings WHERE key='canvas_migration'").fetchone():
                db.execute("INSERT INTO canvases VALUES ('default','默认画布')")
                for row in db.execute('SELECT id,data FROM nodes').fetchall():
                    db.execute('INSERT INTO canvas_nodes VALUES (?,?,?)',('default',row['id'],_json(json.loads(row['data']).get('position',[0,0]))))
                db.execute("INSERT INTO settings VALUES ('canvas_migration',1)")
            db.execute("""CREATE TRIGGER IF NOT EXISTS canvas_new_node AFTER INSERT ON nodes BEGIN
                INSERT INTO canvas_nodes(canvas_id,node_id,position)
                SELECT id,NEW.id,json_extract(NEW.data,'$.position') FROM canvases ORDER BY rowid LIMIT 1;
            END""")

    @contextlib.contextmanager
    def _connection(self, write: bool = False):
        with self._lock:
            db = sqlite3.connect(str(self.database), timeout=30)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    def _safe_output_dir(self, relative: Path) -> Path:
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Managed output paths must remain inside the project")
        target = self.project
        for part in relative.parts:
            target = target / part
            if target.is_symlink():
                raise ValueError("Managed output directories must not contain symbolic links")
            target.mkdir(exist_ok=True)
            if not target.is_dir():
                raise ValueError("Managed output path is not a directory")
        if not target.resolve().is_relative_to(self.project):
            raise ValueError("Managed output path escaped the project")
        return target

    def _source_path(self, raw: Any) -> Path:
        if not isinstance(raw, (str, os.PathLike)) or not str(raw):
            raise ValueError("Every artifact needs a file path")
        raw = str(raw)
        local = raw[6:] if raw.startswith("res://") else raw
        source = Path(local).expanduser()
        if not source.is_absolute():
            if ".." in source.parts:
                raise ValueError("Relative artifact paths must not contain '..'")
            source = self.project / source
            try:
                source.resolve().relative_to(self.project)
            except ValueError as exc:
                raise ValueError("Relative artifact path escaped the project through a symbolic link") from exc
        if source.is_symlink():
            raise ValueError("Artifact source must be a regular file, not a symbolic link")
        if not source.is_file():
            raise ValueError(f"Artifact file does not exist: {raw}")
        return source.resolve()

    def _bump(self, db: sqlite3.Connection) -> None:
        db.execute("UPDATE settings SET value=value+1 WHERE key='revision'")

    def _all(self, db: sqlite3.Connection) -> Dict[str, dict]:
        return {row["id"]: json.loads(row["data"]) for row in db.execute("SELECT id,data FROM nodes ORDER BY rowid")}

    def _node(self, db: sqlite3.Connection, node_id: str) -> dict:
        row = db.execute("SELECT data FROM nodes WHERE id=?", (node_id,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown node: {node_id}")
        return json.loads(row["data"])

    def _save_node(self, db: sqlite3.Connection, node: dict, generation_change: bool = False):
        db.execute("UPDATE nodes SET data=?,generation_revision=generation_revision+? WHERE id=?",
                   (_json(node), int(generation_change), node["id"]))

    def _expanded(self, db: sqlite3.Connection, node: dict) -> dict:
        node = _clone(node)
        node["versions"] = [json.loads(row["data"]) for row in db.execute(
            "SELECT data FROM versions WHERE node_id=? ORDER BY rowid", (node["id"],))]
        latest = db.execute("SELECT data FROM jobs WHERE node_id=? ORDER BY rowid DESC LIMIT 1", (node["id"],)).fetchone()
        node["state"] = "stale" if node["stale"] else ("ready" if node["current_version"] else "empty")
        if latest:
            job = json.loads(latest["data"])
            node["latest_job_id"] = job["id"]
            if job.get("status") in {"planned", "queued", "running", "submitting", "submitted", "polling", "downloading", "failed", "submission_unknown", "submission_uncertain", "paused", "pausing", "interrupted"}:
                node["state"] = job["status"]
        return node

    def _validate_inputs(self, db: sqlite3.Connection, node_id: str, inputs: Any) -> list:
        if not isinstance(inputs, list):
            raise ValueError("Node inputs must be a list of {node_id, role} objects")
        clean = []
        seen = set()
        for edge in inputs:
            if not isinstance(edge, dict):
                raise ValueError("Each input must contain node_id and role")
            source = _identifier(edge.get("node_id"), "Input node ID")
            role = edge.get("role", "input")
            if not isinstance(role, str) or not role.strip() or len(role) > 100:
                raise ValueError("Input role must be a nonempty string of at most 100 characters")
            if source == node_id:
                raise ValueError("A node cannot depend on itself")
            self._node(db, source)
            pair = (source, role)
            if pair in seen:
                raise ValueError("Duplicate input node and role")
            seen.add(pair)
            clean.append({"node_id": source, "role": role})
        graph = self._all(db)
        graph[node_id] = {"inputs": clean}
        visited, active = set(), set()

        def visit(current):
            if current in active:
                raise ValueError("Dependency cycle detected; remove the circular link")
            if current in visited:
                return
            active.add(current)
            for edge in graph[current].get("inputs", []):
                visit(edge["node_id"])
            active.remove(current)
            visited.add(current)

        for current in graph:
            visit(current)
        return clean

    def _descendants(self, db: sqlite3.Connection, node_id: str) -> List[str]:
        nodes = self._all(db)
        result, pending, seen = [], [node_id], {node_id}
        while pending:
            parent = pending.pop(0)
            for current, node in nodes.items():
                if current not in seen and any(edge["node_id"] == parent for edge in node["inputs"]):
                    seen.add(current)
                    result.append(current)
                    pending.append(current)
        return result

    def _invalidate(self, db: sqlite3.Connection, node_id: str) -> None:
        for current in self._descendants(db, node_id):
            node = self._node(db, current)
            node["stale"] = True
            self._save_node(db, node, generation_change=True)

    def snapshot(self) -> dict:
        with self._connection() as db:
            return {
                "project": str(self.project),
                "revision": db.execute("SELECT value FROM settings WHERE key='revision'").fetchone()[0],
                "canvases": [{"id": row["id"], "name": row["name"], "placements": {
                    p["node_id"]: json.loads(p["position"]) for p in db.execute('SELECT node_id,position FROM canvas_nodes WHERE canvas_id=?',(row['id'],))}}
                    for row in db.execute('SELECT * FROM canvases ORDER BY rowid')],
                "nodes": [self._expanded(db, node) for node in self._all(db).values()],
                "jobs": [json.loads(row["data"]) for row in db.execute("SELECT data FROM jobs ORDER BY rowid")],
                "instances": [json.loads(row["data"]) for row in db.execute("SELECT data FROM instances ORDER BY rowid")],
                "batches": [self._expanded_batch(db, json.loads(row["data"])) for row in db.execute("SELECT data FROM batches ORDER BY rowid")],
            }

    def _clean_config(self, data: dict, current: Optional[dict] = None) -> dict:
        node = dict(current or {})
        for field in ("label", "kind", "prompt"):
            value = data.get(field, node.get(field, ""))
            if not isinstance(value, str):
                raise ValueError(f"{field} must be a string")
            node[field] = value
        if not node["label"].strip() or not node["kind"].strip():
            raise ValueError("Node label and kind must not be empty")
        node["params"] = _clone(data.get("params", node.get("params", {})))
        if not isinstance(node["params"], dict):
            raise ValueError("Node params must be an object")
        position = data.get("position", node.get("position", [0, 0]))
        if not isinstance(position, (list, tuple)) or len(position) != 2 or any(
                not isinstance(value, (int, float)) or not math.isfinite(value) for value in position):
            raise ValueError("Node position must contain two finite numbers")
        node["position"] = list(position)
        return node

    def create_node(self, data: dict) -> dict:
        if not isinstance(data, dict):
            raise ValueError("Node data must be an object")
        node_id = _identifier(data.get("id") or "node_" + uuid.uuid4().hex)
        node = self._clean_config({"kind": "reference", "label": node_id, **data})
        node.update(id=node_id, current_version=None, stale=False, created_at=_now())
        with self._connection(write=True) as db:
            if db.execute("SELECT 1 FROM nodes WHERE id=?", (node_id,)).fetchone():
                raise ValueError(f"Node ID already exists: {node_id}")
            node["inputs"] = self._validate_inputs(db, node_id, data.get("inputs", []))
            db.execute("INSERT INTO nodes(id,data) VALUES (?,?)", (node_id, _json(node)))
            self._bump(db)
            return self._expanded(db, node)

    def get_node(self, node_id: str) -> dict:
        with self._connection() as db:
            return self._expanded(db, self._node(db, node_id))

    def update_node(self, node_id: str, patch: dict) -> dict:
        if not isinstance(patch, dict):
            raise ValueError("Node patch must be an object")
        unknown = set(patch) - {"label", "kind", "prompt", "params", "inputs", "position"}
        if unknown:
            raise ValueError("Unsupported editable fields: " + ", ".join(sorted(unknown)))
        with self._connection(write=True) as db:
            old = self._node(db, node_id)
            node = self._clean_config(patch, old)
            if "inputs" in patch:
                node["inputs"] = self._validate_inputs(db, node_id, patch["inputs"])
            changed = any(node.get(key) != old.get(key) for key in ("kind", "prompt", "params", "inputs"))
            if changed:
                node["stale"] = True
            self._save_node(db, node, changed)
            if changed:
                self._invalidate(db, node_id)
            self._bump(db)
            return self._expanded(db, node)

    def _plan(self, db: sqlite3.Connection, node_id: str) -> dict:
        node = self._node(db, node_id)
        inputs, missing, stale_inputs = [], [], []
        for edge in node["inputs"]:
            source = self._node(db, edge["node_id"])
            version_id = source["current_version"]
            files = []
            if version_id:
                row = db.execute("SELECT data FROM versions WHERE id=?", (version_id,)).fetchone()
                if row:
                    files = json.loads(row["data"])["files"]
            if not version_id or not files:
                missing.append(source["id"])
            if source["stale"]:
                stale_inputs.append(source["id"])
            inputs.append({**edge, "version_id": version_id, "files": files})
        generation_revision = db.execute("SELECT generation_revision FROM nodes WHERE id=?", (node_id,)).fetchone()[0]
        definition = {"node_id": node_id, "kind": node["kind"], "prompt": node["prompt"],
                      "params": node["params"], "inputs": inputs, "generation_revision": generation_revision}
        fingerprint = hashlib.sha256(_json(definition).encode("utf-8")).hexdigest()
        return {**definition, "missing_inputs": sorted(set(missing)), "stale_inputs": sorted(set(stale_inputs)),
                "affected": self._descendants(db, node_id), "fingerprint": fingerprint}

    def plan(self, node_id: str) -> dict:
        with self._connection() as db:
            return self._plan(db, node_id)

    def import_version(self, node_id: str, files: list, metadata: Optional[dict] = None,
                       plan: Optional[dict] = None, *, _db=None, _created_dirs=None) -> dict:
        if not isinstance(files, list) or not files:
            raise ValueError("A version requires at least one artifact file")
        metadata = _clone(metadata or {})
        if not isinstance(metadata, dict):
            raise ValueError("Version metadata must be an object")
        sources = []
        for artifact in files:
            if not isinstance(artifact, dict):
                raise ValueError("Artifact must be an object containing path and role")
            role = artifact.get("role", "output")
            if not isinstance(role, str) or not role.strip():
                raise ValueError("Artifact role must be a nonempty string")
            sources.append((self._source_path(artifact.get("path")), role))
        version_id = "ver_" + uuid.uuid4().hex
        output_dir = None
        try:
            with (contextlib.nullcontext(_db) if _db is not None else self._connection(write=True)) as db:
                node = self._node(db, node_id)
                current_plan = self._plan(db, node_id)
                captured = _clone(plan) if plan is not None else current_plan
                if not isinstance(captured, dict) or captured.get("node_id") != node_id or not captured.get("fingerprint"):
                    raise ValueError("The captured plan does not belong to this node")
                for edge in captured.get("inputs", []):
                    pinned = edge.get("version_id")
                    if pinned and not db.execute("SELECT 1 FROM versions WHERE id=? AND node_id=?", (pinned, edge["node_id"])).fetchone():
                        raise ValueError("Captured plan references an unknown input version")
                output_dir = self._safe_output_dir(Path("assets") / "asset_pipeline" / node_id / version_id)
                managed_files = []
                for index, (source, role) in enumerate(sources):
                    name = re.sub(r"[^A-Za-z0-9._-]", "_", source.name).strip(".") or "artifact"
                    destination = output_dir / f"{index + 1:02d}_{name}"
                    shutil.copy2(source, destination)
                    digest = hashlib.sha256()
                    with destination.open("rb") as handle:
                        for block in iter(lambda: handle.read(1024 * 1024), b""):
                            digest.update(block)
                    managed_files.append({"path": destination.relative_to(self.project).as_posix(), "role": role,
                                          "size_bytes": destination.stat().st_size, "sha256": digest.hexdigest()})
                promoted = captured["fingerprint"] == current_plan["fingerprint"]
                version = {"id": version_id, "node_id": node_id, "files": managed_files,
                           "input_versions": {edge["node_id"]: edge.get("version_id") for edge in captured.get("inputs", [])},
                           "inputs": captured.get("inputs", []), "prompt": captured.get("prompt", ""),
                           "params": captured.get("params", {}), "kind": captured.get("kind", node["kind"]),
                           "created_at": _now(), "metadata": metadata, "plan_fingerprint": captured["fingerprint"],
                           "promoted": promoted}
                db.execute("INSERT INTO versions(id,node_id,data) VALUES (?,?,?)", (version_id, node_id, _json(version)))
                if promoted:
                    node["current_version"] = version_id
                    node["stale"] = False
                    self._save_node(db, node, generation_change=True)
                    self._invalidate(db, node_id)
                self._bump(db)
                if _created_dirs is not None:
                    _created_dirs.append(output_dir)
                return version
        except BaseException:
            if output_dir and output_dir.is_dir():
                shutil.rmtree(output_dir)
            raise

    def select_version(self, node_id: str, version_id: str) -> dict:
        with self._connection(write=True) as db:
            node = self._node(db, node_id)
            row = db.execute("SELECT data FROM versions WHERE id=? AND node_id=?", (version_id, node_id)).fetchone()
            if row is None:
                raise ValueError("Version does not belong to this node")
            node["current_version"] = version_id
            # Selection is explicit: keep the selected artifact usable even if its
            # recipe differs. Its immutable recipe remains visible in history.
            node["stale"] = False
            self._save_node(db, node, generation_change=True)
            self._invalidate(db, node_id)
            self._bump(db)
            return self._expanded(db, node)

    def create_job(self, node_id: str, plan: dict) -> dict:
        captured = _clone(plan)
        with self._connection(write=True) as db:
            self._node(db, node_id)
            if not isinstance(captured, dict) or captured.get("node_id") != node_id or not captured.get("fingerprint"):
                raise ValueError("Job plan does not belong to this node")
            if captured["fingerprint"] != self._plan(db, node_id)["fingerprint"]:
                raise ValueError("Node changed after preview; create a fresh plan before submitting")
            job = {"id": "job_" + uuid.uuid4().hex, "node_id": node_id, "plan": captured,
                   "status": "planned", "created_at": _now(), "updated_at": _now(), "remote_task_id": None}
            db.execute("INSERT INTO jobs(id,node_id,data) VALUES (?,?,?)", (job["id"], node_id, _json(job)))
            self._bump(db)
            return job

    def get_job(self, job_id: str) -> dict:
        with self._connection() as db:
            row = db.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise ValueError(f"Unknown job: {job_id}")
            return json.loads(row["data"])

    def update_job(self, job_id: str, patch: dict) -> dict:
        if not isinstance(patch, dict):
            raise ValueError("Job patch must be an object")
        if set(patch) & {"id", "node_id", "plan", "created_at"}:
            raise ValueError("Job identity and captured plan are immutable")
        with self._connection(write=True) as db:
            row = db.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise ValueError(f"Unknown job: {job_id}")
            job = json.loads(row["data"])
            job.update(_clone(patch))
            job["updated_at"] = _now()
            db.execute("UPDATE jobs SET data=? WHERE id=?", (_json(job), job_id))
            self._bump(db)
            return job

    def record_instance(self, data: dict) -> dict:
        if not isinstance(data, dict):
            raise ValueError("Instance data must be an object")
        instance = _clone(data)
        instance["id"] = _identifier(instance.get("id") or "instance_" + uuid.uuid4().hex, "Instance ID")
        node_id = instance.get("asset_id", instance.get("asset_node_id", instance.get("node_id")))
        with self._connection(write=True) as db:
            self._node(db, node_id)
            instance["asset_id"] = node_id
            instance["asset_node_id"] = node_id
            version_id = instance.get("version_id")
            if version_id and not db.execute("SELECT 1 FROM versions WHERE id=? AND node_id=?", (version_id, node_id)).fetchone():
                raise ValueError("Instance version does not belong to the asset")
            instance["updated_at"] = _now()
            db.execute("INSERT INTO instances(id,data) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                       (instance["id"], _json(instance)))
            self._bump(db)
            return instance

    @staticmethod
    def _recipe(node: dict) -> dict:
        return {key: node[key] for key in ("kind", "prompt", "params", "inputs")}

    def _batch(self, db, batch_id):
        row = db.execute("SELECT data FROM batches WHERE id=?", (batch_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown batch: " + str(batch_id))
        return json.loads(row["data"])

    def _expanded_batch(self, db, batch):
        batch = _clone(batch)
        completed, active = [], []
        for node_id in batch["node_ids"]:
            node = self._node(db, node_id)
            pinned = batch.get("completed_versions", {}).get(node_id)
            if pinned and node["current_version"] == pinned and not node["stale"]:
                completed.append(node_id)
            job_id = batch.get("attempts", {}).get(node_id)
            if job_id:
                row = db.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
                if row and json.loads(row["data"])["status"] in {"queued", "submitting", "running", "downloading", "pausing"}:
                    active.append(node_id)
        batch.update(total_nodes=len(batch["node_ids"]), completed_nodes=len(completed),
                     progress={"completed": len(completed), "total": len(batch["node_ids"])},
                     active_node_ids=active, blocked_node_ids=list(batch.get("errors", {})))
        return batch

    def get_batch(self, batch_id):
        with self._connection() as db:
            return self._expanded_batch(db, self._batch(db, batch_id))

    def update_batch(self, batch_id, patch):
        allowed = {"status", "attempts", "completed_versions", "errors", "error"}
        if not isinstance(patch, dict) or set(patch) - allowed:
            raise ValueError("Unsupported batch update")
        with self._connection(write=True) as db:
            batch = self._batch(db, batch_id)
            if any(batch.get(key) != value for key, value in patch.items()):
                batch.update(_clone(patch))
                batch["updated_at"] = _now()
                db.execute("UPDATE batches SET data=? WHERE id=?", (_json(batch), batch_id))
                self._bump(db)
            return self._expanded_batch(db, batch)

    def create_batch(self, spec):
        """Expand and import the complete graph atomically; never contact a provider."""
        if not isinstance(spec, dict):
            raise ValueError("Batch spec must be an object")
        spec = _clone(spec)
        batch_id = _identifier(spec.get("id") or "batch_" + uuid.uuid4().hex, "Batch ID")
        concurrency = spec.get("concurrency", 2)
        if type(concurrency) is not int or concurrency not in (1, 2):
            raise ValueError("Batch concurrency must be 1 or 2")
        assets = spec.get("assets")
        if not isinstance(assets, list) or not 1 <= len(assets) <= 50:
            raise ValueError("Batch assets must contain 1–50 assets")
        label = spec.get("label", "资产生产批次")
        if not isinstance(label, str) or not label.strip():
            raise ValueError("Batch label must be nonempty text")
        created_dirs = []
        try:
            with self._connection(write=True) as db:
                existing = db.execute("SELECT data FROM batches WHERE id=?", (batch_id,)).fetchone()
                if existing:
                    saved = json.loads(existing["data"])
                    if saved["spec"] != spec:
                        raise ValueError("Batch ID already exists with a different spec")
                    return self._expanded_batch(db, saved)
                references = spec.get("references", [])
                if not isinstance(references, list) or len(references) > 50:
                    raise ValueError("references must be an array of at most 50 imported references")
                for item in references:
                    if not isinstance(item, dict):
                        raise ValueError("Imported reference must be an object")
                    node_id = _identifier(item.get("id"), "Reference ID")
                    reference_y = max((node.get("position", [0, 0])[1] for node in self._all(db).values()), default=0) + 400
                    node = self._clean_config({"kind": "reference", "label": node_id,
                                               "position": [30, reference_y], **item})
                    node.update(id=node_id, current_version=None, stale=False, created_at=_now(), batch_id=batch_id)
                    node["inputs"] = self._validate_inputs(db, node_id, item.get("inputs", []))
                    db.execute("INSERT INTO nodes(id,data) VALUES (?,?)", (node_id, _json(node)))
                    self.import_version(node_id, [{"path": item.get("path"), "role": item.get("role", "image")}],
                        metadata={"source": "batch_existing_reference", **item.get("metadata", {})},
                        _db=db, _created_dirs=created_dirs)
                reference = _identifier(spec.get("reference_node_id"), "Reference node ID")
                self._node(db, reference)
                image_references = spec.get("image_reference_inputs", [])
                if not isinstance(image_references, list):
                    raise ValueError("image_reference_inputs must be an ordered array of {node_id, role} objects")
                shared_inputs, seen_references = [], set()
                for edge in image_references:
                    if not isinstance(edge, dict):
                        raise ValueError("Each image reference input must contain node_id and role")
                    source = _identifier(edge.get("node_id"), "Image reference node ID")
                    role = edge.get("role", "reference")
                    if not isinstance(role, str) or not role.strip() or len(role) > 100:
                        raise ValueError("Image reference role must be a nonempty string of at most 100 characters")
                    self._node(db, source)
                    if source not in seen_references:
                        shared_inputs.append({"node_id": source, "role": role})
                        seen_references.add(source)
                stages = ("subject", "front", "right", "back", "model")
                batch_assets, outputs, node_ids, seen_assets = [], {}, [], set()
                row_base = max((node.get("position", [0, 0])[1] for node in self._all(db).values()), default=0) + 400
                for index, asset in enumerate(assets):
                    if not isinstance(asset, dict):
                        raise ValueError("Each asset must be an object")
                    asset_id = _identifier(asset.get("id") or "asset_" + str(index + 1), "Asset ID")
                    if asset_id in seen_assets:
                        raise ValueError("Duplicate asset ID")
                    seen_assets.add(asset_id)
                    name = asset.get("name", asset_id)
                    if not isinstance(name, str) or not name.strip():
                        raise ValueError("Asset name must be nonempty text")
                    adopted = asset.get("existing_node_ids", {})
                    supplied = asset.get("existing_outputs", {})
                    prompts = asset.get("prompts", {})
                    overrides = asset.get("stage_inputs", {})
                    if any(not isinstance(value, dict) for value in (adopted, supplied, prompts, overrides)):
                        raise ValueError("Asset stage fields must be objects")
                    if (set(adopted) | set(supplied) | set(overrides) | set(prompts)) - set(stages):
                        raise ValueError("Unknown asset stage; use subject/front/right/back/model")
                    mapping = {stage: adopted.get(stage) or "node_" + uuid.uuid4().hex for stage in stages}
                    for stage in stages:
                        node_id = _identifier(mapping[stage])
                        if stage in adopted:
                            self._node(db, node_id)
                        else:
                            prompt = prompts.get(stage, "")
                            if stage != "model" and not isinstance(prompt, str):
                                raise ValueError("Stage prompts must be text")
                            if stage != "model" and not prompt.strip() and stage not in supplied:
                                raise ValueError("Missing prompt for " + name + " / " + stage)
                            params = (asset.get("model_params", {"model": "P2-20260801", "quad": False,
                                      "face_limit": 16000, "texture": True, "pbr": True}) if stage == "model"
                                      else asset.get("image_params", {"model": "seedream_v5"}))
                            node = self._clean_config({"kind": "model" if stage == "model" else "subject" if stage == "subject" else "view",
                                "label": name + " / " + {"subject": "主体", "front": "正面", "right": "右侧", "back": "背面", "model": "3D 模型"}[stage],
                                "prompt": "" if stage == "model" else prompt, "params": params,
                                "position": [340 + stages.index(stage) * 320, row_base + index * 350]})
                            node.update(id=node_id, current_version=None, stale=False, inputs=[], created_at=_now(), batch_id=batch_id)
                            db.execute("INSERT INTO nodes(id,data) VALUES (?,?)", (node_id, _json(node)))
                        if node_id not in node_ids:
                            node_ids.append(node_id)
                        if stage in supplied:
                            raw = supplied[stage]
                            raw = {"path": raw} if isinstance(raw, str) else raw
                            if not isinstance(raw, dict):
                                raise ValueError("Existing output must be a path or artifact object")
                            output = {"path": str(self._source_path(raw.get("path"))),
                                      "role": raw.get("role", "model" if stage == "model" else "image"),
                                      "metadata": _clone(raw.get("metadata", {}))}
                            if node_id in outputs and outputs[node_id] != output:
                                raise ValueError("Aliased stages specify different outputs for one node")
                            outputs[node_id] = output
                    # All placeholders exist before validating overrides, including forward references.
                    for stage in stages:
                        if stage in adopted:
                            continue  # Adoption preserves the actual historical recipe and input order.
                        if stage in overrides:
                            edges = overrides[stage]
                            if not isinstance(edges, list):
                                raise ValueError("stage_inputs values must be ordered arrays")
                            inputs = []
                            for edge in edges:
                                if not isinstance(edge, dict) or (bool(edge.get("node_id")) == bool(edge.get("stage"))):
                                    raise ValueError("Stage input needs exactly one of node_id or stage")
                                source = edge.get("node_id") or mapping.get(edge.get("stage"))
                                inputs.append({"node_id": source, "role": edge.get("role", "reference")})
                        elif stage == "subject":
                            refs = asset.get("subject_reference_node_ids", [reference])
                            if not isinstance(refs, list) or not refs:
                                raise ValueError("subject_reference_node_ids must be a nonempty ordered list")
                            inputs = [{"node_id": value, "role": "reference"} for value in refs]
                        elif stage == "front":
                            inputs = [{"node_id": mapping["subject"], "role": "reference"}]
                        elif stage in ("right", "back"):
                            inputs = [{"node_id": value, "role": "reference"} for value in dict.fromkeys([mapping["subject"], mapping["front"]])]
                        else:
                            inputs = [{"node_id": mapping[view], "role": view} for view in ("front", "right", "back")]
                        if stage != "model" and shared_inputs:
                            # References are real image-generation inputs. Keep custom image[n]
                            # ordering/roles, and never rewrite an adopted historical recipe.
                            unique_inputs = {}
                            for edge in inputs + shared_inputs:
                                source = _identifier(edge["node_id"], "Input node ID")
                                role = edge["role"]
                                if not isinstance(role, str) or not role.strip() or len(role) > 100:
                                    raise ValueError("Input role must be a nonempty string of at most 100 characters")
                                unique_inputs.setdefault(source, edge)
                            inputs = list(unique_inputs.values())
                        node = self._node(db, mapping[stage])
                        node["inputs"] = self._validate_inputs(db, node["id"], inputs)
                        self._save_node(db, node)
                    batch_assets.append({"id": asset_id, "name": name, "node_ids": mapping})
                # Stable topological order also ensures imported history captures real upstream versions.
                ordered, pending = [], list(node_ids)
                while pending:
                    ready = [node_id for node_id in pending if all(edge["node_id"] not in pending for edge in self._node(db, node_id)["inputs"])]
                    if not ready:
                        raise ValueError("Batch dependency cycle")
                    ordered.extend(ready)
                    pending = [node_id for node_id in pending if node_id not in ready]
                for node_id in ordered:
                    if node_id in outputs:
                        artifact = outputs[node_id]
                        if not self._node(db, node_id)["current_version"]:
                            self.import_version(node_id, [{"path": artifact["path"], "role": artifact["role"]}],
                                metadata={"source": "batch_existing_output", **artifact["metadata"]}, _db=db, _created_dirs=created_dirs)
                        else:
                            current = self._node(db, node_id)
                            version = json.loads(db.execute("SELECT data FROM versions WHERE id=?", (current["current_version"],)).fetchone()["data"])
                            digest = hashlib.sha256()
                            with Path(artifact["path"]).open("rb") as handle:
                                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                                    digest.update(chunk)
                            if current["stale"] or not any(file.get("sha256") == digest.hexdigest() for file in version["files"]):
                                raise ValueError("Existing output differs from the current usable version of " + node_id + "; import or select it explicitly before adoption")
                attempts, done, recipes, external_versions = {}, {}, {}, {}
                for node_id in ordered:
                    node = self._node(db, node_id)
                    recipes[node_id] = self._recipe(node)
                    latest = db.execute("SELECT data FROM jobs WHERE node_id=? ORDER BY rowid DESC LIMIT 1", (node_id,)).fetchone()
                    job = json.loads(latest["data"]) if latest else None
                    if job and job["status"] != "completed" and (not node["current_version"] or node["stale"] or job["status"] in {"planned", "queued", "submitting", "running", "downloading", "paused", "pausing", "submission_uncertain"}):
                        attempts[node_id] = job["id"]
                    elif node["current_version"] and not node["stale"]:
                        done[node_id] = node["current_version"]
                    for edge in node["inputs"]:
                        if edge["node_id"] not in ordered:
                            external_versions[edge["node_id"]] = self._node(db, edge["node_id"])["current_version"]
                batch = {"id": batch_id, "label": label, "spec": spec, "concurrency": concurrency,
                    "assets": batch_assets, "node_ids": ordered, "status": "planned", "attempts": attempts,
                    "completed_versions": done, "recipes": recipes, "external_versions": external_versions,
                    "errors": {}, "error": None, "created_at": _now(), "updated_at": _now()}
                db.execute("INSERT INTO batches(id,data) VALUES (?,?)", (batch_id, _json(batch)))
                self._bump(db)
                return self._expanded_batch(db, batch)
        except BaseException:
            for directory in created_dirs:
                shutil.rmtree(directory, ignore_errors=True)
            raise

    def create_batch_job(self, batch_id, node_id, plan):
        """Persist the single allowed attempt and its ownership before any remote submission."""
        with self._connection(write=True) as db:
            batch = self._batch(db, batch_id)
            if batch["status"] != "running" or node_id not in batch["node_ids"]:
                raise ValueError("Batch is not running or does not own this node")
            if node_id in batch["attempts"]:
                return batch["attempts"][node_id]
            if plan["fingerprint"] != self._plan(db, node_id)["fingerprint"]:
                raise ValueError("Node changed while preparing batch submission")
            # A manually started task may have appeared since the scheduler's snapshot.
            latest = db.execute("SELECT data FROM jobs WHERE node_id=? ORDER BY rowid DESC LIMIT 1", (node_id,)).fetchone()
            old = json.loads(latest["data"]) if latest else None
            if old and old["status"] != "completed":
                job_id = old["id"]
            else:
                job_id = "job_" + uuid.uuid4().hex
                job = {"id": job_id, "node_id": node_id, "plan": _clone(plan), "batch_id": batch_id,
                       "status": "queued", "created_at": _now(), "updated_at": _now(), "remote_task_id": None}
                db.execute("INSERT INTO jobs(id,node_id,data) VALUES (?,?,?)", (job_id, node_id, _json(job)))
            batch["attempts"][node_id] = job_id
            batch["updated_at"] = _now()
            db.execute("UPDATE batches SET data=? WHERE id=?", (_json(batch), batch_id))
            self._bump(db)
            return job_id
