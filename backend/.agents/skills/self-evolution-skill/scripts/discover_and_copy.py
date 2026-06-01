#!/usr/bin/env python3
# version: 1.0.0
# changelog:
#   1.0.0 - 2026-06-01 - Source discovery and copy script for Phase 2

"""
Phase 2 — Source Discovery & Copy
===================================
Scans the origin path, copies all required source files into the workspace,
generates a source manifest, and reads version headers.

Usage:
    python discover_and_copy.py --origin /path/to/source --workspace /tmp/evo/ws-XXX
"""

import os
import re
import sys
import shutil
import hashlib
import argparse
from datetime import datetime, timezone
from pathlib import Path

# File patterns
INCLUDE_EXTENSIONS = {
    ".py", ".js", ".ts", ".java", ".json", ".yaml", ".yml",
    ".md", ".sh", ".env", ".toml", ".xml", ".html", ".css"
}
EXCLUDE_DIRS = {
    "node_modules", "__pycache__", ".git", "dist", "build",
    ".venv", "venv", ".tox", "target", ".gradle"
}
EXCLUDE_FILES = {".DS_Store", "Thumbs.db"}

# Version header patterns (language-agnostic)
VERSION_PATTERNS = [
    re.compile(r"version:\s*([\d]+\.[\d]+\.[\d]+)", re.IGNORECASE),
    re.compile(r"\"_version\":\s*\"([\d]+\.[\d]+\.[\d]+)\""),
    re.compile(r"Version:\s*([\d]+\.[\d]+\.[\d]+)", re.IGNORECASE),
]


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_prefix(path: Path, length: int = 8) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while chunk := f.read(8192):
                h.update(chunk)
        return h.hexdigest()[:length]
    except Exception:
        return "????????"


def extract_version(path: Path) -> str | None:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")[:2000]  # read first 2KB only
        for pattern in VERSION_PATTERNS:
            match = pattern.search(content)
            if match:
                return match.group(1)
    except Exception:
        pass
    return None


def should_include(path: Path) -> bool:
    if path.name in EXCLUDE_FILES:
        return False
    for part in path.parts:
        if part in EXCLUDE_DIRS:
            return False
    return path.suffix.lower() in INCLUDE_EXTENSIONS or path.suffix == ""


def discover_and_copy(origin: Path, workspace: Path) -> dict:
    src_dest = workspace / "src"
    src_dest.mkdir(parents=True, exist_ok=True)

    manifest_entries = []
    version_entries = []
    log_lines = []

    all_files = list(origin.rglob("*"))
    included = [f for f in all_files if f.is_file() and should_include(f)]

    for src_file in sorted(included):
        rel_path = src_file.relative_to(origin)
        dest_file = src_dest / rel_path
        dest_file.parent.mkdir(parents=True, exist_ok=True)

        shutil.copy2(src_file, dest_file)
        size = src_file.stat().st_size
        digest = sha256_prefix(src_file)
        version = extract_version(src_file)

        manifest_entries.append(f"{rel_path} | {size} bytes | {digest}")
        log_lines.append(f"[{iso_now()}] [PHASE-2] FILE_COPIED  src={src_file} dst={dest_file}")

        if version:
            version_entries.append((str(rel_path), version))
            log_lines.append(f"[{iso_now()}] [PHASE-2] VERSION_READ  file=\"{rel_path}\" version=\"{version}\"")

    # Write source manifest
    manifest_path = workspace / "reports" / "source-manifest.txt"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w") as f:
        f.write("SOURCE MANIFEST\n")
        f.write(f"Generated : {iso_now()}\n")
        f.write(f"Origin    : {origin}\n")
        f.write(f"Workspace : {workspace}\n")
        f.write("─" * 60 + "\n")
        for entry in manifest_entries:
            f.write(entry + "\n")
        f.write("─" * 60 + "\n")
        f.write(f"TOTAL: {len(manifest_entries)} files\n")
        if version_entries:
            f.write("\nVERSIONED FILES:\n")
            for path, ver in version_entries:
                f.write(f"  {path} @ {ver}\n")

    # Append to evolution.log
    log_path = workspace / "evolution.log"
    with open(log_path, "a") as f:
        f.write("\n".join(log_lines) + "\n")
        f.write(f"[{iso_now()}] [PHASE-2] DISCOVERY_COMPLETE  files={len(manifest_entries)}\n")

    return {
        "files_copied": len(manifest_entries),
        "versioned_files": version_entries,
        "manifest_path": str(manifest_path),
    }


def main():
    parser = argparse.ArgumentParser(description="Phase 2 — Source Discovery & Copy")
    parser.add_argument("--origin", required=True, help="Path to the original source directory")
    parser.add_argument("--workspace", required=True, help="Path to the evolution workspace")
    args = parser.parse_args()

    origin = Path(args.origin).resolve()
    workspace = Path(args.workspace).resolve()

    if not origin.exists():
        print(f"ERROR: Origin path does not exist: {origin}", file=sys.stderr)
        sys.exit(1)

    print(f"Phase 2 — Source Discovery & Copy")
    print(f"Origin    : {origin}")
    print(f"Workspace : {workspace}")
    print("")

    result = discover_and_copy(origin, workspace)

    print(f"✓ {result['files_copied']} files copied")
    print(f"✓ Manifest: {result['manifest_path']}")

    if result["versioned_files"]:
        print(f"\nVersioned files found ({len(result['versioned_files'])}):")
        for path, ver in result["versioned_files"]:
            print(f"  {path} @ v{ver}")
    else:
        print("\n⚠ No version headers detected in source files.")


if __name__ == "__main__":
    main()
