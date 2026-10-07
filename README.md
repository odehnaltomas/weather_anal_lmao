# weather_anal_lmao

This repository contains a CHMI archival downloader for the Tišnov weather
research project. It enumerates the complete current CHMI radar indexes,
compares every remote URL with SQLite, and downloads every file not already
archived.

## Package structure
- [chmi_downloader/](chmi_downloader) with the main package modules
- [chmi_downloader/sources/](chmi_downloader/sources) for resource implementations
- [chmi_downloader/commands/](chmi_downloader/commands) for command entry points

## Implemented pieces
- [chmi_downloader/config.py](chmi_downloader/config.py) for loading [config.yaml](config.yaml)
- [chmi_downloader/database.py](chmi_downloader/database.py) for SQLite-backed remote file tracking
- [chmi_downloader/downloader.py](chmi_downloader/downloader.py) for download, checksum, size validation, format checks, atomic move, and DB update
- [chmi_downloader/sources/base.py](chmi_downloader/sources/base.py) and [chmi_downloader/sources/radar.py](chmi_downloader/sources/radar.py) for resource interfaces and radar discovery
- [chmi_downloader/validation.py](chmi_downloader/validation.py) for SHA-256 and size validation

## Usage
Run the CLI:

```bash
py -3 -m chmi_downloader.cli collect
```

Collect radar data:

```bash
py -3 -m chmi_downloader.cli collect radar
```

The first radar run can download several thousand files because it fills the
entire history still present in the public CHMI indexes. Later runs download
only missing URLs. Files keep their original CHMI names and are stored as:

```text
data/raw/radar/<product>/<year>/<month>/<day>/<filename>.hdf
```

Other collection targets are `daily`, `climate-recent`, `climate-historical`,
`station`, `current`, `metadata`, and `all`.

## Current sources and safe updates

`collect current` collects selected Tišnov-region stations from `climate/now`,
Prostějov radiosoundings, wind profiles, and Czech-area IR108BT, WV062 and Airmass
satellite imagery. Additional feeds are raw inputs; the storm analysis does
not automatically interpret them. `collect metadata` refreshes station/element
metadata. `daily` includes metadata, and historical collection includes yearly
CSV data and all matching stations/variables, rather than a single file.

Each configured directory is rediscovered recursively on every run, including
new child directories and file extensions. Traversal stays on the same host
inside the configured subtree (default limits: 100 indexes, depth 6); exceeding
a limit is reported as an error. An empty station selection is reported as a
warning. Entirely new top-level feeds still require an `additional_sources`
entry in `config.yaml`; the collector does not mirror the entire CHMI site.

Mutable climate files and metadata are refreshed even when their URL stays the
same. HTTP ETag/Last-Modified validators avoid retransferring unchanged content;
without validators the checksum is compared after downloading. Additional
feeds are processed newest first, with at most 200 new/changed/failed transfers
per source per run so initial backfill leaves time for radar collection. Set
`max_downloads_per_run` on a source to change this (0 means unlimited). Previous
UTC-day additional snapshots are rechecked once per day; current-day files and
undated files are rechecked each run. Radar observations keep URL deduplication.

Storage under the configured `F:/weather_data` root:

```text
raw/climate/now/                       current selected station observations
raw/additional/<source>/<remote path>  separate radiosonde/wind/satellite feeds
raw/metadata/<source>/                 station and element metadata
raw/versions/<source>/<product>/<URL hash>/<content hash>/<filename>
raw/unclassified/<URL hash>/<content hash>/<filename>
```

Original archived payloads are never overwritten by different bytes. Changed
content receives a separate version path and SQLite `remote_files.file_path`
points to the most recently received copy. `file_versions` retains paths and
checksums of versions seen since this upgrade, including an existing version
when it is first refreshed. A failed refresh preserves the good catalog entry
and remains eligible for `retry`. Files failing the expected basic format check
are preserved under `unclassified`, with that source in the catalog, and logged
as `FORMAT_CHANGED`. HTML error pages and empty responses are rejected. This
is format checking, not a full semantic/schema compatibility check. Unknown
extensions in additional feeds are retained as raw bytes in their source folder.

