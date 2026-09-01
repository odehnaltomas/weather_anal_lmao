from pathlib import Path

from chmi_downloader.database import Database


def test_database_creates_tables_and_records_file(tmp_path):
    db_path = tmp_path / 'catalog.sqlite'
    db = Database(db_path)
    row_id = db.insert_remote_file('radar.hdf5', 'radar', str(tmp_path / 'radar.hdf5'), 'abc', 123, 'downloaded')
    assert row_id > 0
    rows = db.list_remote_files()
    assert len(rows) == 1
    assert rows[0]['name'] == 'radar.hdf5'


def test_database_records_failed_status(tmp_path):
    db_path = tmp_path / 'catalog.sqlite'
    db = Database(db_path)
    db.insert_remote_file('bad.bin', 'radar', str(tmp_path / 'bad.bin'), None, None, 'failed')
    rows = db.list_remote_files()
    assert rows[0]['status'] == 'failed'


def test_database_deduplicates_remote_urls(tmp_path):
    db = Database(tmp_path / 'catalog.sqlite')
    url = 'https://example.test/radar/file.hdf'
    path = tmp_path / 'file.hdf'
    path.write_bytes(b'data')
    db.insert_remote_file(
        'file.hdf', 'radar', str(path), 'abc', 4, 'downloaded',
        product='maxz', remote_url=url,
    )
    db.insert_remote_file(
        'file.hdf', 'radar', str(path), 'abc', 4, 'downloaded',
        product='maxz', remote_url=url,
    )

    assert len(db.list_remote_files()) == 1
    assert db.is_downloaded(url)


def test_database_status_summary_groups_product_and_status(tmp_path):
    db = Database(tmp_path / 'catalog.sqlite')
    db.insert_remote_file(
        'one.hdf', 'radar', str(tmp_path / 'one.hdf'), 'abc', 1,
        'downloaded', product='maxz', remote_url='https://example.test/one.hdf',
    )
    db.insert_remote_file(
        'two.hdf', 'radar', str(tmp_path / 'two.hdf'), None, None,
        'failed', product='maxz', remote_url='https://example.test/two.hdf',
    )

    summary = db.status_summary()

    assert len(summary) == 1
    assert summary[0]['product'] == 'maxz'
    assert summary[0]['total'] == 2
    assert summary[0]['downloaded'] == 1
    assert summary[0]['failed'] == 1
