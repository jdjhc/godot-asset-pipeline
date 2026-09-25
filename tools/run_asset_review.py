#!/usr/bin/env python3
"""Render local GLBs in an isolated Godot project; never imports into the editor.

Example after all generation tasks have completed:
  python3 tools/run_asset_review.py --godot /path/to/Godot \
    --output-dir /absolute/review --manifest /absolute/generated_asset_paths.json

Manifest accepts {"assets": [{"id": "arcade", "path": "/absolute/arcade.glb",
"version_id": "...", "front_axis": "+z"}]} or {"arcade": "/absolute/arcade.glb"}.
Alternatively, supply one or more absolute GLB paths as positional arguments.
Use --check-only to validate the script without loading any generated assets.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("models", nargs="*")
    parser.add_argument("--godot", required=True)
    parser.add_argument("--manifest")
    parser.add_argument("--output-dir")
    parser.add_argument("--size", type=int, default=1024)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--extra-views", action="store_true", help="Also render top and three-quarter views, useful for flat terrain")
    args = parser.parse_args()
    if not args.check_only and (not args.output_dir or not (args.models or args.manifest)):
        parser.error("Supply --output-dir and either --manifest or one or more GLB paths")
    script = Path(__file__).resolve().with_name("render_asset_review.gd")
    with tempfile.TemporaryDirectory(prefix="godot-asset-review-") as temporary:
        project = Path(temporary)
        (project / "project.godot").write_text('''config_version=5
[application]
config/name="Offline Asset Review"
[rendering]
renderer/rendering_method="gl_compatibility"
''')
        command = [str(Path(args.godot).expanduser()), "--path", str(project), "--script", str(script)]
        if args.check_only:
            command[1:1] = ["--headless", "--check-only"]
        else:
            command.extend(["--", "--output-dir=" + str(Path(args.output_dir).resolve()),
                            "--width=" + str(args.size), "--height=" + str(args.size)])
            if args.manifest:
                command.append("--manifest=" + str(Path(args.manifest).resolve()))
            if args.extra_views:
                command.append("--extra-views")
            command.extend(str(Path(model).resolve()) for model in args.models)
        result = subprocess.run(command, text=True, capture_output=True, timeout=args.timeout)
        log = result.stdout + result.stderr
        print(log, end="")
        if not args.check_only:
            output = Path(args.output_dir).resolve()
            output.mkdir(parents=True, exist_ok=True)
            (output / "render.log").write_text(log)
        if result.returncode or "SCRIPT ERROR" in log or "ERROR:" in log:
            raise SystemExit(result.returncode or 1)
        if not args.check_only and "ASSET_REVIEW_COMPLETE " not in log:
            raise SystemExit("Renderer exited without completing the review report")


if __name__ == "__main__":
    main()
