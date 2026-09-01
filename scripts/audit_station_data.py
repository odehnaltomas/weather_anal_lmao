"""Report local CHMI station coverage and missing daily source files."""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from chmi_downloader.config import Config


STATION_FILE = re.compile(
    r"^(?:10m|1h)-0-203-0-(?P<station>\d+)-(?P<day>\d{8})\.json$"
)


def days_between(first: date, last: date) -> set[date]:
    """Return every calendar day in an inclusive range."""
    return {first + timedelta(days=offset) for offset in range((last - first).days + 1)}


def main() -> None:
    config = Config()
    connection = sqlite3.connect(config.database_path())
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT rf.product, rf.name, rf.file_path
        FROM remote_files rf
        JOIN process_status ps ON ps.id = rf.status_id
        WHERE rf.source = 'climate'
          AND rf.product IN ('station_10min', 'station_1hour')
          AND ps.status IN ('downloaded', 'verified')
        ORDER BY rf.product, rf.name
        """
    ).fetchall()

    coverage: dict[tuple[str, str], set[date]] = defaultdict(set)
    invalid: list[str] = []
    for row in rows:
        path = Path(row["file_path"])
        match = STATION_FILE.match(path.name)
        if not path.exists() or not match:
            invalid.append(str(path))
            continue
        # Parsing JSON catches truncated or HTML error payloads that happened
        # to pass a filename/size-only check.
        json.loads(path.read_text(encoding="utf-8-sig"))
        day = date.fromisoformat(
            f'{match.group("day")[:4]}-{match.group("day")[4:6]}-{match.group("day")[6:]}'
        )
        coverage[(row["product"], match.group("station"))].add(day)

    print("Station data coverage")
    for (product, station), days in sorted(coverage.items()):
        missing = sorted(days_between(min(days), max(days)) - days)
        print(
            f"  {product:<15} {station}: {min(days)} to {max(days)}, "
            f"files={len(days)}, gaps={len(missing)}"
        )
        if missing:
            print("    Missing: " + ", ".join(str(day) for day in missing))
    print(f"Invalid or unrecognized files: {len(invalid)}")


if __name__ == "__main__":
    main()
