# version: 1.0.0
# changelog:
#   1.0.0 - 2026-05-29 - Initial IndexManager (CBD v2.2)

"""
COMPONENT: IndexManager
CBD Identity:
  Name: IndexManager
  Reason: Maintain experienced/index.md — the agent reads this FIRST on every task
  Logical Function: Index Management / Metadata

IN-Schema:  { operation: add|update|remove|rebuild, entry: IndexRecord|null }
OUT-Schema: { success, operation, index_row_count }
Error-Schema: { error_code: IM_WRITE_LOCK|IM_CORRUPT_INDEX|IM_ENTRY_NOT_FOUND, message }

Failure Map:
  - Atomic write: temp → rename (prevents partial corruption)
  - try-catch on all file operations
  - rebuild re-reads all EXP-*.md to reconstruct from source of truth
"""
import logging
import threading
from pathlib import Path
from .models import IndexRecord, utc_now

logger = logging.getLogger("experienced.index_manager")

INDEX_HEADER = """# Experienced Knowledge Index

> Agent: Read this file FIRST before any debugging, setup, or problem-solving task.
> Run a keyword search against title, category, and environment_summary columns.

| exp_id | title | category | severity | status | environment_summary | date_encountered | file_path |
|--------|-------|----------|----------|--------|---------------------|------------------|-----------|
"""


