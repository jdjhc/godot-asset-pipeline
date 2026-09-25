#!/usr/bin/env python3
"""Run isolated Godot preview regression against real images and generated GLBs.

Example:
python3 tests/run_preview_probe.py --godot /path/to/Godot --assets /path/to/assets/tripo_urban
Nothing is generated remotely and no user scene or service is started.
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--godot", required=True)
    parser.add_argument("--assets", required=True)
    parser.add_argument("--visible", action="store_true", help="Run with an actual renderer/window instead of headless")
    parser.add_argument("--output-dir", help="Persist framebuffer evidence here when --visible is enabled")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    assets = Path(args.assets)
    mapping = {
        "model_a.glb": assets / "newsstand_multiview_v1.glb",
        "model_b.glb": assets / "newsstand_smartmesh_v1.glb",
        "image_a.png": assets / "references/newsstand_front_v1.png",
        "image_b.png": assets / "references/newsstand_rear_v1.png",
        "image_c.png": assets / "references/newsstand_right_v1.png",
    }
    for path in mapping.values():
        if not path.is_file():
            raise SystemExit("Missing real probe input: " + str(path))
    with tempfile.TemporaryDirectory(prefix="godot-asset-preview-probe-") as temporary:
        project = Path(temporary)
        target = project / "addons/asset_pipeline"
        target.mkdir(parents=True)
        for name in ("asset_preview.gd", "dependency_assets.gd", "panel.gd"):
            shutil.copy2(root / "addons/asset_pipeline" / name, target / name)
        shutil.copy2(root / "tests/godot_preview_probe.gd", project / "probe.gd")
        for name, path in mapping.items():
            shutil.copy2(path, project / name)
        (project / "project.godot").write_text('''config_version=5
[application]
config/name="Asset Preview Regression"
[display]
window/size/viewport_width=1440
window/size/viewport_height=960
[rendering]
renderer/rendering_method="gl_compatibility"
''')
        imported = subprocess.run([args.godot, "--headless", "--path", str(project), "--editor", "--quit"],
                                  text=True, capture_output=True, timeout=120)
        if imported.returncode or "SCRIPT ERROR" in imported.stdout + imported.stderr:
            print(imported.stdout + imported.stderr)
            raise SystemExit(imported.returncode or 1)
        command = [args.godot, "--path", str(project), "--script", "res://probe.gd"]
        if not args.visible:
            command.insert(1, "--headless")
        if args.output_dir:
            command.extend(["--", "--output-dir=" + str(Path(args.output_dir).resolve())])
        probe = subprocess.run(command, text=True, capture_output=True, timeout=120)
        output = probe.stdout + probe.stderr
        print(output)
        if args.output_dir:
            evidence = Path(args.output_dir).resolve()
            evidence.mkdir(parents=True, exist_ok=True)
            (evidence / "probe.log").write_text(output)
        if probe.returncode or "ASSET_PREVIEW_PROBE_PASS" not in output or "SCRIPT ERROR" in output or "ERROR:" in output:
            raise SystemExit(probe.returncode or 1)


if __name__ == "__main__":
    main()
