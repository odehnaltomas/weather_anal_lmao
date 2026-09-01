from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List


class Resource(ABC):
    name: str = ''

    @abstractmethod
    def discover(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Return a list of downloadable resource definitions."""

    @abstractmethod
    def validate(self, path: Path, metadata: Dict[str, Any]) -> bool:
        """Validate a downloaded file."""
