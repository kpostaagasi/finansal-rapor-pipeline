#!/usr/bin/env python3
"""Yerel rapor çıktılarından yayınlanabilir tek bir site klasörü oluşturur."""

from __future__ import annotations

import datetime as dt
import html
import json
import re
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SITE_DIR = ROOT / "site"

REPORTS = (
    {
        "title": "Emtia Futures ve ABD Hazine Eğrisi",
        "description": "WTI, altın, gümüş, platin, bakır ve alüminyum vadeli eğrileri ile ABD Hazine getiri eğrisi.",
        "source": "1_emtia_tahvil_maili/emtia_futures.html",
        "target": "emtia_futures.html",
    },
    {
        "title": "TEFAS Altın Fonlarına Net Akış",
        "description": "Altın yatırım ve emeklilik fonlarının grup bazında günlük, haftalık ve aylık net akışı.",
        "source": "2_tefas_altin_akis/tefas_net_akis.html",
        "target": "tefas_net_akis.html",
    },
    {
        "title": "TEFAS Altın Fonları — Fon Bazında",
        "description": "Altın yatırım ve emeklilik fonlarının ayrı ayrı net giriş ve çıkışları.",
        "source": "3_tefas_fon_akis_maili/tefas_altin_akis.html",
        "target": "tefas_altin_fon_bazinda.html",
    },
    {
        "title": "TEFAS Seçili Fonlara Net Akış",
        "description": "Takip listesindeki seçili fonların fon bazında net giriş ve çıkışları.",
        "source": "3_tefas_fon_akis_maili/tefas_secili_akis.html",
        "target": "tefas_secili_akis.html",
    },
)


def _inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"Proje dışındaki dosya kullanılamaz: {path}") from exc
    return resolved


REPORT_META_RE = re.compile(r"const\s+REPORT_META\s*=\s*(\{.*?\})\s*;", re.DOTALL)


def _read_meta(path: Path) -> dict:
    match = REPORT_META_RE.search(path.read_text(encoding="utf-8"))
    if not match:
        return {}
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _tr_date(value: str | None) -> str:
    if not value:
        return "—"
    try:
        return dt.date.fromisoformat(value[:10]).strftime("%d.%m.%Y")
    except ValueError:
        return value


def _status(meta: dict, now: dt.date) -> str:
    if not meta:
        return "ready"
    try:
        data_date = dt.date.fromisoformat(str(meta.get("data_end_date", ""))[:10])
    except ValueError:
        return "stale"
    if (now - data_date).days > 4:
        return "stale"
    if (meta.get("found_count") != meta.get("expected_count") or meta.get("missing")
            or meta.get("uncomputed_count") or meta.get("recent_gap_count")):
        return "partial"
    if meta.get("stale_quotes"):
        return "stale_quotes"
    return "ready"


def _parse_failures(args: list[str]) -> dict[str, str]:
    allowed = {report["target"] for report in REPORTS}
    failures = {}
    for arg in args:
        if not arg.startswith("--failure="):
            continue
        payload = arg[len("--failure="):]
        if "=" not in payload:
            continue
        target, message = payload.split("=", 1)
        if target in allowed and message:
            failures[target] = message
    return failures


def _card(report: dict[str, str], ready: bool, updated: str | None,
          meta: dict | None = None, state: str = "ready") -> str:
    title = html.escape(report["title"])
    description = html.escape(report["description"])
    meta = meta or {}
    if ready:
        target = html.escape(report["target"], quote=True)
        action = f'<a class="button" href="{target}">Raporu aç →</a>'
        status_label = {
            "ready": "Güncel",
            "partial": "Eksik veri",
            "stale_quotes": "Seyrek fiyat",
            "stale": "Veri güncel değil",
            "failed": "Başarısız",
        }.get(state, "Durum bilinmiyor")
        status = f'<span class="status {html.escape(state)}">{status_label}</span>'
        details = []
        if meta:
            details.append(f"Veri sonu: {_tr_date(meta.get('data_end_date'))}")
            expected = meta.get("expected_count")
            found = meta.get("found_count")
            if expected is not None and found is not None:
                label = html.escape(str(meta.get("count_label", "öğe")))
                if meta.get("candidate_count") is not None:
                    details.append(f"Kapsam: {found} güncel/likit {label}")
                else:
                    details.append(f"Kapsam: {found}/{expected} {label}")
            missing = meta.get("missing") or []
            if missing:
                details.append("Eksik: " + html.escape(", ".join(map(str, missing))))
            if meta.get("uncomputed_count"):
                details.append(
                    "Hesaplanamayan akış: "
                    + html.escape(str(meta["uncomputed_count"]))
                    + " fon"
                )
            if meta.get("recent_gap_count"):
                details.append(
                    "Son 90 günde hesaplanamayan akış: "
                    + html.escape(str(meta["recent_gap_count"]))
                )
            treasury_expected = meta.get("treasury_expected_count")
            treasury_found = meta.get("treasury_found_count")
            if treasury_expected is not None and treasury_found is not None:
                details.append(f"ABD Hazine: {treasury_found}/{treasury_expected} vade")
            excluded_stale_quotes = meta.get("excluded_stale_quotes") or []
            if excluded_stale_quotes:
                rendered_quotes = []
                for quote in excluded_stale_quotes:
                    symbol = html.escape(str(quote.get("symbol", "?")))
                    try:
                        age = f"{float(quote.get('age_days')):.1f}".replace(".", ",")
                    except (TypeError, ValueError):
                        age = "?"
                    rendered_quotes.append(f"{symbol} ({age} gün)")
                details.append("Filtrelenen seyrek vadeler: " + ", ".join(rendered_quotes))
            if meta.get("source"):
                details.append("Kaynak: " + html.escape(str(meta["source"])))
            if meta.get("last_successful_run"):
                run = str(meta["last_successful_run"]).replace("T", " ")[:16]
                details.append("Son başarılı üretim: " + html.escape(run))
            if meta.get("error_message"):
                details.append("Hata: " + html.escape(str(meta["error_message"])))
        else:
            details.append("Yerel çıktı: " + html.escape(updated or ""))
        detail_html = "".join(f"<li>{item}</li>" for item in details)
        metadata = f'<ul class="meta">{detail_html}</ul>'
    else:
        action = '<span class="button disabled">Henüz hazır değil</span>'
        status = '<span class="status missing">Henüz hazır değil</span>'
        metadata = ""
    return f"""
      <article class="card">
        <div>{status}</div>
        <h2>{title}</h2>
        <p>{description}</p>
        {metadata}
        {action}
      </article>"""


