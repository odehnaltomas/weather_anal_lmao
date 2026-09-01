from datetime import datetime, timezone

import numpy as np

from chmi_downloader.analysis.storm_tracking import (
    Detection,
    Track,
    connected_components,
    link_detections,
    summarize_track,
)


def detection(minute, x, *, west=False, center=False, east=False, peak=45.0):
    return Detection(
        time=datetime(2026, 7, 19, 10, minute, tzinfo=timezone.utc),
        centroid_x_km=x,
        centroid_y_km=0.0,
        peak_dbz=peak,
        mean_dbz=peak - 2,
        area_km2=5.0,
        pixels=frozenset({(10, int(x + 40))}),
        touches_west=west,
        touches_center=center,
        touches_east=east,
    )


def test_connected_components_uses_diagonal_neighbors():
    mask = np.zeros((5, 5), dtype=bool)
    mask[0, 0] = True
    mask[1, 1] = True
    mask[4, 4] = True

    components = connected_components(mask)

    assert sorted(len(component) for component in components) == [1, 2]


def test_link_and_summarize_western_track_crossing_tisnov():
    frames = [
        [detection(0, -20, west=True, peak=52)],
        [detection(5, -10, west=True, peak=48)],
        [detection(10, 0, center=True, peak=44)],
        [detection(15, 12, east=True, peak=46)],
    ]

    tracks = link_detections(frames, max_speed_kmh=160)
    summary = summarize_track(
        tracks[0],
        west_direction_min=260,
        west_direction_max=290,
        min_track_frames=3,
    )

    assert len(tracks) == 1
    assert summary['western_arrival'] is True
    assert summary['crossed_tisnov_5km'] is True
    assert summary['touched_east_band'] is True
    assert summary['center_minus_west_db'] == -8
    assert summary['weakened_at_least_5db'] is True


def test_track_dissipating_west_of_tisnov_is_classified():
    track = Track(1, [
        detection(0, -25, west=True),
        detection(5, -18, west=True),
        detection(10, -11, west=True),
    ])

    summary = summarize_track(
        track,
        west_direction_min=260,
        west_direction_max=290,
        min_track_frames=3,
    )

    assert summary['western_arrival'] is True
    assert summary['dissipated_before_tisnov'] is True