class IndexManager:
    """
    Manages experienced/index.md.
    Trace points: index_updated, index_rebuild, write_lock_acquired, write_lock_released
    Thread-safe via in-process lock (sufficient for single-agent use).
    """

    def __init__(self, base_path: str):
        self.base_path = Path(base_path)
        self.index_path = self.base_path / "index.md"
        self._lock = threading.Lock()

    def execute(self, operation: str, entry: IndexRecord = None) -> dict:
        """Dispatch add/update/remove/rebuild. Returns OUT-Schema. Never raises."""
        try:
            if operation == "add":
                return self._add(entry)
            elif operation == "update":
                return self._update(entry)
            elif operation == "remove":
                return self._remove(entry.exp_id if entry else None)
            elif operation == "rebuild":
                return self._rebuild()
            elif operation == "init":
                return self._init_empty()
            else:
                return {"error_code": "IM_UNKNOWN_OP", "message": f"Unknown operation: {operation}"}
        except Exception as e:
            logger.error(f"IndexManager.{operation} crash: {e}", exc_info=True)
            return {"error_code": "IM_WRITE_LOCK", "message": str(e)}

    def _init_empty(self) -> dict:
        """Create an empty index.md if it doesn't exist."""
        try:
            self.base_path.mkdir(parents=True, exist_ok=True)
            if not self.index_path.exists():
                self._atomic_write(INDEX_HEADER)
                logger.info("index_initialized empty")
            return {"success": True, "operation": "init", "index_row_count": 0}
        except Exception as e:
            return {"error_code": "IM_WRITE_LOCK", "message": str(e)}

    def _load_rows(self) -> list:
        """Parse existing index rows as raw line strings (data rows only)."""
        if not self.index_path.exists():
            return []
        try:
            lines = self.index_path.read_text(encoding="utf-8").splitlines()
            rows = []
            in_table = False
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("| exp_id"):
                    in_table = True
                    continue
                if in_table and stripped.startswith("|---"):
                    continue
                if in_table and stripped.startswith("|"):
                    rows.append(stripped)
            return rows
        except Exception as e:
            logger.error(f"_load_rows error: {e}", exc_info=True)
            return []

    def _record_to_row(self, r: IndexRecord) -> str:
        return f"| {r.exp_id} | {r.title} | {r.category} | {r.severity} | {r.status} | {r.environment_summary} | {r.date_encountered} | {r.file_path} |"

    def _rows_to_content(self, rows: list) -> str:
        return INDEX_HEADER + "\n".join(rows) + ("\n" if rows else "")

    def _atomic_write(self, content: str):
        """Write content to index.md via temp file rename."""
        tmp = self.index_path.with_suffix(".tmp")
        try:
            self.base_path.mkdir(parents=True, exist_ok=True)
            tmp.write_text(content, encoding="utf-8")
            tmp.rename(self.index_path)
        except Exception:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            raise

    def _add(self, entry: IndexRecord) -> dict:
        if not entry:
            return {"error_code": "IM_ENTRY_NOT_FOUND", "message": "entry is required for add"}
        with self._lock:
            logger.info("write_lock_acquired op=add")
            try:
                rows = self._load_rows()
                # Check duplicate
                for row in rows:
                    if f"| {entry.exp_id} |" in row:
                        return {"error_code": "IM_ENTRY_NOT_FOUND",
                                "message": f"{entry.exp_id} already in index. Use update instead."}
                rows.append(self._record_to_row(entry))
                self._atomic_write(self._rows_to_content(rows))
                logger.info(f"index_updated op=add exp_id={entry.exp_id} row_count={len(rows)}")
                return {"success": True, "operation": "add", "index_row_count": len(rows)}
            finally:
                logger.info("write_lock_released op=add")

    def _update(self, entry: IndexRecord) -> dict:
        if not entry:
            return {"error_code": "IM_ENTRY_NOT_FOUND", "message": "entry is required for update"}
        with self._lock:
            logger.info("write_lock_acquired op=update")
            try:
                rows = self._load_rows()
                new_rows = []
                found = False
                for row in rows:
                    if f"| {entry.exp_id} |" in row:
                        new_rows.append(self._record_to_row(entry))
                        found = True
                    else:
                        new_rows.append(row)
                if not found:
                    return {"error_code": "IM_ENTRY_NOT_FOUND",
                            "message": f"{entry.exp_id} not found in index. Use add instead."}
                self._atomic_write(self._rows_to_content(new_rows))
                logger.info(f"index_updated op=update exp_id={entry.exp_id} row_count={len(new_rows)}")
                return {"success": True, "operation": "update", "index_row_count": len(new_rows)}
            finally:
                logger.info("write_lock_released op=update")

    def _remove(self, exp_id: str) -> dict:
        if not exp_id:
            return {"error_code": "IM_ENTRY_NOT_FOUND", "message": "exp_id is required for remove"}
        with self._lock:
            logger.info(f"write_lock_acquired op=remove exp_id={exp_id}")
            try:
                rows = self._load_rows()
                new_rows = [r for r in rows if f"| {exp_id} |" not in r]
                if len(new_rows) == len(rows):
                    return {"error_code": "IM_ENTRY_NOT_FOUND", "message": f"{exp_id} not found in index."}
                self._atomic_write(self._rows_to_content(new_rows))
                logger.info(f"index_updated op=remove exp_id={exp_id} row_count={len(new_rows)}")
                return {"success": True, "operation": "remove", "index_row_count": len(new_rows)}
            finally:
                logger.info(f"write_lock_released op=remove exp_id={exp_id}")

    def _rebuild(self) -> dict:
        """Reconstruct index from all EXP-*.md files on disk (source of truth)."""
        with self._lock:
            logger.info("write_lock_acquired op=rebuild")
            try:
                rows = []
                for md_file in sorted(self.base_path.glob("EXP-*.md")):
                    try:
                        content = md_file.read_text(encoding="utf-8")
                        record = self._parse_entry_to_record(content, md_file)
                        if record:
                            rows.append(self._record_to_row(record))
                    except Exception as e:
                        logger.warning(f"rebuild: skip {md_file.name}: {e}")
                self._atomic_write(self._rows_to_content(rows))
                logger.info(f"index_rebuild total_entries={len(rows)}")
                return {"success": True, "operation": "rebuild", "index_row_count": len(rows)}
            finally:
                logger.info("write_lock_released op=rebuild")

    def _parse_entry_to_record(self, content: str, file_path: Path) -> IndexRecord:
        """Extract index fields from an EXP-*.md file."""
        try:
            exp_id = file_path.stem
            # Parse first heading for title
            title = exp_id
            for line in content.splitlines():
                if line.startswith("# "):
                    title = line[2:].split("—", 1)[-1].strip() if "—" in line else line[2:].strip()
                    break

            def _get_field(label):
                for line in content.splitlines():
                    if line.strip().startswith(f"**{label}**:"):
                        return line.split(":", 1)[-1].strip()
                return ""

            category = _get_field("Category")
            severity = _get_field("Severity")
            status = _get_field("Status")
            date = _get_field("Date Encountered")

            # Environment summary from table
            env_summary = ""
            in_env_table = False
            for line in content.splitlines():
                if "Runtime Version" in line and "|" in line:
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 3:
                        env_summary = parts[-2]
                    break

            return IndexRecord(
                exp_id=exp_id,
                title=title,
                category=category or "unknown",
                severity=severity or "unknown",
                status=status or "DRAFT",
                environment_summary=env_summary or "N/A",
                date_encountered=date or "unknown",
                file_path=str(file_path),
            )
        except Exception as e:
            logger.error(f"_parse_entry_to_record error {file_path}: {e}", exc_info=True)
            return None
