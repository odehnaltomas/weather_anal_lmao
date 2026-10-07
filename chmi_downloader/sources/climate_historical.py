"""Resource definitions for interval-specific historical CHMI climate data."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urljoin

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
            # Reuse the regional daily station selection unless explicitly
            # overridden; recurse through variable/year subdirectories as well.
            resources.append({
                'name': f'climate_historical_{interval}',
                'source': 'climate',
                'product': f'historical_{interval}',
                'format': 'csv',
                'url': urljoin(section.get('url', '').rstrip('/') + '/', interval + '/'),
                'interval': interval,
                'station_ids': section.get('station_ids', config.get('climate', {}).get('recent', {}).get('station_ids', [])),
                'recursive': True,
                'archive_all': True,
                'refresh_existing': True,
            })
        return resources

    def validate(self, path: Path, metadata: Dict[str, Any]) -> bool:
        if not path.exists():
            return False
        if path.stat().st_size <= 0:
            return False
        return path.suffix.lower() == '.csv'