def _index(cards: str) -> str:
    generated = dt.datetime.now().strftime("%d.%m.%Y %H:%M")
    return f"""<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Otomatik Finansal Raporlar</title>
  <style>
    :root {{ color-scheme: light; --bg:#f5f6f8; --card:#fff; --ink:#172033; --muted:#667085; --line:#e4e7ec; --accent:#9e1b32; --ok:#067647; --warn:#b54708; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--ink); font-family:system-ui,-apple-system,"Segoe UI",sans-serif; }}
    main {{ max-width:1040px; margin:0 auto; padding:42px 20px 60px; }}
    h1 {{ margin:0 0 8px; font-size:30px; }}
    .lead {{ margin:0 0 30px; color:var(--muted); }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:18px; }}
    .card {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:22px; box-shadow:0 2px 8px rgba(16,24,40,.04); }}
    .card h2 {{ margin:14px 0 8px; font-size:19px; }}
    .card p {{ min-height:66px; color:var(--muted); line-height:1.5; }}
    .status {{ font-size:12px; font-weight:650; }}
    .status.ready {{ color:var(--ok); }} .status.partial, .status.stale_quotes, .status.stale, .status.failed, .status.missing {{ color:var(--warn); }}
    .meta {{ min-height:106px; margin:12px 0 0; padding-left:18px; color:var(--muted); font-size:12px; line-height:1.55; }}
    .button {{ display:inline-block; margin-top:10px; padding:9px 13px; border-radius:8px; background:var(--accent); color:#fff; text-decoration:none; font-weight:650; }}
    .button.disabled {{ background:#e4e7ec; color:#667085; }}
    footer {{ margin-top:28px; color:var(--muted); font-size:12px; }}
  </style>
</head>
<body><main>
  <h1>Otomatik Finansal Raporlar</h1>
  <p class="lead">Emtia ve TEFAS verilerinden üretilen güncel raporlar.</p>
  <section class="grid">{cards}
  </section>
  <footer>Site paketi: {generated} (İstanbul). Her raporun gerçek veri tarihi kendi sayfasında gösterilir.</footer>
</main></body>
</html>
"""


def build_site(root: Path = ROOT, site_dir: Path = SITE_DIR,
               now: dt.date | None = None,
               failures: dict[str, str] | None = None) -> dict[str, int | str]:
    root = root.resolve()
    site_dir = _inside(site_dir, root)
    now = now or dt.date.today()
    failures = failures or {}
    site_dir.mkdir(parents=True, exist_ok=True)
    cards = []
    statuses = {}
    ready = 0

    for report in REPORTS:
        source = _inside(root / report["source"], root)
        target = _inside(site_dir / report["target"], site_dir)
        exists = source.is_file()
        updated = None
        meta = {}
        state = "missing"
        if exists:
            shutil.copy2(source, target)
            updated = dt.datetime.fromtimestamp(source.stat().st_mtime).strftime("%d.%m.%Y %H:%M")
            meta = _read_meta(source)
            state = _status(meta, now)
            if report["target"] in failures:
                state = "failed"
                meta = {**meta, "error_message": failures[report["target"]]}
            ready += 1
        elif target.exists():
            target.unlink()
        statuses[report["target"]] = {
            "status": state,
            "title": report["title"],
            **meta,
        }
        cards.append(_card(report, exists, updated, meta, state))

    index_path = site_dir / "index.html"
    index_path.write_text(_index("".join(cards)), encoding="utf-8")
    (site_dir / "report_status.json").write_text(
        json.dumps(statuses, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"ready": ready, "missing": len(REPORTS) - ready, "index": str(index_path)}


def main() -> int:
    result = build_site(failures=_parse_failures(sys.argv[1:]))
    print(f"Site hazır: {result['index']} — {result['ready']} rapor hazır, {result['missing']} eksik")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
