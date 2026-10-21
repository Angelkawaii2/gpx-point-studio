#!/usr/bin/env python3
"""Trim a GPX track, or report the distance travelled in a time window.

Examples:
  python3 trim_gpx.py ride.gpx cut.gpx \
    --start '2026-07-20 14:30:00+08:00' \
    --end   '2026-07-20 15:00:00+08:00' \
    --downsample 3

  # Compress the timeline itself: 5 hours becomes 1 hour.
  python3 trim_gpx.py ride.gpx fast.gpx --speedup 5

  # Merge two days first, then filter and accelerate the combined track.
  python3 trim_gpx.py day1.gpx day2.gpx combined.gpx \
    --start '2026-07-20T00:00:00+08:00' --speedup 5

  # Omit the destination filename to report the distance in a time range.
  python3 trim_gpx.py ride.gpx \
    --start '2026-07-20T20:00:00+08:00' \
    --end '2026-07-20T23:59:59+08:00'

Only one boundary, or neither boundary, may be supplied.  For example,
``--start 2026-07-20T20:00:00`` keeps everything after that time, while
``--downsample 3`` by itself thins the complete GPX file.

``--downsample 3`` retains one point out of every three (plus the final point
in each track segment).  ``--speedup 3`` compresses all retained timestamps to
one third of their original offsets from the first retained point. These
options can be combined.
"""

from __future__ import annotations

import argparse
import copy
import math
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


def parse_timezone(value: str) -> timezone:
    """Parse UTC, Z, +08:00, or -05:30 into a fixed-offset timezone."""
    if value.upper() in {"UTC", "Z"}:
        return timezone.utc
    try:
        sign = 1 if value[0] == "+" else -1
        hours, minutes = value[1:].split(":", maxsplit=1)
        return timezone(sign * timedelta(hours=int(hours), minutes=int(minutes)))
    except (IndexError, ValueError) as error:
        raise argparse.ArgumentTypeError(
            "timezone must be UTC, Z, or an offset such as +08:00"
        ) from error


def parse_timestamp(value: str, assumed_timezone: timezone) -> datetime:
    """Parse an ISO-8601 timestamp; apply assumed_timezone when it is naive."""
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "time must be ISO-8601, e.g. 2026-07-20T14:30:00+08:00"
        ) from error
    return parsed.replace(tzinfo=assumed_timezone) if parsed.tzinfo is None else parsed


def local_name(tag: str) -> str:
    return tag.rsplit("}", maxsplit=1)[-1]


def time_for_point(point: ET.Element) -> datetime | None:
    """Return the timestamp of a GPX point, or None if it does not have one."""
    for child in point:
        if local_name(child.tag) == "time" and child.text:
            try:
                return datetime.fromisoformat(child.text.strip().replace("Z", "+00:00"))
            except ValueError as error:
                raise ValueError(f"Invalid GPX point timestamp: {child.text!r}") from error
    return None


def time_element_for_point(point: ET.Element) -> ET.Element | None:
    """Return the GPX <time> child for a point, if present."""
    for child in point:
        if local_name(child.tag) == "time":
            return child
    return None


@dataclass
class Stats:
    total: int = 0
    in_window: int = 0
    kept: int = 0
    missing_time: int = 0


@dataclass
class DistanceStats:
    metres: float = 0.0
    points_in_window: int = 0
    contributing_segments: int = 0


EARTH_RADIUS_METRES = 6_371_008.8


def filter_points(
    container: ET.Element,
    point_tag: str,
    start: datetime | None,
    end: datetime | None,
    stride: int,
    stats: Stats,
) -> int:
    """Filter direct point children, preserving the first and final kept point."""
    candidates: list[ET.Element] = []
    for child in list(container):
        if local_name(child.tag) != point_tag:
            continue
        stats.total += 1
        timestamp = time_for_point(child)
        if timestamp is None:
            stats.missing_time += 1
            container.remove(child)
        elif (start is None or timestamp >= start) and (end is None or timestamp <= end):
            stats.in_window += 1
            candidates.append(child)
        else:
            container.remove(child)

    kept = candidates[::stride]
    if candidates and candidates[-1] not in kept:
        kept.append(candidates[-1])
    kept_ids = {id(point) for point in kept}
    for point in candidates:
        if id(point) not in kept_ids:
            container.remove(point)
    stats.kept += len(kept)
    return len(kept)


