#!/usr/bin/env python3
"""Wrap an already-built native ARM64 executable in a self-contained macOS .app.

The recompiled binary embeds the original PE image, so the only external data a
game needs is its original asset folder. Those files go into Contents/Resources
and the launcher changes directory there before exec, making the bundle
independent of where the original game was downloaded.

This is packaging, not validation: run tools.game_runner + tools.frame_proof on
the produced launcher to obtain evidence that the app actually renders.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path


PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key><string>en</string>
  <key>CFBundleExecutable</key><string>{launcher}</string>
  <key>CFBundleIdentifier</key><string>com.gamerecomp.{ident}</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleName</key><string>{name}</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
"""

LAUNCHER = """#!/bin/sh
# Launch the recompiled native game from the bundle's Resources folder.
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../Resources" || exit 1
exec "$HERE/{binary}" "$@"
"""


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def make_app(binary: Path, game_dir: Path, out: Path, name: str) -> Path:
    if not binary.is_file():
        raise ValueError(f"native binary not found: {binary}")
    if not game_dir.is_dir():
        raise ValueError(f"game asset folder not found: {game_dir}")
    app = out / f"{name}.app"
    macos = app / "Contents" / "MacOS"
    resources = app / "Contents" / "Resources"
    if app.exists():
        shutil.rmtree(app)
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)

    exe_name = binary.name
    shutil.copy2(binary, macos / exe_name)
    for item in sorted(game_dir.iterdir()):
        dst = resources / item.name
        if item.is_dir():
            shutil.copytree(item, dst)
        elif item.is_file():
            shutil.copy2(item, dst)

    launcher_name = "".join(c if c.isalnum() else "" for c in name) or "Launch"
    launcher = macos / launcher_name
    launcher.write_text(LAUNCHER.format(binary=exe_name))
    launcher.chmod(0o755)
    (app / "Contents" / "Info.plist").write_text(
        PLIST.format(launcher=launcher_name,
                     ident=launcher_name.lower(), name=name))
    (app / "Contents" / "manifest.json").write_text(json.dumps({
        "native_binary": exe_name,
        "native_sha256": sha256(macos / exe_name),
        "assets": sorted(p.name for p in resources.iterdir()),
        "launcher": launcher_name,
    }, indent=2) + "\n")
    return app


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--binary", required=True, type=Path, help="built native executable")
    ap.add_argument("--game-dir", required=True, type=Path, help="original game's asset folder")
    ap.add_argument("--out", required=True, type=Path, help="directory to write the .app into")
    ap.add_argument("--name", default="GameRecompGame")
    args = ap.parse_args(argv)
    try:
        app = make_app(args.binary, args.game_dir, args.out.resolve(), args.name)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    manifest = json.loads((app / "Contents" / "manifest.json").read_text())
    print(f"wrote {app}")
    print(f"launcher: {app / 'Contents' / 'MacOS' / manifest['launcher']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
