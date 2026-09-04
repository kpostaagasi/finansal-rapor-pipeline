# -*- coding: utf-8 -*-
"""Research rapor artifactlerini doğrulayan saf veri katmanı."""
from __future__ import annotations

import hashlib
import json
import math
import os
import urllib.request
from numbers import Real
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = 1
ALLOWED_STATUS = {"ready", "partial", "stale", "failed"}
EXPECTED_FILES = {
    "fund_flows.json": "fund_flows",
    "commodities_treasury.json": "commodities_treasury",
}
DEFAULT_ARTIFACT_URL = "https://kpostaagasi.github.io/finansal-raporlar"


def _is_streamlit_cloud() -> bool:
    """Streamlit Community Cloud container'ı `appuser` kullanıcısıyla çalışır."""
    return os.environ.get("HOME", "").startswith("/home/appuser")


# Deployed Streamlit uygulamasında artifact'ler uzak kaynaktan (Pages)
# hash doğrulamalı çekilir; yerel geliştirmede yerel dosyalar kullanılır.
# RESEARCH_ARTIFACT_URL ortam değişkeni her zaman bu kararı ezer.
_remote_env = os.environ.get("RESEARCH_ARTIFACT_URL")
if _remote_env is None:
    _remote_env = DEFAULT_ARTIFACT_URL if _is_streamlit_cloud() else ""
REMOTE_BASE = _remote_env.strip().rstrip("/")
REQUIRED_FIELDS = {
    "schema_version",
    "report_type",
    "generated_at",
    "latest_data_date",
    "source",
    "status",
    "data",
}


class ReportContractError(ValueError):
    """Artifact veya manifest sözleşmesi geçersiz olduğunda yükselir."""


