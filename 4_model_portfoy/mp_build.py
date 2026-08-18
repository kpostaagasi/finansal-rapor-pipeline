#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Model Portföy Performans — workbook güncelleme + geçmiş sheet + grafik + mail.
mp_core.py'deki veri/hesap çekirdeğini kullanır.

Yaptıkları:
  1) TEFAS'tan fon fiyatları (+1 iş günü RT kayması), bültenlerden tahvil, workbook'tan KYD(+carry-forward)
  2) Database A:I'yi statik değerle YENİLER (RasDaily/RT bağımlılığı kalkar), tarihleri günceller
  3) "Performans Geçmişi" sheet'i: her hafta × 3 portföy getiri+benchmark+kümülatif endeks (değer)
  4) "Bu Hafta" sheet'i: son tamamlanan haftanın 3 portföy tablosu (değer)
  5) Kümülatif performans grafiği (PNG) + HTML tablo → output/  (mail gövdesi)
"""
import os, sys, datetime as dt
from collections import OrderedDict
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker

import mp_core as M

HOME = os.path.expanduser("~")
WB_PATH = os.path.join(HOME, "Documents", "Model Portföy Performans.xlsx")
OUT_DIR = os.path.join(HOME, "debt_instruments", "model_portfoy", "output")
os.makedirs(OUT_DIR, exist_ok=True)

PORT_ORDER = ["Temkinli", "Temkinli USD", "Agresif"]
# Kurumsal renk paleti (Excel'deki gibi): ana kurumsal kırmızı C00000 + iki ayırt edici tonu
PORT_COLORS = {"Temkinli": "#C00000", "Temkinli USD": "#E08A7D", "Agresif": "#6E0B0B"}
BENCH_COLOR = "#9aa0a6"
BRAND_RED = "#C00000"

# Varlık bazında bar grafiği + tablolarda kullanılan görünen etiketler
ASSET_LABEL = {"TRT": "TRT090130T12"}
# Bar grafiği varlık sırası: fonlar (BSD,BV1,BVD,BVF,BVZ,SPR) + tahvil + mevduat endeksleri
BAR_ORDER = list(M.FUNDS) + ["TRT", "TL Mevduat", "USD Mevduat"]


# ----------------------------- veri toplama -----------------------------
def gather():
    tefas = M.fetch_fund_prices()
    rt = M.build_rt_series(tefas)
    # tahvil: bülten + workbook elle serisi birleştir (elle öncelikli)
    bond = M.load_bond_series()
    wbv = openpyxl.load_workbook(WB_PATH, data_only=True)
    dbv = wbv["Database"]
    manual = {}
    for r in range(1, dbv.max_row + 1):
        s = dbv.cell(r, 19).value; t = dbv.cell(r, 20).value
        if isinstance(s, dt.datetime) and isinstance(t, (int, float)):
            manual[s.date().isoformat()] = float(t)
    merged = dict(bond); merged.update(manual)
    bond = OrderedDict(sorted(merged.items()))
    coupons = M.load_bond_coupons()
    seed_path = os.path.join(HOME, "debt_instruments", "model_portfoy", "inputs", "kyd_seed.csv")
    tl, usd = M.load_kyd_seed(seed_path)
    last_tefas = max(max(v.keys()) for v in tefas.values())
    last_data = dt.date.fromisoformat(last_tefas) - dt.timedelta(days=1)
    return dict(tefas=tefas, rt=rt, bond=bond, coupons=coupons,
                kyd_tl=tl, kyd_usd=usd, last_data=last_data)


def last_completed_friday(today=None):
    today = today or dt.date.today()
    # en son geçmiş cuma (bugün cuma ise bir önceki cuma tamamlanmış sayılmaz; veri cuma akşamı gelir)
    offset = (today.weekday() - 4) % 7   # cuma=4
    fri = today - dt.timedelta(days=offset)
    if fri >= today:  # bugün cuma/öncesi ise bir hafta geri
        fri -= dt.timedelta(days=7)
    return fri


def archive_weeks(data):
    """Hesaplanabilir ilk cuma (tahvil verisi olan) → son tamamlanan cuma."""
    bond_dates = list(data["bond"].keys())
    # ilk cuma: en erken tarih + 7 sonrası ki hem F hem F-7 tahvil bulunsun; pratikte 2026-06-12
    start = dt.date(2026, 6, 12)
    end = min(last_completed_friday(), data["last_data"])
    weeks = []
    d = start
    while d <= end:
        weeks.append(d)
        d += dt.timedelta(days=7)
    return weeks


def compute_all(data):
    rows = []
    for fri in archive_weeks(data):
        r = M.compute_week(fri, data["rt"], data["bond"], data["kyd_tl"],
                           data["kyd_usd"], data["last_data"], data["coupons"])
        rows.append(r)
    return rows


# ----------------------------- Database A:I statik yenile -----------------------------
def refresh_database_prices(wb, data):
    ws = wb["Database"]
    rt = data["rt"]; tl = data["kyd_tl"]; usd = data["kyd_usd"]
    last = data["last_data"]
    fund_cols = {"BSD": 2, "BV1": 3, "BVD": 4, "BVF": 5, "BVZ": 6, "SPR": 7}
    filled = 0
    for r in range(2, ws.max_row + 1):
        dcell = ws.cell(r, 1).value
        if not isinstance(dcell, dt.datetime):
            continue
        d = dcell.date()
        if d > last:
            # veri yok: RasDaily formülünü temizle
            for c in range(2, 10):
                if ws.cell(r, c).value is not None:
                    ws.cell(r, c).value = None
            continue
        for f, c in fund_cols.items():
            v = M.xlookup_le(rt[f], d.isoformat())
            ws.cell(r, c).value = v
        ws.cell(r, 8).value = M.kyd_value_at(tl, d)
        ws.cell(r, 9).value = M.kyd_value_at(usd, d)
        filled += 1
    return filled


# ----------------------------- yeni sheet'ler -----------------------------
THIN = Side(style="thin", color="D0D0D0")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HDR_FILL = PatternFill("solid", fgColor="C00000")
HDR_FONT = Font(color="FFFFFF", bold=True)
SUB_FILL = PatternFill("solid", fgColor="E8EEF4")


def _pct(x):
    return None if x is None else round(x * 100, 4)


def write_history_sheet(wb, rows):
    name = "Performans Geçmişi"
    if name in wb.sheetnames:
        del wb[name]
    ws = wb.create_sheet(name, 0)  # en başa
    headers = ["Hafta (Cuma)", "Dönem"]
    for p in PORT_ORDER:
        headers += [f"{p} Getiri %", f"{p} Benchmark %"]
    ws.append(headers)
    for c in range(1, len(headers) + 1):
        cell = ws.cell(1, c); cell.fill = HDR_FILL; cell.font = HDR_FONT
        cell.alignment = Alignment(horizontal="center", wrap_text=True, vertical="center")
        cell.border = BORDER
    # kümülatif endeksler
    cum = {p: 100.0 for p in PORT_ORDER}
    cum_bench = {p: 100.0 for p in PORT_ORDER}
    for r in rows:
        fri = r["friday"]; prev = r["prev"]
        line = [fri, f"{prev.strftime('%d.%m')}–{fri.strftime('%d.%m.%Y')}"]
        for p in PORT_ORDER:
            pr = r["portfolios"][p]["return"]; bm = r["portfolios"][p]["benchmark"]
            line += [_pct(pr), _pct(bm)]
        ws.append(line)
        rr = ws.max_row
        ws.cell(rr, 1).number_format = "dd.mm.yyyy"
        for c in range(3, len(headers) + 1):
            ws.cell(rr, c).number_format = "0.00"
    # kümülatif endeks bloğu (sağ tarafa)
    base_col = len(headers) + 2
    ws.cell(1, base_col, "Kümülatif Endeks (başlangıç=100)").font = Font(bold=True)
    ws.cell(2, base_col, "Hafta (Cuma)").font = Font(bold=True)
    for i, p in enumerate(PORT_ORDER):
        ws.cell(2, base_col + 1 + i, p).font = Font(bold=True)
    rowi = 3
    # başlangıç 100 satırı
    first_prev = rows[0]["prev"] if rows else None
    if first_prev:
        ws.cell(rowi, base_col, first_prev).number_format = "dd.mm.yyyy"
        for i, p in enumerate(PORT_ORDER):
            ws.cell(rowi, base_col + 1 + i, 100.0).number_format = "0.00"
        rowi += 1
    for r in rows:
        ws.cell(rowi, base_col, r["friday"]).number_format = "dd.mm.yyyy"
        for i, p in enumerate(PORT_ORDER):
            pr = r["portfolios"][p]["return"] or 0.0
            cum[p] *= (1 + pr)
            ws.cell(rowi, base_col + 1 + i, round(cum[p], 3)).number_format = "0.000"
        rowi += 1
    widths = [14, 16] + [15] * (len(PORT_ORDER) * 2) + [4, 14] + [13] * len(PORT_ORDER)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    return cum, cum_bench


def write_thisweek_sheet(wb, rows):
    name = "Bu Hafta"
    if name in wb.sheetnames:
        del wb[name]
    ws = wb.create_sheet(name, 1)
    if not rows:
        ws["A1"] = "Veri yok"; return
    r = rows[-1]
    fri = r["friday"]; prev = r["prev"]
    ws["A1"] = f"Model Portföy Haftalık Performans — {prev.strftime('%d.%m.%Y')} → {fri.strftime('%d.%m.%Y')}"
    ws["A1"].font = Font(bold=True, size=13)
    row = 3
    for p in PORT_ORDER:
        pd = r["portfolios"][p]
        ws.cell(row, 1, p).font = Font(bold=True, size=12, color=PORT_COLORS[p].replace("#", ""))
        row += 1
        for c, h in enumerate(["Varlık", "Ağırlık", "Hf. Getiri %"], 1):
            cell = ws.cell(row, c, h); cell.fill = SUB_FILL; cell.font = Font(bold=True); cell.border = BORDER
        row += 1
        for sym, w, ar in pd["assets"]:
            ws.cell(row, 1, sym).border = BORDER
            ws.cell(row, 2, w).number_format = "0%"; ws.cell(row, 2).border = BORDER
            cc = ws.cell(row, 3, _pct(ar)); cc.number_format = "0.00"; cc.border = BORDER
            row += 1
        ws.cell(row, 1, "Portföy Getirisi").font = Font(bold=True)
        ws.cell(row, 3, _pct(pd["return"])).number_format = "0.00"
        ws.cell(row, 3).font = Font(bold=True)
        row += 1
        bmtxt = "Benchmark" + (" (yaklaşık)" if pd["benchmark_approx"] else "")
        ws.cell(row, 1, bmtxt)
        ws.cell(row, 3, _pct(pd["benchmark"])).number_format = "0.00"
        row += 2
    for i, w in enumerate([18, 10, 13], 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w


# ----------------------------- varlık bazında getiriler -----------------------------
def asset_returns_lastweek(data, rows):
    """Son tamamlanan haftanın varlık bazında haftalık getirileri (bar grafiği için).
    Dönen: (friday, prev, OrderedDict{sym: getiri|None}) — BAR_ORDER sırasında."""
    r = rows[-1]
    fri = r["friday"]; prev = r["prev"]
    out = OrderedDict()
    for f in M.FUNDS:  # BSD, BV1, BVD, BVF, BVZ, SPR
        out[f] = M.fund_week_return(data["rt"], f, fri, prev, data["last_data"])
    out["TRT"] = M.bond_week_return(data["bond"], fri, prev, data["coupons"])
    out["TL Mevduat"] = M.kyd_week_return_carryforward(data["kyd_tl"], fri, prev)[0]
    out["USD Mevduat"] = M.kyd_week_return_carryforward(data["kyd_usd"], fri, prev)[0]
    return fri, prev, out


# ----------------------------- grafik + mail HTML -----------------------------
def make_chart(fri, prev, arets, path, baslik=None, y_etiket="Haftalık getiri (%)"):
    """Varlık bazında haftalık getiri bar grafiği (ekrandaki grafiğin birebir hali).
    baslik/y_etiket verilirse aylık gibi başka dönemler için de kullanılabilir (mp_month.py)."""
    labels, vals = [], []
    for sym in BAR_ORDER:
        v = arets.get(sym)
        if v is None:
            continue
        labels.append(ASSET_LABEL.get(sym, sym))
        vals.append(v * 100)
    if not vals:
        return None
    fig, ax = plt.subplots(figsize=(9, 4.8), dpi=150)
    bars = ax.bar(labels, vals, color=BRAND_RED, width=0.62, zorder=3)
    ax.axhline(0, color="#333333", lw=0.9, zorder=2)
    ax.set_title(baslik or f"Haftalık Getiriler — Varlık Bazında  ({prev.strftime('%d.%m')} → {fri.strftime('%d.%m.%Y')})",
                 fontsize=13, weight="bold")
    ax.set_ylabel(y_etiket)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda y, _: f"{y:.2f}%"))
    ax.grid(True, axis="y", alpha=0.3, zorder=0)
    pad = max(0.15, (max(vals) - min(vals)) * 0.08)
    for b, v in zip(bars, vals):
        ax.annotate(f"{v:+.2f}%", (b.get_x() + b.get_width() / 2, v),
                    ha="center", va="bottom" if v >= 0 else "top",
                    xytext=(0, 3 if v >= 0 else -3), textcoords="offset points",
                    fontsize=8, color="#333333")
    ax.set_ylim(min(0, min(vals)) - pad, max(0, max(vals)) + pad)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right", fontsize=9)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def _ret_color(x):
    if x is None:
        return "#666"
    return "#137333" if x >= 0 else "#c5221f"


def build_portfolio_card(name, pd, getiri_basligi="Haftalık Getiri"):
    """Ekrandaki üst tabloların birebir HTML hali: Varlık | Portföy Ağırlığı | Haftalık Getiri
    + Portföy Getirisi + Benchmark satırları. getiri_basligi aylık raporda değişir."""
    def fmt(x): return "—" if x is None else f"{x*100:+.2f}%"
    body = ""
    for sym, w, ar in pd["assets"]:
        label = ASSET_LABEL.get(sym, sym)
        body += f"""
        <tr>
          <td style="padding:6px 10px;border-bottom:1px solid #f0f0f0">{label}</td>
          <td style="padding:6px 10px;border-bottom:1px solid #f0f0f0;text-align:right;color:#444">{w*100:.0f}%</td>
          <td style="padding:6px 10px;border-bottom:1px solid #f0f0f0;text-align:right;font-weight:600;color:{_ret_color(ar)}">{fmt(ar)}</td>
        </tr>"""
    pr = pd["return"]; bm = pd["benchmark"]
    bmtxt = "Benchmark" + (" *" if pd.get("benchmark_approx") else "")
    return f"""
    <table style="border-collapse:collapse;width:100%;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08);font-size:13px">
      <thead>
        <tr><th colspan="3" style="background:{PORT_COLORS[name]};color:#fff;padding:9px 10px;text-align:left;font-size:14px">{name}</th></tr>
        <tr style="background:#f4f0f0;color:#555">
          <th style="padding:6px 10px;text-align:left;font-weight:600">Varlık</th>
          <th style="padding:6px 10px;text-align:right;font-weight:600">Portföy Ağırlığı</th>
          <th style="padding:6px 10px;text-align:right;font-weight:600">{getiri_basligi}</th>
        </tr>
      </thead>
      <tbody>{body}</tbody>
      <tfoot>
        <tr style="background:#faf5f5">
          <td colspan="2" style="padding:8px 10px;font-weight:700;border-top:2px solid {PORT_COLORS[name]}">Portföy Getirisi</td>
          <td style="padding:8px 10px;text-align:right;font-weight:700;border-top:2px solid {PORT_COLORS[name]};color:{_ret_color(pr)}">{fmt(pr)}</td>
        </tr>
        <tr>
          <td colspan="2" style="padding:6px 10px;color:#777">{bmtxt}</td>
          <td style="padding:6px 10px;text-align:right;color:#777">{fmt(bm)}</td>
        </tr>
      </tfoot>
    </table>"""


def build_email_html(rows, chart_rel):
    r = rows[-1]
    fri = r["friday"]; prev = r["prev"]
    any_approx = any(r["portfolios"][p].get("benchmark_approx") for p in PORT_ORDER)
    # 3 portföy kartını yan yana (mail için tek satırda hizalı) yerleştir
    cells = ""
    for p in PORT_ORDER:
        cells += f"""<td valign="top" style="padding:0 8px;width:33.33%">{build_portfolio_card(p, r['portfolios'][p])}</td>"""
    approx_note = (" Benchmark'ta * işaretli değerler tahmini (KYD endeksi carry-forward)." if any_approx else "")
    html = f"""<!doctype html><html><body style="margin:0;background:#f4f6f8;font-family:-apple-system,Segoe UI,Arial,sans-serif">
      <div style="max-width:900px;margin:0 auto;padding:24px">
        <h2 style="color:#C00000;margin:0 0 4px">Model Portföy Haftalık Performans</h2>
        <div style="color:#666;margin-bottom:18px">Bakılan dönem: {prev.strftime('%d.%m.%Y')} → {fri.strftime('%d.%m.%Y')}</div>
        <table style="width:100%;border-collapse:separate;border-spacing:0"><tr>{cells}</tr></table>
        <img src="cid:chart" style="width:100%;margin-top:22px;border-radius:8px;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.08)" alt="Varlık bazında haftalık getiriler"/>
        <div style="color:#333;font-size:14px;margin-top:8px"><br><br><br>saygılarımla,</div>
        <div style="color:#9aa0a6;font-size:12px;margin-top:16px">
          Kaynak: TEFAS (fonlar), BIST ttb bülteni (TRT090130T12), KYD mevduat endeksleri (carry-forward).{approx_note}
        </div>
      </div></body></html>"""
    return html


def main():
    data = gather()
    rows = compute_all(data)
    print(f"Hesaplanan hafta sayısı: {len(rows)} | son hafta: {rows[-1]['friday'] if rows else '-'}")

    wb = openpyxl.load_workbook(WB_PATH)  # formülleri korur
    filled = refresh_database_prices(wb, data)
    print(f"Database A:I yenilendi: {filled} satır statik değer")
    write_history_sheet(wb, rows)
    write_thisweek_sheet(wb, rows)
    # Formül tabanlı "Haftalık" sheet'i Excel açılışında yeniden hesaplasın (aksi halde
    # openpyxl formülleri yeniden hesaplamadığı için cache'te #VALUE!/eski değer kalır)
    wb.calculation.fullCalcOnLoad = True
    wb.save(WB_PATH)
    print(f"Workbook kaydedildi: {WB_PATH}")

    chart_path = os.path.join(OUT_DIR, "performans.png")
    fri, prev, arets = asset_returns_lastweek(data, rows)
    make_chart(fri, prev, arets, chart_path)
    print("Varlık bazında getiriler:", {ASSET_LABEL.get(k, k): (round(v*100, 2) if v is not None else None) for k, v in arets.items()})
    html = build_email_html(rows, "performans.png")
    with open(os.path.join(OUT_DIR, "mail_preview.html"), "w") as f:
        f.write(html)
    print("Grafik + mail önizleme yazıldı:", OUT_DIR)
    return rows


if __name__ == "__main__":
    main()
