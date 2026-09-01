from pathlib import Path

import pytest

from chmi_downloader.database import Database
from chmi_downloader.downloader import Downloader
from chmi_downloader.config import Config


class DummyConfig:
    def __init__(self, tmp_path):
        self._tmp_path = tmp_path

    def staging_dir(self):
        return str(self._tmp_path / 'staging')

    def raw_dir(self):
        return str(self._tmp_path / 'raw')

    def processed_dir(self):
        return str(self._tmp_path / 'processed')


def test_downloader_skips_html_response(tmp_path, monkeypatch):
    class DummyResponse:
        def __init__(self):
            self.headers = {'content-type': 'text/html'}
            self.content = b'<html></html>'
            self.text = self.content.decode()

        def raise_for_status(self):
            return None

    def fake_get(url, timeout=60):
        return DummyResponse()

    import requests
    monkeypatch.setattr(requests, 'get', fake_get)

    config = DummyConfig(tmp_path)
    db = Database(tmp_path / 'catalog.sqlite')
    downloader = Downloader(config, db)
    result = downloader.download_resource({'name': 'test.html', 'product': 'radar', 'url': 'https://example.test'})
    assert result['status'] == 'failed'
    assert result['reason'] == 'No supported files found in directory listing'


def test_downloader_uses_newest_matching_file_from_listing(tmp_path, monkeypatch):
    class DummyResponse:
        def __init__(self, content, content_type):
            self.headers = {'content-type': content_type}
            self.content = content
            self.text = content.decode('utf-8', errors='ignore')

        def raise_for_status(self):
            return None

    listing = DummyResponse(
        b'<a href="../">parent</a>'
        b'<a href="T_TEST_C_OKPR_20260726120000.hdf">old</a>'
        b'<a href="T_TEST_C_OKPR_20260726120500.hdf">new</a>',
        'text/html',
    )
    hdf = DummyResponse(b'\x89HDF\r\n\x1a\npayload', 'application/octet-stream')
    requested = []

    def fake_get(url, timeout=60):
        requested.append(url)
        return listing if url.endswith('/') else hdf

    import requests
    monkeypatch.setattr(requests, 'get', fake_get)

    config = DummyConfig(tmp_path)
    db = Database(tmp_path / 'catalog.sqlite')
    downloader = Downloader(config, db)
    result = downloader.download_resource({
        'name': 'radar.hdf5',
        'product': 'maxz',
        'format': 'hdf5',
        'url': 'https://example.test/radar/',
    })

    assert result['status'] == 'downloaded'
    assert Path(result['path']).name == 'T_TEST_C_OKPR_20260726120500.hdf'
    assert requested == [
        'https://example.test/radar/',
        'https://example.test/radar/T_TEST_C_OKPR_20260726120500.hdf',
    ]


def test_archive_discovery_returns_every_radar_file(tmp_path, monkeypatch):
    class DummyResponse:
        headers = {'content-type': 'text/html'}
        content = (
            b'<a href="../">parent</a>'
            b'<a href="T_PASV23_C_OKPR_20260726120000.hdf">first</a>'
            b'<a href="T_PASV23_C_OKPR_20260726120500.hdf">second</a>'
        )
        text = content.decode()

        def raise_for_status(self):
            return None

    import requests
    monkeypatch.setattr(requests, 'get', lambda url, timeout=60: DummyResponse())

    downloader = Downloader(DummyConfig(tmp_path), Database(tmp_path / 'catalog.sqlite'))
    files = downloader.discover_resource_files({
        'source': 'radar',
        'product': 'maxz',
        'format': 'hdf5',
        'archive_all': True,
        'url': 'https://example.test/radar/',
    })

    assert len(files) == 2
    assert files[1]['filename'].endswith('120500.hdf')
    assert files[1]['observation_time_utc'] == '2026-07-26T12:05:00+00:00'


def test_station_archive_filters_allowlisted_stations_and_keeps_all_days(tmp_path, monkeypatch):
    class DummyResponse:
        headers = {'content-type': 'text/html'}
        content = (
            b'<a href="../">parent</a>'
            b'<a href="10m-0-203-0-41501115001-20260803.json">tisnov-old</a>'
            b'<a href="10m-0-203-0-41501115001-20260804.json">tisnov-new</a>'
            b'<a href="10m-0-203-0-99999999999-20260804.json">unrelated</a>'
        )
        text = content.decode()

        def raise_for_status(self):
            return None

    import requests
    monkeypatch.setattr(requests, 'get', lambda url, timeout=60: DummyResponse())

    downloader = Downloader(DummyConfig(tmp_path), Database(tmp_path / 'catalog.sqlite'))
    files = downloader.discover_resource_files({
        'source': 'climate',
        'product': 'station_10min',
        'format': 'json',
        'archive_all': True,
        'station_ids': ['41501115001'],
        'url': 'https://example.test/10min/',
    })

    assert [item['filename'] for item in files] == [
        '10m-0-203-0-41501115001-20260803.json',
        '10m-0-203-0-41501115001-20260804.json',
    ]


def test_station_archive_returns_empty_when_no_configured_station_is_present(tmp_path, monkeypatch):
    class DummyResponse:
        headers = {'content-type': 'text/html'}
        content = b'<a href="unrelated-20260804.json">unrelated</a>'
        text = content.decode()

        def raise_for_status(self):
            return None

    import requests
    monkeypatch.setattr(requests, 'get', lambda url, timeout=60: DummyResponse())

    downloader = Downloader(DummyConfig(tmp_path), Database(tmp_path / 'catalog.sqlite'))
    files = downloader.discover_resource_files({
        'source': 'climate',
        'product': 'station_10min',
        'format': 'json',
        'archive_all': True,
        'station_ids': ['41501115001'],
        'url': 'https://example.test/10min/',
    })

    assert files == []
