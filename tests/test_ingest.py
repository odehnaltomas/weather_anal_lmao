from pathlib import Path

from chmi_downloader.database import Database
from chmi_downloader.ingest import IngestionService


class DummyDownloader:
    def __init__(self):
        self.calls = []

    def download_resource(self, resource):
        self.calls.append(resource)
        return {'status': 'downloaded', 'path': 'dummy.bin', 'sha256': 'abc', 'size_bytes': 1}

    def discover_resource_files(self, resource):
        return [resource]


def test_ingestion_service_collects_resources(tmp_path):
    db = Database(tmp_path / 'catalog.sqlite')
    downloader = DummyDownloader()
    ingestion = IngestionService(config=None, database=db, downloader=downloader)
    resources = [{'name': 'sample', 'url': 'https://example.com/sample'}]
    results = ingestion.collect(resources)
    assert len(results) == 1
    assert downloader.calls[0]['name'] == 'sample'


def test_ingestion_service_emits_discovery_and_result_progress(tmp_path):
    db = Database(tmp_path / 'catalog.sqlite')
    downloader = DummyDownloader()
    ingestion = IngestionService(config=None, database=db, downloader=downloader)
    events = []

    ingestion.collect(
        [{'name': 'sample', 'product': 'maxz', 'url': 'https://example.com/sample'}],
        progress=events.append,
    )

    assert [event['event'] for event in events] == [
        'discovering',
        'discovered',
        'result',
    ]
    assert events[1]['count'] == 1
