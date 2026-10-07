"""Current observations, metadata and separately archived additional sources."""

from pathlib import Path
from .base import Resource


class CurrentResource(Resource):
    """Group frequently updated observations and separately stored extra feeds."""

    name = 'current'

    def discover(self, config):
        """Describe enabled feeds; the downloader expands their directory trees."""
        resources = []
        now = config.get('climate', {}).get('now', {})
        if now.get('enabled', False):
            resources.append(dict(
                now, source='climate', product='now', recursive=True,
                refresh_existing=True, archive_all=True,
            ))
        for name, section in config.get('additional_sources', {}).items():
            if section.get('enabled', False):
                # Share the radar schedule while bounding initial backfill.
                # Older dated snapshots need fewer repeat checks than live data.
                resources.append(dict(
                    section, source='additional', product=name, recursive=True,
                    refresh_existing=True, archive_all=True,
                    older_recheck_seconds=86400,
                    max_downloads_per_run=section.get('max_downloads_per_run', 200),
                ))
        return resources

    def validate(self, path: Path, metadata):
        return path.is_file() and path.stat().st_size > 0


class MetadataResource(Resource):
    """Refresh station/element dictionaries alongside the daily observations."""

    name = 'metadata'

    def discover(self, config):
        """Keep each metadata tree separate and detect known formats by suffix."""
        return [dict(section, source='metadata', product=name, recursive=True,
                     refresh_existing=True, archive_all=True, format='auto')
                for name, section in config.get('metadata', {}).items()
                if section.get('enabled', False)]

    def validate(self, path: Path, metadata):
        return path.is_file() and path.stat().st_size > 0