The existing scheduled radar runner also invokes `current` every 30 minutes;
no task reinstallation is needed for an installation using that runner. The
daily task refreshes recent measurements and metadata. These schedules only
run while the computer/user session permits the existing Windows tasks.

Inspect recorded files:

```bash
py -3 -m chmi_downloader.cli status
```

`status` prints a compact product-level summary. To inspect individual catalog
rows, show the latest 100 rows (or another limit):

```bash
py -3 -m chmi_downloader.cli status --details
py -3 -m chmi_downloader.cli status --details --limit 25
```

Use `--limit 0` only when you intentionally want every catalog row.

Retry failed downloads and verify archived checksums:

```bash
py -3 -m chmi_downloader.cli retry
py -3 -m chmi_downloader.cli verify
```

Analyze convective cells in an 80 x 80 km area around Tišnov:

```bash
py -3 -m chmi_downloader.cli analyze-storms
```

The analysis detects connected PseudoCAPPI areas at or above 40 dBZ, links
them between five-minute frames, estimates their direction, and separately
summarizes systems arriving from 260-290 degrees. By default it writes a JSON
report under `F:\weather_data\processed`. Threshold, area, coordinates,
direction range, and output path can be overridden, for example:

```bash
py -3 -m chmi_downloader.cli analyze-storms `
  --threshold-dbz 40 --area-km 80 `
  --west-direction-min 260 --west-direction-max 290
```

Run the tests:

```bash
py -3 -m pytest -q
```

Refresh/discovery regression tests use simulated HTTP responses and temporary
archives. Scheduling tests exercise separate Python processes and the actual
Windows PowerShell/VBScript launchers against temporary fixtures, including
lock contention, stderr output and child-process failures. They do not run the
production scheduled tasks or download data. Launcher-specific tests are
skipped when the Windows tools are unavailable.

## Windows 11 periodic execution
The project includes a Task Scheduler installer with logging and overlap
protection. It creates:

- radar collection every 30 minutes;
- selected Tišnov-region daily, 10-minute, and hourly station collection at 03:20;
- historical climate collection on the fifth day of each month;
- archive verification every Sunday.

Check the locally archived station ranges and gaps with:

```powershell
py -3 scripts\audit_station_data.py
```

1. Install Python and ensure the `py` launcher is available.
2. Install the dependencies:

```powershell
py -3 -m pip install -r requirements.txt
```

3. Install or update the scheduled tasks:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\windows_setup.ps1
```

The installer is idempotent: running it again updates the same tasks. Tasks use
the exact Python interpreter found during installation and do not depend on the
scheduled process's `PATH`.

Logs are appended to:

```text
F:\weather_data\logs\scheduled-JOB-YYYY-MM.log
```

The tasks run as the current Windows user while that user is logged on. Task
Scheduler's `StartWhenAvailable` setting catches a missed start after the
computer resumes. `IgnoreNew` and `collector.lock` prevent overlapping runs.
Scheduled collection and verification wait up to one hour for archive access,
including catalog initialization. Failed tasks retry up to six times at
ten-minute intervals. Manual commands retain a one-second wait; override it
with `--lock-timeout SECONDS`. The hidden launcher accepts both the three
arguments used by maintenance and the optional fourth job argument, and
propagates launch/child-process failures to Task Scheduler.

The archive lock covers each complete `collect`, `retry` or `verify` command;
a historical backfill can therefore delay other scheduled collection. On lock
timeout the CLI exits with code 2, while the scheduled runner records failure
and exits with code 1 for Task Scheduler. Log output alone is not a success
signal: the runner preserves both current-feed and radar exit codes and keeps
native stderr in the log for diagnosis. Restart settings apply to tasks created
or updated by the installer; updating the scripts alone does not update an
existing task's settings.

Verification checks the currently cataloged payload paths, sizes and hashes.
It reports missing/corrupt files as failures and skips old discovery placeholders
that have neither a remote URL nor a content hash. It does not establish
meteorological completeness or validate every older entry in `file_versions`.

To install the tasks and immediately start radar collection:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\install_scheduled_tasks.ps1 -RunRadarNow
```

You can also use the batch files in [scripts](scripts) directly for manual execution.
