"""Detect and track convective radar cells around Tišnov."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Sequence

import h5py
import numpy as np
from pyproj import CRS, Transformer


@dataclass
class Detection:
    """One connected radar cell in one PseudoCAPPI frame."""

    time: datetime
    centroid_x_km: float
    centroid_y_km: float
    peak_dbz: float
    mean_dbz: float
    area_km2: float
    pixels: frozenset[tuple[int, int]] = field(repr=False)
    touches_center: bool = False
    touches_west: bool = False
    touches_east: bool = False
    echo_top_m: float | None = None

    @property
    def distance_from_center_km(self) -> float:
        return math.hypot(self.centroid_x_km, self.centroid_y_km)


@dataclass
class Track:
    """A time-ordered sequence of matched detections."""

    track_id: int
    detections: list[Detection] = field(default_factory=list)


def connected_components(mask: np.ndarray) -> list[list[tuple[int, int]]]:
    """Return 8-connected components from a two-dimensional boolean mask."""
    if mask.ndim != 2:
        raise ValueError('Cell mask must be two-dimensional.')
    seen = np.zeros(mask.shape, dtype=bool)
    components: list[list[tuple[int, int]]] = []
    height, width = mask.shape
    for row, col in np.argwhere(mask):
        row = int(row)
        col = int(col)
        if seen[row, col]:
            continue
        stack = [(row, col)]
        seen[row, col] = True
        component: list[tuple[int, int]] = []
        while stack:
            current_row, current_col = stack.pop()
            component.append((current_row, current_col))
            for row_offset in (-1, 0, 1):
                for col_offset in (-1, 0, 1):
                    if row_offset == 0 and col_offset == 0:
                        continue
                    next_row = current_row + row_offset
                    next_col = current_col + col_offset
                    if not (0 <= next_row < height and 0 <= next_col < width):
                        continue
                    if mask[next_row, next_col] and not seen[next_row, next_col]:
                        seen[next_row, next_col] = True
                        stack.append((next_row, next_col))
        components.append(component)
    return components


def _centroid_distance(first: Detection, second: Detection) -> float:
    return math.hypot(
        first.centroid_x_km - second.centroid_x_km,
        first.centroid_y_km - second.centroid_y_km,
    )


def link_detections(
    frames: Iterable[list[Detection]],
    *,
    max_gap_minutes: int = 10,
    max_speed_kmh: float = 140.0,
) -> list[Track]:
    """Greedily link cells using predicted/observed centroid distance."""
    tracks: list[Track] = []
    active: list[Track] = []
    next_id = 1
    for detections in frames:
        if not detections:
            continue
        frame_time = detections[0].time
        active = [
            track for track in active
            if frame_time - track.detections[-1].time <= timedelta(minutes=max_gap_minutes)
        ]
        candidates: list[tuple[float, int, int]] = []
        for track_index, track in enumerate(active):
            last = track.detections[-1]
            gap_hours = (frame_time - last.time).total_seconds() / 3600
            if gap_hours <= 0:
                continue
            allowed_distance = max(3.0, max_speed_kmh * gap_hours)
            for detection_index, detection in enumerate(detections):
                distance = _centroid_distance(last, detection)
                if distance <= allowed_distance:
                    overlap = len(last.pixels & detection.pixels)
                    cost = distance - min(overlap, 20) * 0.15
                    candidates.append((cost, track_index, detection_index))
        used_tracks: set[int] = set()
        used_detections: set[int] = set()
        for _, track_index, detection_index in sorted(candidates):
            if track_index in used_tracks or detection_index in used_detections:
                continue
            active[track_index].detections.append(detections[detection_index])
            used_tracks.add(track_index)
            used_detections.add(detection_index)
        for detection_index, detection in enumerate(detections):
            if detection_index in used_detections:
                continue
            track = Track(next_id, [detection])
            next_id += 1
            tracks.append(track)
            active.append(track)
    return tracks


def _direction(track: Track) -> tuple[float | None, float | None, float]:
    """Return motion bearing, origin bearing, and net displacement."""
    if len(track.detections) < 2:
        return None, None, 0.0
    start_time = track.detections[0].time
    seconds = np.array([
        (detection.time - start_time).total_seconds()
        for detection in track.detections
    ], dtype=float)
    east = np.array([detection.centroid_x_km for detection in track.detections])
    north = np.array([detection.centroid_y_km for detection in track.detections])
    if np.all(seconds == seconds[0]):
        return None, None, 0.0
    velocity_east = float(np.polyfit(seconds, east, 1)[0])
    velocity_north = float(np.polyfit(seconds, north, 1)[0])
    motion_bearing = math.degrees(math.atan2(velocity_east, velocity_north)) % 360
    origin_bearing = (motion_bearing + 180) % 360
    displacement = math.hypot(east[-1] - east[0], north[-1] - north[0])
    return motion_bearing, origin_bearing, displacement


def _in_direction_range(direction: float | None, minimum: float, maximum: float) -> bool:
    if direction is None:
        return False
    if minimum <= maximum:
        return minimum <= direction <= maximum
    return direction >= minimum or direction <= maximum


def summarize_track(
    track: Track,
    *,
    west_direction_min: float,
    west_direction_max: float,
    min_track_frames: int,
) -> dict[str, object]:
    detections = track.detections
    motion, origin, displacement = _direction(track)
    western = (
        len(detections) >= min_track_frames
        and displacement >= 5.0
        and _in_direction_range(origin, west_direction_min, west_direction_max)
    )
    west = [detection for detection in detections if detection.touches_west]
    center = [detection for detection in detections if detection.touches_center]
    east = [detection for detection in detections if detection.touches_east]
    west_peak = max((detection.peak_dbz for detection in west), default=None)
    center_peak = max((detection.peak_dbz for detection in center), default=None)
    east_peak = max((detection.peak_dbz for detection in east), default=None)
    attenuation = (
        center_peak - west_peak
        if center_peak is not None and west_peak is not None
        else None
    )
    first = detections[0]
    last = detections[-1]
    return {
        'track_id': track.track_id,
        'start_utc': first.time.isoformat(),
        'end_utc': last.time.isoformat(),
        'duration_minutes': (last.time - first.time).total_seconds() / 60,
        'frames': len(detections),
        'motion_bearing_deg': round(motion, 1) if motion is not None else None,
        'origin_bearing_deg': round(origin, 1) if origin is not None else None,
        'displacement_km': round(displacement, 2),
        'peak_dbz': max(detection.peak_dbz for detection in detections),
        'peak_echo_top_m': max(
            (detection.echo_top_m for detection in detections if detection.echo_top_m is not None),
            default=None,
        ),
        'minimum_center_distance_km': round(
            min(detection.distance_from_center_km for detection in detections), 2
        ),
        'western_arrival': western,
        'touched_west_band': bool(west),
        'crossed_tisnov_5km': bool(center),
        'touched_east_band': bool(east),
        'west_peak_dbz': west_peak,
        'center_peak_dbz': center_peak,
        'east_peak_dbz': east_peak,
        'center_minus_west_db': round(attenuation, 1) if attenuation is not None else None,
        'weakened_at_least_5db': western and attenuation is not None and attenuation <= -5,
        'dissipated_before_tisnov': (
            western
            and bool(west)
            and not center
            and last.centroid_x_km < -5
            and last.centroid_x_km > first.centroid_x_km
        ),
        'formed_near_tisnov_then_east': (
            western
            and not west
            and first.distance_from_center_km <= 10
            and bool(east)
        ),
    }


class StormTrackAnalyzer:
    """Run the Tišnov storm-cell analysis over catalogued radar files."""

    def __init__(
        self,
        *,
        latitude: float = 49.348719,
        longitude: float = 16.424380,
        area_size_km: float = 80.0,
        threshold_dbz: float = 40.0,
        min_component_pixels: int = 2,
        min_track_frames: int = 3,
        west_direction_min: float = 260.0,
        west_direction_max: float = 290.0,
    ) -> None:
        self.latitude = latitude
        self.longitude = longitude
        self.area_size_km = area_size_km
        self.threshold_dbz = threshold_dbz
        self.min_component_pixels = min_component_pixels
        self.min_track_frames = min_track_frames
        self.west_direction_min = west_direction_min
        self.west_direction_max = west_direction_max

    @staticmethod
    def _attrs(group: h5py.Group) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in group.attrs.items():
            if isinstance(value, bytes):
                result[key] = value.decode()
            elif hasattr(value, 'item'):
                result[key] = value.item()
            else:
                result[key] = value
        return result

    def _grid(self, sample_path: Path) -> dict[str, object]:
        with h5py.File(sample_path, 'r') as handle:
            where = self._attrs(handle['where'])
        transformer = Transformer.from_crs(
            'EPSG:4326', CRS.from_proj4(str(where['projdef'])), always_xy=True
        )
        center_x, center_y = transformer.transform(self.longitude, self.latitude)
        xscale = float(where['xscale'])
        yscale = float(where['yscale'])
        xsize = int(where['xsize'])
        ysize = int(where['ysize'])
        columns = np.arange(xsize)
        rows = np.arange(ysize)
        pixel_x = (columns + 0.5) * xscale
        # CHMI's false origin is the upper-left corner; y is negative southward.
        pixel_y = -(rows + 0.5) * yscale
        xx, yy = np.meshgrid(pixel_x, pixel_y)
        relative_x = xx - center_x
        relative_y = yy - center_y
        half_size_m = self.area_size_km * 500
        area = (np.abs(relative_x) <= half_size_m) & (np.abs(relative_y) <= half_size_m)
        selected_rows, selected_columns = np.where(area)
        row_start, row_end = int(selected_rows.min()), int(selected_rows.max()) + 1
        col_start, col_end = int(selected_columns.min()), int(selected_columns.max()) + 1
        relative_x = relative_x[row_start:row_end, col_start:col_end]
        relative_y = relative_y[row_start:row_end, col_start:col_end]
        return {
            'slice': (row_start, row_end, col_start, col_end),
            'x_km': relative_x / 1000,
            'y_km': relative_y / 1000,
            'center_mask': relative_x**2 + relative_y**2 <= 5_000**2,
            'west_mask': (
                (relative_x >= -30_000)
                & (relative_x <= -10_000)
                & (np.abs(relative_y) <= 10_000)
            ),
            'east_mask': (
                (relative_x >= 10_000)
                & (relative_x <= 30_000)
                & (np.abs(relative_y) <= 10_000)
            ),
            'pixel_area_km2': xscale * yscale / 1_000_000,
            'pixel_scale_m': [xscale, yscale],
        }

    def _read_field(self, path: Path, grid: dict[str, object]) -> np.ndarray:
        row_start, row_end, col_start, col_end = grid['slice']
        with h5py.File(path, 'r') as handle:
            raw = handle['dataset1/data1/data'][row_start:row_end, col_start:col_end]
            what = self._attrs(handle['dataset1/data1/what'])
        data = raw.astype(np.float32) * float(what['gain']) + float(what['offset'])
        data[raw == int(what['nodata'])] = np.nan
        data[raw == int(what['undetect'])] = -np.inf
        return data

    def _detections(
        self,
        field: np.ndarray,
        time: datetime,
        grid: dict[str, object],
        echo_top: np.ndarray | None,
    ) -> list[Detection]:
        detections: list[Detection] = []
        for component in connected_components(np.isfinite(field) & (field >= self.threshold_dbz)):
            if len(component) < self.min_component_pixels:
                continue
            rows = np.array([pixel[0] for pixel in component])
            columns = np.array([pixel[1] for pixel in component])
            values = field[rows, columns]
            pixels = frozenset(component)
            detections.append(Detection(
                time=time,
                centroid_x_km=float(np.mean(grid['x_km'][rows, columns])),
                centroid_y_km=float(np.mean(grid['y_km'][rows, columns])),
                peak_dbz=float(np.max(values)),
                mean_dbz=float(np.mean(values)),
                area_km2=float(len(component) * grid['pixel_area_km2']),
                pixels=pixels,
                touches_center=bool(np.any(grid['center_mask'][rows, columns])),
                touches_west=bool(np.any(grid['west_mask'][rows, columns])),
                touches_east=bool(np.any(grid['east_mask'][rows, columns])),
                echo_top_m=(
                    float(np.nanmax(echo_top[rows, columns]))
                    if echo_top is not None
                    and np.any(np.isfinite(echo_top[rows, columns]))
                    else None
                ),
            ))
        return detections

    def analyze(
        self,
        pcap_files: Sequence[dict[str, object]],
        echotop_files: Sequence[dict[str, object]],
    ) -> dict[str, object]:
        if not pcap_files:
            raise ValueError('No PseudoCAPPI files are available for analysis.')
        existing = [row for row in pcap_files if Path(str(row['file_path'])).exists()]
        if not existing:
            raise ValueError('No catalogued PseudoCAPPI files exist on disk.')
        grid = self._grid(Path(str(existing[0]['file_path'])))
        echo_by_time = {
            str(row['observation_time_utc']): Path(str(row['file_path']))
            for row in echotop_files
            if row.get('observation_time_utc') and Path(str(row['file_path'])).exists()
        }
        frames: list[list[Detection]] = []
        readable_frames = 0
        detection_count = 0
        for row in existing:
            time_text = str(row['observation_time_utc'])
            time = datetime.fromisoformat(time_text.replace('Z', '+00:00'))
            field = self._read_field(Path(str(row['file_path'])), grid)
            echo_path = echo_by_time.get(time_text)
            echo_top = self._read_field(echo_path, grid) if echo_path else None
            detections = self._detections(field, time, grid, echo_top)
            frames.append(detections)
            readable_frames += 1
            detection_count += len(detections)
        tracks = link_detections(frames)
        summaries = [
            summarize_track(
                track,
                west_direction_min=self.west_direction_min,
                west_direction_max=self.west_direction_max,
                min_track_frames=self.min_track_frames,
            )
            for track in tracks
            if len(track.detections) >= self.min_track_frames
        ]
        western = [track for track in summaries if track['western_arrival']]
        western_corridor = [
            track for track in western
            if track['minimum_center_distance_km'] <= 15
            or track['touched_west_band']
            or track['touched_east_band']
        ]
        return {
            'method': {
                'center_latitude': self.latitude,
                'center_longitude': self.longitude,
                'area_size_km': self.area_size_km,
                'threshold_dbz': self.threshold_dbz,
                'minimum_component_pixels': self.min_component_pixels,
                'minimum_track_frames': self.min_track_frames,
                'western_origin_direction_deg': [
                    self.west_direction_min, self.west_direction_max
                ],
                'pixel_scale_m': grid['pixel_scale_m'],
            },
            'coverage': {
                'first_utc': str(existing[0]['observation_time_utc']),
                'last_utc': str(existing[-1]['observation_time_utc']),
                'pcappi_frames': readable_frames,
                'echotop_frames_matched': len(echo_by_time),
            },
            'summary': {
                'detections': detection_count,
                'tracks_at_least_minimum_length': len(summaries),
                'western_arrival_tracks': len(western),
                'western_tisnov_corridor_tracks': len(western_corridor),
                'western_crossed_tisnov_5km': sum(
                    bool(track['crossed_tisnov_5km']) for track in western
                ),
                'western_dissipated_before_tisnov': sum(
                    bool(track['dissipated_before_tisnov']) for track in western
                ),
                'western_weakened_at_least_5db': sum(
                    bool(track['weakened_at_least_5db']) for track in western
                ),
                'formed_near_tisnov_then_east': sum(
                    bool(track['formed_near_tisnov_then_east']) for track in summaries
                ),
            },
            'tracks': summaries,
        }

    @staticmethod
    def write_json(result: dict[str, object], path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
