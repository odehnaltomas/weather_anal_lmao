"""SQLite catalog for remote CHMI objects and their local archive state."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class Database:
    """Persist download identity, integrity metadata, paths, and status."""

    def __init__(self, db_path: str | os.PathLike[str]) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _initialize(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            # WAL and a busy timeout make short SQLite writes safer when the
            # status command reads the catalog while a collector is finishing.
            conn.execute('PRAGMA journal_mode = WAL')
            conn.execute('PRAGMA foreign_keys = ON')
            conn.execute('PRAGMA busy_timeout = 5000')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS process_status (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    status TEXT NOT NULL UNIQUE
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS remote_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    source TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    sha256 TEXT,
                    size_bytes INTEGER,
                    status_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(status_id) REFERENCES process_status(id)
                )
            ''')
            existing = {
                row[1] for row in conn.execute('PRAGMA table_info(remote_files)').fetchall()
            }
            # Upgrade catalogs created by early versions in place. SQLite
            # lacks ADD COLUMN IF NOT EXISTS, hence the PRAGMA inspection.
            migrations = {
                'product': 'TEXT',
                'remote_url': 'TEXT',
                'observation_time_utc': 'TEXT',
                'remote_size': 'INTEGER',
                'remote_modified': 'TEXT',
                'attempts': 'INTEGER NOT NULL DEFAULT 0',
                'downloaded_at': 'TEXT',
                'verified_at': 'TEXT',
                'last_error': 'TEXT',
            }
            for column, definition in migrations.items():
                if column not in existing:
                    conn.execute(f'ALTER TABLE remote_files ADD COLUMN {column} {definition}')
            conn.execute(
                'CREATE UNIQUE INDEX IF NOT EXISTS idx_remote_files_url '
                'ON remote_files(remote_url) WHERE remote_url IS NOT NULL'
            )
            conn.commit()

    def ensure_status(self, status: str) -> int:
        """Return the foreign-key ID for a status, creating it if necessary."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute('SELECT id FROM process_status WHERE status = ?', (status,)).fetchone()
            if row is not None:
                return int(row[0])
            cur = conn.execute('INSERT INTO process_status(status) VALUES (?)', (status,))
            conn.commit()
            return int(cur.lastrowid)

    def insert_remote_file(
        self,
        name: str,
        source: str,
        file_path: str,
        sha256: Optional[str],
        size_bytes: Optional[int],
        status: str,
        *,
        product: Optional[str] = None,
        remote_url: Optional[str] = None,
        observation_time_utc: Optional[str] = None,
        last_error: Optional[str] = None,
    ) -> int:
        """Insert a new remote object or update the row identified by its URL."""
        status_id = self.ensure_status(status)
        downloaded_at = (
            datetime.now(timezone.utc).isoformat()
            if status in {'downloaded', 'verified'}
            else None
        )
        with sqlite3.connect(self.db_path) as conn:
            if remote_url:
                # A remote URL identifies one CHMI object. Repeated collection
                # updates its existing row instead of producing duplicates.
                existing = conn.execute(
                    'SELECT id FROM remote_files WHERE remote_url = ?', (remote_url,)
                ).fetchone()
                if existing is not None:
                    conn.execute(
                        '''
                        UPDATE remote_files
                        SET name = ?, source = ?, product = ?, file_path = ?,
                            sha256 = ?, size_bytes = ?, status_id = ?,
                            observation_time_utc = ?, attempts = attempts + 1,
                            downloaded_at = COALESCE(?, downloaded_at), last_error = ?
                        WHERE id = ?
                        ''',
                        (
                            name, source, product, file_path, sha256, size_bytes,
                            status_id, observation_time_utc, downloaded_at,
                            last_error, int(existing[0]),
                        ),
                    )
                    conn.commit()
                    return int(existing[0])
            cur = conn.execute(
                '''
                INSERT INTO remote_files(
                    name, source, product, remote_url, observation_time_utc,
                    file_path, sha256, size_bytes, status_id, attempts,
                    downloaded_at, last_error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ''',
                (
                    name, source, product, remote_url, observation_time_utc,
                    file_path, sha256, size_bytes, status_id, downloaded_at,
                    last_error,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def update_remote_file_status(self, file_path: str, status: str) -> None:
        """Update all catalog rows associated with a local file path."""
        status_id = self.ensure_status(status)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('UPDATE remote_files SET status_id = ? WHERE file_path = ?', (status_id, file_path))
            conn.commit()

    def is_downloaded(self, remote_url: str) -> bool:
        """Return true only when the catalog row and local file both exist."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                '''
                SELECT rf.file_path
                FROM remote_files rf
                JOIN process_status ps ON ps.id = rf.status_id
                WHERE rf.remote_url = ? AND ps.status IN ('downloaded', 'verified')
                ''',
                (remote_url,),
            ).fetchone()
        return row is not None and Path(row[0]).exists()

    def failed_files(self) -> list[dict[str, object]]:
        """Return rows eligible for the retry command."""
        return self.list_remote_files(status='failed')

    def radar_files(self, product: str) -> list[dict[str, object]]:
        """Return readable catalog entries for one radar product in time order."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                '''
                SELECT rf.file_path, rf.observation_time_utc, rf.sha256,
                       rf.size_bytes, ps.status
                FROM remote_files rf
                JOIN process_status ps ON ps.id = rf.status_id
                WHERE rf.source = 'radar' AND rf.product = ?
                  AND ps.status IN ('downloaded', 'verified')
                  AND rf.observation_time_utc IS NOT NULL
                ORDER BY rf.observation_time_utc
                ''',
                (product,),
            ).fetchall()
            return [dict(row) for row in rows]

    def status_summary(self) -> list[dict[str, object]]:
        """Aggregate catalog state by source and product for compact output."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                '''
                SELECT rf.source,
                       COALESCE(rf.product, '(legacy)') AS product,
                       COUNT(*) AS total,
                       SUM(CASE WHEN ps.status = 'downloaded' THEN 1 ELSE 0 END) AS downloaded,
                       SUM(CASE WHEN ps.status = 'verified' THEN 1 ELSE 0 END) AS verified,
                       SUM(CASE WHEN ps.status = 'failed' THEN 1 ELSE 0 END) AS failed,
                       SUM(CASE WHEN ps.status = 'missing' THEN 1 ELSE 0 END) AS missing,
                       SUM(CASE WHEN ps.status = 'corrupt' THEN 1 ELSE 0 END) AS corrupt,
                       SUM(CASE WHEN ps.status = 'skipped' THEN 1 ELSE 0 END) AS skipped,
                       MIN(rf.observation_time_utc) AS first_observation,
                       MAX(rf.observation_time_utc) AS last_observation
                FROM remote_files rf
                JOIN process_status ps ON ps.id = rf.status_id
                GROUP BY rf.source, COALESCE(rf.product, '(legacy)')
                ORDER BY rf.source, product
                '''
            ).fetchall()
            return [dict(row) for row in rows]

    def list_remote_files(self, status: str | None = None) -> list[dict[str, object]]:
        """Return catalog rows as dictionaries, optionally filtered by status."""
        parameters: tuple[object, ...] = ()
        where = ''
        if status is not None:
            where = 'WHERE ps.status = ?'
            parameters = (status,)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f'''
                SELECT rf.id, rf.name, rf.source, rf.product, rf.remote_url,
                       rf.observation_time_utc, rf.file_path, rf.sha256,
                       rf.size_bytes, ps.status, rf.attempts, rf.last_error
                FROM remote_files rf
                JOIN process_status ps ON ps.id = rf.status_id
                {where}
                ORDER BY rf.id
                ''',
                parameters,
            ).fetchall()
            return [dict(row) for row in rows]
