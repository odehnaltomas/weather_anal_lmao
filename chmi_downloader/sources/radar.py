"""Configuration-driven discovery and filename parsing for CHMI radar data."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from .base import Resource


class RadarResource(Resource):
    """Build one archive definition for each enabled radar product/format."""

    name = 'radar'

    def discover(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        resources: List[Dict[str, Any]] = []
        for product_name, product in (config.get('radar', {}) or {}).get('products', {}).items():
            if not product.get('enabled', False):
                continue
            for fmt in product.get('formats', []):
                resources.append({
                    'name': f'{product_name}.{fmt}',
                    'source': 'radar',
                    'product': product_name,
                    'format': fmt,
                    'url': product.get('url', ''),
                    # Radar indexes have short retention, so archive every
                    # listed file rather than selecting only the newest one.
                    'archive_all': True,
                    'recursive': True,
                })
        return resources

    def validate(self, path: Path, metadata: Dict[str, Any]) -> bool:
        if not path.exists():
            return False
        if path.stat().st_size <= 0:
            return False
        if metadata.get('expected_size') and path.stat().st_size != int(metadata['expected_size']):
            return False
        return path.suffix.lower() in {'.hdf', '.hdf5', '.h5', '.nc', '.bin', '.gz'}


RADAR_FILENAME = re.compile(
    r'^T_[A-Z0-9]+_C_OKPR_(?P<time>\d{14})\.(?:hdf|h5|hdf5)$',
    re.IGNORECASE,
)


def parse_radar_time(filename: str) -> datetime | None:
    """Extract the UTC observation time encoded in a CHMI radar filename."""
    match = RADAR_FILENAME.match(filename)
    if match is None:
        # Unknown files remain discoverable; they simply lack a date partition.
        return None
    return datetime.strptime(match.group('time'), '%Y%m%d%H%M%S').replace(tzinfo=timezone.utc)
