#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Model Portföy AYLIK performans — takvim ayı (önceki ay sonu kapanışı → ay sonu kapanışı).

Haftalık pipeline'ın (mp_core/mp_build) aynı veri ve hesap çekirdeğini kullanır;
tek fark dönemin bir hafta yerine bir takvim ayı olması:
    varlık getirisi = fiyat(ay_sonu) / fiyat(önceki_ay_sonu) - 1
    portföy getirisi = Σ(varlık getirisi × portföy ağırlığı)
    benchmark = KYD mevduat endeksi dönem getirisi + yıllık spread × gün/365

Workbook'a YAZMAZ (haftalık sheet'leri bozmamak için); sadece output/ altına
aylik_performans.png + mail_aylik_preview.html üretir ve isteğe bağlı mailler.

Kullanım:
  python3 mp_month.py                      # önceki takvim ayı, sadece üret
  python3 mp_month.py --ay 2026-07         # belirli ay
  python3 mp_month.py --ay 2026-07 --example   # SADECE gönderene [ÖRNEK] mail
  python3 mp_month.py --ay 2026-07 --send      # config'teki 4 alıcıya gönder
"""
import os, sys, ssl, smtplib, calendar, datetime as dt
from collections import OrderedDict
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mp_core as M
import mp_build as B
import mp_send as S

AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
         "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]

OUT_DIR = B.OUT_DIR
CHART_PATH = os.path.join(OUT_DIR, "aylik_performans.png")
HTML_PATH = os.path.join(OUT_DIR, "mail_aylik_preview.html")


# ----------------------------- dönem -----------------------------
def ay_donemi(yil, ay):
    """Takvim ayı dönemi: (önceki ay son günü, bu ay son günü)."""
    bitis = dt.date(yil, ay, calendar.monthrange(yil, ay)[1])
    baslangic = dt.date(yil, ay, 1) - dt.timedelta(days=1)
    return baslangic, bitis


def onceki_ay(today=None):
    today = today or dt.date.today()
    ilk = dt.date(today.year, today.month, 1)
    son = ilk - dt.timedelta(days=1)
    return son.year, son.month


# ----------------------------- hesap -----------------------------
def compute_period(baslangic, bitis, data):
    """compute_week'in dönem-genel hali: 3 portföyün dönem getirisi + benchmark."""
    def asset_ret(sym):
        if sym == "TRT":
            return M.bond_week_return(data["bond"], bitis, baslangic, data["coupons"])
        return M.fund_week_return(data["rt"], sym, bitis, baslangic, data["last_data"])

    gun = (bitis - baslangic).days
    res = {"baslangic": baslangic, "bitis": bitis, "gun": gun, "portfolios": {}}
    for name, weights in M.PORTFOLIOS.items():
        assets = []
        pr = 0.0
        ok = True
        for sym, w in weights:
            r = asset_ret(sym)
            assets.append((sym, w, r))
            if r is None:
                ok = False
            else:
                pr += w * r
        dep, spread = M.BENCH[name]
        od = data["kyd_tl"] if dep == "TL" else data["kyd_usd"]
        bench_base, approx = M.kyd_week_return_carryforward(od, bitis, baslangic)
        bench = None if bench_base is None else bench_base + spread * gun / 365
        res["portfolios"][name] = {
            "assets": assets, "return": pr if ok else None,
            "benchmark": bench, "benchmark_approx": approx,
        }
    return res


def asset_returns_period(baslangic, bitis, data):
    """Bar grafiği için varlık bazında dönem getirileri (BAR_ORDER sırasında)."""
    out = OrderedDict()
    for f in M.FUNDS:
        out[f] = M.fund_week_return(data["rt"], f, bitis, baslangic, data["last_data"])
    out["TRT"] = M.bond_week_return(data["bond"], bitis, baslangic, data["coupons"])
    out["TL Mevduat"] = M.kyd_week_return_carryforward(data["kyd_tl"], bitis, baslangic)[0]
    out["USD Mevduat"] = M.kyd_week_return_carryforward(data["kyd_usd"], bitis, baslangic)[0]
    return out


