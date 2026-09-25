#!/usr/bin/env python3
"""Run the batch UI regression in an isolated headless project with mocked RPC."""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--godot", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="godot-batch-ui-probe-") as temporary:
        project = Path(temporary)
        target = project / "addons/asset_pipeline"
        target.mkdir(parents=True)
        for name in ("asset_preview.gd", "dependency_assets.gd", "panel.gd"):
            shutil.copy2(root / "addons/asset_pipeline" / name, target / name)
        shutil.copy2(root / "tests/godot_batch_probe.gd", project / "probe.gd")
        (project / "project.godot").write_text('''config_version=5
[application]
config/name="Batch UI Regression"
[display]
window/size/viewport_width=1440
window/size/viewport_height=960
[rendering]
renderer/rendering_method="gl_compatibility"
''')
        result = subprocess.run([args.godot, "--headless", "--path", str(project), "--script", "res://probe.gd"],
                                text=True, capture_output=True, timeout=60)
        output = result.stdout + result.stderr
        print(output)
        if result.returncode or "ASSET_BATCH_UI_PROBE_PASS" not in output or "SCRIPT ERROR" in output or "ERROR:" in output:
            raise SystemExit(result.returncode or 1)


if __name__ == "__main__":
    main()
