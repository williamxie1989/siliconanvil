# SPDX-License-Identifier: Apache-2.0
"""Recompute historical macOS residency metrics from recorded VM samples."""

import argparse
import hashlib
import json
import math
from itertools import pairwise
from pathlib import Path

GIB = 2**30
MAX_SKEW_S = 1.5  # The historical probe sampled at 1 Hz.
INPUTS = ("samples.jsonl", "events.jsonl", "final.json")


def monotonic_time(row, key="mono"):
    value = row[key]
    if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
        raise ValueError(f"invalid monotonic time: {key}")
    return value


def vm_sample(row):
    page = row["page"]
    wired = row["vm"]["Pages wired down"]
    swapouts = row["vm"]["Swapouts"]
    if type(page) is not int or page <= 0:
        raise ValueError("page size must be a positive integer")
    if any(type(value) is not int or value < 0 for value in (wired, swapouts)):
        raise ValueError("wired and Swapouts must be non-negative page counts")
    return {
        "mono": monotonic_time(row),
        "page": page,
        "system_wired_gib": wired * page / GIB,
        "swapouts": swapouts,
    }


def event(events, kind, label=None):
    matches = [
        row
        for row in events
        if row["kind"] == kind and (label is None or row.get("label") == label)
    ]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one event: {kind} {label or ''}".rstrip())
    return matches[0]


def idle_point(samples, anchor, offset):
    target = anchor + offset
    sample = min(samples, key=lambda row: abs(row["mono"] - target))
    skew = sample["mono"] - target
    if abs(skew) > MAX_SKEW_S:
        raise ValueError(f"T+{offset} has no sample close enough")
    return {"system_wired_gib": sample["system_wired_gib"], "sample_skew_s": skew}


def pressure_bounds(events, label):
    start = monotonic_time(event(events, "pressure_start", label))
    end_event = event(events, "pressure_end", label)
    end = monotonic_time(end_event)
    if start >= end or end_event["returncode"] != 0:
        raise ValueError(f"{label} pressure window is invalid")
    return start, end


def pressure_window(samples, bounds, label):
    start, end = bounds
    boundary = [row for row in samples if row["mono"] <= start]
    inside = [row for row in samples if start < row["mono"] <= end]
    if not boundary or not inside:
        raise ValueError(f"{label} pressure window lacks a preceding or in-window sample")
    series = [boundary[-1], *inside]
    times = [row["mono"] for row in series] + [end]
    if any(b - a > MAX_SKEW_S for a, b in pairwise(times)):
        raise ValueError(f"{label} pressure window has a sampling gap")
    return {
        "duration_s": end - start,
        "system_wired_min_gib": min(row["system_wired_gib"] for row in inside),
        "swapouts_delta_pages": series[-1]["swapouts"] - series[0]["swapouts"],
        "samples": len(inside),
    }


def replay(directory):
    directory = directory.resolve()
    raw = {name: (directory / name).read_bytes() for name in INPUTS}
    samples = [
        vm_sample(json.loads(line))
        for line in raw["samples.jsonl"].splitlines()
        if line.strip()
    ]
    events = [
        json.loads(line) for line in raw["events.jsonl"].splitlines() if line.strip()
    ]
    final = vm_sample(json.loads(raw["final.json"]))
    anchor = monotonic_time(event(events, "idle_anchor"), "t0")
    complete = monotonic_time(event(events, "sequence_complete"))
    unload_event = event(events, "unload_done")
    unload = monotonic_time(unload_event)
    end_event = event(events, "experiment_end")
    end = monotonic_time(end_event)
    if end_event["aborted"] is not False or any(
        row["kind"] == "watchdog_abort" for row in events
    ):
        raise ValueError("experiment aborted")
    if unload_event["result"]["status"] != "ok":
        raise ValueError("unload failed")
    windows = {label: pressure_bounds(events, label) for label in ("warm", "cold")}
    warm_start, warm_end = windows["warm"]
    cold_start, cold_end = windows["cold"]
    if not (
        anchor < warm_start < warm_end < cold_start < cold_end <= complete
        and anchor + 310 <= complete <= unload <= final["mono"] <= end
    ):
        raise ValueError("invalid ordering of idle, pressure windows, experiment completion, unload, and final sample")
    if len(samples) < 2 or any(a["mono"] >= b["mono"] for a, b in pairwise(samples)):
        raise ValueError("too few system samples or monotonic time not strictly increasing")
    if samples[-1]["mono"] > final["mono"]:
        raise ValueError("final sample precedes the end of the system samples")
    series = [*samples, final]
    if any(row["page"] != final["page"] for row in series):
        raise ValueError("page size changed during the experiment")
    if any(a["swapouts"] > b["swapouts"] for a, b in pairwise(series)):
        raise ValueError("Swapouts counter went backwards")
    active_samples = [row for row in samples if anchor <= row["mono"] <= complete]
    if not active_samples:
        raise ValueError("no samples during the experiment")

    return {
        "run_id": directory.name,
        "input_directory": str(directory),
        "input_sha256": {
            name: hashlib.sha256(data).hexdigest() for name, data in raw.items()
        },
        "samples": len(samples),
        "page_bytes": final["page"],
        "idle": {
            str(offset): idle_point(active_samples, anchor, offset)
            for offset in (290, 310)
        },
        "pressure": {
            label: pressure_window(active_samples, bounds, label)
            for label, bounds in windows.items()
        },
        "after_unload": {
            "system_wired_gib": final["system_wired_gib"],
            "sample_delay_s": final["mono"] - unload,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "runs", nargs="+", type=Path, help="experiment directories containing raw samples and events"
    )
    args = parser.parse_args()
    try:
        runs = [replay(directory) for directory in args.runs]
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.exit(2, f"cannot replay: {exc}\n")
    print(json.dumps({"schema": 1, "scope": "historical", "runs": runs}, indent=2))


if __name__ == "__main__":
    main()
