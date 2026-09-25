"""Individual jobs and explicit batch scheduling, with one submission per attempt."""
import json
import re
import tempfile
import threading
import uuid
from pathlib import Path

from .store import Store, _json, _now
from .tripo import TripoProvider, SubmissionUncertain, ProviderUnavailable, InvalidPlan, SubmissionRejected


class PipelineService:
    def __init__(self, project, provider=None, poll_interval=3):
        self.store = Store(str(project))
        self.provider = provider or TripoProvider(Path(project))
        self.poll_interval = poll_interval
        self.lock = threading.RLock()
        self.workers = {}
        self.batch_workers = {}
        self.stopping = threading.Event()
        for job in self.store.snapshot()["jobs"]:
            if job["status"] in ("submitting", "running", "downloading", "queued", "pausing"):
                status = "paused" if job.get("task_id") else "submission_uncertain"
                if job["status"] == "queued":
                    status = "interrupted"
                self.store.update_job(job["id"], {"status": status,
                    "error": "后台已重启。已有任务可继续查询；未知提交结果不会自动重发。"})
        for batch in self.store.snapshot()["batches"]:
            if batch["status"] == "running":
                self.store.update_batch(batch["id"], {"status": "paused",
                    "error": "后台已重启。继续批次会复用已保存任务，不会重发未知提交。"})

    def dispatch(self, method, params):
        from . import canvases
        if not isinstance(params, dict): raise ValueError("params 必须为对象")
        with self.lock:
            if method.startswith('canvas.'):
                return canvases.dispatch(self,method,params)
            canvas_id = params.get('canvas_id')
            creates = method in ('manual.create','manual.import','manual.copy','manual.template_load','manual.rebuild','node.create','batch.create','subject.selection','video.commit')
            if canvas_id and creates:
                canvases.require(self.store,canvas_id)
                before = {n['id'] for n in self.store.snapshot()['nodes']}
                try:
                    return self._dispatch(method,params)
                finally:
                    new = [n['id'] for n in self.store.snapshot()['nodes'] if n['id'] not in before]
                    canvases.assign_new(self.store,canvas_id,new)
            return self._dispatch(method,params)

    def _dispatch(self, method, params):
        if not isinstance(params, dict):
            raise ValueError("params 必须为对象")
        if method == "subject.selection":
            from .subject_selection import create
            with self.lock:
                return create(self.store, params)
        if method.startswith("manual."):
            from . import manual
            with self.lock:
                return manual.dispatch(self, method, params)
        if method == "video.thumbnail":
            from .video import thumbnail
            return thumbnail(self.store, params)
        if method == "video.sessions":
            folder = self.store.project / "artifacts/asset_pipeline_video"
            return [json.loads(f.read_text()) for f in sorted(folder.glob("video_*/manifest.json"))]
        if method == "video.selection":
            session = params["session_id"]
            if not re.fullmatch(r"video_[A-Za-z0-9]+", session):
                raise ValueError("无效视频会话")
            receipt = self.store.project / "artifacts/asset_pipeline_video" / session / "selection.json"
            return json.loads(receipt.read_text()) if receipt.exists() else {"selection": {}}
        if method in ("video.sample", "video.commit"):
            from . import video
            with self.lock:
                return video.sample(self.store, params) if method == "video.sample" else video.commit(self.store, params)
        if method == "snapshot":
            return self.store.snapshot()
        if method == "provider.status":
            return self.provider.available()
        if method == "batch.create":
            return self.store.create_batch(params["spec"])
        if method == "batch.plan":
            return self.plan_batch(params["batch_id"])
        if method in ("batch.run", "batch.resume"):
            return self.run_batch(params["batch_id"])
        if method == "batch.pause":
            return self.pause_batch(params["batch_id"])
        if method == "batch.retry_rejected":
            return self.retry_rejected(params["batch_id"], params["node_ids"], params["reason"])
        if method == "node.create":
            return self.store.create_node(params["node"])
        if method == "node.update":
            return self.store.update_node(params["node_id"], params["patch"])
        if method == "node.plan":
            plan = self.store.plan(params["node_id"])
            try:
                plan["provider_preview"] = self.provider.preview(plan)
            except (ValueError, RuntimeError) as error:
                plan["provider_preview"] = {"error": str(error)}
            plan["will_generate"] = [plan["node_id"]]
            plan["cost"] = "以 Tripo API 实际计费为准；预览不上传、不提交"
            return plan
        if method == "node.run":
            return self.run(params["node_id"])
        if method == "artifact.import":
            return self.store.import_version(params["node_id"],
                [{"path": params["path"], "role": params.get("role", "image")}],
                metadata={"source": "manual_import"})
        if method == "version.select":
            return self.store.select_version(params["node_id"], params["version_id"])
        if method == "job.resume":
            return self.resume(params["job_id"])
        if method == "job.pause":
            return self.pause(params["job_id"])
        if method == "job.attach":
            return self.attach(params["job_id"], params["task_id"])
        if method == "instance.record":
            return self.store.record_instance(params["instance"])
        if method == "template":
            return self.template(params.get("name", "新资产"))
        raise ValueError("未知操作: " + str(method))

    def run(self, node_id):
        with self.lock:
            if self.store.get_node(node_id).get("archived"):
                raise ValueError("请先恢复归档节点，再运行生成")
            for existing in self.store.snapshot()["jobs"]:
                if existing["node_id"] == node_id and existing["status"] in (
                        "queued", "submitting", "running", "downloading", "submission_uncertain", "paused", "pausing"):
                    raise ValueError("此节点已有未完成任务，请继续原任务；未知提交结果需先关联任务 ID。")
            plan = self.store.plan(node_id)
            if plan.get("missing_inputs"):
                raise ValueError("上游尚无产物: " + str(plan["missing_inputs"]))
            # Validate before recording/submitting. This must be side-effect free.
            plan["provider_preview"] = self.provider.preview(plan)
            job = self.store.create_job(node_id, plan)
            self.store.update_job(job["id"], {"status": "queued"})
            self._start(job["id"], True)
            return self.store.get_job(job["id"])

    def _start(self, job_id, submit):
        if job_id in self.workers and self.workers[job_id][0].is_alive():
            raise ValueError("任务正在运行")
        paused = threading.Event()
        worker = threading.Thread(target=self._execute, args=(job_id, submit, paused), daemon=True)
        self.workers[job_id] = (worker, paused)
        worker.start()

    @staticmethod
    def _is_image_plan(plan):
        return str(plan.get("kind", "")).replace("_", "-") in {
            "concept", "subject", "view", "multiview", "text-to-image", "image-to-image", "image-to-multiview"}

    def _reserve_submission(self, job_id, paused):
        """Serialize image generation across manual jobs and every local batch.

        The account's image limit is one, independent of a batch's worker limit.
        Pausing a local poll does not free its remote slot. An uncertain POST may
        also have occupied a slot, so it requires explicit reconciliation first.
        Existing task IDs never enter this gate and can always be polled.
        """
        terminal = {"success", "succeeded", "completed", "failed", "cancelled", "canceled", "banned", "expired"}
        while not self.stopping.is_set() and not paused.is_set():
            with self.lock:
                job = self.store.get_job(job_id)
                occupied = False
                if self._is_image_plan(job["plan"]):
                    for other in self.store.snapshot()["jobs"]:
                        if other["id"] == job_id or not self._is_image_plan(other["plan"]):
                            continue
                        if str(other.get("remote_status", "")).lower() in terminal or other["status"] == "completed":
                            continue
                        if other.get("task_id") or other.get("remote_task_id") or other["status"] in ("submitting", "submission_uncertain"):
                            occupied = True
                            break
                if not occupied and not self.stopping.is_set() and not paused.is_set():
                    # Persist the reservation while still holding the shared
                    # lock, before the POST. Another worker cannot race it.
                    self.store.update_job(job_id, {"status": "submitting", "error": None})
                    return True
                if job["status"] != "queued":
                    self.store.update_job(job_id, {"status": "queued", "error": None})
            paused.wait(min(self.poll_interval, 0.25))
        self.store.update_job(job_id, {"status": "interrupted", "error": "提交前已暂停，尚未请求 Tripo。"})
        return False

    def _execute(self, job_id, submit, paused):
        try:
            job = self.store.get_job(job_id)
            # A crash can occur after committing the artifact but before updating the job.
            for version in self.store.get_node(job["node_id"])["versions"]:
                if job.get("task_id") and version.get("metadata", {}).get("task_id") == job["task_id"] and version.get("plan_fingerprint") == job["plan"]["fingerprint"]:
                    self.store.update_job(job_id, {"status": "completed", "progress": 100,
                        "version_id": version["id"], "promoted": version.get("promoted", True), "error": None})
                    return
            if submit:
                if not self._reserve_submission(job_id, paused):
                    return
                try:
                    remote = self.provider.submit(job["plan"])
                except SubmissionUncertain as error:
                    self.store.update_job(job_id, {"status": "submission_uncertain", "error": str(error)})
                    return
                except SubmissionRejected as error:
                    self.store.update_job(job_id, {"status": "failed", "error": str(error),
                        "submission_rejection": getattr(error, "diagnostics", {})})
                    return
                except (ProviderUnavailable, InvalidPlan) as error:
                    self.store.update_job(job_id, {"status": "failed", "error": str(error)})
                    return
                if not remote.get("task_id"):
                    self.store.update_job(job_id, {"status": "submission_uncertain",
                        "error": "提交返回缺少任务 ID；请查询 Tripo 任务列表后关联，勿重复提交。"})
                    return
                self.store.update_job(job_id, {"task_id": remote["task_id"], "status": "running"})
            while not self.stopping.is_set() and not paused.is_set():
                job = self.store.get_job(job_id)
                result = self.provider.poll(job["task_id"])
                remote_status = str(result.get("status", "unknown")).lower()
                self.store.update_job(job_id, {"status": "running",
                    "progress": result.get("progress", 0), "remote_status": remote_status})
                if remote_status in ("success", "succeeded", "completed"):
                    if paused.is_set() or self.stopping.is_set():
                        break
                    self.store.update_job(job_id, {"status": "downloading"})
                    with tempfile.TemporaryDirectory(prefix="asset-pipeline-") as directory:
                        files = self.provider.download(result, Path(directory))
                        if not files:
                            raise RuntimeError("任务完成但没有可下载产物")
                        version = self.store.import_version(job["node_id"], files,
                            metadata={"provider": "tripo", "task_id": job["task_id"],
                                "credits_consumed": result.get("credits_consumed"),
                                "request_preview": job["plan"].get("provider_preview"),
                                "transport_version": "tripo-cli/0.5.1"}, plan=job["plan"])
                    self.store.update_job(job_id, {"status": "completed", "progress": 100,
                        "version_id": version["id"], "promoted": version.get("promoted", True), "error": None})
                    return
                if remote_status in ("failed", "cancelled", "canceled", "banned", "expired"):
                    self.store.update_job(job_id, {"status": "failed",
                        "error": str(result.get("error") or ("Tripo 任务状态: " + remote_status))})
                    return
                paused.wait(self.poll_interval)
            self.store.update_job(job_id, {"status": "paused"})
        except Exception as error:
            # After an ID exists a retry polls that ID; never performs another submission.
            job = self.store.get_job(job_id)
            status = "paused" if job.get("task_id") else "failed"
            if job.get("status") == "submitting":
                status = "submission_uncertain"
            self.store.update_job(job_id, {"status": status, "error": str(error)[:2000]})

    def resume(self, job_id):
        with self.lock:
            job = self.store.get_job(job_id)
            if not job.get("task_id"):
                raise ValueError("缺少远端任务 ID，不能安全继续；请关联已有任务。")
            if job["status"] == "completed":
                raise ValueError("任务已经完成，无需再次下载")
            self._start(job_id, False)
            return self.store.get_job(job_id)

    def pause(self, job_id):
        with self.lock:
            if job_id in self.workers:
                self.workers[job_id][1].set()
            job = self.store.get_job(job_id)
            if job["status"] in ("running", "downloading"):
                self.store.update_job(job_id, {"status": "pausing"})
            return {"job": self.store.get_job(job_id),
                    "notice": "只暂停本地查询。远端生成可能仍在进行和计费；继续时复用任务 ID。"}

    def attach(self, job_id, task_id):
        import uuid
        uuid.UUID(task_id)
        with self.lock:
            job = self.store.get_job(job_id)
            if job["status"] not in ("submission_uncertain", "interrupted"):
                raise ValueError("只可给未确定提交结果的任务关联远端 ID")
            return self.store.update_job(job_id, {"task_id": task_id, "status": "paused", "error": None})

    def plan_batch(self, batch_id):
        batch = self.store.get_batch(batch_id)
        plans = []
        for node_id in batch["node_ids"]:
            plan = self.store.plan(node_id)
            plan["action"] = ("reuse_version" if node_id in batch["completed_versions"] else
                              "reuse_job" if node_id in batch["attempts"] else "generate")
            if not plan["missing_inputs"] and plan["action"] == "generate":
                try:
                    plan["provider_preview"] = self.provider.preview(plan)
                except (ValueError, RuntimeError) as error:
                    plan["provider_preview"] = {"error": str(error)}
            plans.append(plan)
        return {**batch, "plans": plans, "notice": "只预览；运行批次才会按依赖顺序提交，每阶段最多一次，不自动重试付费生成。"}

    def run_batch(self, batch_id):
        with self.lock:
            batch = self.store.get_batch(batch_id)
            if batch["status"] == "completed":
                return batch
            worker = self.batch_workers.get(batch_id)
            if worker and worker.is_alive():
                if batch["status"] == "running":
                    return batch
                raise ValueError("批次正在暂停，请稍后继续")
            self.store.update_batch(batch_id, {"status": "running", "error": None, "errors": {}})
            # Adopt jobs started manually since batch creation before allocating new work.
            latest = {job["node_id"]: job for job in self.store.snapshot()["jobs"]}
            for node_id in batch["node_ids"]:
                job = latest.get(node_id)
                if node_id not in batch["attempts"] and node_id not in batch["completed_versions"] and job and job["status"] != "completed":
                    self.store.create_batch_job(batch_id, node_id, self.store.plan(node_id))
            batch = self.store.get_batch(batch_id)
            # A paused query may be retried only once in response to this explicit run/resume.
            resume_ids = set(batch["attempts"].values())
            worker = threading.Thread(target=self._execute_batch, args=(batch_id, resume_ids), daemon=True)
            self.batch_workers[batch_id] = worker
            worker.start()
            return self.store.get_batch(batch_id)

    def pause_batch(self, batch_id):
        with self.lock:
            batch = self.store.get_batch(batch_id)
            if batch["status"] == "completed":
                return batch
            self.store.update_batch(batch_id, {"status": "paused",
                "error": "已暂停后续阶段；远端已提交任务可能仍在运行与计费，继续会复用原任务 ID。"})
            for job_id in batch["attempts"].values():
                self.pause(job_id)
            return self.store.get_batch(batch_id)

    @staticmethod
    def _rejected_http_status(job):
        diagnostics = job.get("submission_rejection")
        if isinstance(diagnostics, dict) and type(diagnostics.get("http_status")) is int:
            return diagnostics["http_status"]
        # Compatibility for jobs recorded before structured provider errors.
        # Generic errors, guessed status codes, and partial matches are unsafe.
        match = re.match(r"\ATripo rejected this request \(HTTP (400|429)\)\. ", str(job.get("error", "")))
        return int(match.group(1)) if match else None

    def retry_rejected(self, batch_id, node_ids, reason):
        """Explicitly replace only proven, unaccepted submissions; never run them.

        All requested stages are validated before any write, in one SQLite
        transaction. A changed HTTP 400 recipe belongs in a new recovery batch,
        whose frozen recipes capture the intended correction. Historical jobs
        and the original batch remain intact.
        """
        if not isinstance(node_ids, list) or not node_ids or any(not isinstance(value, str) for value in node_ids) or len(set(node_ids)) != len(node_ids):
            raise ValueError("node_ids 必须是非空且不重复的节点 ID 列表")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("必须说明明确重试的原因")
        with self.lock, self.store._connection(write=True) as db:
            batch = self.store._batch(db, batch_id)
            if batch["status"] not in ("paused", "planned", "blocked"):
                raise ValueError("只有暂停、待运行或受阻的批次可准备重试")
            worker = self.batch_workers.get(batch_id)
            if worker and worker.is_alive():
                raise ValueError("请等待批次工作线程停止后再准备重试")
            prepared = []
            for node_id in node_ids:
                if node_id not in batch["node_ids"] or node_id in batch["completed_versions"]:
                    raise ValueError("节点不属于本批次待恢复阶段: " + node_id)
                old_id = batch["attempts"].get(node_id)
                row = db.execute("SELECT data FROM jobs WHERE id=?", (old_id,)).fetchone()
                old = json.loads(row["data"]) if row else None
                if not old or old["node_id"] != node_id or old["status"] != "failed" or old.get("task_id") or old.get("remote_task_id"):
                    raise ValueError("只可重试确认未被受理且没有远端任务 ID 的失败提交: " + node_id)
                live = self.workers.get(old_id)
                if live and live[0].is_alive():
                    raise ValueError("原任务尚未停止: " + node_id)
                latest = db.execute("SELECT id FROM jobs WHERE node_id=? ORDER BY rowid DESC LIMIT 1", (node_id,)).fetchone()
                if latest["id"] != old_id:
                    raise ValueError("节点已有较新的任务，不可重复准备重试: " + node_id)
                status = self._rejected_http_status(old)
                if status not in (400, 429):
                    raise ValueError("缺少可恢复的明确 HTTP 400/429 拒绝证据: " + node_id)
                node = self.store._node(db, node_id)
                if self.store._recipe(node) != batch["recipes"][node_id]:
                    raise ValueError("配方已改变，请先建立捕获新配方的恢复批次: " + node_id)
                plan = self.store._plan(db, node_id)
                if plan["missing_inputs"] or plan["stale_inputs"]:
                    raise ValueError("重试阶段的上游产物尚未就绪: " + node_id)
                for edge in plan["inputs"]:
                    if edge["node_id"] not in batch["node_ids"] and edge["version_id"] != batch["external_versions"].get(edge["node_id"]):
                        raise ValueError("外部参考版本已改变: " + edge["node_id"])
                if status == 400 and plan["fingerprint"] == old["plan"]["fingerprint"]:
                    raise ValueError("HTTP 400 的请求必须先修正配方或输入，不可原样重试: " + node_id)
                plan["provider_preview"] = self.provider.preview(plan)
                prepared.append((node_id, old_id, plan))
            jobs = []
            now = _now()
            for node_id, old_id, plan in prepared:
                job = {"id": "job_" + uuid.uuid4().hex, "node_id": node_id, "plan": plan,
                       "batch_id": batch_id, "status": "planned", "created_at": now, "updated_at": now,
                       "remote_task_id": None, "retry_of": old_id, "retry_reason": reason.strip()}
                db.execute("INSERT INTO jobs(id,node_id,data) VALUES (?,?,?)", (job["id"], node_id, _json(job)))
                batch["attempts"][node_id] = job["id"]
                jobs.append(job)
            batch.update(status="planned", errors={}, error=None, updated_at=now)
            db.execute("UPDATE batches SET data=? WHERE id=?", (_json(batch), batch_id))
            self.store._bump(db)
            return {"batch": self.store._expanded_batch(db, batch), "jobs": jobs,
                    "notice": "已记录显式恢复尝试，尚未提交；运行批次后才会生成。原任务完整保留。"}

    def _execute_batch(self, batch_id, resume_ids):
        try:
            while not self.stopping.is_set():
                with self.lock:
                    batch = self.store.get_batch(batch_id)
                    if batch["status"] != "running":
                        return
                    if not self._batch_step(batch, resume_ids):
                        return
                self.stopping.wait(min(self.poll_interval, 0.25))
        except Exception as error:
            self.store.update_batch(batch_id, {"status": "blocked", "error": str(error)[:2000]})
        finally:
            if self.stopping.is_set() and self.store.get_batch(batch_id)["status"] == "running":
                self.store.update_batch(batch_id, {"status": "paused", "error": "后台已停止，继续批次会复用已保存的任务。"})

    def _batch_step(self, batch, resume_ids):
        snapshot = self.store.snapshot()
        nodes = {node["id"]: node for node in snapshot["nodes"]}
        jobs = {job["id"]: job for job in snapshot["jobs"]}
        done, errors, active, pending = dict(batch["completed_versions"]), {}, set(), set()
        for node_id in batch["node_ids"]:
            node = nodes[node_id]
            if node.get("archived"):
                errors[node_id] = "节点已归档，请先恢复"
                continue
            job_id = batch["attempts"].get(node_id)
            job = jobs.get(job_id)
            live = job_id in self.workers and self.workers[job_id][0].is_alive()
            if job and live and job["status"] not in ("completed", "failed", "submission_uncertain"):
                active.add(node_id)
                continue  # Let an already paid task retain its result even if its recipe changed.
            if self.store._recipe(node) != batch["recipes"][node_id]:
                errors[node_id] = "节点配方已改变；本批次保留原任务，不自动购买新版本。"
                continue
            if node_id in done:
                if node["current_version"] != done[node_id] or node["stale"]:
                    errors[node_id] = "批次已完成产物或其上游版本已变化；不会自动重新生成。"
                continue
            if job and job["status"] == "completed":
                if job.get("promoted", True) and job.get("version_id") == node["current_version"] and not node["stale"]:
                    done[node_id] = node["current_version"]
                else:
                    errors[node_id] = "原任务产物已保留，但配方或输入已变化，后续阶段已停止。"
                continue
            # Resolve current actual inputs only after their batch stages have completed.
            waiting = False
            for edge in node["inputs"]:
                source_id = edge["node_id"]
                source = nodes[source_id]
                if source_id in errors:
                    errors[node_id] = "上游阶段需要处理: " + source_id
                    break
                if source_id in batch["node_ids"]:
                    if source_id not in done:
                        waiting = True
                elif source["current_version"] != batch["external_versions"].get(source_id) or not source["current_version"] or source["stale"]:
                    errors[node_id] = "外部参考缺少产物或版本已变化: " + source_id
                    break
            if node_id in errors or waiting:
                continue
            if job:
                if job["status"] in ("planned", "queued", "interrupted") and not job.get("task_id"):
                    if self.store.plan(node_id)["fingerprint"] != job["plan"]["fingerprint"]:
                        errors[node_id] = "未提交任务的配方已变化，已阻止提交。"
                    else:
                        pending.add(node_id)
                elif job_id in resume_ids and job.get("task_id") and job["status"] in ("paused", "running", "downloading", "pausing"):
                    pending.add(node_id)
                else:
                    errors[node_id] = job.get("error") or "原任务需处理: " + job["status"]
            elif node["current_version"] and not node["stale"]:
                done[node_id] = node["current_version"]
            else:
                pending.add(node_id)
        # A failed asset branch stops as a unit; unrelated assets can continue.
        for asset in batch["assets"]:
            branch = list(dict.fromkeys(asset["node_ids"][stage] for stage in ("subject", "front", "right", "back", "model")))
            failures = [node_id for node_id in branch if node_id in errors]
            if failures:
                for node_id in branch:
                    if node_id not in done and node_id not in active:
                        errors.setdefault(node_id, "此资产的前序阶段需要处理: " + failures[0])
                        pending.discard(node_id)
        self.store.update_batch(batch["id"], {"completed_versions": done, "errors": errors,
            "error": next(iter(errors.values()), None)})
        if len(done) == len(batch["node_ids"]) and not errors:
            self.store.update_batch(batch["id"], {"status": "completed", "error": None})
            return False
        slots = max(0, batch["concurrency"] - len(active))
        started = False
        scheduling_groups = [list(dict.fromkeys(asset["node_ids"][stage] for stage in
            ("subject", "front", "right", "back", "model"))) for asset in batch["assets"]]
        if not batch["assets"]:
            scheduling_groups = [[node_id] for node_id in batch["node_ids"]]
        for branch in scheduling_groups:
            if not slots:
                break
            if any(node_id in active or node_id in errors for node_id in branch):
                continue
            candidates = [node_id for node_id in branch if node_id in pending]
            if not candidates:
                continue
            node_id = candidates[0]
            job_id = batch["attempts"].get(node_id)
            try:
                if not job_id:
                    plan = self.store.plan(node_id)
                    if plan["missing_inputs"] or plan["stale_inputs"]:
                        raise ValueError("阶段上游尚未就绪")
                    plan["provider_preview"] = self.provider.preview(plan)
                    job_id = self.store.create_batch_job(batch["id"], node_id, plan)
                job = self.store.get_job(job_id)
                live = job_id in self.workers and self.workers[job_id][0].is_alive()
                if not live:
                    if job["status"] in ("planned", "queued", "interrupted") and not job.get("task_id"):
                        self._start(job_id, True)
                    elif job_id in resume_ids and job.get("task_id") and job["status"] in ("paused", "running", "downloading", "pausing"):
                        self._start(job_id, False)
                    else:
                        raise ValueError("已有任务不会自动重新提交: " + job["status"])
                resume_ids.discard(job_id)
                active.add(node_id)
                pending.discard(node_id)
                slots -= 1
                started = True
            except (ValueError, RuntimeError) as error:
                errors[node_id] = str(error)
                pending.discard(node_id)
        if errors:
            self.store.update_batch(batch["id"], {"errors": errors, "error": next(iter(errors.values()))})
        if not active and not started:
            for node_id in batch["node_ids"]:
                if node_id not in done:
                    errors.setdefault(node_id, "依赖尚未就绪或原任务需处理；本批次不会自动重复提交。")
            self.store.update_batch(batch["id"], {"status": "blocked", "errors": errors,
                "error": next(iter(errors.values()), "批次需要处理")})
            return False
        return True

    def template(self, name):
        created = []
        def add(label, kind, x, y, prompt="", inputs=None, params=None):
            node = self.store.create_node({"label": label, "kind": kind, "position": [x, y],
                "prompt": prompt, "inputs": inputs or [], "params": params or {}})
            created.append(node)
            return node["id"]
        concept = add(name + " / 总概念", "concept", 30, 180,
                      "Create a cohesive stylized urban street scene, clear silhouettes, readable props.")
        subject = add(name + " / 主体", "subject", 340, 180,
                      "Extract the chosen subject as one complete isolated object. Preserve structure and colors. Neutral background.",
                      [{"node_id": concept, "role": "reference"}])
        front = add("正面", "view", 650, 30,
                    "Exact front view of this object, orthographic, unchanged proportions, neutral background.",
                    [{"node_id": subject, "role": "reference"}], {"view": "front"})
        right = add("右侧", "view", 650, 240,
                    "Exact right side view of this object, orthographic, preserve proportions and details.",
                    [{"node_id": subject, "role": "reference"}], {"view": "right"})
        back = add("背面", "view", 650, 450,
                   "Exact rear view of this object, orthographic, preserve proportions, infer hidden details consistently.",
                   [{"node_id": subject, "role": "reference"}], {"view": "back"})
        add(name + " / 3D 模型", "model", 970, 180, "",
            [{"node_id": front, "role": "front"}, {"node_id": right, "role": "right"}, {"node_id": back, "role": "back"}],
            {"model": "P2-20260801", "quad": False, "face_limit": 16000, "texture": True, "pbr": True})
        return {"nodes": created, "notice": "只创建节点，不发起生成。总概念或任意中间结果均可直接导入。"}
