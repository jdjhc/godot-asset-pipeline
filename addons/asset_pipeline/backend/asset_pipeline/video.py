"""Local, lossless video sampling and user-labelled multiview imports. No provider calls."""
import hashlib
import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path


def sample(store, params):
    count = params.get("count", 20)
    if isinstance(count, bool) or not isinstance(count, int) or not 4 <= count <= 100:
        raise ValueError("抽帧数量必须为 4–100 的整数")
    source = store._source_path(params["path"])
    session = "video_" + uuid.uuid4().hex
    folder = store.project / "artifacts/asset_pipeline_video" / session
    folder.mkdir(parents=True)
    copied = folder / ("source" + source.suffix.lower())
    shutil.copy2(source, copied)
    try:
        if sys.platform == "darwin" and shutil.which("swift"):
            subprocess.run(["swift", str(Path(__file__).with_name("sample_video.swift")), str(copied), str(folder), str(count)], check=True, capture_output=True, timeout=75)
            result = json.loads((folder / "frames.json").read_text())
            result["extractor"] = "AVFoundation sequential decoding"
        elif shutil.which("ffmpeg") and shutil.which("ffprobe"):
            probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames", "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(copied)],check=True,capture_output=True,timeout=30)
            frames = json.loads(probe.stdout)["frames"]
            total = len(frames)
            if not total: raise ValueError("视频没有可解码的帧")
            n = min(count,total)
            indices = sorted(set(round(i*(total-1)/max(1,n-1)) for i in range(n)))
            expression = "+".join("eq(n,%d)" % i for i in indices)
            subprocess.run(["ffmpeg", "-v", "error", "-i", str(copied), "-vf", "select='"+expression+"'", "-vsync", "0", str(folder / "sample_%03d.png")],check=True,capture_output=True,timeout=75)
            result = {"total_frames":total,"extractor":"FFmpeg sequential decoding","frames":[{"index":i,"seconds":float(frames[i]["best_effort_timestamp_time"]),"file":"sample_%03d.png"%(j+1)} for j,i in enumerate(indices)]}
        else:
            raise ValueError("视频抽帧需要 FFmpeg；macOS 也可使用 Swift / AVFoundation")
        result.update(session_id=session, source_path=str(copied.relative_to(store.project)), source_sha256=hashlib.sha256(copied.read_bytes()).hexdigest(), requested_count=count)
        for frame in result["frames"]:
            frame["path"] = str((folder/frame["file"]).relative_to(store.project))
        (folder/"manifest.json").write_text(json.dumps(result,ensure_ascii=False,indent=2))
        return result
    except Exception:
        shutil.rmtree(folder,ignore_errors=True)
        raise


def commit(store, params):
    session = params["session_id"]
    if not isinstance(session,str) or not session.startswith("video_") or not session[6:].isalnum():
        raise ValueError("无效视频会话")
    folder = store.project / "artifacts/asset_pipeline_video" / session
    manifest = json.loads((folder/"manifest.json").read_text())
    selection = params.get("selection",{})
    if not isinstance(selection,dict) or set(selection) - {"front","left","back","right"} or "front" not in selection or len(selection)<2:
        raise ValueError("请选择正面和至少另一个方向")
    valid = {f["index"]:f for f in manifest["frames"]}
    if any(type(v) is not int or v not in valid for v in selection.values()) or len(set(selection.values())) != len(selection):
        raise ValueError("每个方向必须选择不同的有效帧")
    receipt = folder/"selection.json"
    if receipt.exists():
        old=json.loads(receipt.read_text())
        if old["selection"] == selection: return old
    label = str(params.get("label") or "视频主体")[:100]
    source = store.create_node({"kind":"reference","label":label+" · 源视频"})
    store.import_version(source["id"],[{"path":manifest["source_path"],"role":"video"}],metadata={"source_sha256":manifest["source_sha256"]})
    edges=[]
    for role,index in selection.items():
        frame=valid[index]
        node=store.create_node({"kind":"reference","label":label+" · "+{"front":"正面","left":"左侧","back":"背面","right":"右侧"}[role],"inputs":[{"node_id":source["id"],"role":"source_video"}]})
        store.import_version(node["id"],[{"path":frame["path"],"role":"image"}],metadata={"source":"user_selected_video_frame","source_sha256":manifest["source_sha256"],"frame_index_zero_based":index,"seconds":frame["seconds"],"view":role,"extractor":manifest["extractor"]})
        edges.append({"node_id":node["id"],"role":role})
    model=store.create_node({"kind":"model","label":label+" · 视频多视图模型","prompt":"","params":{"model":"P2-20260801","quad":False,"face_limit":16000,"texture":True,"pbr":True},"inputs":edges})
    result={"selection":selection,"model_node_id":model["id"],"inputs":edges}
    receipt.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return result


def thumbnail(store, params):
    """Decode only the first frame; cache by source identity, never call providers."""
    source = store._source_path(params["path"])
    if source.suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".ogv"}:
        raise ValueError("不是支持的视频文件")
    stat = source.stat()
    key = hashlib.sha256(f"{source}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()
    folder = store.project / ".godot/asset_pipeline_video_previews"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (key + ".png")
    if not target.exists():
        temporary = folder / (key + ".tmp.png")
        try:
            if shutil.which("ffmpeg"):
                command = ["ffmpeg", "-v", "error", "-y", "-i", str(source), "-frames:v", "1", "-vf", "scale=640:640:force_original_aspect_ratio=decrease", str(temporary)]
            elif sys.platform == "darwin" and shutil.which("swift"):
                command = ["swift", str(Path(__file__).with_name("video_thumbnail.swift")), str(source), str(temporary)]
            else:
                return {"path": "", "message": "安装 FFmpeg 后可显示视频首帧"}
            subprocess.run(command, check=True, capture_output=True, timeout=45)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return {"path": str(target.relative_to(store.project))}
