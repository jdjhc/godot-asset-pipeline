#!/usr/bin/env python3
"""Verify raw GLB previews are read-only in an isolated real Godot editor process."""
import argparse
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import zlib


def fixture_glb():
    def png_chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))
    image = (b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
             + png_chunk(b"IDAT", zlib.compress(b"\0\xff\0\0\0\xff\0\0\0\0\xff\xff\xff\xff")) + png_chunk(b"IEND", b""))
    blobs = [struct.pack("<9f", -1, 0, 0, 1, 0, 0, 0, 1, 0),
             struct.pack("<6f", 0, 0, 1, 0, .5, 1), image]
    binary = b""
    views = []
    for blob in blobs:
        views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(blob)})
        binary += blob + b"\0" * (-len(blob) % 4)
    document = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}], "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "TEXCOORD_0": 1}, "material": 0}]}],
        "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}, "metallicFactor": 0}}],
        "textures": [{"source": 0}], "images": [{"bufferView": 2, "mimeType": "image/png", "name": "embedded_color"}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3", "min": [-1, 0, 0], "max": [1, 1, 0]},
                      {"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC2"}],
        "buffers": [{"byteLength": len(binary)}], "bufferViews": views}
    encoded = json.dumps(document, separators=(",", ":")).encode()
    encoded += b" " * (-len(encoded) % 4)
    chunks = struct.pack("<I4s", len(encoded), b"JSON") + encoded + struct.pack("<I4s", len(binary), b"BIN\0") + binary
    return struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--godot", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="godot-raw-preview-") as temporary:
        project = Path(temporary)
        target = project / "addons/asset_pipeline"
        target.mkdir(parents=True)
        shutil.copy2(root / "addons/asset_pipeline/asset_preview.gd", target / "asset_preview.gd")
        probe = project / "addons/raw_preview_probe"
        probe.mkdir()
        shutil.copy2(root / "tests/godot_raw_preview_probe.gd", probe / "probe.gd")
        (probe / "plugin.cfg").write_text('[plugin]\nname="Raw preview probe"\ndescription="Isolated regression"\nauthor="Test"\nversion="1.0"\nscript="probe.gd"\n')
        (project / "fixture.bin").write_bytes(fixture_glb())
        (project / "project.godot").write_text('config_version=5\n[application]\nconfig/name="Raw preview regression"\n[rendering]\nrenderer/rendering_method="gl_compatibility"\n[editor_plugins]\nenabled=PackedStringArray("res://addons/raw_preview_probe/plugin.cfg")\n')
        try:
            result = subprocess.run([args.godot, "--headless", "--editor", "--path", str(project)],
                                    capture_output=True, text=True, timeout=15)
        except subprocess.TimeoutExpired as error:
            for captured in (error.stdout, error.stderr):
                print(captured.decode(errors="replace") if isinstance(captured, bytes) else captured or "")
            raise SystemExit("Raw preview editor probe timed out")
        output = result.stdout + result.stderr
        print(output)
        if result.returncode or "RAW_PREVIEW_PROBE_PASS" not in output or "SCRIPT ERROR" in output or "ERROR:" in output:
            raise SystemExit(result.returncode or 1)


if __name__ == "__main__":
    main()
