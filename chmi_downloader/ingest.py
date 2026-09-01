"""Orchestrate remote discovery and local download for configured resources."""

from __future__ import annotations

from typing import Any, Callable, Dict, List

from .database import Database
from .downloader import Downloader


class IngestionService:
    """Connect source-level discovery with the atomic downloader."""

    def __init__(self, config: Any, database: Database, downloader: Downloader) -> None:
        self.config = config
        self.database = database
        self.downloader = downloader

    def collect(
        self,
        resources: List[Dict[str, Any]],
        progress: Callable[[Dict[str, Any]], None] | None = None,
    ) -> List[Dict[str, Any]]:
        """Discover and collect resources, emitting optional progress events."""
        results: List[Dict[str, Any]] = []
        for resource in resources:
            if progress:
                progress({
                    'event': 'discovering',
                    'product': resource.get('product'),
                    'url': resource.get('url'),
                })
            try:
                discovered = self.downloader.discover_resource_files(resource)
            except Exception as exc:
                result = {
                    'status': 'failed',
                    'url': resource.get('url'),
                    'reason': f'Discovery failed: {exc}',
                }
                results.append(result)
                if progress:
                    progress({'event': 'result', 'result': result})
                continue
            if progress:
                progress({
                    'event': 'discovered',
                    'product': resource.get('product'),
                    'count': len(discovered),
                })
            for index, item in enumerate(discovered, start=1):
                result = self.downloader.download_resource(item)
                results.append(result)
                # Already-downloaded rows are intentionally quiet except for
                # periodic checkpoints; a radar archive contains thousands.
                if progress and (
                    result.get('status') != 'already-downloaded'
                    or index % 500 == 0
                    or index == len(discovered)
                ):
                    progress({
                        'event': 'result',
                        'product': resource.get('product'),
                        'index': index,
                        'total': len(discovered),
                        'result': result,
                    })
        return results
