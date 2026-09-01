import sqlite3
from pathlib import Path

from scripts.migrate_storage_paths import expected_path


def test_expected_radar_path_uses_product_and_observation_date(tmp_path):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        """
        SELECT
            'T_PADV23_C_OKPR_20260723083500.hdf' AS name,
            'radar' AS source,
            'echotop' AS product,
            '2026-07-23T08:35:00+00:00' AS observation_time_utc
        """
    ).fetchone()

    assert expected_path(row, tmp_path) == (
        tmp_path
        / "raw"
        / "radar"
        / "echotop"
        / "2026"
        / "07"
        / "23"
        / "T_PADV23_C_OKPR_20260723083500.hdf"
    )


def test_expected_climate_path_has_no_date_partition(tmp_path):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        """
        SELECT
            'daily.json' AS name,
            'climate' AS source,
            'recent' AS product,
            NULL AS observation_time_utc
        """
    ).fetchone()

    assert expected_path(row, tmp_path) == (
        tmp_path / "raw" / "climate" / "recent" / "daily.json"
    )
