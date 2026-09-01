from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from chmi_downloader.config import Config


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expected_path(row: sqlite3.Row, storage_root: Path) -> Path:
    source = str(row["source"])
    product = str(row["product"])
    destination = storage_root / "raw" / source / product
    observation = row["observation_time_utc"]
    if source == "radar" and observation:
        observed_at = datetime.fromisoformat(str(observation).replace("Z", "+00:00"))
        destination /= (
            Path(f"{observed_at.year:04d}")
            / f"{observed_at.month:02d}"
            / f"{observed_at.day:02d}"
        )
    return destination / str(row["name"])


def load_migration_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT rf.id, rf.name, rf.source, rf.product, rf.remote_url,
               rf.observation_time_utc, rf.file_path, rf.size_bytes, rf.sha256,
               ps.status
        FROM remote_files rf
        JOIN process_status ps ON ps.id = rf.status_id
        WHERE ps.status IN ('downloaded', 'verified')
          AND rf.remote_url IS NOT NULL
        ORDER BY rf.id
        """
    ).fetchall()


def plan_migration(
    connection: sqlite3.Connection,
    storage_root: Path,
    *,
    verify_hashes: bool = True,
) -> tuple[list[tuple[str, int]], list[dict[str, Any]]]:
    updates: list[tuple[str, int]] = []
    problems: list[dict[str, Any]] = []
    for row in load_migration_rows(connection):
        current = Path(str(row["file_path"]))
        destination = expected_path(row, storage_root)
        if current.resolve() == destination.resolve():
            continue
        if not destination.exists():
            problems.append(
                {
                    "id": row["id"],
                    "reason": "destination file is missing",
                    "expected_path": str(destination),
                }
            )
            continue
        if row["size_bytes"] is not None and destination.stat().st_size != row["size_bytes"]:
            problems.append(
                {
                    "id": row["id"],
                    "reason": "size does not match catalog",
                    "expected_path": str(destination),
                }
            )
            continue
        if verify_hashes and row["sha256"] and sha256(destination) != row["sha256"]:
            problems.append(
                {
                    "id": row["id"],
                    "reason": "SHA-256 does not match catalog",
                    "expected_path": str(destination),
                }
            )
            continue
        updates.append((str(destination), int(row["id"])))
    return updates, problems


def backup_database(connection: sqlite3.Connection, database_path: Path) -> Path:
    backup_dir = database_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"catalog-before-path-migration-{timestamp}.sqlite"
    with sqlite3.connect(backup_path) as backup:
        connection.backup(backup)
    return backup_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate catalog file paths after moving the CHMI data root."
    )
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the migration. Without this flag, only a dry-run is performed.",
    )
    parser.add_argument(
        "--skip-hashes",
        action="store_true",
        help="Check existence and size only. SHA-256 verification is enabled by default.",
    )
    args = parser.parse_args()

    config = Config(args.config)
    database_path = config.database_path()
    storage_root = config.storage_root()
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        updates, problems = plan_migration(
            connection,
            storage_root,
            verify_hashes=not args.skip_hashes,
        )
        print(f"Database: {database_path}")
        print(f"Storage root: {storage_root}")
        print(f"Paths ready to migrate: {len(updates)}")
        print(f"Problems: {len(problems)}")
        for problem in problems[:20]:
            print(problem)
        if problems:
            raise SystemExit(
                "Migration aborted: resolve the reported file problems first."
            )
        if not args.apply:
            print("Dry-run only. Re-run with --apply to update the database.")
            return

        backup_path = backup_database(connection, database_path)
        with connection:
            connection.executemany(
                "UPDATE remote_files SET file_path = ? WHERE id = ?",
                updates,
            )
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity check failed after migration: {integrity}")
        print(f"Migrated paths: {len(updates)}")
        print(f"Backup: {backup_path}")
        print("SQLite integrity check: ok")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
