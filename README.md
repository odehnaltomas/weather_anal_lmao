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
`station`, and `all`.

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

## Windows 11 periodic execution
The project includes a Task Scheduler installer with logging and overlap
protection. It creates:

- radar collection every 30 minutes;
- selected Tišnov-region daily, 10-minute, and hourly station collection at 03:20;

Check the locally archived station ranges and gaps with:

```powershell
py -3 scripts\audit_station_data.py
```
- historical climate collection on the fifth day of each month;
- archive verification every Sunday.

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
F:\weather_data\logs\scheduled-YYYY-MM.log
```

The tasks run as the current Windows user while that user is logged on. Task
Scheduler's `StartWhenAvailable` setting catches a missed start after the
computer resumes. `IgnoreNew` and `collector.lock` prevent overlapping runs.

To install the tasks and immediately start radar collection:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\install_scheduled_tasks.ps1 -RunRadarNow
```

You can also use the batch files in [scripts](scripts) directly for manual execution.
