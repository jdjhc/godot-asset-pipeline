#!/usr/bin/env python3
"""Import the existing local demonstration history. Never submits generation."""
import argparse
import json
from pathlib import Path
import sys

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "addons/asset_pipeline/backend"))
from asset_pipeline.store import Store


def seed(project):
    project = Path(project).resolve()
    store = Store(str(project))
    if any(n["id"] == "zzz_reference" for n in store.snapshot()["nodes"]):
        return {"seeded": False, "reason": "示例已存在，保留现有编辑"}
    references = project / "assets/tripo_urban/references"
    required = [project / "artifacts/tripo_urban/sixth_street_reference.jpg"]
    required.extend(references / ("newsstand_" + name + "_v1.png") for name in ("subject", "front", "right", "rear"))
    required.extend(references / ("newsstand_" + name + "_v1_prompt.txt") for name in ("subject", "front", "right", "rear"))
    required.extend(project / "assets/tripo_urban" / file for file in ("newsstand_multiview_v1.glb", "newsstand_smartmesh_v1.glb"))
    for path in required:
        if not path.is_file():
            raise ValueError("缺少已有示例文件: " + str(path))
    def create(id, label, kind, position, inputs=None, prompt="", params=None):
        return store.create_node({"id": id, "label": label, "kind": kind, "position": position,
            "inputs": inputs or [], "prompt": prompt, "params": params or {}})
    create("zzz_reference", "六分街 · 原始参考", "reference", [20, 250], params={})
    store.import_version("zzz_reference", [{"path": str(required[0]), "role": "image"}],
        {"source": "imported_game_reference", "source_url": "https://nexushub.co.za/images/gallery/00015/15728_sixth_street.jpg",
         "note": "外部游戏截图；原始生成提示词不存在。仅用于本地演示，不包含在插件发布包中。"})
    create("zzz_subject", "报刊亭 · 主体提取", "subject", [340, 250],
        [{"node_id": "zzz_reference", "role": "reference"}],
        (references / "newsstand_subject_v1_prompt.txt").read_text())
    store.import_version("zzz_subject", [{"path": str(references / "newsstand_subject_v1.png"), "role": "image"}],
        {"provider": "imagegen", "source": "existing_local_output", "note": "实际由内置生图工具生成；此时只导入记录。"})
    for name, role, y, label in [("front", "front", 20, "正面"), ("right", "right", 280, "右侧"), ("rear", "back", 540, "背面 · 推测补全")]:
        inputs = [{"node_id": "zzz_subject", "role": "reference"}]
        if name != "front":
            inputs.append({"node_id": "zzz_front", "role": "reference"})
        create("zzz_" + name, label, "view", [680, y], inputs,
            (references / ("newsstand_" + name + "_v1_prompt.txt")).read_text(), {"view": role})
        store.import_version("zzz_" + name, [{"path": str(references / ("newsstand_" + name + "_v1.png")), "role": role}],
            {"provider": "imagegen", "source": "existing_local_output", "inferred_hidden_surface": name == "rear"})
    create("zzz_model", "报刊亭 · 3D 资产", "model", [1040, 250],
        [{"node_id": "zzz_front", "role": "front"}, {"node_id": "zzz_right", "role": "right"}, {"node_id": "zzz_rear", "role": "back"}],
        params={"model": "v3.1", "face_limit": 20000, "texture": True, "pbr": True})
    old = store.import_version("zzz_model", [{"path": "assets/tripo_urban/newsstand_multiview_v1.glb", "role": "model"}],
        {"provider": "tripo_studio", "task_id": "ce3ba88d-9d19-44b2-a204-47c4f3dae6b3", "source": "existing_local_output", "model_display": "H3.1"})
    store.update_node("zzz_model", {"params": {"model": "P2-20260801", "quad": True, "face_limit": 8000, "texture": True, "pbr": True}})
    current = store.import_version("zzz_model", [{"path": "assets/tripo_urban/newsstand_smartmesh_v1.glb", "role": "model"}],
        {"provider": "tripo_studio", "task_id": "1d383ed1-a066-4b0c-805c-0b956ef11d73", "source": "existing_local_output", "model_display": "P2.0",
         "source_manifest": "assets/tripo_urban/newsstand_smartmesh_manifest.json", "note": "实际历史来自网页会员生成，不计为 API 新任务。"})
    return {"seeded": True, "nodes": 6, "model_versions": [old["id"], current["id"]], "credits_spent": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    print(json.dumps(seed(parser.parse_args().project), ensure_ascii=False, indent=2))
