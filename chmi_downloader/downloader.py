"""Discover CHMI indexes and download validated files into the local archive."""

from __future__ import annotations

import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict
from urllib.parse import unquote, urljoin, urlparse

import requests

from .database import Database
from .sources.radar import parse_radar_time
from .validation import Validator, ValidationError


class Downloader:
    """Handle index traversal, deduplication, validation, and atomic storage."""

    def __init__(self, config: Any, database: Database) -> None:
        # Creating all working directories here keeps later download methods
        # focused on one file and makes missing storage fail early.
        self.config = config
        self.database = database
        self.staging_dir = Path(config.staging_dir())
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir = Path(config.raw_dir())
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir = Path(config.processed_dir())
        self.processed_dir.mkdir(parents=True, exist_ok=True)

    def _extract_listing_links(self, html: str, base_url: str) -> list[str]:
        """Extract safe absolute links from a simple Apache directory index."""
        if not html:
            return []
        candidates: list[str] = []
        for href in re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.IGNORECASE):
            cleaned = href.strip()
            # Parent/query links are navigation controls, not data resources.
            if not cleaned or cleaned.startswith(('#', '?', '../')):
                continue
            if cleaned.startswith(('http://', 'https://')):
                candidates.append(cleaned)
            else:
                candidates.append(urljoin(base_url, cleaned))
        return candidates

    @staticmethod
    def _is_html_response(response: requests.Response) -> bool:
        """Detect directory/error pages even when Content-Type is incorrect."""
        content_type = response.headers.get('content-type', '').lower()
        body = response.content[:2048].decode('utf-8', errors='ignore').lstrip()
        return 'text/html' in content_type or body.startswith(('<', '<!DOCTYPE'))

    @staticmethod
    def _allowed_suffixes(resource: Dict[str, Any]) -> tuple[str, ...]:
        """Map logical formats from config to extensions used by CHMI."""
        expected_format = str(resource.get('format', '')).lower()
        if expected_format == 'hdf5':
            return ('.hdf', '.hdf5', '.h5')
        if expected_format == 'csv':
            return ('.csv', '.csv.gz', '.zip')
        if expected_format == 'json':
            return ('.json', '.json.gz', '.zip')
        return ('.zip', '.gz', '.hdf', '.hdf5', '.h5', '.nc', '.bin', '.csv', '.json')

    @staticmethod
    def _file_sort_key(url: str) -> tuple[str, str]:
        # CHMI filenames contain observation timestamps. Sorting by that token
        # is more accurate than sorting by station/product prefixes.
        path = unquote(urlparse(url).path).lower()
        filename = path.rsplit('/', 1)[-1]
        timestamps = re.findall(r'(?<!\d)(\d{8,14})(?!\d)', filename)
        return (timestamps[-1] if timestamps else '', filename)

    def discover_resource_files(self, resource: Dict[str, Any]) -> list[Dict[str, Any]]:
        """Expand a CHMI index URL into concrete downloadable files."""
        url = resource.get('url', '')
        if not url:
            raise ValueError('Resource URL is empty')
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        if not self._is_html_response(response):
            return [dict(resource)]

        body = getattr(response, 'text', '') or response.content.decode('utf-8', errors='ignore')
        suffixes = self._allowed_suffixes(resource)
        file_links = [
            link for link in self._extract_listing_links(body, url)
            if urlparse(link).path.lower().endswith(suffixes)
        ]
        # Climate indexes contain files for hundreds of stations.  A bounded
        # allow-list keeps collection useful for Tišnov without mirroring the
        # entire national archive.  Matching is done against the filename,
        # where CHMI embeds the complete WSI station identifier.
        station_ids = [str(item) for item in resource.get('station_ids', [])]
        if station_ids and file_links:
            file_links = [
                link for link in file_links
                if any(station_id in unquote(Path(urlparse(link).path).name)
                       for station_id in station_ids)
            ]
            if not file_links:
                return []
        if not file_links:
            if resource.get('archive_all'):
                # Radar endpoints must be direct indexes. Refusing an
                # unexpected nested tree prevents an accidental broad crawl.
                raise ValidationError(
                    'Archive source does not point directly to a file listing'
                )
            resolved_url, _ = self._resolve_file_url(resource, url)
            file_links = [resolved_url]
        elif not resource.get('archive_all'):
            file_links = [max(file_links, key=self._file_sort_key)]

        discovered: list[Dict[str, Any]] = []
        for link in sorted(file_links, key=self._file_sort_key):
            item = dict(resource)
            filename = unquote(Path(urlparse(link).path).name)
            observation = parse_radar_time(filename) if item.get('source') == 'radar' else None
            item.update({
                'url': link,
                'filename': filename,
                'observation_time_utc': observation.isoformat() if observation else None,
            })
            discovered.append(item)
        return discovered

    def _resolve_file_url(
        self,
        resource: Dict[str, Any],
        url: str,
        *,
        depth: int = 0,
        visited: set[str] | None = None,
    ) -> tuple[str, requests.Response]:
        """Resolve a bounded nested climate index to one newest data file."""
        if depth > 8:
            # Climate indexes are nested; keep recursion bounded so a changed
            # server layout cannot send the collector through the whole site.
            raise ValidationError('Directory listing is nested too deeply')
        visited = visited if visited is not None else set()
        normalized_url = url.split('#', 1)[0]
        if normalized_url in visited:
            raise ValidationError('Directory listing contains a loop')
        visited.add(normalized_url)

        response = requests.get(normalized_url, timeout=60)
        response.raise_for_status()
        if not self._is_html_response(response):
            return normalized_url, response

        body = getattr(response, 'text', '') or response.content.decode('utf-8', errors='ignore')
        links = self._extract_listing_links(body, normalized_url)
        suffixes = self._allowed_suffixes(resource)
        file_links = [
            link for link in links
            if urlparse(link).path.lower().endswith(suffixes)
        ]
        if file_links:
            newest_file = max(file_links, key=self._file_sort_key)
            return self._resolve_file_url(resource, newest_file, depth=depth + 1, visited=visited)

        directories = [link for link in links if urlparse(link).path.endswith('/')]
        if not directories:
            raise ValidationError(f'No {resource.get("format", "supported")} files found in directory listing')

        interval = str(resource.get('interval', '')).strip('/').lower()
        preferred = [
            link for link in directories
            if unquote(urlparse(link).path).rstrip('/').rsplit('/', 1)[-1].lower() == interval
        ]
        # Prefer a directory matching the configured interval (for example
        # 10min). If none matches, deterministic lexical selection is safer
        # than walking every branch of a potentially huge climate tree.
        candidates = preferred or directories
        newest_directory = max(
            candidates,
            key=lambda link: unquote(urlparse(link).path).rstrip('/').rsplit('/', 1)[-1].lower(),
        )
        return self._resolve_file_url(resource, newest_directory, depth=depth + 1, visited=visited)

    def _download_response(
        self,
        resource: Dict[str, Any],
        url: str,
        response: requests.Response,
        destination: Path,
        temp_path: Path,
    ) -> Dict[str, Any]:
        """Validate a received payload, store it atomically, and catalog it."""

        temp_path.write_bytes(response.content)
        metadata = Validator.validate_file(
            temp_path,
            expected_size=resource.get('expected_size'),
            required_suffix=resource.get('suffix'),
        )
        if not Validator.basic_format_check(temp_path, resource.get('format')):
            raise ValidationError('Basic format validation failed')

        # replace() is atomic on the same volume: an interrupted collection
        # leaves only a staging .part file, never a truncated final file.
        temp_path.replace(destination)
        self.database.insert_remote_file(
            name=destination.name,
            source=resource.get('source', resource.get('product', 'unknown')),
            file_path=str(destination),
            sha256=metadata['sha256'],
            size_bytes=int(metadata['size_bytes']),
            status='downloaded',
            product=resource.get('product'),
            remote_url=url,
            observation_time_utc=resource.get('observation_time_utc'),
        )
        return {
            'status': 'downloaded',
            'path': str(destination),
            'url': url,
            'sha256': metadata['sha256'],
            'size_bytes': int(metadata['size_bytes']),
        }

    def _destination_for(self, resource: Dict[str, Any], filename: str) -> Path:
        """Build the stable local path for a discovered remote object."""
        source = resource.get('source', resource.get('product', 'unknown'))
        product = resource.get('product', 'unknown')
        target_dir = self.raw_dir / source / product
        observation = resource.get('observation_time_utc')
        if source == 'radar' and observation:
            # Date partitions keep thousands of five-minute radar composites
            # out of a single slow directory.
            timestamp = observation.replace('Z', '+00:00')
            try:
                parsed = datetime.fromisoformat(timestamp)
                target_dir = target_dir / f'{parsed.year:04d}' / f'{parsed.month:02d}' / f'{parsed.day:02d}'
            except ValueError:
                # Preserve files with unknown timestamps at product level
                # rather than discarding data from a new filename convention.
                pass
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir / filename

    def download_resource(self, resource: Dict[str, Any]) -> Dict[str, Any]:
        """Download one concrete resource unless its URL is already archived."""
        url = resource.get('url', '')
        if not url:
            raise ValueError('Resource URL is empty')

        if self.database.is_downloaded(url) and not resource.get('refresh_existing'):
            # URL-level deduplication also allows two files with identical
            # content or names to remain distinct remote observations.
            return {'status': 'already-downloaded', 'url': url}

        with tempfile.NamedTemporaryFile(dir=self.staging_dir, delete=False, suffix='.part') as tmp_file:
            temp_path = Path(tmp_file.name)

        try:
            resolved_url, response = self._resolve_file_url(resource, url)
            remote_name = unquote(Path(urlparse(resolved_url).path).name)
            filename = resource.get('filename') or remote_name or resource.get('name') or 'download'
            destination = self._destination_for(resource, filename)
            return self._download_response(resource, resolved_url, response, destination, temp_path)
        except Exception as exc:
            # Failures are cataloged with their URL so a later retry or normal
            # collection can recover them while the server still retains data.
            filename = (
                resource.get('filename')
                or unquote(Path(urlparse(url).path).name)
                or resource.get('name')
                or 'download'
            )
            destination = self._destination_for(resource, filename)
            self.database.insert_remote_file(
                name=destination.name,
                source=resource.get('source', resource.get('product', 'unknown')),
                file_path=str(destination),
                sha256=None,
                size_bytes=None,
                status='failed',
                product=resource.get('product'),
                remote_url=url,
                observation_time_utc=resource.get('observation_time_utc'),
                last_error=str(exc),
            )
            return {
                'status': 'failed',
                'path': str(destination),
                'sha256': None,
                'size_bytes': None,
                'reason': str(exc),
                'url': url,
            }
        finally:
            # This is harmless after a successful replace() and essential
            # after network, validation, or database failures.
            temp_path.unlink(missing_ok=True)
