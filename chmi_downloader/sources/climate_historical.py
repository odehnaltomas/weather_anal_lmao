"""Resource definitions for interval-specific historical CHMI climate data."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from .base import Resource


class ClimateHistoricalResource(Resource):
    """Create a separately cataloged resource for every configured interval."""

    name = 'climate_historical'

    def discover(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        section = (config.get('climate', {}) or {}).get('historical_csv', {})
        if not section.get('enabled', False):
            return []
        resources: List[Dict[str, Any]] = []
        for interval in section.get('intervals', []):
            # Keeping intervals separate makes status output and storage paths
            # useful even though they share one top-level CHMI index.
            resources.append({
                'name': f'climate_historical_{interval}',
                'source': 'climate',
                'product': f'historical_{interval}',
                'format': 'csv',
                'url': section.get('url', ''),
                'interval': interval,
            })
        return resources

    def validate(self, path: Path, metadata: Dict[str, Any]) -> bool:
        if not path.exists():
            return False
        if path.stat().st_size <= 0:
            return False
        return path.suffix.lower() == '.csv'
