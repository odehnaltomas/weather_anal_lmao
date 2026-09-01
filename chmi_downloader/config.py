"""Load project configuration and resolve all storage locations."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


class Config:
    """Typed access to the YAML configuration used by all commands."""

    def __init__(self, config_path: str | os.PathLike[str] | None = None) -> None:
        # The repository-level config is the default, but tests and alternate
        # installations may supply an explicit path.
        self.config_path = Path(config_path or Path(__file__).resolve().parents[1] / 'config.yaml')
        if not self.config_path.exists():
            raise FileNotFoundError(f'Config file not found: {self.config_path}')
        if yaml is None:
            raise RuntimeError('PyYAML is required to read config.yaml')
        with self.config_path.open(encoding='utf-8') as handle:
            self.data = yaml.safe_load(handle) or {}

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def storage_root(self) -> Path:
        """Return the absolute root containing raw, processed, and log data."""
        storage = self.data.get('storage', {})
        root = storage.get('root', './data')
        return Path(root).resolve()

    def database_path(self) -> Path:
        """Return the absolute path of the SQLite catalog."""
        storage = self.data.get('storage', {})
        database = storage.get('database', './data/catalog.sqlite')
        return Path(database).resolve()

    def staging_dir(self) -> Path:
        """Return the temporary directory used for incomplete downloads."""
        storage = self.data.get('storage', {})
        staging = storage.get('temporary', './data/staging')
        return Path(staging).resolve()

    def raw_dir(self) -> Path:
        return self.storage_root() / 'raw'

    def processed_dir(self) -> Path:
        return self.storage_root() / 'processed'

    def logs_dir(self) -> Path:
        return self.storage_root() / 'logs'
