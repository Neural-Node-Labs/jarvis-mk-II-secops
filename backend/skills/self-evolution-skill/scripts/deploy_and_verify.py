#!/usr/bin/env python3
# version: 1.0.0
# changelog:
#   1.0.0 - 2026-06-01 - Deploy and verify script for Phase 8

"""
Phase 8 — Deploy & Verify
==========================
Copies validated workspace/src files back to the original location.
Creates a backup first. Verifies every file after copy.
Rolls back automatically on any verification failure.

Usage:
    python deploy_and_verify.py --workspace /tmp/evo/ws-XXX --origin /path/to/source
    python deploy_and_verify.py --workspace /tmp/evo/ws-XXX --origin /path/to/source --dry-run
"""

import os
import sys
import shutil
import hashlib
import argparse
from datetime import datetime, timezone
from pathlib import Path


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


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except Exception:
        return -1


def log(log_path: Path, message: str):
    line = f"[{iso_now()}] {message}"
    print(line)
    with open(log_path, "a") as f:
        f.write(line + "\n")


def deploy(workspace: Path, origin: Path, dry_run: bool = False) -> bool:
    src_dir = workspace / "src"
    backup_dir = workspace / "reports" / "backup"
    log_path = workspace / "evolution.log"
    manifest_lines = []
    failures = []

    if not src_dir.exists():
        print(f"ERROR: workspace/src not found at {src_dir}", file=sys.stderr)
        return False

    all_src_files = list(src_dir.rglob("*"))
    files_to_deploy = [f for f in all_src_files if f.is_file()]

    log(log_path, f"[PHASE-8] DEPLOY_START  files={len(files_to_deploy)} dry_run={dry_run}")

    # Step 1 — Backup originals
    if not dry_run:
        backup_dir.mkdir(parents=True, exist_ok=True)
        for src_file in files_to_deploy:
            rel_path = src_file.relative_to(src_dir)
            orig_file = origin / rel_path
            if orig_file.exists():
                backup_file = backup_dir / rel_path
                backup_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(orig_file, backup_file)
        log(log_path, f"[PHASE-8] BACKUP_CREATED  path={backup_dir}")

    # Step 2 — Deploy files
    for src_file in files_to_deploy:
        rel_path = src_file.relative_to(src_dir)
        dest_file = origin / rel_path

        src_size = file_size(src_file)
        src_hash = sha256_prefix(src_file)

        if dry_run:
            status = "DRY_RUN"
            manifest_lines.append(f"{dest_file} | {src_size}B | {src_hash} | DRY_RUN")
            log(log_path, f"[PHASE-8] DRY_RUN  dst={dest_file}")
            continue

        dest_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dest_file)
        log(log_path, f"[PHASE-8] FILE_DEPLOYED  src={src_file} dst={dest_file}")

        # Step 3 — Post-copy verification
        if not dest_file.exists():
            status = "MISSING"
            failures.append(f"MISSING: {dest_file}")
        elif file_size(dest_file) != src_size:
            status = "SIZE_MISMATCH"
            failures.append(f"SIZE_MISMATCH: {dest_file} (src={src_size} dst={file_size(dest_file)})")
        elif sha256_prefix(dest_file) != src_hash:
            status = "HASH_MISMATCH"
            failures.append(f"HASH_MISMATCH: {dest_file}")
        else:
            status = "VERIFIED"

        manifest_lines.append(f"{dest_file} | {src_size}B | {src_hash} | {status}")
        log(log_path, f"[PHASE-8] VERIFY  dst={dest_file} status={status}")

    # Write deploy manifest
    manifest_path = workspace / "reports" / "deploy-manifest.txt"
    all_verified = len(failures) == 0
    with open(manifest_path, "w") as f:
        f.write("DEPLOYMENT MANIFEST\n")
        f.write(f"Deployed at    : {iso_now()}\n")
        f.write(f"Workspace      : {workspace}\n")
        f.write(f"Origin         : {origin}\n")
        f.write(f"Dry Run        : {dry_run}\n")
        f.write("─" * 70 + "\n")
        for line in manifest_lines:
            f.write(line + "\n")
        f.write("─" * 70 + "\n")
        f.write(f"TOTAL DEPLOYED : {len(manifest_lines)} files\n")
        f.write(f"ALL VERIFIED   : {'YES' if all_verified else 'NO'}\n")
        if failures:
            f.write("\nFAILURES:\n")
            for fail in failures:
                f.write(f"  {fail}\n")

    # Step 4 — Rollback on failure
    if failures and not dry_run:
        log(log_path, f"[PHASE-8] DEPLOY_FAILED  failures={len(failures)} — INITIATING ROLLBACK")
        for src_file in files_to_deploy:
            rel_path = src_file.relative_to(src_dir)
            backup_file = backup_dir / rel_path
            dest_file = origin / rel_path
            if backup_file.exists():
                shutil.copy2(backup_file, dest_file)
                log(log_path, f"[PHASE-8] ROLLBACK  file={dest_file}")
        log(log_path, f"[PHASE-8] ROLLBACK_COMPLETE")
        print(f"\n✗ DEPLOY FAILED — {len(failures)} verification failure(s). Rollback complete.")
        for f in failures:
            print(f"  {f}")
        return False

    if not dry_run:
        log(log_path, f"[PHASE-8] DEPLOY_COMPLETE  files={len(manifest_lines)} all_verified={all_verified}")

    print(f"\n{'DRY RUN — ' if dry_run else ''}✓ {len(manifest_lines)} files deployed and verified")
    print(f"✓ Manifest: {manifest_path}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Phase 8 — Deploy & Verify")
    parser.add_argument("--workspace", required=True, help="Path to the evolution workspace")
    parser.add_argument("--origin", required=True, help="Original source path to deploy back to")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be deployed without copying")
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    origin = Path(args.origin).resolve()

    if not workspace.exists():
        print(f"ERROR: Workspace not found: {workspace}", file=sys.stderr)
        sys.exit(1)

    success = deploy(workspace, origin, dry_run=args.dry_run)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