def _parse_date(value: Any, field: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ReportContractError(f"geçersiz {field}: {value!r}") from exc


def _parse_datetime(value: Any, field: str) -> datetime:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ReportContractError(f"geçersiz {field}: {value!r}") from exc


def _require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ReportContractError(f"{field} JSON nesnesi olmalı")
    return value


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReportContractError(f"{field} boş olmayan metin olmalı")
    return value


def _validate_numeric_series(value: Any, expected_length: int, field: str) -> None:
    if not isinstance(value, list) or len(value) != expected_length:
        raise ReportContractError(f"{field} tarih serisiyle aynı uzunlukta olmalı")
    for item in value:
        if item is None:
            continue
        if isinstance(item, bool) or not isinstance(item, Real) or not math.isfinite(item):
            raise ReportContractError(f"{field} yalnız sayısal veya null değer içerebilir")


def _validate_dates(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ReportContractError(f"{field} boş olmayan liste olmalı")
    parsed = [_parse_date(item, field) for item in value]
    if parsed != sorted(parsed) or len(parsed) != len(set(parsed)):
        raise ReportContractError(f"{field} benzersiz ve artan sırada olmalı")
    return value


def _validate_report_metadata(value: Any, field: str) -> Mapping[str, Any]:
    meta = _require_mapping(value, field)
    for key in ("data_end_date", "expected_count", "found_count"):
        if key not in meta:
            raise ReportContractError(f"{field}.{key} eksik")
    _parse_date(meta["data_end_date"], f"{field}.data_end_date")
    expected = meta["expected_count"]
    found = meta["found_count"]
    if (
        isinstance(expected, bool)
        or isinstance(found, bool)
        or not isinstance(expected, int)
        or not isinstance(found, int)
        or expected < 0
        or found < 0
        or found > expected
    ):
        raise ReportContractError(f"{field} kapsam sayıları geçersiz")
    return meta


def _validate_flow_report(value: Any, field: str, group: bool) -> None:
    report = _require_mapping(value, field)
    _require_text(report.get("title"), f"{field}.title")
    if report.get("status") not in ALLOWED_STATUS:
        raise ReportContractError(f"{field}.status geçersiz")
    _validate_report_metadata(report.get("metadata"), f"{field}.metadata")
    series = _require_mapping(report.get("series"), f"{field}.series")
    dates = _validate_dates(series.get("d"), f"{field}.series.d")
    if group:
        _validate_numeric_series(series.get("yf"), len(dates), f"{field}.series.yf")
        _validate_numeric_series(series.get("eyf"), len(dates), f"{field}.series.eyf")
        return
    funds = _require_mapping(series.get("f"), f"{field}.series.f")
    names = _require_mapping(series.get("ad"), f"{field}.series.ad")
    if not funds:
        raise ReportContractError(f"{field}.series.f boş olamaz")
    for code, values in funds.items():
        if code not in names:
            raise ReportContractError(f"{field}.series.ad fon adını içermiyor: {code}")
        _validate_numeric_series(values, len(dates), f"{field}.series.f.{code}")


# Fon akışı artifactindeki zorunlu raporlar. `gold_total` grup serisi (yf/eyf)
# taşır; diğerleri satır bazlı seri (f/ad) taşır — `fund_groups` satırları fon
# değil grup olsa da yapı aynıdır, bu yüzden aynı kurallarla doğrulanır.
GROUP_SERIES_REPORTS = ("gold_total",)
REQUIRED_FUND_REPORTS = (
    "gold_total",
    "gold_by_fund",
    "selected_funds",
    "precious_metals",
    "money_market",
    "participation",
    "equity",
    "debt",
    "fund_groups",
)


def _validate_fund_artifact(data: Mapping[str, Any]) -> None:
    reports = _require_mapping(data.get("reports"), "data.reports")
    missing = sorted(set(REQUIRED_FUND_REPORTS) - reports.keys())
    if missing:
        raise ReportContractError("zorunlu fon raporu eksik: " + ", ".join(missing))
    for key in sorted(reports):
        _validate_flow_report(
            reports[key],
            f"data.reports.{key}",
            group=key in GROUP_SERIES_REPORTS,
        )


def _validate_market_artifact(value: Mapping[str, Any], data: Mapping[str, Any]) -> None:
    meta = _validate_report_metadata(value.get("metadata"), "metadata")
    max_age_days = meta.get("max_quote_age_days")
    if (
        isinstance(max_age_days, bool)
        or not isinstance(max_age_days, Real)
        or not math.isfinite(max_age_days)
        or max_age_days <= 0
    ):
        raise ReportContractError("metadata.max_quote_age_days geçersiz")
    source_run = _parse_datetime(meta.get("last_successful_run"), "metadata.last_successful_run")
    if source_run.tzinfo is None:
        raise ReportContractError("metadata.last_successful_run saat dilimi içermeli")

    for key in ("treasury_expected_count", "treasury_found_count"):
        if not isinstance(meta.get(key), int) or isinstance(meta.get(key), bool):
            raise ReportContractError(f"metadata.{key} geçersiz")
    treasury_expected = meta["treasury_expected_count"]
    treasury_found = meta["treasury_found_count"]
    if treasury_expected < 0 or treasury_found < 0 or treasury_found > treasury_expected:
        raise ReportContractError("metadata Hazine kapsam sayıları geçersiz")

    metadata_excluded = meta.get("excluded_stale_quotes")
    if not isinstance(metadata_excluded, list):
        raise ReportContractError("metadata.excluded_stale_quotes liste olmalı")
    metadata_excluded_symbols = set()
    for item_index, item_value in enumerate(metadata_excluded):
        item = _require_mapping(item_value, f"metadata.excluded_stale_quotes[{item_index}]")
        symbol = item.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            raise ReportContractError("metadata.excluded_stale_quotes symbol içermeli")
        if symbol in metadata_excluded_symbols:
            raise ReportContractError(f"tekrarlanan filtrelenmiş sembol: {symbol}")
        metadata_excluded_symbols.add(symbol)

    curves = data.get("curves")
    if not isinstance(curves, list):
        raise ReportContractError("data.curves liste olmalı")
    point_symbols = set()
    curve_excluded_symbols = set()
    total_points = 0
    total_requested = 0
    for curve_index, curve_value in enumerate(curves):
        curve = _require_mapping(curve_value, f"data.curves[{curve_index}]")
        _require_text(curve.get("key"), f"data.curves[{curve_index}].key")
        _require_text(curve.get("title"), f"data.curves[{curve_index}].title")
        curve_found = curve.get("found_count")
        curve_requested = curve.get("requested_count")
        if (
            isinstance(curve_found, bool)
            or isinstance(curve_requested, bool)
            or not isinstance(curve_found, int)
            or not isinstance(curve_requested, int)
            or curve_found < 0
            or curve_requested < curve_found
        ):
            raise ReportContractError(f"data.curves[{curve_index}] kapsam sayıları geçersiz")
        points = curve.get("points")
        if not isinstance(points, list):
            raise ReportContractError(f"data.curves[{curve_index}].points liste olmalı")
        if curve_found != len(points):
            raise ReportContractError(f"data.curves[{curve_index}].found_count points ile uyuşmuyor")
        total_points += len(points)
        total_requested += curve_requested
        for point_index, point_value in enumerate(points):
            point = _require_mapping(
                point_value, f"data.curves[{curve_index}].points[{point_index}]"
            )
            _require_text(
                point.get("label"), f"data.curves[{curve_index}].points[{point_index}].label"
            )
            symbol = point.get("sym")
            if not isinstance(symbol, str) or not symbol:
                raise ReportContractError(
                    f"data.curves[{curve_index}].points[{point_index}].sym eksik"
                )
            if symbol in point_symbols:
                raise ReportContractError(f"tekrarlanan kontrat sembolü: {symbol}")
            point_symbols.add(symbol)
            quote_time = point.get("quote_time")
            if (
                isinstance(quote_time, bool)
                or not isinstance(quote_time, Real)
                or not math.isfinite(quote_time)
                or quote_time <= 0
            ):
                raise ReportContractError(
                    f"data.curves[{curve_index}].points[{point_index}].quote_time geçersiz"
                )
            try:
                quote_datetime = datetime.fromtimestamp(float(quote_time), tz=timezone.utc)
            except (OverflowError, OSError, ValueError) as exc:
                raise ReportContractError(f"kontrat quote_time tarih aralığı dışında: {symbol}") from exc
            quote_age_seconds = source_run.timestamp() - quote_datetime.timestamp()
            if quote_age_seconds > float(max_age_days) * 86_400:
                raise ReportContractError(f"eski kontrat eğriye dahil edilmiş: {symbol}")
            _validate_numeric_series(
                [point.get("value")], 1, f"data.curves[{curve_index}].points[{point_index}].value"
            )
            if point.get("value") is None:
                raise ReportContractError(f"kontrat fiyatı null olamaz: {symbol}")

        curve_excluded = curve.get("excluded_stale_quotes")
        if not isinstance(curve_excluded, list):
            raise ReportContractError(
                f"data.curves[{curve_index}].excluded_stale_quotes liste olmalı"
            )
        for item_index, item_value in enumerate(curve_excluded):
            item = _require_mapping(
                item_value,
                f"data.curves[{curve_index}].excluded_stale_quotes[{item_index}]",
            )
            symbol = item.get("symbol")
            if not isinstance(symbol, str) or not symbol:
                raise ReportContractError("filtrelenen kontrat symbol içermeli")
            if symbol in curve_excluded_symbols:
                raise ReportContractError(f"tekrarlanan filtrelenmiş sembol: {symbol}")
            curve_excluded_symbols.add(symbol)

    if meta["found_count"] != total_points or meta["expected_count"] != total_requested:
        raise ReportContractError("emtia üst kapsamı eğri kapsamlarıyla uyuşmuyor")
    if metadata_excluded_symbols != curve_excluded_symbols:
        raise ReportContractError("üst ve eğri filtrelenen sembol listeleri uyuşmuyor")
    overlap = point_symbols & metadata_excluded_symbols
    if overlap:
        raise ReportContractError("filtrelenen sembol eğriye dahil edilmiş: " + ", ".join(sorted(overlap)))

    rates = _require_mapping(data.get("rates"), "data.rates")
    rate_points = rates.get("points")
    if not isinstance(rate_points, list):
        raise ReportContractError("data.rates.points liste olmalı")
    if treasury_found != len(rate_points):
        raise ReportContractError("Hazine kapsamı points ile uyuşmuyor")
    for point_index, point_value in enumerate(rate_points):
        point = _require_mapping(point_value, f"data.rates.points[{point_index}]")
        _require_text(point.get("label"), f"data.rates.points[{point_index}].label")
        _validate_numeric_series(
            [point.get("value")], 1, f"data.rates.points[{point_index}].value"
        )


def validate_artifact(value: Any, expected_type: str) -> dict[str, Any]:
    """Tek bir artifactin üst ve rapora özgü iç sözleşmesini doğrular."""
    if not isinstance(value, dict):
        raise ReportContractError("artifact JSON nesnesi olmalı")
    missing = sorted(REQUIRED_FIELDS - value.keys())
    if missing:
        raise ReportContractError("zorunlu alan eksik: " + ", ".join(missing))
    if value["schema_version"] != SCHEMA_VERSION:
        raise ReportContractError(
            f"desteklenmeyen schema_version: {value['schema_version']!r}"
        )
    if value["report_type"] != expected_type:
        raise ReportContractError(
            f"report_type {value['report_type']!r}; beklenen {expected_type!r}"
        )
    if value["status"] not in ALLOWED_STATUS:
        raise ReportContractError(f"geçersiz status: {value['status']!r}")
    _parse_datetime(value["generated_at"], "generated_at")
    _parse_date(value["latest_data_date"], "latest_data_date")
    if not isinstance(value["source"], list) or not value["source"]:
        raise ReportContractError("source boş olmayan liste olmalı")
    data = _require_mapping(value["data"], "data")
    if expected_type == "fund_flows":
        _validate_fund_artifact(data)
    elif expected_type == "commodities_treasury":
        _validate_market_artifact(value, data)
    return value


def _manifest_entries(manifest: Any) -> dict[str, Any]:
    """Manifest sözleşmesini doğrular ve `files` sözlüğünü döndürür."""
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
        raise ReportContractError("manifest schema_version geçersiz")
    _parse_datetime(manifest.get("generated_at"), "manifest.generated_at")
    entries = manifest.get("files")
    if not isinstance(entries, dict):
        raise ReportContractError("manifest.files nesne olmalı")
    return entries


def _fetch_bytes(url: str) -> bytes:
    """Uzak artifact'i küçük zaman aşımıyla indirir; hata sözleşme hatasına dönüşür."""
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()
    except Exception as exc:
        raise ReportContractError(f"uzak artifact çekilemedi ({url}): {exc}") from exc


def _decode_artifact(raw: bytes, filename: str, expected_hash: str, report_type: str) -> dict[str, Any]:
    """Ham baytları hash + JSON + şema ile doğrular."""
    actual_hash = hashlib.sha256(raw).hexdigest()
    if actual_hash != expected_hash:
        raise ReportContractError(f"artifact hash uyuşmuyor: {filename}")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReportContractError(f"artifact JSON geçersiz: {filename}") from exc
    return validate_artifact(value, report_type)


def load_snapshot(directory: Path | str) -> dict[str, dict[str, Any]]:
    """Manifest hashleri eşleşen iki Research artifactini birlikte yükler.

    RESEARCH_ARTIFACT_URL set edilmişse uzak kaynaktan (Pages) çeker;
    aksi halde yerel dosyalardan okur.
    """
    if REMOTE_BASE:
        return load_snapshot_remote(REMOTE_BASE)
    return load_snapshot_local(directory)


def load_snapshot_remote(base_url: str) -> dict[str, dict[str, Any]]:
    """Manifest + artifact'leri uzak kaynaktan hash doğrulamalı çeker."""
    manifest_raw = _fetch_bytes(f"{base_url}/manifest.json")
    try:
        manifest = json.loads(manifest_raw)
    except json.JSONDecodeError as exc:
        raise ReportContractError("uzak manifest JSON geçersiz") from exc
    entries = _manifest_entries(manifest)

    loaded: dict[str, dict[str, Any]] = {}
    for filename, report_type in EXPECTED_FILES.items():
        entry = entries.get(filename)
        if not isinstance(entry, dict) or entry.get("report_type") != report_type:
            raise ReportContractError(f"uzak manifest girdisi geçersiz: {filename}")
        raw = _fetch_bytes(f"{base_url}/{filename}")
        loaded[report_type] = _decode_artifact(raw, filename, entry.get("sha256", ""), report_type)
    return loaded


def load_snapshot_local(directory: Path | str) -> dict[str, dict[str, Any]]:
    """Yerel dosyalardan manifest hashleri eşleşen artifactleri yükler."""
    root = Path(directory)
    manifest_path = root / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportContractError(f"manifest okunamadı: {exc}") from exc
    entries = _manifest_entries(manifest)

    loaded: dict[str, dict[str, Any]] = {}
    for filename, report_type in EXPECTED_FILES.items():
        entry = entries.get(filename)
        if not isinstance(entry, dict):
            raise ReportContractError(f"manifest girdisi eksik: {filename}")
        if entry.get("report_type") != report_type:
            raise ReportContractError(f"manifest report_type geçersiz: {filename}")
        path = root / filename
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ReportContractError(f"artifact okunamadı: {filename}: {exc}") from exc
        loaded[report_type] = _decode_artifact(raw, filename, entry.get("sha256", ""), report_type)
    return loaded


def classify_metadata(
    meta: Mapping[str, Any], today: date | None = None, max_age_days: int = 4
) -> str:
    """Bir alt raporun tarih/kapsam metadata'sından fail-closed durum üretir."""
    if meta.get("status") == "failed":
        return "failed"
    value = meta.get("data_end_date") or meta.get("latest_data_date")
    if not value:
        return "failed"
    data_date = _parse_date(value, "data_end_date")
    reference = today or date.today()
    if (reference - data_date).days > max_age_days:
        return "stale"
    expected = meta.get("expected_count")
    found = meta.get("found_count")
    if (
        meta.get("status") == "partial"
        or expected is None
        or found is None
        or found != expected
        or bool(meta.get("missing"))
        or bool(meta.get("uncomputed_count"))
        or bool(meta.get("recent_gap_count"))
    ):
        return "partial"
    return "ready"


def complete_total(values: Iterable[float | int | None]) -> float | int | None:
    """Eksik hücre varsa kısmi toplam döndürmez; boş seri de hesaplanamaz."""
    items = list(values)
    if not items or any(value is None for value in items):
        return None
    return sum(value for value in items if value is not None)
