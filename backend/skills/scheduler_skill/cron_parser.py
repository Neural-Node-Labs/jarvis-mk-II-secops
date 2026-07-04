#!/usr/bin/env python3
"""
cron_parser.py — Task Scheduling Skill v1.0.0

Parse standard 5-field cron expressions and @shortcuts to compute
next run time. Supports all standard cron features including ranges,
steps, lists, and wildcards.

CHANGELOG:
  v1.0.0 — Initial implementation
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Optional


# ---- @shortcut mappings ----
SHORTCUTS = {
    "@yearly":    "0 0 1 1 *",
    "@annually":  "0 0 1 1 *",
    "@monthly":   "0 0 1 * *",
    "@weekly":    "0 0 * * 0",
    "@daily":     "0 0 * * *",
    "@midnight":  "0 0 * * *",
    "@hourly":    "0 * * * *",
}

# ---- Every-N-seconds pattern ----
EVERY_N_RE = re.compile(r"^@every_(\d+)s$")


class CronParser:
    """Parse cron expressions and compute next run times."""

    def __init__(self):
        self.field_parsers = {
            0: self._parse_minute,   # minute (0-59)
            1: self._parse_hour,     # hour (0-23)
            2: self._parse_day,      # day of month (1-31)
            3: self._parse_month,    # month (1-12)
            4: self._parse_dow,      # day of week (0-7, 0=Sun, 7=Sun)
        }
        self.field_names = ["minute", "hour", "day", "month", "day_of_week"]

    def parse(self, expr: str) -> Optional[dict]:
        """
        Parse a cron expression and return a structured representation.

        Supports:
        - Standard 5-field cron: "*/5 * * * *"
        - @shortcuts: @hourly, @daily, @weekly, @monthly, @yearly
        - @every_Ns: @every_30s (every 30 seconds)

        Returns dict with:
        - original: the original expression
        - type: "cron", "every_n", or "shortcut"
        - fields: list of 5 parsed field sets
        - interval_seconds: for @every_N expressions
        - description: human-readable description

        Returns None on parse failure.
        """
        original = expr.strip()

        # Check for @every_Ns format
        every_match = EVERY_N_RE.match(original)
        if every_match:
            seconds = int(every_match.group(1))
            return {
                "original": original,
                "type": "every_n",
                "interval_seconds": seconds,
                "fields": None,
                "description": f"Every {seconds} seconds",
            }

        # Resolve @shortcuts
        if original in SHORTCUTS:
            expr = SHORTCUTS[original]
            shortcut_name = original
        else:
            expr = original
            shortcut_name = None

        # Split and validate
        parts = expr.strip().split()
        if len(parts) != 5:
            return None

        fields = []
        for i, part in enumerate(parts):
            parsed = self._parse_field(i, part)
            if parsed is None:
                return None
            fields.append(parsed)

        result = {
            "original": original,
            "type": "cron",
            "interval_seconds": None,
            "fields": fields,
            "description": self._describe(parts),
        }
        if shortcut_name:
            result["shortcut"] = shortcut_name

        return result

    def get_next_run(self, expr: str, after: Optional[datetime] = None) -> Optional[datetime]:
        """
        Compute the next run time for a cron expression.

        Args:
            expr: Cron expression (5-field, @shortcut, or @every_Ns)
            after: Reference time (defaults to now UTC)

        Returns:
            datetime of next run, or None if expression is invalid
        """
        parsed = self.parse(expr)
        if parsed is None:
            return None

        after = after or datetime.now(timezone.utc)

        # @every_N: just add interval
        if parsed["type"] == "every_n":
            return after + timedelta(seconds=parsed["interval_seconds"])

        # Standard cron: find next matching time
        return self._find_next(parsed["fields"], after)

    def _find_next(self, fields: list, after: datetime) -> datetime:
        """Find the next datetime matching the parsed cron fields."""
        # Start from the next minute
        candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)

        # Search up to 4 years ahead to avoid infinite loops
        max_search = after + timedelta(days=1461)

        while candidate <= max_search:
            if not self._matches_month(candidate, fields[3]):
                # Skip to next month, 1st day, 0 hour, 0 minute
                if candidate.month == 12:
                    candidate = candidate.replace(year=candidate.year + 1, month=1, day=1,
                                                  hour=0, minute=0)
                else:
                    candidate = candidate.replace(month=candidate.month + 1, day=1,
                                                  hour=0, minute=0)
                continue

            if not self._matches_day(candidate, fields[2], fields[4]):
                # Skip to next day
                candidate += timedelta(days=1)
                candidate = candidate.replace(hour=0, minute=0)
                continue

            if not self._matches_hour(candidate, fields[1]):
                # Skip to next hour
                candidate += timedelta(hours=1)
                candidate = candidate.replace(minute=0)
                continue

            if not self._matches_minute(candidate, fields[0]):
                # Skip to next minute
                candidate += timedelta(minutes=1)
                continue

            # All fields match!
            return candidate

        return None

    def _matches_minute(self, dt: datetime, field: set) -> bool:
        return dt.minute in field

    def _matches_hour(self, dt: datetime, field: set) -> bool:
        return dt.hour in field

    def _matches_day(self, dt: datetime, day_field: set, dow_field: set) -> bool:
        """Match day of month OR day of week (cron OR logic)."""
        dom_match = dt.day in day_field
        dow_match = dt.weekday() in dow_field  # Monday=0, Sunday=6

        # If both are wildcards, it's a match
        # If one is wildcard, use the other
        # If neither is wildcard, match if EITHER matches
        dom_wild = (len(day_field) == 31)
        dow_wild = (len(dow_field) == 7)

        if dom_wild and dow_wild:
            return True
        if dom_wild:
            return dow_match
        if dow_wild:
            return dom_match
        return dom_match or dow_match

    def _matches_month(self, dt: datetime, field: set) -> bool:
        return dt.month in field

    def _parse_field(self, index: int, part: str) -> Optional[set]:
        """Parse a single cron field into a set of valid values."""
        parser = self.field_parsers.get(index)
        if parser is None:
            return None
        return parser(part)

    def _parse_minute(self, part: str) -> Optional[set]:
        return self._parse_range(part, 0, 59)

    def _parse_hour(self, part: str) -> Optional[set]:
        return self._parse_range(part, 0, 23)

    def _parse_day(self, part: str) -> Optional[set]:
        return self._parse_range(part, 1, 31)

    def _parse_month(self, part: str) -> Optional[set]:
        return self._parse_range(part, 1, 12)

    def _parse_dow(self, part: str) -> Optional[set]:
        """Parse day of week (0-7, 0=Sun, 7=Sun -> convert to 0-6)."""
        values = self._parse_range(part, 0, 7)
        if values is None:
            return None
        # Convert 7 to 0 (Sunday)
        result = set()
        for v in values:
            if v == 7:
                result.add(0)
            else:
                result.add(v)
        return result

    def _parse_range(self, part: str, min_val: int, max_val: int) -> Optional[set]:
        """Parse a cron field with ranges, steps, lists, and wildcards."""
        part = part.strip()

        # Wildcard
        if part == "*":
            return set(range(min_val, max_val + 1))

        values = set()

        # Handle comma-separated lists
        for segment in part.split(","):
            segment = segment.strip()

            # Handle step: */5, 1-10/2
            if "/" in segment:
                range_part, step = segment.split("/", 1)
                try:
                    step = int(step)
                except ValueError:
                    return None

                if range_part == "*":
                    start, end = min_val, max_val
                elif "-" in range_part:
                    parts = range_part.split("-", 1)
                    try:
                        start = int(parts[0])
                        end = int(parts[1])
                    except ValueError:
                        return None
                else:
                    try:
                        single = int(range_part)
                        values.add(single)
                        continue
                    except ValueError:
                        return None

                if start < min_val or end > max_val or step <= 0:
                    return None
                for v in range(start, end + 1, step):
                    values.add(v)

            elif "-" in segment:
                # Range: 1-5
                parts = segment.split("-", 1)
                try:
                    start = int(parts[0])
                    end = int(parts[1])
                except ValueError:
                    return None
                if start < min_val or end > max_val:
                    return None
                for v in range(start, end + 1):
                    values.add(v)

            else:
                # Single value
                try:
                    v = int(segment)
                except ValueError:
                    return None
                if v < min_val or v > max_val:
                    return None
                values.add(v)

        return values if values else None

    def _describe(self, parts: list) -> str:
        """Generate a human-readable description of a cron expression."""
        # Common patterns
        if parts == ["0", "0", "*", "*", "*"]:
            return "Daily at midnight"
        if parts == ["0", "*", "*", "*", "*"]:
            return "Every hour at minute 0"
        if parts == ["*/5", "*", "*", "*", "*"]:
            return "Every 5 minutes"
        if parts == ["*/1", "*", "*", "*", "*"] or parts == ["*", "*", "*", "*", "*"]:
            return "Every minute"
        if parts[0] == "0" and parts[1] == "9" and parts[2] == "*" and parts[3] == "*" and parts[4] == "1-5":
            return "Weekdays at 9:00 AM"

        return f"{parts[0]} {parts[1]} {parts[2]} {parts[3]} {parts[4]}"

    def validate(self, expr: str) -> tuple[bool, str]:
        """Validate a cron expression. Returns (is_valid, message)."""
        parsed = self.parse(expr)
        if parsed is None:
            return False, f"Invalid cron expression: '{expr}'"
        return True, parsed["description"]
