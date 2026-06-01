# version: 1.0.0
# changelog:
#   1.0.0 - 2026-05-29 - Initial ExperienceReader and ExperienceSearcher (CBD v2.2)

"""
COMPONENT: ExperienceReader
  Reason: Load one entry by ID from disk
  Logical Function: Read / Query

COMPONENT: ExperienceSearcher
  Reason: Search index.md by keyword/symptom for Phase 0 lookups
  Logical Function: Search / Query

Failure Map:
  - try-catch on all file reads
  - Missing index returns ES_INDEX_MISSING (non-blocking)
  - Empty query rejected before search
"""
import logging
import re
from pathlib import Path

logger = logging.getLogger("experienced.reader")


class ExperienceReader:
    """
    Load a raw EXP-XXXX.md file by ID.
    Trace points: entry_found, entry_missing
    """

    def __init__(self, base_path: str):
        self.base_path = Path(base_path)

    def read(self, exp_id: str) -> dict:
        """Returns { exp_id, file_path, content } or Error-Schema. Never raises."""
        try:
            target = self.base_path / f"{exp_id}.md"
            if not target.exists():
                logger.warning(f"entry_missing exp_id={exp_id}")
                return {
                    "error_code": "ER_NOT_FOUND",
                    "message": f"{exp_id} not found at {target}",
                }
            content = target.read_text(encoding="utf-8")
            logger.info(f"entry_found exp_id={exp_id}")
            return {
                "exp_id": exp_id,
                "file_path": str(target),
                "content": content,
            }
        except Exception as e:
            logger.error(f"read_error exp_id={exp_id}: {e}", exc_info=True)
            return {"error_code": "ER_READ_ERROR", "message": str(e)}

    def list_all(self) -> list:
        """Return sorted list of all EXP-XXXX IDs on disk. Never raises."""
        try:
            return sorted(p.stem for p in self.base_path.glob("EXP-*.md"))
        except Exception as e:
            logger.error(f"list_all error: {e}", exc_info=True)
            return []


class ExperienceSearcher:
    """
    Keyword search over experienced/index.md.
    Matches query against: exp_id, title, category, severity, environment_summary.
    Trace points: search_hit, search_miss, index_read_error

    The index is a Markdown table with columns:
    | exp_id | title | category | severity | status | environment_summary | date_encountered | file_path |
    """

    INDEX_FILE = "index.md"

    def __init__(self, base_path: str):
        self.base_path = Path(base_path)
        self.index_path = self.base_path / self.INDEX_FILE

    def search(self, query: str, filters: dict = None, max_results: int = 5) -> dict:
        """
        Search index for query string.
        Returns OUT-Schema: { results, total_found, query_used } or Error-Schema.
        Never raises. Missing index is non-blocking (returns ES_INDEX_MISSING).
        """
        try:
            if not query or not query.strip():
                return {"error_code": "ES_EMPTY_QUERY", "message": "Query must not be empty."}

            query = query.strip()

            if not self.index_path.exists():
                logger.warning(f"search_miss — index missing at {self.index_path}")
                return {
                    "error_code": "ES_INDEX_MISSING",
                    "message": f"Index not found at {self.index_path}. Run IndexManager rebuild to create it.",
                    "results": [],
                    "total_found": 0,
                    "query_used": query,
                }

            try:
                raw = self.index_path.read_text(encoding="utf-8")
            except Exception as e:
                logger.error(f"index_read_error: {e}", exc_info=True)
                return {"error_code": "ES_READ_ERROR", "message": str(e)}

            rows = self._parse_index_table(raw)
            filters = filters or {}
            results = []

            q_lower = query.lower()
            for row in rows:
                # Apply filters first
                if filters.get("category") and row.get("category", "").lower() != filters["category"].lower():
                    continue
                if filters.get("severity") and row.get("severity", "").lower() != filters["severity"].lower():
                    continue
                if filters.get("environment_runtime"):
                    if filters["environment_runtime"].lower() not in row.get("environment_summary", "").lower():
                        continue

                # Keyword match — any token of the query matching any searchable field is a hit
                matched_field = None
                tokens = [t for t in q_lower.split() if len(t) >= 3]  # skip tiny words
                if not tokens:
                    tokens = [q_lower]
                for field_name in ("title", "category", "environment_summary", "exp_id"):
                    field_val = row.get(field_name, "").lower()
                    if any(tok in field_val for tok in tokens):
                        matched_field = field_name
                        break

                if matched_field:
                    results.append({
                        "exp_id": row.get("exp_id", ""),
                        "title": row.get("title", ""),
                        "category": row.get("category", ""),
                        "severity": row.get("severity", ""),
                        "relevance_signal": matched_field,
                        "file_path": row.get("file_path", ""),
                    })

                if len(results) >= max_results:
                    break

            total = len(results)
            if total > 0:
                logger.info(f"search_hit query={repr(query)} total={total} top={results[0]['exp_id']}")
            else:
                logger.info(f"search_miss query={repr(query)}")

            return {
                "results": results,
                "total_found": total,
                "query_used": query,
            }

        except Exception as e:
            logger.error(f"ExperienceSearcher crash: {e}", exc_info=True)
            return {"error_code": "ES_READ_ERROR", "message": str(e), "results": [], "total_found": 0}

    def _parse_index_table(self, markdown: str) -> list:
        """Parse Markdown table rows into dicts. Returns [] on any parse error."""
        rows = []
        try:
            lines = markdown.splitlines()
            header = None
            for line in lines:
                line = line.strip()
                if not line.startswith("|"):
                    continue
                cells = [c.strip() for c in line.split("|")[1:-1]]
                if header is None:
                    header = [h.lower().replace(" ", "_") for h in cells]
                    continue
                # Skip separator lines like |---|---|
                if all(re.match(r"^-+$", c) for c in cells):
                    continue
                if len(cells) == len(header):
                    rows.append(dict(zip(header, cells)))
        except Exception as e:
            logger.error(f"_parse_index_table error: {e}", exc_info=True)
        return rows
