from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from .base import Analyzer


class RadarAnalyzer(Analyzer):
    name = 'radar'

    def analyze(self, path: Path, metadata: Dict[str, Any]) -> Dict[str, Any]:
        return {
            'path': str(path),
            'size_bytes': path.stat().st_size,
            'kind': 'radar_composite',
            'format': path.suffix.lower(),
            'sha256': metadata.get('sha256'),
        }
