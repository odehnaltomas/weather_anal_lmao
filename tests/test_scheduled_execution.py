"""Exercise Windows launchers and real archive-lock contention in isolation."""

import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

from filelock import FileLock
import pytest

from chmi_downloader.database import Database


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which('powershell.exe')
CSCRIPT = shutil.which('cscript.exe')
windows_only = pytest.mark.skipif(
    os.name != 'nt' or not POWERSHELL or not CSCRIPT,
    reason='Requires Windows PowerShell and Windows Script Host',
)


@pytest.mark.parametrize('command', [('collect', 'daily'), ('retry',), ('verify',)])
def test_archive_writer_waits_before_initializing_database(tmp_path, command):
    root = tmp_path / 'archive'
    root.mkdir()
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'storage': {
        'root': str(root), 'database': str(root / 'catalog.sqlite'),
        'temporary': str(root / 'staging'),
    }}))
    args = [sys.executable, '-m', 'chmi_downloader.cli', '--config', str(config)]
    # Separate processes exercise the OS lock rather than a mocked lock API.
    # A blocked writer must not even create/migrate the catalog yet.
    with FileLock(str(root / 'collector.lock')):
        blocked = subprocess.run(
            [*args, *command, '--lock-timeout', '0'], cwd=ROOT,
            capture_output=True, text=True, timeout=15,
        )
        assert blocked.returncode == 2, blocked.stdout + blocked.stderr
        assert not (root / 'catalog.sqlite').exists()
        process = subprocess.Popen(
            [*args, *command, '--lock-timeout', '10'], cwd=ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            assert 'Waiting for archive access' in process.stdout.readline()
            assert process.poll() is None
            assert not (root / 'catalog.sqlite').exists()
        except BaseException:
            process.kill()
            process.communicate()
            raise
    try:
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, stdout + stderr
        assert (root / 'catalog.sqlite').exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


@windows_only
@pytest.mark.parametrize('job', [None, '', 'daily'])
def test_hidden_launcher_accepts_optional_job_and_returns_child_failure(tmp_path, job):
    script = tmp_path / 'child with spaces.ps1'
    script.write_text(
        "param([string]$PythonExe, [string]$Job)\n"
        "@{python=$PythonExe;job=$Job} | ConvertTo-Json | "
        "Set-Content -LiteralPath (Join-Path $PSScriptRoot 'result.json')\nexit 7\n"
    )
    args = [CSCRIPT, '//B', '//NoLogo', str(ROOT / 'scripts/run_hidden.vbs'),
            POWERSHELL, str(script), 'audit python with spaces']
    if job is not None:
        args.append(job)
    result = subprocess.run(args, capture_output=True, timeout=15)
    assert result.returncode == 7
    output = json.loads((tmp_path / 'result.json').read_text(encoding='utf-8-sig'))
    assert output == {'python': 'audit python with spaces', 'job': job or ''}


@windows_only
def test_hidden_launcher_reports_failure_to_start(tmp_path):
    result = subprocess.run(
        [CSCRIPT, '//B', '//NoLogo', str(ROOT / 'scripts/run_hidden.vbs'),
         str(tmp_path / 'missing.exe'), 'missing.ps1', sys.executable],
        capture_output=True, timeout=15,
    )
    assert result.returncode != 0


@windows_only
@pytest.mark.parametrize('fail_current', [False, True])
def test_scheduled_runner_logs_stderr_and_preserves_each_exit_code(tmp_path, fail_current):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    shutil.copy2(ROOT / 'scripts/run_scheduled_job.ps1', scripts)
    package = tmp_path / 'chmi_downloader'
    package.mkdir()
    (package / '__init__.py').touch()
    (package / 'config.py').write_text(
        'from pathlib import Path\nclass Config:\n'
        '    def storage_root(self):\n'
        '        return Path(__file__).resolve().parents[1] / "archive"\n'
    )
    (package / 'cli.py').write_text(
        'import sys\nfrom pathlib import Path\n'
        'assert sys.argv[-2:] == ["--lock-timeout", "3600"]\n'
        'target = sys.argv[2]\n'
        'with Path("calls.txt").open("a") as f: f.write(target + "\\n")\n'
        'print("diagnostic from " + target, file=sys.stderr, flush=True)\n'
        'print("finished " + target, flush=True)\n'
        f'raise SystemExit(7 if {fail_current!r} and target == "current" else 0)\n'
    )
    result = subprocess.run(
        [POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
         '-File', str(scripts / 'run_scheduled_job.ps1'), '-Job', 'radar',
         '-PythonExe', sys.executable],
        cwd=tmp_path, capture_output=True, timeout=20,
    )
    assert result.returncode == (1 if fail_current else 0), result.stderr
    assert (tmp_path / 'calls.txt').read_text().splitlines() == ['current', 'radar']
    log = next((tmp_path / 'archive/logs').glob('scheduled-radar-*.log'))
    text = log.read_text(encoding='utf-8-sig')
    assert 'diagnostic from current' in text and 'finished radar' in text
    assert ('Scheduled job completed successfully.' in text) == (not fail_current)
    if fail_current:
        assert 'Downloader exited with code 7.' in text


@windows_only
@pytest.mark.parametrize('fail_historical', [False, True])
def test_maintenance_launches_monthly_and_weekly_work_and_propagates_failure(tmp_path, fail_historical):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    for name in ['run_scheduled_job.ps1', 'run_scheduled_maintenance.ps1']:
        shutil.copy2(ROOT / 'scripts' / name, scripts)
    package = tmp_path / 'chmi_downloader'
    package.mkdir()
    (package / '__init__.py').touch()
    (package / 'config.py').write_text(
        'from pathlib import Path\nclass Config:\n'
        '    def storage_root(self):\n'
        '        return Path(__file__).resolve().parents[1] / "archive"\n'
    )
    (package / 'cli.py').write_text(
        'import sys\nfrom pathlib import Path\n'
        'label = sys.argv[1] + (" " + sys.argv[2] if sys.argv[1] == "collect" else "")\n'
        'with Path("calls.txt").open("a") as f: f.write(label + "\\n")\n'
        f'raise SystemExit(7 if {fail_historical!r} and label == "collect climate-historical" else 0)\n'
    )
    entry = scripts / 'entry.ps1'
    # Exercise both maintenance branches on a fixed Sunday/monthly boundary;
    # only the temporary child package runs, never the production archive.
    entry.write_text(
        'param([string]$PythonExe)\n'
        "function Get-Date { [datetime]'2026-04-05' }\n"  # Sunday, fifth of month.
        "& (Join-Path $PSScriptRoot 'run_scheduled_maintenance.ps1') -PythonExe $PythonExe\n"
        'exit $LASTEXITCODE\n'
    )
    result = subprocess.run(
        [CSCRIPT, '//B', '//NoLogo', str(ROOT / 'scripts/run_hidden.vbs'),
         POWERSHELL, str(entry), sys.executable],
        cwd=tmp_path, capture_output=True, timeout=25,
    )
    assert result.returncode == (1 if fail_historical else 0), result.stderr
    assert (tmp_path / 'calls.txt').read_text().splitlines() == ['collect climate-historical', 'verify']


@pytest.mark.parametrize('corrupt', [False, True])
def test_verification_skips_legacy_placeholders_but_detects_damaged_payloads(tmp_path, corrupt):
    catalog = tmp_path / 'catalog.sqlite'
    database = Database(catalog)
    database.insert_remote_file('legacy', 'maxz', str(tmp_path / 'placeholder'), None, None, 'skipped')
    payload = tmp_path / 'payload.json'
    good = b'{"value":1}'
    payload.write_bytes(b'{"value":2}' if corrupt else good)
    database.insert_remote_file(
        payload.name, 'climate', str(payload), hashlib.sha256(good).hexdigest(),
        len(good), 'downloaded', remote_url='https://example.test/payload.json',
    )
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'storage': {
        'root': str(tmp_path), 'database': str(catalog),
        'temporary': str(tmp_path / 'staging'),
    }}))
    result = subprocess.run(
        [sys.executable, '-m', 'chmi_downloader.cli', '--config', str(config), 'verify'],
        cwd=ROOT, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == (1 if corrupt else 0), result.stderr
    assert 'skipped legacy=1' in result.stdout
    statuses = {row['name']: row['status'] for row in database.list_remote_files()}
    assert statuses == {'legacy': 'skipped', 'payload.json': 'corrupt' if corrupt else 'verified'}