def accelerate_timestamps(root: ET.Element, factor: float) -> None:
    """Compress retained timestamps by ``factor`` around the first point."""
    if factor == 1:
        return

    timestamped_points: list[tuple[ET.Element, datetime]] = []
    for point in root.iter():
        if local_name(point.tag) not in {"trkpt", "rtept"}:
            continue
        time_element = time_element_for_point(point)
        timestamp = time_for_point(point)
        if time_element is not None and timestamp is not None:
            timestamped_points.append((time_element, timestamp))

    if not timestamped_points:
        return
    base = min(timestamp for _, timestamp in timestamped_points)
    for time_element, timestamp in timestamped_points:
        compressed = base + timedelta(seconds=(timestamp - base).total_seconds() / factor)
        time_element.text = compressed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def merge_gpx(sources: list[Path]) -> ET.ElementTree:
    """Merge tracks/routes from all sources into the first GPX document."""
    merged_tree = ET.parse(sources[0])
    merged_root = merged_tree.getroot()
    for source in sources[1:]:
        source_root = ET.parse(source).getroot()
        # Keep the first file's metadata and append recording elements from
        # subsequent files. Deep-copy avoids sharing elements between trees.
        for child in source_root:
            if local_name(child.tag) in {"trk", "rte", "wpt"}:
                merged_root.append(copy.deepcopy(child))
    return merged_tree


def point_coordinates(point: ET.Element) -> tuple[float, float]:
    """Read a GPX point's latitude and longitude, with a useful validation error."""
    try:
        return float(point.attrib["lat"]), float(point.attrib["lon"])
    except (KeyError, ValueError) as error:
        raise ValueError("GPX point has invalid or missing lat/lon coordinates") from error


def haversine_metres(start: ET.Element, end: ET.Element) -> float:
    """Distance between two GPX points on the WGS84-equivalent mean Earth sphere."""
    lat_a, lon_a = point_coordinates(start)
    lat_b, lon_b = point_coordinates(end)
    lat_a, lon_a, lat_b, lon_b = map(math.radians, (lat_a, lon_a, lat_b, lon_b))
    lat_delta = lat_b - lat_a
    lon_delta = lon_b - lon_a
    half_chord = math.sin(lat_delta / 2) ** 2 + math.cos(lat_a) * math.cos(lat_b) * math.sin(lon_delta / 2) ** 2
    return 2 * EARTH_RADIUS_METRES * math.asin(math.sqrt(half_chord))


def distance_for_sequence(
    points: list[ET.Element], start: datetime, end: datetime, stats: DistanceStats
) -> None:
    """Add the time-clipped distance for one GPX track segment or route."""
    timed_points: list[tuple[ET.Element, datetime]] = []
    for point in points:
        timestamp = time_for_point(point)
        if timestamp is not None:
            timed_points.append((point, timestamp))
            if start <= timestamp <= end:
                stats.points_in_window += 1

    for (point_a, time_a), (point_b, time_b) in zip(timed_points, timed_points[1:]):
        if time_b <= time_a:
            continue
        overlap_start = max(start, time_a)
        overlap_end = min(end, time_b)
        if overlap_end <= overlap_start:
            continue
        fraction = (overlap_end - overlap_start).total_seconds() / (time_b - time_a).total_seconds()
        stats.metres += haversine_metres(point_a, point_b) * fraction
        stats.contributing_segments += 1


def distance_in_window(sources: list[Path], start: datetime, end: datetime) -> DistanceStats:
    """Measure movement without writing a GPX file; tracks and routes stay separate."""
    root = merge_gpx(sources).getroot()
    stats = DistanceStats()
    for track in [element for element in root if local_name(element.tag) == "trk"]:
        for segment in [element for element in track if local_name(element.tag) == "trkseg"]:
            distance_for_sequence(
                [point for point in segment if local_name(point.tag) == "trkpt"], start, end, stats
            )
    for route in [element for element in root if local_name(element.tag) == "rte"]:
        distance_for_sequence(
            [point for point in route if local_name(point.tag) == "rtept"], start, end, stats
        )
    return stats


