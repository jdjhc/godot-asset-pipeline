"""Tripo V3 provider backed by the installed official tripo-cli 0.5.1.

No API keys are read by this module. The official client resolves credentials
inside a short-lived Node child process. The helper forces maxRetries=0;
ordinary CLI generation commands retry POSTs and must not be used as fallback.

API references verified 2026-09-25:
https://developers.tripo3d.ai/en/docs/generation-multiview-to-model/p
https://developers.tripo3d.ai/en/docs/generation-text-to-image
https://developers.tripo3d.ai/en/docs/generation-image-to-image
https://developers.tripo3d.ai/en/docs/generation-image-to-multiview
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class TripoError(RuntimeError):
    """Safe, user-facing provider error; never contains raw CLI stderr."""

    def __init__(self, message: str, diagnostics: dict[str, Any] | None = None):
        super().__init__(message)
        self.diagnostics = dict(diagnostics or {})


class ProviderUnavailable(TripoError):
    pass


class SubmissionUncertain(TripoError):
    """A submit process started but returned no confirmed ID. Never retry it."""


class SubmissionRejected(TripoError):
    """An explicit HTTP client error confirmed that the task was not accepted."""


class InvalidPlan(TripoError, ValueError):
    pass


VIEW_ORDER = ("front", "left", "back", "right")
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
MODEL_EXTENSIONS = {".glb", ".gltf", ".fbx", ".obj", ".stl", ".usdz", ".ply", ".zip"}
KINDS = {
    "concept": "text-to-image", "text-to-image": "text-to-image",
    "subject": "image-to-image", "view": "image-to-image", "image-to-image": "image-to-image",
    "multiview": "image-to-multiview", "image-to-multiview": "image-to-multiview",
    "model": "multiview-to-model", "multiview-to-model": "multiview-to-model",
}
MODEL_ALIASES = {
    "tripo-p2": "P2-20260801", "p2": "P2-20260801", "P2-20260801": "P2-20260801",
    "tripo-p1": "P1-20260311", "p1": "P1-20260311", "P1-20260311": "P1-20260311",
    "tripo-v3.1": "v3.1-20260211", "v3.1-20260211": "v3.1-20260211",
}
TASK_ID_RE = re.compile(r"(?:task_[A-Za-z0-9_-]+|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})\Z")
SAFE_KEY_RE = re.compile(r"[a-z][a-z0-9_]*\Z")
SECRET_KEY_RE = re.compile(r"api_?key|token|authorization|password|secret|credential", re.I)
REQUEST_ID_RE = re.compile(r"(?:[a-fA-F0-9]{32}|[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}|req_[A-Za-z0-9]{8,64})\Z")
# Static translations of the verified CLI 0.5.1 error catalog. Never display the
# remote message/suggestion: those can contain echoed inputs or sensitive data.
API_ERROR_HINTS = {
    1000: "Tripo reported a server error; retain the request ID for support.",
    1001: "Tripo reported a fatal server error; retain the request ID for support.",
    1002: "Authentication failed; check the configured account and API region.",
    1003: "The request body format is invalid.",
    1004: "One or more request parameters are invalid.",
    1005: "The account does not have access to this resource.",
    1007: "The global request rate limit was reached.",
    2000: "The generation concurrency limit was reached; task families share a pool.",
    2001: "The task was not found for this account and region.",
    2002: "This generation task type is unsupported.",
    2003: "An input image file is empty.",
    2004: "An input image file type is unsupported.",
    2005: "The source draft task has not succeeded.",
    2006: "The upstream task type is unsupported for this operation.",
    2007: "The upstream task has not succeeded.",
    2008: "The provider rejected the input under its content policy.",
    2009: "The provider reported invalid prompt characters.",
    2010: "The API account has insufficient credits.",
    2013: "The requested priority is invalid.",
    2014: "The provider's content audit failed; contact support with the request ID.",
    2015: "The model version is deprecated.",
    2016: "The request type is deprecated.",
    2017: "The model version is invalid.",
    2018: "The model is too complex to remesh.",
    2019: "An uploaded input file was not found or has expired.",
    2020: "An input image URL is invalid or inaccessible.",
    2021: "An input file exceeds the size limit.",
    2022: "An input image exceeds the dimension limit.",
    4001: "The requested API endpoint does not exist.",
}


def _failure_diagnostics(data: dict[str, Any]) -> dict[str, Any]:
    """Validate the child result again before storing or displaying diagnostics."""
    result = {}
    for key, low, high in (("http_status", 100, 599), ("api_code", 1, 999999)):
        value = data.get(key)
        if type(value) is int and low <= value <= high:
            result[key] = value
    request_id = data.get("request_id")
    if isinstance(request_id, str) and REQUEST_ID_RE.fullmatch(request_id):
        result["request_id"] = request_id
    return result


def _failure_detail(diagnostics: dict[str, Any]) -> str:
    parts = []
    if "http_status" in diagnostics:
        parts.append("HTTP %s" % diagnostics["http_status"])
    if "api_code" in diagnostics:
        parts.append("API code %s" % diagnostics["api_code"])
    if "request_id" in diagnostics:
        parts.append("request_id=%s" % diagnostics["request_id"])
    detail = (" [" + "; ".join(parts) + "]") if parts else ""
    hint = API_ERROR_HINTS.get(diagnostics.get("api_code"))
    return detail + (" " + hint if hint else "")


def discover_executable(explicit: str | Path | None = None) -> Path | None:
    """Discover GUI-safe npm installs without reading config/credential files."""
    requested = explicit or os.environ.get("TRIPO_CLI")
    if requested:
        candidate = Path(str(requested)).expanduser()
        found = shutil.which(str(requested)) if not candidate.is_file() else str(candidate)
        return Path(found).absolute() if found else None
    found = shutil.which("tripo")
    if found:
        return Path(found).absolute()
    candidates: list[Path] = []
    for name in ("NPM_CONFIG_PREFIX", "npm_config_prefix", "NPM_PREFIX", "VOLTA_HOME"):
        if os.environ.get(name):
            prefix = Path(os.environ[name]).expanduser()
            candidates.extend((prefix / "bin/tripo", prefix / "tripo.cmd"))
    home = Path.home()
    candidates.extend((home / ".npm-global/bin/tripo", home / ".local/bin/tripo", home / ".volta/bin/tripo"))
    for root, pattern in ((home / ".nvm/versions/node", "*/bin/tripo"),
                          (home / ".local/share/fnm/node-versions", "*/installation/bin/tripo"),
                          (home / "Library/Application Support/fnm/node-versions", "*/installation/bin/tripo")):
        candidates.extend(sorted(root.glob(pattern), reverse=True))
    candidates.extend((Path("/opt/homebrew/bin/tripo"), Path("/usr/local/bin/tripo"), Path("/usr/bin/tripo")))
    if os.environ.get("APPDATA"):
        candidates.append(Path(os.environ["APPDATA"]) / "npm/tripo.cmd")
    return next((path.absolute() for path in candidates if path.is_file()), None)


def _json_value(value: Any) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise InvalidPlan("Parameters must contain finite JSON values.") from exc
    return value if isinstance(value, str) else encoded


def _role(value: Any) -> str:
    value = str(value or "").lower()
    return "back" if value == "rear" else value


class TripoProvider:
    """Store owns approval, job states and remote IDs; this class owns transport."""

    def __init__(self, project: Path, executable: str | Path | None = None):
        self.project = Path(project).expanduser().resolve()
        self.executable = discover_executable(executable)

    def _path(self, raw: str | Path, *, existing: bool = True) -> Path:
        text = str(raw)
        if text.startswith("res://"):
            text = text[6:]
        path = Path(text).expanduser()
        path = (path if path.is_absolute() else self.project / path).resolve()
        if not path.is_relative_to(self.project):
            raise InvalidPlan("Asset paths must remain inside the project.")
        if existing and not path.is_file():
            raise InvalidPlan("An input asset file is missing.")
        return path

    def _layout(self) -> tuple[Path, Path, str]:
        if not self.executable:
            raise ProviderUnavailable("Tripo CLI was not found. Configure TRIPO_CLI or install the official CLI.")
        entry = self.executable.resolve()
        roots = [entry.parent.parent, self.executable.parent / "node_modules/tripo-cli",
                 self.executable.parent.parent / "lib/node_modules/tripo-cli"]
        package_root = None
        version = ""
        for root in roots:
            try:
                info = json.loads((root / "package.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if info.get("name") == "tripo-cli" and (root / "dist/core/client.js").is_file():
                package_root, version = root.resolve(), str(info.get("version", ""))
                break
        if package_root is None:
            raise ProviderUnavailable("Cannot locate the official Tripo CLI package behind the executable.")
        candidates = [self.executable.parent / "node", self.executable.parent / "node.exe"]
        found = shutil.which("node")
        if found:
            candidates.append(Path(found))
        node = next((candidate for candidate in candidates if candidate.is_file()), None)
        if node is None:
            raise ProviderUnavailable("Node.js is required by the official Tripo CLI.")
        return node, package_root, version

    def available(self) -> dict[str, Any]:
        """Local check only: neither login nor a network/account query is made."""
        try:
            _, _, version = self._layout()
            compatible = version == "0.5.1"
            return {"available": compatible, "installed": True, "version": version,
                    "authenticated": None, "authentication": "not_checked", "transport": "official_cli_modules",
                    "single_attempt_submission": compatible,
                    "reason": None if compatible else "CLI version requires transport compatibility review."}
        except ProviderUnavailable as exc:
            return {"available": False, "authenticated": None, "reason": str(exc)}

    def _files(self, plan: dict[str, Any], stage: str) -> list[dict[str, str]]:
        files = []
        for item in plan.get("inputs", []):
            for file in item.get("files", []):
                raw = file.get("path")
                if not raw:
                    continue
                role = _role(file.get("role"))
                if role in ("", "image"):
                    role = _role(item.get("role")) or "image"
                path = self._path(raw)
                if path.suffix.lower() not in IMAGE_EXTENSIONS:
                    continue
                if role in ("preview", "thumbnail", "model"):
                    continue
                files.append({"path": str(path), "role": role})
        if stage == "text-to-image":
            if files:
                raise InvalidPlan("Concept generation is text-to-image and must not have image inputs.")
            return []
        if stage == "image-to-image":
            if not files:
                raise InvalidPlan("Subject/view generation requires at least one reference image.")
            # Preserve the DAG's order: prompts can refer to image[1], image[2].
            # Provider-specific limits are checked after selecting the model.
            return files
        if stage != "multiview-to-model":
            if len(files) != 1:
                raise InvalidPlan("This image stage requires exactly one source image.")
            return files
        roles = [file["role"] for file in files]
        if any(role not in VIEW_ORDER for role in roles):
            raise InvalidPlan("Model inputs require explicit front/left/back/right roles; filenames are not used.")
        if len(set(roles)) != len(roles):
            raise InvalidPlan("There must be only one image per view role.")
        if "front" not in roles or not 2 <= len(roles) <= 4:
            raise InvalidPlan("Model generation needs front plus at least one other view, up to four views.")
        return sorted(files, key=lambda file: VIEW_ORDER.index(file["role"]))

    def _compile(self, plan: dict[str, Any]) -> dict[str, Any]:
        kind = str(plan.get("kind", "")).replace("_", "-")
        stage = KINDS.get(kind)
        if stage is None:
            raise InvalidPlan("Unsupported Tripo generation node kind.")
        prompt = plan.get("prompt", "") or ""
        if not isinstance(prompt, str):
            raise InvalidPlan("Prompt must be text.")
        # The current V3 image endpoints publish no hard 1024-character cap.
        # Text-to-image recommends <=600 English words / <=300 Chinese chars;
        # this is guidance, not a rejection rule. Preserve historical prompts
        # verbatim and leave model-specific limits to the provider. The CLI's
        # 1024-character generation validator is for 3D model requests only.
        params = dict(plan.get("params") or {})
        options = params.pop("options", {})
        if not isinstance(options, dict):
            raise InvalidPlan("params.options must be an object.")
        params = {**options, **params}
        model = params.pop("model", None)
        for key, value in params.items():
            if not isinstance(key, str) or not SAFE_KEY_RE.fullmatch(key) or SECRET_KEY_RE.search(key):
                raise InvalidPlan("Unsupported or sensitive parameter name.")
            if key in {"input", "inputs", "prompt", "model_version", "endpoint", "type", "profile"}:
                raise InvalidPlan("Input, prompt and model fields are controlled by the plan.")
            _json_value(value)
        if stage == "text-to-image":
            if not prompt.strip():
                raise InvalidPlan("Concept generation requires a prompt.")
            model = model or "seedream_v4"
        elif stage == "image-to-image":
            if not prompt.strip() and not params.get("template"):
                raise InvalidPlan("Subject/view generation requires a prompt or an image template.")
            model = model or "seedream_v5"
            if model == "seedream_v4":
                raise InvalidPlan("seedream_v4 is not supported by image-to-image.")
        elif stage == "image-to-multiview":
            if model or params:
                raise InvalidPlan("Image-to-multiview accepts its source image only, with no model/options.")
            if prompt:
                raise InvalidPlan("Image-to-multiview has no prompt parameter; use a view node for prompted views.")
        if stage in ("text-to-image", "image-to-image"):
            if not isinstance(model, str) or model in MODEL_ALIASES or model.startswith(("P1-", "P2-", "tripo-", "v3.")):
                raise InvalidPlan("Image stages require an image model, not a 3D geometry model.")
        warnings = []
        if stage == "multiview-to-model":
            if prompt:
                raise InvalidPlan("Multiview model generation uses images; do not attach an unsupported prompt.")
            model = MODEL_ALIASES.get(model or "tripo-p2")
            if model is None:
                raise InvalidPlan("Unsupported geometry model; use P2, P1 or tripo-v3.1.")
            for key in ("quad", "texture", "pbr", "auto_size", "export_uv", "delight"):
                if key in params and type(params[key]) is not bool:
                    raise InvalidPlan(f"{key} must be a boolean.")
            if model.startswith("P"):
                forbidden = {"smart_low_poly", "generate_parts", "geometry_quality"}
                if model.startswith("P1"):
                    forbidden.add("quad")
                if forbidden.intersection(params):
                    raise InvalidPlan("This P-series model does not support one or more requested options.")
            if "face_limit" in params:
                face = params["face_limit"]
                # Godot JSON represents numbers as doubles; accept exact integers.
                if type(face) is float and math.isfinite(face) and face.is_integer():
                    face = int(face)
                    params["face_limit"] = face
                low, high = (48, 25000 if params.get("quad") else 50000) if model.startswith("P2") else (50, 20000) if model.startswith("P1") else (1, 2000000)
                if type(face) is not int or not low <= face <= high:
                    raise InvalidPlan(f"face_limit must be an integer from {low} to {high} for this model.")
            if params.get("texture") is False and params.get("pbr", True) is True:
                raise InvalidPlan("texture=false also requires pbr=false; PBR otherwise forces texturing.")
            if params.get("quad"):
                warnings.append("quad=true produces FBX, not GLB; engine import will triangulate quads.")
            if params.get("texture_quality") == "fast":
                # Current API accepts fast, but the verified CLI 0.5.1 builder
                # still rejects it. Do not silently submit a different quality.
                raise InvalidPlan("CLI 0.5.1 does not yet support texture_quality=fast.")
            if "texture_quality" in params and params["texture_quality"] not in {"standard", "detailed", "extreme"}:
                raise InvalidPlan("Invalid texture quality.")
        files = self._files(plan, stage)
        if stage == "image-to-image":
            limit = 4 if model.startswith("seedream") else 10 if model.startswith(("banana", "gemini")) else 16 if model.startswith("chat_image") else 1
            if len(files) > limit:
                raise InvalidPlan(f"This image model supports at most {limit} reference images.")
            if len(files) > 1:
                warnings.append("Reference order follows the saved input edges/files; use image[1], image[2], etc. in the prompt.")
        placeholders = [f"<upload:{file['path']}>" for file in files]
        payload = dict(params)
        if model:
            payload["model"] = model
        if stage in ("text-to-image", "image-to-image") and prompt:
            payload["prompt"] = prompt
        if stage == "multiview-to-model":
            payload["inputs"] = [{file["role"]: value} for file, value in zip(files, placeholders)]
        elif stage == "image-to-image" and len(placeholders) > 1:
            payload["inputs"] = placeholders
        elif stage != "text-to-image":
            payload["input"] = placeholders[0]
        return {"stage": stage, "prompt": prompt, "model": model, "params": params, "files": files,
                "expected_payload": payload, "endpoint": f"/v3/generation/{stage}", "warnings": warnings}

    def preview(self, plan: dict[str, Any]) -> dict[str, Any]:
        """Pure local preview: no process, uploads, network, directory or file writes."""
        spec = self._compile(plan)
        return {"provider": "tripo", "transport": "official_cli_modules", "endpoint": spec["endpoint"],
                "api_payload": spec["expected_payload"], "inputs": spec["files"], "cost": "unknown",
                "warnings": spec["warnings"], "submission_attempts": 1,
                "note": "Upload placeholders are replaced with file tokens only during an approved submission."}

    def _helper_command(self) -> list[str]:
        node, root, version = self._layout()
        if version != "0.5.1":
            raise ProviderUnavailable("CLI version changed; review the no-retry transport before submitting.")
        return [str(node), str(Path(__file__).with_name("tripo_transport.mjs")), str(root)]

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self.executable:
            env["PATH"] = str(self.executable.parent) + os.pathsep + env.get("PATH", "")
        return env

    def submit(self, plan: dict[str, Any]) -> dict[str, Any]:
        spec = self._compile(plan)
        command = self._helper_command()  # Fail locally before declaring uncertainty.
        spec["operation"] = "submit"
        try:
            result = subprocess.run(command, input=json.dumps(spec, ensure_ascii=False), capture_output=True,
                                    text=True, cwd=self.project, env=self._env(), timeout=180, check=False)
        except (FileNotFoundError, PermissionError) as exc:
            raise ProviderUnavailable("Could not start the official Tripo transport.") from exc
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise SubmissionUncertain("Submission has no confirmed task ID. Do not resubmit; reconcile the remote task first.") from exc
        try:
            data = json.loads(result.stdout)
        except (ValueError, TypeError):
            data = {}
        task_id = data.get("task_id") if isinstance(data, dict) else None
        diagnostics = _failure_diagnostics(data) if isinstance(data, dict) else {}
        detail = _failure_detail(diagnostics)
        if isinstance(data, dict) and data.get("failure_phase") == "pre_submit":
            raise ProviderUnavailable("Tripo failed before submitting. Check login, input upload or CLI compatibility; no generation was submitted." + detail, diagnostics)
        if isinstance(data, dict) and data.get("failure_phase") == "rejected":
            raise SubmissionRejected("Tripo rejected this request. Check API balance, authorization and parameters before trying again." + detail, diagnostics)
        if not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id):
            raise SubmissionUncertain("Submission has no confirmed task ID. Do not resubmit; reconcile the remote task first." + detail, diagnostics)
        return {"task_id": task_id, "status": "queued", "provider": "tripo", "credits_consumed": None,
                "type": data.get("type", spec["stage"].replace("-", "_"))}

    def poll(self, task_id: str) -> dict[str, Any]:
        """Poll one exact ID persisted by Store; never use @last or list/history."""
        if not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id):
            raise InvalidPlan("Polling requires an explicit saved Tripo task ID, not a history alias.")
        node, root, _ = self._layout()
        command = [str(node), str(root / "dist/cli.js"), "--json", "--quiet", "--no-open", "task", "get", task_id]
        try:
            result = subprocess.run(command, capture_output=True, text=True, cwd=self.project, env=self._env(), timeout=45, check=False)
            if result.returncode:
                raise TripoError("Could not query the saved task. Polling can be retried without generating again.")
            data = json.loads(result.stdout)
        except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
            raise TripoError("Could not query the saved task. Polling can be retried without generating again.") from exc
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            data = data["data"]
        if not isinstance(data, dict) or data.get("task_id") != task_id:
            raise TripoError("Task response did not match the saved task ID.")
        statuses = {"queued", "running", "success", "failed", "cancelled", "canceled", "expired", "pending", "processing", "banned"}
        status = data.get("status")
        if status not in statuses:
            raise TripoError("Unrecognized remote task status; retain the task ID and inspect before proceeding.")
        progress = data.get("progress")
        credits = data.get("credits_consumed")
        return {"task_id": task_id, "status": status, "progress": progress if isinstance(progress, (int, float)) else None,
                "credits_consumed": credits if isinstance(credits, (int, float)) and math.isfinite(credits) else None,
                "outputs": self._outputs(data.get("output") or {}), "provider": "tripo"}

    @staticmethod
    def _outputs(output: Any) -> list[dict[str, str]]:
        results: list[dict[str, str]] = []
        urls: set[str] = set()
        names: set[str] = set()

        def visit(value: Any, field: str) -> None:
            if isinstance(value, dict):
                for key, nested in value.items():
                    visit(nested, f"{field}.{key}" if field else str(key))
            elif isinstance(value, list):
                for index, nested in enumerate(value):
                    visit(nested, f"{field}_{index}")
            elif isinstance(value, str) and value.startswith(("https://", "http://")) and value not in urls:
                parsed = urlsplit(value)
                extension = Path(parsed.path).suffix.lower()
                tokens = re.split(r"[^a-z]+", field.lower())
                view = next((role for role in VIEW_ORDER if role in tokens), None)
                if view:
                    role = view
                elif any(token in tokens for token in ("preview", "rendered", "thumbnail")):
                    role = "preview"
                elif extension == ".zip":
                    role = "archive"
                elif extension in MODEL_EXTENSIONS or "model" in tokens:
                    role = "model"
                elif any(token in tokens for token in ("texture", "normal", "metallic", "roughness", "albedo")):
                    role = "texture"
                else:
                    role = "image"
                if not re.fullmatch(r"\.[a-z0-9]{1,8}", extension):
                    extension = ".png" if role in {"image", "preview", *VIEW_ORDER} else ".bin"
                basename = view or re.sub(r"[^a-zA-Z0-9_-]+", "_", re.sub(r"(?:_url|\.url)$", "", field)).strip("_") or role
                filename = basename + extension
                counter = 2
                while filename in names:
                    filename = f"{basename}_{counter}{extension}"
                    counter += 1
                names.add(filename)
                urls.add(value)
                results.append({"url": value, "role": role, "filename": filename})
        visit(output, "")
        return results

    def download(self, status: dict[str, Any], directory: str | Path) -> list[dict[str, str]]:
        """Atomic downloads only. Expired URLs need a fresh poll, never regeneration."""
        if status.get("status") != "success":
            raise TripoError("Only a successful task can be downloaded.")
        outputs = status.get("outputs") or []
        if not outputs:
            raise TripoError("Successful task returned no downloadable output.")
        target = Path(directory).expanduser()
        target = (target if target.is_absolute() else self.project / target).resolve()
        target.mkdir(parents=True, exist_ok=True)
        files = []
        for item in outputs:
            filename = item.get("filename", "")
            if not filename or filename != Path(filename).name or filename in {".", ".."} or "\\" in filename:
                raise TripoError("Invalid artifact filename.")
            url = item.get("url", "")
            parsed = urlsplit(url)
            if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
                raise TripoError("Invalid artifact URL.")
            destination = (target / filename).resolve()
            if not destination.is_relative_to(target):
                raise TripoError("Artifact destination escapes the download directory.")
            temporary = None
            try:
                with urlopen(Request(url, headers={"User-Agent": "GodotAssetPipeline/1.0"}), timeout=60) as response:
                    with tempfile.NamedTemporaryFile(dir=target, prefix=".download-", suffix=".part", delete=False) as stream:
                        temporary = Path(stream.name)
                        digest = hashlib.sha256()
                        size = 0
                        while True:
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            size += len(chunk)
                            if size > 1024 * 1024 * 1024:
                                raise TripoError("Artifact exceeds the 1 GiB download limit.")
                            stream.write(chunk)
                            digest.update(chunk)
                        if not size:
                            raise TripoError("Downloaded artifact is empty.")
                        expected = response.headers.get("Content-Length")
                        if expected and expected.isdigit() and int(expected) != size:
                            raise TripoError("Artifact download was incomplete.")
                    temporary.replace(destination)
                    temporary = None
                saved_path = destination.relative_to(self.project).as_posix() if destination.is_relative_to(self.project) else str(destination)
                files.append({"path": saved_path, "role": item["role"], "sha256": digest.hexdigest()})
            except (OSError, ValueError) as exc:
                raise TripoError("Artifact download failed. Refresh the saved task and retry downloading only.") from exc
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        return files
