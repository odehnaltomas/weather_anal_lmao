from pathlib import Path

from chmi_downloader.downloader import Downloader


class DummyConfig:
    def staging_dir(self):
        return './data/staging'

    def raw_dir(self):
        return './data/raw'

    def processed_dir(self):
        return './data/processed'


class DummyDatabase:
    def insert_remote_file(self, *args, **kwargs):
        return 1

    def is_downloaded(self, remote_url):
        return False


def test_extract_listing_links_parses_common_href_patterns():
    downloader = Downloader(DummyConfig(), DummyDatabase())
    html = '<html><body><a href="../">parent</a><a href="./file.hdf">file</a><a href="other.csv">other</a></body></html>'
    links = downloader._extract_listing_links(html, 'https://example.test/base/')
    assert links == [
        'https://example.test/base/file.hdf',
        'https://example.test/base/other.csv',
    ]


def test_hdf_format_accepts_real_hdf5_signature_with_hdf_extension(tmp_path):
    from chmi_downloader.validation import Validator

    path = tmp_path / 'radar.hdf'
    path.write_bytes(b'\x89HDF\r\n\x1a\npayload')

    assert Validator.basic_format_check(path, 'hdf5')


def test_file_sort_key_prioritizes_embedded_timestamp_over_station_number():
    older_high_station = 'https://example.test/10m-0-99999-20260720.json'
    newer_low_station = 'https://example.test/10m-0-10000-20260721.json'

    assert Downloader._file_sort_key(newer_low_station) > Downloader._file_sort_key(older_high_station)
