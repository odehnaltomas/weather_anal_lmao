from pathlib import Path
import sqlite3

import pytest
import requests

from chmi_downloader.database import Database
from chmi_downloader.downloader import Downloader
from chmi_downloader.sources.climate_historical import ClimateHistoricalResource
from chmi_downloader.sources.current import CurrentResource, MetadataResource
from test_downloader_pipeline import DummyConfig


class Response:
    def __init__(self, content=b'{"value":1}', headers=None, status=200):
        self.content = content
        self.text = content.decode('utf-8', errors='replace')
        self.headers = requests.structures.CaseInsensitiveDict(headers or {})
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


@pytest.fixture
def collector(tmp_path):
    return Downloader(DummyConfig(tmp_path), Database(tmp_path / 'catalog.sqlite'))


def resource(**updates):
    return dict(source='climate', product='now', format='json',
                url='https://example.test/now/today.json', refresh_existing=True,
                accept_changed_format=True, **updates)


def test_changed_payload_preserves_both_versions_and_latest_catalog(collector, monkeypatch):
    responses = iter([Response(b'{"value":1}'), Response(b'{"value":2}'), Response(b'{"value":2}')])
    monkeypatch.setattr(requests, 'get', lambda *a, **k: next(responses))
    first = collector.download_resource(resource())
    second = collector.download_resource(resource())
    third = collector.download_resource(resource())
    assert Path(first['path']).read_bytes() == b'{"value":1}'
    assert Path(second['path']).read_bytes() == b'{"value":2}'
    assert 'versions' in Path(second['path']).parts
    assert third['status'] == 'already-downloaded'
    assert collector.database.remote_file(resource()['url'])['file_path'] == second['path']
    with sqlite3.connect(collector.database.db_path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM file_versions').fetchone()[0] == 2


def test_conditional_refresh_304_does_not_replace_data(collector, monkeypatch):
    calls = []
    responses = iter([Response(headers={'ETag': '"first"'}), Response(b'', status=304)])
    def get(url, **kwargs):
        calls.append(kwargs)
        return next(responses)
    monkeypatch.setattr(requests, 'get', get)
    first = collector.download_resource(resource())
    second = collector.download_resource(resource())
    assert calls[1]['headers']['If-None-Match'] == '"first"'
    assert second['status'] == 'already-downloaded'
    assert Path(first['path']).read_bytes() == b'{"value":1}'


def test_failed_refresh_keeps_successful_catalog_and_is_retryable(collector, monkeypatch):
    responses = iter([Response(), Response(b'', status=503)])
    monkeypatch.setattr(requests, 'get', lambda *a, **k: next(responses))
    first = collector.download_resource(resource())
    assert collector.download_resource(resource())['status'] == 'failed'
    assert collector.database.is_downloaded(resource()['url'])
    assert collector.database.remote_file(resource()['url'])['sha256'] == first['sha256']
    assert len(collector.database.failed_files()) == 1


def test_new_format_is_preserved_separately(collector, monkeypatch):
    responses = iter([Response(), Response(b'completely different binary format')])
    monkeypatch.setattr(requests, 'get', lambda *a, **k: next(responses))
    first = collector.download_resource(resource())
    changed = collector.download_resource(resource())
    assert changed['status'] == 'downloaded'
    assert changed['format_changed']
    assert 'unclassified' in Path(changed['path']).parts
    assert Path(first['path']).exists()
    assert collector.database.remote_file(resource()['url'])['source'] == 'unclassified'


def test_nested_discovery_keeps_all_stations_branches_and_unknown_extensions(collector, monkeypatch):
    pages = {
        'https://example.test/data/': '<a href="new/">new</a><a href="old/">old</a><a href="https://other.test/stolen.json">external</a>',
        'https://example.test/data/new/': '<a href="obs-123-20261004.xyz">unknown</a>',
        'https://example.test/data/old/': '<a href="obs-123-20261003.json">old</a><a href="obs-999-20261004.json">other</a>',
    }
    monkeypatch.setattr(requests, 'get', lambda url, **k: Response(pages[url].encode(), {'content-type': 'text/html'}))
    files = collector.discover_resource_files(dict(url='https://example.test/data/', source='additional',
        product='new_source', recursive=True, format='auto', station_ids=['123']))
    assert len(files) == 2
    assert files[0]['relative_path'] == 'new/obs-123-20261004.xyz'
    assert files[0]['format'] is None
    assert files[1]['relative_path'] == 'old/obs-123-20261003.json'


def test_listing_cannot_escape_subtree(collector):
    html = ''.join(f'<a href="{link}">x</a>' for link in [
        '../x', '/other/x', 'https://evil.test/x', '?sort=name', '%2e%2e/x',
        'http://example.test/data/x', 'ok.json',
    ])
    assert collector._extract_listing_links(html, 'https://example.test/data/') == ['https://example.test/data/ok.json']


def test_monthly_dates_sort_before_station_identifiers():
    old = 'https://example.test/10m-0-203-0-41502057001-202608.json'
    new = 'https://example.test/10m-0-203-0-41501115001-202610.json'
    assert Downloader._file_sort_key(new) > Downloader._file_sort_key(old)


def test_discovery_limit_fails_explicitly(collector, monkeypatch):
    monkeypatch.setattr(requests, 'get', lambda *a, **k: Response(b'<a href="nested/">nested</a>', {'content-type': 'text/html'}))
    with pytest.raises(Exception, match='Discovery limit reached'):
        collector.discover_resource_files(dict(url='https://example.test/data/', recursive=True, max_indexes=2))


def test_sources_include_refreshing_now_metadata_and_each_historical_interval():
    config = {'climate': {'now': {'enabled': True, 'url': 'https://example.test/now/'},
        'recent': {'station_ids': ['123']}, 'historical_csv': {'enabled': True,
        'url': 'https://example.test/history/', 'intervals': ['daily', 'yearly']}},
        'metadata': {'climate': {'enabled': True, 'url': 'https://example.test/meta/'}}}
    assert CurrentResource().discover(config)[0]['refresh_existing']
    assert MetadataResource().discover(config)[0]['recursive']
    history = ClimateHistoricalResource().discover(config)
    assert [item['url'] for item in history] == ['https://example.test/history/daily/', 'https://example.test/history/yearly/']
    assert all(item['station_ids'] == ['123'] and item['refresh_existing'] for item in history)


def test_identical_names_from_different_urls_cannot_destroy_each_other(collector, monkeypatch):
    responses = iter([Response(b'{"a":1}'), Response(b'{"a":2}')])
    monkeypatch.setattr(requests, 'get', lambda *a, **k: next(responses))
    first = collector.download_resource(resource())
    other = resource()
    other['url'] = 'https://example.test/elsewhere/today.json'
    second = collector.download_resource(other)
    assert Path(first['path']).read_bytes() == b'{"a":1}'
    assert Path(second['path']).read_bytes() == b'{"a":2}'
