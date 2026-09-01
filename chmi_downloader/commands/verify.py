from __future__ import annotations

from ..config import Config
from ..database import Database


def main(config_path: str | None = None) -> None:
    config = Config(config_path)
    database = Database(config.database_path())
    print(f'Verification ready. DB: {config.database_path()}')
    print(database.list_remote_files())
