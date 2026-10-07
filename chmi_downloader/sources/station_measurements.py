"""Resource definitions for recent station measurement feeds."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from .base import Resource


class StationMeasurementsResource(Resource):
    """Build enabled 10-minute/hourly station feed definitions."""

    name = 'station_measurements'

    def discover(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        resources: List[Dict[str, Any]] = []
        station_cfg = (config.get('station', {}) or {}).get('measurements', {})
        for interval_name, interval_cfg in station_cfg.items():
            if not interval_cfg.get('enabled', False):
                continue
            resources.append({
                'name': f'station_{interval_name}',
                'source': 'climate',
                'product': f'station_{interval_name}',
                'format': interval_cfg.get('format', 'json'),
                'url': interval_cfg.get('url', ''),
                'interval': interval_name,
                'station_ids': interval_cfg.get('station_ids', []),
                'archive_all': True,
                'recursive': True,
                # Nested monthly files can grow or be corrected at the same
                # URL, so an existing catalog entry is not proof of freshness.
                'refresh_existing': True,
            })
        return resources

    def validate(self, path: Path, metadata: Dict[str, Any]) -> bool:
        if not path.exists():
            return False
        if path.stat().st_size <= 0:
            return False
        return path.suffix.lower() in {'.json', '.csv'}
