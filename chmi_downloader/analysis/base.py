from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict


class Analyzer(ABC):
    name: str = ''

    @abstractmethod
    def analyze(self, path: Path, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze a downloaded file and return structured results."""