# ----------------------------- mail gövdesi -----------------------------
def build_email_html(r, ay_adi):
    bas = r["baslangic"]; bit = r["bitis"]
    any_approx = any(r["portfolios"][p].get("benchmark_approx") for p in B.PORT_ORDER)
    cells = ""
    for p in B.PORT_ORDER:
        kart = B.build_portfolio_card(p, r["portfolios"][p], getiri_basligi="Aylık Getiri")
        cells += f"""<td valign="top" style="padding:0 8px;width:33.33%">{kart}</td>"""
    approx_note = (" Benchmark'ta * işaretli değerler tahmini (KYD endeksi carry-forward)."
                   if any_approx else "")
    return f"""<!doctype html><html><body style="margin:0;background:#f4f6f8;font-family:-apple-system,Segoe UI,Arial,sans-serif">
      <div style="max-width:900px;margin:0 auto;padding:24px">
        <h2 style="color:#C00000;margin:0 0 4px">Model Portföy Aylık Performans — {ay_adi}</h2>
        <div style="color:#666;margin-bottom:18px">Bakılan dönem: {bas.strftime('%d.%m.%Y')} → {bit.strftime('%d.%m.%Y')} kapanış ({r['gun']} gün)</div>
        <table style="width:100%;border-collapse:separate;border-spacing:0"><tr>{cells}</tr></table>
        <img src="cid:chart" style="width:100%;margin-top:22px;border-radius:8px;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.08)" alt="Varlık bazında aylık getiriler"/>
        <div style="color:#333;font-size:14px;margin-top:8px"><br><br><br>saygılarımla,</div>
        <div style="color:#9aa0a6;font-size:12px;margin-top:16px">
          Kaynak: TEFAS (fonlar), BIST ttb bülteni (TRT090130T12), KYD mevduat endeksleri (carry-forward).{approx_note}
        </div>
      </div></body></html>"""


def build_message(cfg, ay_adi):
    with open(HTML_PATH, encoding="utf-8") as f:
        html = f.read()
    msg = EmailMessage()
    msg["Subject"] = cfg["subject"]
    msg["From"] = formataddr((cfg.get("sender_name", ""), cfg["sender"]))
    msg["To"] = ", ".join(cfg["recipients"])
    msg["Date"] = formatdate(localtime=True)

    chart_cid = make_msgid(domain="modelportfoy")[1:-1]
    html = html.replace("cid:chart", f"cid:{chart_cid}")
    msg.set_content(f"Model Portföy {ay_adi} aylık performans raporu. Bu mail HTML biçimindedir.")
    msg.add_alternative(html, subtype="html")
    with open(CHART_PATH, "rb") as f:
        msg.get_payload()[1].add_related(f.read(), "image", "png", cid=f"<{chart_cid}>")
    return msg


# ----------------------------- main -----------------------------
def main():
    args = sys.argv[1:]
    example = "--example" in args
    send = "--send" in args
    if "--ay" in args:
        yil, ay = (int(x) for x in args[args.index("--ay") + 1].split("-"))
    else:
        yil, ay = onceki_ay()
    ay_adi = f"{AYLAR[ay - 1]} {yil}"

    baslangic, bitis = ay_donemi(yil, ay)
    data = B.gather()
    if bitis > data["last_data"]:
        print(f"UYARI: {bitis} için veri yok, son veri {data['last_data']} — getiriler oraya kadar.")

    r = compute_period(baslangic, bitis, data)
    print(f"Dönem: {baslangic} → {bitis} ({r['gun']} gün) | {ay_adi}")
    arets = asset_returns_period(baslangic, bitis, data)
    print("Varlık bazında aylık getiriler:",
          {B.ASSET_LABEL.get(k, k): (round(v * 100, 2) if v is not None else None)
           for k, v in arets.items()})
    for p in B.PORT_ORDER:
        pd = r["portfolios"][p]
        f = lambda x: "—" if x is None else f"{x*100:+.2f}%"
        print(f"  {p:<14} getiri {f(pd['return'])} | benchmark {f(pd['benchmark'])}")

    B.make_chart(bitis, baslangic, arets, CHART_PATH,
                 baslik=f"Aylık Getiriler — Varlık Bazında  ({baslangic.strftime('%d.%m')} → {bitis.strftime('%d.%m.%Y')})",
                 y_etiket="Aylık getiri (%)")
    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(build_email_html(r, ay_adi))
    print("Grafik + mail önizleme yazıldı:", OUT_DIR)

    if not (send or example):
        print("Mail gönderilmedi (göndermek için --send, örnek için --example).")
        return 0

    cfg = dict(S.load_config())
    cfg["subject"] = f"Model Portföy Aylık Performans — {ay_adi}"
    if example:
        cfg["recipients"] = [cfg["sender"]]
        cfg["subject"] = "[ÖRNEK] " + cfg["subject"]

    pw = S.keychain_password(cfg["keychain_service"], cfg["sender"])
    if not pw:
        print("HATA: SMTP şifresi Keychain'de bulunamadı.", file=sys.stderr)
        return 2
    msg = build_message(cfg, ay_adi)
    with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=60) as s:
        s.starttls(context=S.ssl_context())
        s.login(cfg["sender"], pw)
        s.send_message(msg)
    tag = "ÖRNEK mail" if example else "mail gönderildi"
    print(f"{dt.datetime.now():%Y-%m-%d %H:%M} — {tag}: "
          f"{len(cfg['recipients'])} alıcı → {', '.join(cfg['recipients'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
