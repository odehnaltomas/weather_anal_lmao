"""Discover CHMI indexes and download validated files into the local archive."""

from __future__ import annotations

import re
import hashlib
import time
import tempfile
from datetime import datetime, timezone
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

    def _get(self, url: str, headers: dict | None = None) -> requests.Response:
        """Apply the configured pacing and retries to index and payload requests."""
        network = self.config.get('network', {}) if hasattr(self.config, 'get') else {}
        attempts = max(1, int(network.get('retries', 0)) + 1)
        for attempt in range(attempts):
            if network.get('delay_seconds'):
                time.sleep(float(network['delay_seconds']))
            options = {'timeout': network.get('timeout_seconds', 60)}
            request_headers = dict(headers or {})
            if network.get('user_agent'):
                request_headers['User-Agent'] = network['user_agent']
            if request_headers:
                options['headers'] = request_headers
            try:
                response = requests.get(url, **options)
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                status = getattr(exc.response, 'status_code', None)
                # Retry connection failures, throttling and server errors.
                # Other client errors usually need a URL/configuration change.
                if attempt + 1 == attempts or (status and status < 500 and status != 429):
                    raise
                time.sleep(min(2 ** attempt, 8))

    @staticmethod
    def _safe_part(value: str) -> str:
        """Reject traversal and Windows-special names before composing a path."""
        if (not value or value in {'.', '..'} or re.search(r'[<>:"/\\|?*\x00-\x1f]', value)
                or value.endswith((' ', '.'))
                or re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', value)):
            raise ValidationError(f'Unsafe archive path component: {value!r}')
        return value

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
            link = urljoin(base_url, cleaned)
            base, target = urlparse(base_url), urlparse(link)
            base_path, target_path = unquote(base.path), unquote(target.path)
            if (target.scheme == base.scheme and target.netloc == base.netloc
                    and target_path.startswith(base_path)
                    and target_path != base_path and not target.query and not target.fragment
                    and not any(p in {'.', '..'} for p in target_path.split('/'))
                    and '\\' not in target_path):
                candidates.append(link)
        return list(dict.fromkeys(candidates))

    @staticmethod
    def _is_html_response(response: requests.Response) -> bool:
        """Detect directory/error pages even when Content-Type is incorrect."""
        content_type = response.headers.get('content-type', '').lower()
        body = response.content[:2048].decode('utf-8', errors='ignore').lstrip()
        return 'text/html' in content_type or body.lower().startswith(('<html', '<!doctype html'))

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
        timestamps = re.findall(r'(?<!\d)(20\d{2}(?:\d{2}){0,5})(?!\d)', filename)
        formats = {4: '%Y', 6: '%Y%m', 8: '%Y%m%d', 10: '%Y%m%d%H',
                   12: '%Y%m%d%H%M', 14: '%Y%m%d%H%M%S'}
        parsed = []
        for token in timestamps:
            try:
                parsed.append(datetime.strptime(token, formats[len(token)]).strftime('%Y%m%d%H%M%S'))
            except ValueError:
                continue
        # Soundings use YYMMDDHH instead of the four-digit year used by the
        # other feeds. Normalize both before ordering observations together.
        sonde = re.match(r'^(\d{8})_prostejov_', filename)
        if sonde:
            try:
                parsed.append(datetime.strptime(sonde[1], '%y%m%d%H').strftime('%Y%m%d%H%M%S'))
            except ValueError:
                pass
        return (parsed[-1] if parsed else '', filename)

    def discover_resource_files(self, resource: Dict[str, Any]) -> list[Dict[str, Any]]:
        """Expand a CHMI index URL into concrete downloadable files."""
        url = resource.get('url', '')
        if not url:
            raise ValueError('Resource URL is empty')
        if resource.get('recursive'):
            return self._discover_tree(resource)
        response = self._get(url)
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
        for link in sorted(file_links, key=self._file_sort_key, reverse=resource.get('newest_first', False)):
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

    def _discover_tree(self, resource: Dict[str, Any]) -> list[Dict[str, Any]]:
        """Walk only a configured subtree; preserve its relative layout and all formats."""
        root = resource['url'].rstrip('/') + '/'
        pending = [(root, 0)]
        visited: set[str] = set()
        files: list[Dict[str, Any]] = []
        stations = [str(s) for s in resource.get('station_ids', [])]
        while pending:
            url, depth = pending.pop()
            if url in visited:
                continue
            # Fail visibly rather than treating a truncated traversal as a
            # complete archive. Limits are adjustable per configured source.
            if depth > resource.get('max_depth', 6) or len(visited) >= resource.get('max_indexes', 100):
                raise ValidationError('Discovery limit reached; increase max_depth/max_indexes for this source')
            visited.add(url)
            response = self._get(url)
            if not self._is_html_response(response):
                raise ValidationError(f'Expected a directory index: {url}')
            for link in self._extract_listing_links(response.text, url):
                if urlparse(link).path.endswith('/'):
                    pending.append((link, depth + 1))
                    continue
                relative = unquote(urlparse(link).path)[len(unquote(urlparse(root).path)):]
                parts = [self._safe_part(p) for p in relative.split('/')]
                filename = parts[-1]
                if stations and not any(re.search(rf'(?<!\d){re.escape(s)}(?!\d)', filename) for s in stations):
                    continue
                if resource.get('filename_pattern') and not re.search(resource['filename_pattern'], filename):
                    continue
                item = dict(resource, url=link, filename=filename, relative_path=relative,
                            recursive=False, accept_changed_format=True)
                # Completed, timestamped snapshots can be rechecked daily;
                # current-day files still refresh on every collection.
                dates = re.findall(r'(?<!\d)(20\d{6})(?:\d{4,6})?(?!\d)', filename)
                if not dates and re.match(r'^\d{8}_Prostejov_', filename):
                    dates = ['20' + filename[:6]]
                if dates and resource.get('older_recheck_seconds'):
                    if dates[-1] < datetime.now(timezone.utc).strftime('%Y%m%d'):
                        item['recheck_seconds'] = resource['older_recheck_seconds']
                observation = parse_radar_time(filename) if item.get('source') == 'radar' else None
                item['observation_time_utc'] = observation.isoformat() if observation else None
                if resource.get('format') == 'auto':
                    suffix = Path(filename).suffix.lower()
                    # Unknown formats remain raw bytes; discovery must not
                    # silently drop a newly published extension.
                    item['format'] = {'.json': 'json', '.csv': 'csv', '.hdf': 'hdf5', '.h5': 'hdf5', '.hdf5': 'hdf5'}.get(suffix)
                files.append(item)
        # Sort the whole tree before ingestion applies its per-run budget,
        # otherwise an older directory could delay current observations.
        return sorted(files, key=lambda item: self._file_sort_key(item['url']), reverse=True)

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

        response = self._get(normalized_url)
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
        format_changed = not Validator.basic_format_check(temp_path, resource.get('format'))
        if format_changed:
            if not resource.get('accept_changed_format'):
                raise ValidationError('Basic format validation failed')
            # Keep unexpected nonempty payloads for inspection without
            # presenting them to downstream code as the expected format.
            destination = self.raw_dir / 'unclassified' / hashlib.sha256(url.encode()).hexdigest() / metadata['sha256'] / destination.name
            destination.parent.mkdir(parents=True, exist_ok=True)

        previous = self.database.remote_file(url)
        if previous and previous['sha256'] and Path(previous['file_path']).is_file():
            # Adopt copies made before version tracking was introduced. An
            # unchanged response must not replace their established path.
            old_path = Path(previous['file_path'])
            self.database.remember_version(url, previous['sha256'], old_path, old_path.stat().st_size)
            if previous['sha256'] == metadata['sha256'] and Validator.sha256(old_path) == metadata['sha256']:
                self.database.set_http_metadata(url, response.headers)
                return {'status': 'already-downloaded', 'url': url, 'path': str(old_path)}
        if destination.exists() and Validator.sha256(destination) != metadata['sha256']:
            # Keep the original pathname intact. Each changed payload receives
            # a content-addressed path, even if two URLs share a filename.
            destination = self.raw_dir / 'versions' / self._safe_part(str(resource.get('source', 'unknown'))) / self._safe_part(str(resource.get('product', 'unknown'))) / hashlib.sha256(url.encode()).hexdigest() / metadata['sha256'] / destination.name
            destination.parent.mkdir(parents=True, exist_ok=True)

        # replace() is atomic on the same volume: an interrupted collection
        # leaves only a staging .part file, never a truncated final file.
        temp_path.replace(destination)
        self.database.insert_remote_file(
            name=destination.name,
            source='unclassified' if format_changed else resource.get('source', resource.get('product', 'unknown')),
            file_path=str(destination),
            sha256=metadata['sha256'],
            size_bytes=int(metadata['size_bytes']),
            status='downloaded',
            product=resource.get('product'),
            remote_url=url,
            observation_time_utc=resource.get('observation_time_utc'),
        )
        self.database.remember_version(url, metadata['sha256'], destination, int(metadata['size_bytes']))
        self.database.set_http_metadata(url, response.headers)
        return {
            'status': 'downloaded',
            'path': str(destination),
            'url': url,
            'sha256': metadata['sha256'],
            'size_bytes': int(metadata['size_bytes']),
            'format_changed': format_changed,
        }

    def _destination_for(self, resource: Dict[str, Any], filename: str) -> Path:
        """Build the stable local path for a discovered remote object."""
        source = self._safe_part(str(resource.get('source', resource.get('product', 'unknown'))))
        product = self._safe_part(str(resource.get('product', 'unknown')))
        filename = self._safe_part(filename)
        target_dir = self.raw_dir / source / product
        if resource.get('relative_path'):
            parts = [self._safe_part(p) for p in resource['relative_path'].split('/')]
            target_dir = target_dir.joinpath(*parts[:-1])
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
        """Archive one resource, conditionally refreshing mutable URLs.

        Immutable feeds skip existing URLs. Mutable feeds use HTTP validators
        when available, then compare content hashes before retaining a version.
        """
        url = resource.get('url', '')
        if not url:
            raise ValueError('Resource URL is empty')

        if self.database.is_downloaded(url) and not resource.get('refresh_existing'):
            # URL-level deduplication also allows two files with identical
            # content or names to remain distinct remote observations.
            return {'status': 'already-downloaded', 'url': url}

        previous = self.database.remote_file(url)
        # Only successful checks qualify for the age-based shortcut; an earlier
        # refresh failure must remain retryable even if a good local copy exists.
        if (previous and previous.get('checked_at') and not previous.get('last_error')
                and resource.get('recheck_seconds') and self.database.is_downloaded(url)):
            elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(previous['checked_at'])).total_seconds()
            if elapsed < resource['recheck_seconds']:
                return {'status': 'already-downloaded', 'url': url, 'path': previous['file_path']}

        with tempfile.NamedTemporaryFile(dir=self.staging_dir, delete=False, suffix='.part') as tmp_file:
            temp_path = Path(tmp_file.name)

        try:
            previous = self.database.remote_file(url)
            headers = {}
            if previous and self.database.is_downloaded(url) and resource.get('refresh_existing'):
                if previous.get('etag'):
                    headers['If-None-Match'] = previous['etag']
                if previous.get('remote_modified'):
                    headers['If-Modified-Since'] = previous['remote_modified']
            if headers:
                resolved_url, response = url, self._get(url, headers)
                if response.status_code == 304:
                    # Retain the previous validators when 304 omits them.
                    self.database.set_http_metadata(url, {
                        'ETag': response.headers.get('ETag', previous.get('etag')),
                        'Last-Modified': response.headers.get('Last-Modified', previous.get('remote_modified')),
                    })
                    return {'status': 'already-downloaded', 'url': url, 'path': previous['file_path']}
                if self._is_html_response(response):
                    raise ValidationError('HTML response received instead of a data file')
            else:
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
                last_error=str(exc) or type(exc).__name__,
            )
            return {
                'status': 'failed',
                'path': str(destination),
                'sha256': None,
                'size_bytes': None,
                'reason': str(exc) or type(exc).__name__,
                'url': url,
            }
        finally:
            # This is harmless after a successful replace() and essential
            # after network, validation, or database failures.
            temp_path.unlink(missing_ok=True)
