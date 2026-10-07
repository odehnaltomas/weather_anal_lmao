"""Command-line interface for collection, retry, verification, and status."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Type
from urllib.parse import unquote, urlparse

REQUIRED_PACKAGES = {
    'filelock': 'filelock',
    'requests': 'requests',
    'yaml': 'PyYAML',
}

for module_name, package_name in REQUIRED_PACKAGES.items():
    # Fail early with one actionable message instead of a later traceback.
    try:
        __import__(module_name)
    except ImportError:
        print(
            f"Missing dependency: {package_name}. Install it with 'py -3 -m pip install -r requirements.txt'.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None

from .analysis.radar import RadarAnalyzer
from .config import Config
from .database import Database
from .downloader import Downloader
from .ingest import IngestionService
from .sources.climate_historical import ClimateHistoricalResource
from .sources.climate_recent import ClimateRecentResource
from .sources.radar import RadarResource
from .sources.station_measurements import StationMeasurementsResource
from .sources.base import Resource
from .sources.current import CurrentResource, MetadataResource
from .validation import Validator


def print_collection_progress(event: dict[str, object]) -> None:
    """Render compact progress suitable for interactive use and log files."""
    event_type = event.get('event')
    product = event.get('product') or 'unknown'
    if event_type == 'discovering':
        print(f'[{product}] Reading CHMI index...', flush=True)
    elif event_type == 'discovered':
        print(f'[{product}] Found {event.get("count", 0)} remote files.', flush=True)
        if not event.get('count'):
            print(f'[{product}] WARNING: No files matched the configured source/stations.', flush=True)
    elif event_type == 'deferred':
        print(f'[{product}] Transfer limit reached; {event["count"]} remaining candidates deferred to the next run.', flush=True)
    elif event_type == 'result':
        result = event.get('result')
        if not isinstance(result, dict):
            return
        status = result.get('status')
        index = event.get('index')
        total = event.get('total')
        position = f' {index}/{total}' if index and total else ''
        if status == 'downloaded':
            print(
                f'[{product}]{position} Downloaded {Path(str(result["path"])).name} '
                f'({result.get("size_bytes", 0)} bytes).',
                flush=True,
            )
            if result.get('format_changed'):
                print(f'[{product}] FORMAT_CHANGED: saved separately in {result["path"]}', flush=True)
        elif status == 'failed':
            print(
                f'[{product}]{position} FAILED: {result.get("reason", "unknown error")}',
                file=sys.stderr,
                flush=True,
            )
        elif status == 'already-downloaded':
            # This event is emitted only at checkpoints, not for every file.
            print(f'[{product}]{position} Archive comparison in progress...', flush=True)


def build_parser() -> argparse.ArgumentParser:
    """Build the public command/target interface used by scripts and users."""
    parser = argparse.ArgumentParser(description='CHMI weather downloader')
    parser.add_argument('--config', default=None, help='Path to config.yaml')
    parser.add_argument(
        '--lock-timeout', type=int, default=1,
        help='Seconds to wait for another archive writer (default: 1).',
    )
    parser.add_argument(
        '--details',
        action='store_true',
        help='Show individual database rows for the status command.',
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=100,
        help='Maximum rows shown by status --details (default: 100; 0 means all).',
    )
    parser.add_argument(
        'command',
        nargs='?',
        default='collect',
        choices=['collect', 'status', 'retry', 'verify', 'analyze', 'analyze-storms'],
    )
    parser.add_argument(
        'target',
        nargs='?',
        default='all',
        choices=['all', 'radar', 'current', 'metadata', 'daily', 'climate-recent', 'climate-historical', 'station'],
    )
    parser.add_argument('--latitude', type=float, default=49.348719)
    parser.add_argument('--longitude', type=float, default=16.424380)
    parser.add_argument('--area-km', type=float, default=80.0)
    parser.add_argument('--threshold-dbz', type=float, default=40.0)
    parser.add_argument('--west-direction-min', type=float, default=260.0)
    parser.add_argument('--west-direction-max', type=float, default=290.0)
    parser.add_argument(
        '--output',
        default=None,
        help='JSON destination for analyze-storms (defaults under processed/).',
    )
    return parser


def main() -> None:
    """Dispatch the requested CLI command."""
    parser = build_parser()
    args = parser.parse_args()
    if args.lock_timeout < 0:
        parser.error('--lock-timeout must be non-negative')
    config = Config(args.config)
    if args.command in {'collect', 'retry', 'verify'}:
        from filelock import FileLock, Timeout

        config.storage_root().mkdir(parents=True, exist_ok=True)
        lock_path = config.storage_root() / 'collector.lock'
        print(f'Waiting for archive access (up to {args.lock_timeout} seconds).', flush=True)
        try:
            # Lock before catalog initialization as well as file writes. This
            # also serializes simultaneous first-run schema migrations.
            # Verification updates catalog status, so it shares the writer
            # lock with collection and retry to avoid inspecting mid-refresh.
            with FileLock(str(lock_path), timeout=args.lock_timeout):
                run_command(args, config)
        except Timeout:
            # Exit nonzero so scheduled runners can request another attempt
            # instead of silently losing a daily run after sleep/resume.
            print('Another archive writer is still running; retry this job.', file=sys.stderr)
            raise SystemExit(2) from None
    else:
        run_command(args, config)


def run_command(args: argparse.Namespace, config: Config) -> None:
    """Execute a command after acquiring the archive lock when needed."""
    database = Database(config.database_path())
    downloader = Downloader(config, database)
    ingestion = IngestionService(config, database, downloader)

    if args.command == 'collect':
        # Target names map to source classes so collection can be scheduled at
        # different frequencies without duplicating downloader logic.
        source_types: dict[str, list[Type[Resource]]] = {
            'all': [
                RadarResource, ClimateRecentResource,
                ClimateHistoricalResource, StationMeasurementsResource,
                CurrentResource, MetadataResource,
            ],
            'radar': [RadarResource],
            'current': [CurrentResource],
            'metadata': [MetadataResource],
            'daily': [ClimateRecentResource, StationMeasurementsResource, MetadataResource],
            'climate-recent': [ClimateRecentResource],
            'climate-historical': [ClimateHistoricalResource],
            'station': [StationMeasurementsResource],
        }
        resources = [
            item
            for resource_cls in source_types[args.target]
            for item in resource_cls().discover(config.data)
        ]
        print(f'Collecting target: {args.target}')
        print(f'Storage root: {config.storage_root()}')
        results = ingestion.collect(resources, progress=print_collection_progress)
        counts = Counter(str(item.get('status', 'unknown')) for item in results)
        print('\nCollection summary:')
        print(f'  Remote files considered: {len(results)}')
        print(f'  Newly downloaded:        {counts["downloaded"]}')
        print(f'  Already archived:        {counts["already-downloaded"]}')
        print(f'  Failed:                  {counts["failed"]}')
        if any(item.get('status') == 'failed' for item in results):
            raise SystemExit(1)
    elif args.command == 'status':
        summary = database.status_summary()
        print(f'Catalog: {config.database_path()}')
        print(f'Storage root: {config.storage_root()}')
        print('\nCollection status:')
        header = (
            f'{"Source":<18} {"Product":<24} {"Total":>7} '
            f'{"Ready":>7} {"Failed":>7} {"Skipped":>7} '
            f'{"Missing":>7} {"Corrupt":>7}'
        )
        print(header)
        print('-' * len(header))
        for row in summary:
            ready = int(row['downloaded'] or 0) + int(row['verified'] or 0)
            print(
                f'{str(row["source"]):<18.18} '
                f'{str(row["product"]):<24.24} '
                f'{int(row["total"]):>7} '
                f'{ready:>7} '
                f'{int(row["failed"] or 0):>7} '
                f'{int(row["skipped"] or 0):>7} '
                f'{int(row["missing"] or 0):>7} '
                f'{int(row["corrupt"] or 0):>7}'
            )

        totals = {
            key: sum(int(row[key] or 0) for row in summary)
            for key in (
                'total', 'downloaded', 'verified', 'failed',
                'skipped', 'missing', 'corrupt',
            )
        }
        print('\nTotals:')
        print(f'  Catalog records: {totals["total"]}')
        print(f'  Downloaded/verified: {totals["downloaded"] + totals["verified"]}')
        print(f'  Failed: {totals["failed"]}')
        print(f'  Skipped (legacy): {totals["skipped"]}')
        print(f'  Missing: {totals["missing"]}')
        print(f'  Corrupt: {totals["corrupt"]}')

        active_failures = [
            row for row in database.failed_files() if row.get('remote_url')
        ]
        if active_failures:
            print('\nRetryable failures:')
            for row in active_failures[:10]:
                print(f'  {row["product"]}: {row["remote_url"]}')
            if len(active_failures) > 10:
                print(f'  ... and {len(active_failures) - 10} more')

        if args.details:
            rows = database.list_remote_files()
            shown = rows if args.limit == 0 else rows[-max(args.limit, 0):]
            print(f'\nDetailed rows ({len(shown)} of {len(rows)}):')
            for row in shown:
                print(row)
    elif args.command == 'retry':
        resources = []
        for row in database.failed_files():
            # Legacy failed rows lack a remote URL and cannot be retried.
            if not row.get('remote_url'):
                continue
            # Use the remote URL rather than a possibly stale placeholder name.
            remote_name = unquote(Path(urlparse(str(row['remote_url'])).path).name)
            suffix = Path(remote_name).suffix.lower()
            resources.append({
                'name': remote_name,
                'filename': remote_name,
                'source': row['source'],
                'product': row['product'],
                'url': row['remote_url'],
                'observation_time_utc': row['observation_time_utc'],
                'format': 'hdf5' if suffix in {'.hdf', '.h5', '.hdf5'} else 'json' if suffix == '.json' else 'csv' if suffix == '.csv' else None,
                'refresh_existing': True,
                'accept_changed_format': True,
            })
        for item in [downloader.download_resource(resource) for resource in resources]:
            print(item)
    elif args.command == 'analyze':
        analyzer = RadarAnalyzer()
        for row in database.list_remote_files():
            if row['source'] != 'radar':
                continue
            path = Path(str(row['file_path']))
            if path.exists():
                print(analyzer.analyze(path, {'sha256': row['sha256']}))
    elif args.command == 'analyze-storms':
        # Heavy scientific dependencies are imported only for this command so
        # scheduled collection remains lightweight and independent.
        from .analysis.storm_tracking import StormTrackAnalyzer

        analyzer = StormTrackAnalyzer(
            latitude=args.latitude,
            longitude=args.longitude,
            area_size_km=args.area_km,
            threshold_dbz=args.threshold_dbz,
            west_direction_min=args.west_direction_min,
            west_direction_max=args.west_direction_max,
        )
        result = analyzer.analyze(
            database.radar_files('pseudocappi2km'),
            database.radar_files('echotop'),
        )
        coverage = result['coverage']
        first_day = str(coverage['first_utc'])[:10].replace('-', '')
        last_day = str(coverage['last_utc'])[:10].replace('-', '')
        output = Path(args.output) if args.output else (
            config.processed_dir() / f'tisnov-storm-tracks-{first_day}-{last_day}.json'
        )
        analyzer.write_json(result, output)
        summary = result['summary']
        print('Tišnov storm-track analysis')
        print(f'  PseudoCAPPI frames: {coverage["pcappi_frames"]}')
        print(f'  Cell detections: {summary["detections"]}')
        print(f'  Tracks (minimum length): {summary["tracks_at_least_minimum_length"]}')
        print(f'  Western arrivals in 80 x 80 km area: {summary["western_arrival_tracks"]}')
        print(f'  Western arrivals in Tišnov corridor: {summary["western_tisnov_corridor_tracks"]}')
        print(f'  Crossed Tišnov (5 km): {summary["western_crossed_tisnov_5km"]}')
        print(f'  Dissipated before Tišnov: {summary["western_dissipated_before_tisnov"]}')
        print(f'  Weakened by at least 5 dB: {summary["western_weakened_at_least_5db"]}')
        print(f'  Formed near Tišnov then moved east: {summary["formed_near_tisnov_then_east"]}')
        print(f'  JSON: {output}')
    elif args.command == 'verify':
        failed = 0
        verified = 0
        skipped_legacy = 0
        for row in database.list_remote_files():
            # Early catalogs include failed/skipped discovery placeholders,
            # not downloaded payloads. Do not label those as lost archives.
            if not row.get('remote_url') and not row.get('sha256'):
                skipped_legacy += 1
                continue
            path = Path(str(row['file_path']))
            try:
                metadata = Validator.validate_file(
                    path,
                    expected_size=row['size_bytes'],
                    expected_sha256=row['sha256'],
                )
                database.update_remote_file_status(str(path), 'verified')
                verified += 1
                print({'status': 'verified', 'path': str(path), **metadata})
            except Exception as exc:
                failed += 1
                # Missing and corrupt are distinct operational states: missing
                # files may be redownloaded, while corrupt files need review.
                database.update_remote_file_status(str(path), 'corrupt' if path.exists() else 'missing')
                print({'status': 'failed', 'path': str(path), 'reason': str(exc)})
        print(f'Verification summary: verified={verified}, failed={failed}, skipped legacy={skipped_legacy}')
        if failed:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