def trim_gpx(
    sources: list[Path],
    destination: Path,
    start: datetime | None,
    end: datetime | None,
    downsample: int,
    speedup: float,
) -> Stats:
    tree = merge_gpx(sources)
    root = tree.getroot()
    namespace = root.tag.partition("}")[0].lstrip("{")
    if namespace:
        ET.register_namespace("", namespace)

    stats = Stats()
    for track in [element for element in root if local_name(element.tag) == "trk"]:
        for segment in [element for element in track if local_name(element.tag) == "trkseg"]:
            if not filter_points(segment, "trkpt", start, end, downsample, stats):
                track.remove(segment)
        if not any(local_name(element.tag) == "trkseg" for element in track):
            root.remove(track)

    for route in [element for element in root if local_name(element.tag) == "rte"]:
        if not filter_points(route, "rtept", start, end, downsample, stats):
            root.remove(route)

    if stats.kept == 0:
        raise ValueError("No timestamped GPX points fall within the requested time range.")

    accelerate_timestamps(root, speedup)
    destination.parent.mkdir(parents=True, exist_ok=True)
    tree.write(destination, encoding="utf-8", xml_declaration=True)
    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Trim GPX points, optionally downsample them, and/or accelerate time."
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="Source .gpx files; append a new destination filename to export, or omit it to report distance",
    )
    parser.add_argument("--output", type=Path, help="Explicit destination .gpx file (must not exist)")
    parser.add_argument("--start", help="Start time, inclusive, in ISO-8601 (optional)")
    parser.add_argument("--end", help="End time, inclusive, in ISO-8601 (optional)")
    parser.add_argument(
        "--timezone",
        type=parse_timezone,
        default=timezone(timedelta(hours=8)),
        help="Timezone for naive --start/--end values (default: +08:00)",
    )
    parser.add_argument(
        "--downsample",
        type=int,
        default=1,
        metavar="N",
        help="Keep one point in every N; 3 retains points 1, 4, 7… (default: 1)",
    )
    parser.add_argument(
        "--speedup",
        type=float,
        default=1,
        metavar="N",
        help="Compress timestamps by N; 5 turns 5 hours into 1 hour (default: 1)",
    )
    return parser


def resolve_inputs_and_output(paths: list[Path], explicit_output: Path | None) -> tuple[list[Path], Path | None]:
    """Preserve the legacy ``inputs output`` syntax while allowing report-only mode."""
    if explicit_output is not None:
        return paths, explicit_output
    if all(path.is_file() for path in paths):
        return paths, None
    if len(paths) >= 2 and all(path.is_file() for path in paths[:-1]) and not paths[-1].exists():
        return paths[:-1], paths[-1]
    raise ValueError(
        "Provide existing source GPX file(s). For export, append a new destination filename or use --output."
    )


def main() -> int:
    args = build_parser().parse_args()
    if args.downsample < 1:
        print("error: --downsample must be at least 1", file=sys.stderr)
        return 2
    if args.speedup <= 0:
        print("error: --speedup must be greater than 0", file=sys.stderr)
        return 2
    try:
        inputs, output = resolve_inputs_and_output(args.paths, args.output)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    missing_inputs = [path for path in inputs if not path.is_file()]
    if missing_inputs:
        print("error: input file(s) not found: " + ", ".join(map(str, missing_inputs)), file=sys.stderr)
        return 2
    if output is not None and output.exists():
        print(f"error: refusing to overwrite existing file: {output}", file=sys.stderr)
        return 2

    start = parse_timestamp(args.start, args.timezone) if args.start else None
    end = parse_timestamp(args.end, args.timezone) if args.end else None
    if start is not None and end is not None and end < start:
        print("error: --end must not be before --start", file=sys.stderr)
        return 2

    if output is None:
        if start is None or end is None:
            print("error: distance-report mode requires both --start and --end", file=sys.stderr)
            return 2
        try:
            distance = distance_in_window(inputs, start, end)
        except (ET.ParseError, ValueError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        print(f"Distance: {distance.metres / 1000:.3f} km ({distance.metres:.1f} m)")
        print(f"Range: {start.isoformat()} -> {end.isoformat()}")
        print(
            f"Sources: {len(inputs)}; {distance.points_in_window} sampled points in range; "
            f"{distance.contributing_segments} time-clipped segments"
        )
        return 0

    try:
        stats = trim_gpx(inputs, output, start, end, args.downsample, args.speedup)
    except (ET.ParseError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(f"Wrote: {output}")
    print(f"Inputs merged: {len(inputs)}")
    print(
        f"Points: {stats.kept} kept / {stats.in_window} in time range / {stats.total} total"
        + (f"; {stats.missing_time} without timestamps discarded" if stats.missing_time else "")
    )
    print(f"Downsample: every {args.downsample}; timestamp speedup: x{args.speedup:g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
