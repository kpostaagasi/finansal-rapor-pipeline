#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Doğrulanmış finansal HTML çıktılarından dashboard JSON artifactleri kurar.

Bu adapter finansal hesap yapmaz. Üreticilerin HTML içine gömdüğü RAW/DATA ve
REPORT_META JSON nesnelerini aynen çıkarır, sözleşmeye sarar ve hash manifestiyle
yazar.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_DIR = REPO_ROOT / "Güncellenecek Kodlar"
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

from research_report_data import (  # noqa: E402  # pyright: ignore[reportMissingImports]
    classify_metadata,
    validate_artifact,
)


STATUS_SEVERITY = {"ready": 0, "partial": 1, "stale": 2, "failed": 3}


def extract_json_constant(source: str, name: str) -> Any:
    """`const NAME = <JSON>;` içindeki JSON'u parantez/regex kestirmeden okur."""
    marker = f"const {name} ="
    start = source.find(marker)
    if start < 0:
        raise ValueError(f"JSON sabiti bulunamadı: {name}")
    payload = source[start + len(marker):].lstrip()
    try:
        value, _ = json.JSONDecoder().raw_decode(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON sabiti geçersiz: {name}: {exc}") from exc
    return value


def _generated_at(value: str | None) -> str:
    return value or datetime.now().astimezone().isoformat(timespec="seconds")


def _worst_status(statuses: list[str]) -> str:
    return max(statuses, key=lambda value: STATUS_SEVERITY[value])


def _read_report(source: str, data_name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = extract_json_constant(source, data_name)
    meta = extract_json_constant(source, "REPORT_META")
    if not isinstance(raw, dict) or not isinstance(meta, dict):
        raise ValueError("rapor RAW/DATA ve REPORT_META nesne olmalı")
    return raw, meta


def build_fund_artifact(
    group_html: str,
    gold_detail_html: str,
    selected_html: str,
    generated_at: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Üç TEFAS çıktısını tek, sürümlü ve fail-closed artifacte sarar."""
    inputs = [
        ("gold_total", "Altın Fonları Toplam Net Akış", group_html),
        ("gold_by_fund", "Altın Fonları Fon Bazında Net Akış", gold_detail_html),
        ("selected_funds", "Seçili 15 Fon Net Akış", selected_html),
    ]
    reports: dict[str, Any] = {}
    dates: list[str] = []
    statuses: list[str] = []
    for key, title, source in inputs:
        series, meta = _read_report(source, "RAW")
        status = classify_metadata(meta, today=today)
        statuses.append(status)
        dates.append(str(meta["data_end_date"]))
        reports[key] = {
            "title": title,
            "status": status,
            "metadata": meta,
            "series": series,
        }

    artifact = {
        "schema_version": 1,
        "report_type": "fund_flows",
        "generated_at": _generated_at(generated_at),
        "latest_data_date": min(dates),
        "source": ["TEFAS"],
        "status": _worst_status(statuses),
        "methodology": {
            "formula": "(bugünkü tedavüldeki pay - önceki gerçek TEFAS veri tarihindeki pay) × bugünkü fiyat",
            "missing_observation_policy": (
                "Ardışık iki gerçek gözlem yoksa akış null bırakılır; sıfır, ileri taşıma "
                "veya sonraki güne yığma yapılmaz."
            ),
            "period_total_policy": (
                "Dönemde null hücre varsa kısmi toplam tam sonuç gibi gösterilmez."
            ),
        },
        "data": {"reports": reports},
    }
    return validate_artifact(artifact, "fund_flows")


def build_market_artifact(
    market_html: str,
    generated_at: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Emtia/Hazine çıktısını kaynak tanımı ve kapsamıyla artifacte sarar."""
    data, meta = _read_report(market_html, "DATA")
    meta = dict(meta)
    meta["max_quote_age_days"] = 5
    status = classify_metadata(meta, today=today)
    if meta.get("treasury_found_count") != meta.get("treasury_expected_count"):
        status = _worst_status([status, "partial"])
    clean_data = dict(data)
    clean_data.pop("report_meta", None)
    artifact = {
        "schema_version": 1,
        "report_type": "commodities_treasury",
        "generated_at": _generated_at(generated_at),
        "latest_data_date": str(meta["data_end_date"]),
        "source": ["Yahoo Finance", "ABD Hazinesi"],
        "status": status,
        "methodology": {
            "quote_definition": (
                "Yahoo Finance son erişilebilir piyasa fiyatıdır; resmî settlement değildir."
            ),
            "liquidity_filter": "Beş günden eski futures fiyatları eğri analizine alınmaz.",
            "treasury_source": "ABD Hazinesi günlük getiri eğrisi CSV'si.",
        },
        "metadata": meta,
        "data": clean_data,
    }
    return validate_artifact(artifact, "commodities_treasury")


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def write_snapshot(
    output_dir: Path | str,
    fund: dict[str, Any],
    market: dict[str, Any],
    generated_at: str | None = None,
) -> None:
    """İki artifacti yazar; manifest en son atomik değişir ve snapshotı aktive eder."""
    validate_artifact(fund, "fund_flows")
    validate_artifact(market, "commodities_treasury")
    root = Path(output_dir)
    payloads = {
        "fund_flows.json": _json_bytes(fund),
        "commodities_treasury.json": _json_bytes(market),
    }
    entries = {
        name: {
            "report_type": (
                "fund_flows" if name == "fund_flows.json" else "commodities_treasury"
            ),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        for name, raw in payloads.items()
    }
    for name, raw in payloads.items():
        _atomic_write(root / name, raw)
    manifest = {
        "schema_version": 1,
        "generated_at": _generated_at(generated_at),
        "files": entries,
    }
    _atomic_write(root / "manifest.json", _json_bytes(manifest))


def build_from_source(source_root: Path, generated_at: str | None = None):
    paths = {
        "group": source_root / "2_tefas_altin_akis" / "tefas_net_akis.html",
        "gold": source_root / "3_tefas_fon_akis_maili" / "tefas_altin_akis.html",
        "selected": source_root / "3_tefas_fon_akis_maili" / "tefas_secili_akis.html",
        "market": source_root / "1_emtia_tahvil_maili" / "emtia_futures.html",
    }
    contents = {}
    for key, path in paths.items():
        try:
            contents[key] = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(f"zorunlu kaynak okunamadı: {path}: {exc}") from exc
    fund = build_fund_artifact(
        contents["group"], contents["gold"], contents["selected"], generated_at
    )
    market = build_market_artifact(contents["market"], generated_at)
    return fund, market


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "Output" / "research_reports",
    )
    args = parser.parse_args(argv)
    generated = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        fund, market = build_from_source(args.source_root, generated)
        write_snapshot(args.output_dir, fund, market, generated)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"HATA: Research artifactleri üretilemedi: {exc}", file=sys.stderr)
        return 1
    print(
        f"OK: {args.output_dir} — fon akışları={fund['status']}, "
        f"emtia/Hazine={market['status']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
