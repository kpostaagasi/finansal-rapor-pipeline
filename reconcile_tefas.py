#!/usr/bin/env python3
"""İki TEFAS cache'ini tarih/fon/gözlem düzeyinde read-only karşılaştırır."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def normalize_cache(cache: dict) -> dict[str, dict[str, list]]:
    if isinstance(cache.get("fon"), dict):
        return cache["fon"]
    normalized = {}
    for group in ("yf", "eyf"):
        for code, series in cache.get(group, {}).items():
            if code in normalized:
                raise ValueError(f"Fon birden fazla grupta bulunuyor: {code}")
            normalized[code] = series
    if not normalized:
        raise ValueError("Desteklenen TEFAS cache biçimi bulunamadı")
    return normalized


def compare_caches(baseline: dict, current: dict) -> dict:
    old = normalize_cache(baseline)
    new = normalize_cache(current)
    old_codes, new_codes = set(old), set(new)
    common = sorted(old_codes & new_codes)
    changed = []
    observations_added = []
    observations_removed = []

    for fund in common:
        old_dates, new_dates = set(old[fund]), set(new[fund])
        observations_added.extend(
            {"fund": fund, "date": date, "value": new[fund][date]}
            for date in sorted(new_dates - old_dates)
        )
        observations_removed.extend(
            {"fund": fund, "date": date, "value": old[fund][date]}
            for date in sorted(old_dates - new_dates)
        )
        for date in sorted(old_dates & new_dates):
            if old[fund][date] != new[fund][date]:
                changed.append({
                    "fund": fund,
                    "date": date,
                    "baseline": old[fund][date],
                    "current": new[fund][date],
                })

    return {
        "funds_added": sorted(new_codes - old_codes),
        "funds_removed": sorted(old_codes - new_codes),
        "observations_added_count": len(observations_added),
        "observations_removed_count": len(observations_removed),
        "changed_count": len(changed),
        "observations_added": observations_added,
        "observations_removed": observations_removed,
        "changed": changed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path, help="Kabul edilmiş eski cache JSON")
    parser.add_argument("current", type=Path, help="Yeni/tam çekilmiş cache JSON")
    args = parser.parse_args(argv)
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    current = json.loads(args.current.read_text(encoding="utf-8"))
    result = compare_caches(baseline, current)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    has_diff = any((
        result["funds_added"], result["funds_removed"],
        result["observations_added_count"], result["observations_removed_count"],
        result["changed_count"],
    ))
    return 1 if has_diff else 0


if __name__ == "__main__":
    raise SystemExit(main())
