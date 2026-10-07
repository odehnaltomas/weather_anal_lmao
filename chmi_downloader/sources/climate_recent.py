"""Resource definition for CHMI's recent climate observations."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from .base import Resource


class ClimateRecentResource(Resource):
    """Select the configured recent-data interval without broad crawling."""

    name = 'climate_recent'

    def discover(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        section = (config.get('climate', {}) or {}).get('recent', {})
        if not section.get('enabled', False):
            return []
        return [{
            'name': 'climate_recent',
            'source': 'climate',
            'product': 'recent',
            'format': section.get('format', 'json'),
            'url': section.get('url', ''),
            'interval': section.get('interval', 'daily'),
            'station_ids': section.get('station_ids', []),
            # Daily files represent a growing month and keep the same URL.
            # Refreshing them is required to receive newly appended days.
            'archive_all': True,
            'recursive': True,
            'refresh_existing': True,
        }]

    def validate(self, path: Path, metadata: Dict[str, Any]) -> bool:
        if not path.exists():
            return False
        if path.stat().st_size <= 0:
            return False
        return path.suffix.lower() in {'.json', '.csv'}
