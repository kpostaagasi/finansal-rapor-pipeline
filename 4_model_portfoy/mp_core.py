#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Model Portföy Performans — veri + hesap çekirdeği.

Kaynaklar:
  - Fon fiyatları: TEFAS  api/funds/fonFiyatBilgiGetir  (RT konvansiyonu için +1 iş günü kaydırma)
  - Tahvil (TRT090130T12): ~/debt_instruments/bulletins/ttb*.csv  (KESN_T0, AĞ.ORT. TAKAS FİYATI = kirli fiyat)
  - KYD TL/USD mevduat endeksleri: workbook'taki cache seri + carry-forward (TEFAS'ta yok)

Not: Bu modül sadece VERİ ve HESAP üretir; workbook yazımı mp_build.py'de.
"""
import os, json, glob, datetime as dt
from collections import OrderedDict
import requests

HOME = os.path.expanduser("~")
BULLETINS_DIR = os.path.join(HOME, "debt_instruments", "bulletins")

FUNDS = ["BSD", "BV1", "BVD", "BVF", "BVZ", "SPR"]  # Database B..G sırası: BSD,BV1,BVD,BVF,BVZ,SPR
BOND_ISIN = "TRT090130T12"
BOND_ROW_CODE = "TRT090130T12_KESN_T0"   # Kesin Alım-Satım Normal Emirler, T+0 valör
BOND_TAKAS_COL = 15                       # 0-index: 16. sütun "AĞ. ORT. TAKAS FİYATI" (kirli fiyat = temiz + işlemiş faiz)
BOND_ACCRUED_COL = 9                      # 0-index: 10. sütun "BIRIKMIS FAIZ/KIRA" (işlemiş faiz)
BOND_KKG_COL = 8                          # 0-index: 9. sütun "KKG" = Kupona Kalan Gün
COUPON_DROP_THRESH = 1.0                  # işlemiş faiz bu kadar düşerse kupon ödenmiş say (günlük tahakkuk ~0.1)

# Portföy tanımları (Haftalık sheet'ten birebir)
PORTFOLIOS = {
    "Temkinli":     [("BVF",.40),("BVZ",.30),("BVD",.10),("SPR",.05),("BV1",.05),("TRT",.10)],
    "Temkinli USD": [("BSD",.80),("BVZ",.05),("BVF",.05),("TRT",.10)],
    "Agresif":      [("BVF",.05),("BVZ",.15),("BVD",.10),("SPR",.40),("BV1",.10),("TRT",.20)],
}
# Benchmark: mevduat endeksi haftalık getirisi + yıllık spread*7/365
BENCH = {  # (mevduat: 'TL'|'USD', yıllık spread)
    "Temkinli":     ("TL", 0.01),
    "Temkinli USD": ("USD", 0.01),
    "Agresif":      ("TL", 0.05),
}

TEFAS_URL = "https://www.tefas.gov.tr/api/funds/fonFiyatBilgiGetir"
TEFAS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Content-Type": "application/json", "Origin": "https://www.tefas.gov.tr",
    "Referer": "https://www.tefas.gov.tr/FonAnaliz.aspx", "Accept": "application/json, text/plain, */*",
}


# ----------------------------- TEFAS fon fiyatları -----------------------------
def fetch_fund_prices(period="12"):
    """Her fon için {date_str: fiyat} TEFAS ham serisi (tarih = TEFAS ilan tarihi)."""
    out = {}
    s = requests.Session(); s.headers.update(TEFAS_HEADERS)
    for f in FUNDS:
        r = s.post(TEFAS_URL, data=json.dumps({"fonKodu": f, "periyod": period}), timeout=40)
        rows = r.json().get("resultList") or []
        out[f] = {x["tarih"]: float(x["fiyat"]) for x in rows}
        if not rows:
            raise RuntimeError(f"TEFAS {f} boş döndü")
    return out


def build_rt_series(tefas_prices):
    """RT konvansiyonu: RT[D] = TEFAS[bir sonraki iş günü].
    Yani TEFAS tarihi ds[i]'nin fiyatı, RT tarihi ds[i-1]'e yazılır.
    Dönen: fon -> OrderedDict{rt_date_str: fiyat} (artan tarih)."""
    rt = {}
    for f, pm in tefas_prices.items():
        ds = sorted(pm.keys())
        od = OrderedDict()
        for i in range(1, len(ds)):
            od[ds[i-1]] = pm[ds[i]]
        rt[f] = od
    return rt


def xlookup_le(series_od, target_str):
    """XLOOKUP match=-1: target'tan küçük/eşit en büyük tarihin değeri."""
    best = None
    for d, v in series_od.items():
        if d <= target_str:
            best = v
        else:
            break
    return best


def fund_week_return(rt, fund, friday, prev_friday, last_data_date):
    """(XLOOKUP(min(F,son)) / XLOOKUP(F-7)) - 1  — sheet ile birebir."""
    end = min(friday, last_data_date)
    a = xlookup_le(rt[fund], end.isoformat())
    b = xlookup_le(rt[fund], prev_friday.isoformat())
    if a is None or b is None or b == 0:
        return None
    return a / b - 1


# ----------------------------- Tahvil (ttb bültenleri) -----------------------------
def load_bond_series():
    """Tüm ttb*.csv bültenlerinden TRT090130T12 KESN_T0 takas (kirli) fiyatını topla.
    Dönen: OrderedDict{date_str: fiyat} (artan tarih)."""
    prices = {}
    for path in glob.glob(os.path.join(BULLETINS_DIR, "ttb*.csv")):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if BOND_ROW_CODE not in line:
                        continue
                    parts = line.rstrip("\n").split(";")
                    if len(parts) <= BOND_TAKAS_COL:
                        continue
                    if parts[2].strip() != BOND_ROW_CODE:
                        continue
                    date_str = parts[1].strip()
                    raw = parts[BOND_TAKAS_COL].strip().replace(",", ".")
                    if not raw:
                        continue
                    try:
                        prices[date_str] = float(raw)
                    except ValueError:
                        continue
        except OSError:
            continue
    return OrderedDict(sorted(prices.items()))


def bond_price_le(bond_od, target_str):
    return bond_price_le_dated(bond_od, target_str)[1]


def bond_price_le_dated(bond_od, target_str):
    """target'tan küçük/eşit en son kaydın (tarih, fiyat)'ı. Kupon penceresini
    hedef tarihe değil FİİLEN KULLANILAN fiyatın tarihine göre kurmak için gerekli:
    bülten boşluğunda hedef kupondan sonra ama kullanılan fiyat kupondan önce olabilir."""
    best = (None, None)
    for d, v in bond_od.items():
        if d <= target_str:
            best = (d, v)
        else:
            break
    return best


def load_bond_coupons():
    """Bültenden kupon olaylarını çıkar: (kupon_tarihi, kupon_tutarı).

    Kirli (takas) fiyat = temiz fiyat + işmiş faiz. Kupon ödendiğinde işlemiş faiz
    sıfırlanır ve kirli fiyat kupon kadar DÜŞER — düzeltilmezse ödeme döneminde sahte
    bir kayıp görünür.

    Tarih bültendeki **KKG (Kupona Kalan Gün, col 8)** sütunundan kesin okunur:
        kupon_tarihi = bülten_tarihi + KKG
    Böylece kupon gününün bülteni elimizde olmasa bile tarih doğru çıkar (işlemiş faiz
    düşüşünün "görüldüğü" güne bakan eski yöntem, bülten boşluğunda tarihi ileri kaydırıyordu).

    Tutar, dönemin son iki gözleminden günlük tahakkuk bulunup kupon gününe taşınarak
    hesaplanır (accrued sadece kupon gününden ÖNCEKİ güne kadar işler):
        günlük = (a2-a1)/(d2-d1) ;  kupon = a2 + günlük × (kupon_tarihi - d2)
    """
    obs = {}   # tarih -> (accrued, kupon_tarihi)
    for path in glob.glob(os.path.join(BULLETINS_DIR, "ttb*.csv")):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if BOND_ROW_CODE not in line:
                        continue
                    parts = line.rstrip("\n").split(";")
                    if len(parts) <= BOND_ACCRUED_COL or parts[2].strip() != BOND_ROW_CODE:
                        continue
                    try:
                        d = dt.date.fromisoformat(parts[1].strip())
                        a = float(parts[BOND_ACCRUED_COL].strip().replace(",", "."))
                        kkg = int(float(parts[BOND_KKG_COL].strip().replace(",", ".")))
                    except ValueError:
                        continue
                    obs[d] = (a, d + dt.timedelta(days=kkg))
        except OSError:
            continue
    if not obs:
        return []

    # kupon tarihine göre grupla (aynı kupon dönemindeki gözlemler)
    donemler = {}
    for d, (a, kup) in obs.items():
        donemler.setdefault(kup, []).append((d, a))

    son_gozlem = max(obs)
    events = []
    for kup, kayitlar in sorted(donemler.items()):
        if kup > son_gozlem:
            continue          # kupon henüz ödenmedi
        kayitlar.sort()
        (d2, a2) = kayitlar[-1]
        if len(kayitlar) >= 2:
            (d1, a1) = kayitlar[-2]
            gun = (d2 - d1).days
            gunluk = (a2 - a1) / gun if gun else 0.0
            tutar = a2 + gunluk * (kup - d2).days
        else:
            tutar = a2       # tek gözlem: taşıyacak günlük tahakkuk yok
        if tutar > COUPON_DROP_THRESH:
            events.append((kup, tutar))
    return events


def bond_week_return(bond_od, friday, prev_friday, coupons=None):
    """Kirli (takas) fiyatla haftalık getiri; hafta içinde kupon ödendiyse geri eklenir:
    (kirli_son + kupon) / kirli_önceki - 1.  coupons=None ise ham kirli getiri (eski davranış)."""
    da, a = bond_price_le_dated(bond_od, friday.isoformat())
    db, b = bond_price_le_dated(bond_od, prev_friday.isoformat())
    if a is None or b is None or b == 0:
        return None
    # Kupon, ancak KULLANILAN iki fiyatın tarihleri arasına düşüyorsa eklenir. Hedef
    # tarihlere göre kurulsaydı, bülten boşluğu yüzünden kupon öncesi bir fiyat kupon
    # sonrası sanılıp tutar sahte kazanç olarak eklenirdi.
    coup = 0.0
    if coupons:
        d_end = dt.date.fromisoformat(da)
        d_start = dt.date.fromisoformat(db)
        for ex, amt in coupons:
            if d_start < ex <= d_end:
                coup += amt
    return (a + coup) / b - 1


# ----------------------------- KYD mevduat endeksleri -----------------------------
def load_kyd_seed(path):
    """Değişmez KYD seed CSV'sinden (date,kyd_tl,kyd_usd) gerçek endeks serilerini oku.
    RT eklentisi olmadan gerçek KYD verisi seed'in son tarihinde donar; sonrası tahmindir.
    Seed asla çalıştırma çıktısıyla güncellenmez (besleme döngüsü olmasın diye)."""
    import csv
    tl, usd = OrderedDict(), OrderedDict()
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            tl[row["date"]] = float(row["kyd_tl"])
            usd[row["date"]] = float(row["kyd_usd"])
    return tl, usd


def estimate_weekly_return(od, weeks=4):
    """Seed'in son `weeks` haftasındaki gerçekleşen GEOMETRİK haftalık getiri.
    İleriye dönük carry-forward için kullanılır. Trailing-20-gün aritmetik ortalaması
    ay-sonu sıçramalarından şişer; bu yüzden gerçek uçtan-uca büyüme kullanılır."""
    items = [(dt.date.fromisoformat(d), v) for d, v in od.items() if isinstance(v, (int, float)) and v > 0]
    if len(items) < 2:
        return 0.0
    last_d, last_v = items[-1]
    target = last_d - dt.timedelta(days=weeks * 7)
    base_d, base_v = items[0]
    for d, v in items:
        if d <= target:
            base_d, base_v = d, v
        else:
            break
    days = (last_d - base_d).days
    if days <= 0 or base_v <= 0:
        return 0.0
    daily = (last_v / base_v) ** (1.0 / days)
    return daily ** 7 - 1.0


def kyd_value_at(od, target):
    """KYD endeksinin `target` tarihindeki değeri: seed içindeyse en yakın-önceki gerçek,
    seed ötesindeyse son gerçek değerden tahmini günlük faktörle carry-forward."""
    if not od:
        return None
    keys = list(od.keys())
    last_d = dt.date.fromisoformat(keys[-1])
    ts = target.isoformat()
    best_v = None
    for d, v in od.items():
        if d <= ts:
            best_v = v
        else:
            break
    if best_v is None:
        return None
    if target <= last_d:
        return best_v
    daily_fac = (1.0 + estimate_weekly_return(od)) ** (1.0 / 7)
    gap = (target - last_d).days
    return od[keys[-1]] * (daily_fac ** gap)


def kyd_week_return_carryforward(od, friday, prev_friday):
    """KYD endeksi haftalık getirisi. Her iki uç seed içindeyse GERÇEK; seed'in
    ötesindeyse tahmini carry-forward. Dönen: (getiri, yaklaşık_mı)."""
    if not od:
        return None, None
    last_d = dt.date.fromisoformat(list(od.keys())[-1])
    a = kyd_value_at(od, friday); b = kyd_value_at(od, prev_friday)
    if a is None or b is None or b == 0:
        return None, None
    return a / b - 1, friday > last_d


# ----------------------------- Hafta hesabı -----------------------------
def fridays_between(start_friday, end_date):
    """start_friday'den başlayıp end_date'e kadar (dahil) tüm cuma tarihleri."""
    out = []
    d = start_friday
    while d <= end_date:
        out.append(d)
        d = d + dt.timedelta(days=7)
    return out


def compute_week(friday, rt, bond_od, kyd_tl, kyd_usd, last_data_date, coupons=None):
    """Bir hafta için 3 portföyün getiri/benchmark sonucu."""
    prev = friday - dt.timedelta(days=7)
    # varlık haftalık getirileri
    def asset_ret(sym):
        if sym == "TRT":
            return bond_week_return(bond_od, friday, prev, coupons)
        return fund_week_return(rt, sym, friday, prev, last_data_date)
    res = {"friday": friday, "prev": prev, "portfolios": {}}
    for name, weights in PORTFOLIOS.items():
        assets = []
        pr = 0.0; ok = True
        for sym, w in weights:
            r = asset_ret(sym)
            assets.append((sym, w, r))
            if r is None:
                ok = False
            else:
                pr += w * r
        dep, spread = BENCH[name]
        od = kyd_tl if dep == "TL" else kyd_usd
        bench_base, approx = kyd_week_return_carryforward(od, friday, prev)
        bench = None if bench_base is None else bench_base + spread * 7 / 365
        res["portfolios"][name] = {
            "assets": assets, "return": pr if ok else None,
            "benchmark": bench, "benchmark_approx": approx,
        }
    return res


if __name__ == "__main__":
    # Kendi kendini test: workbook cache değerleriyle karşılaştır
    import openpyxl
    WB = os.path.join(HOME, "Documents", "Model Portföy Performans.xlsx")
    tefas = fetch_fund_prices()
    rt = build_rt_series(tefas)
    bond = load_bond_series()
    coupons = load_bond_coupons()
    print("Tespit edilen kupon olayları:", [(d.isoformat(), round(a, 3)) for d, a in coupons])
    seed = os.path.join(HOME, "debt_instruments", "model_portfoy", "inputs", "kyd_seed.csv")
    tl, usd = load_kyd_seed(seed)
    last_tefas = max(max(v.keys()) for v in tefas.values())
    last_data = dt.date.fromisoformat(last_tefas) - dt.timedelta(days=1)  # RT konv son tarih
    print("Son TEFAS:", last_tefas, "| RT-konv son:", last_data)
    print("Tahvil serisi:", len(bond), "kayıt,", (list(bond.items())[-3:] if bond else "YOK"))
    print("KYD TL son:", list(tl.items())[-1], "| USD son:", list(usd.items())[-1])
    # Bilinen haftalar (workbook cache): (cuma, Temkinli, TemkinliUSD, Agresif)
    known = {
        dt.date(2026,6,26): (0.00316, 0.00739, -0.00656),
        dt.date(2026,6,19): (0.00898, 0.00598, 0.00380),
        dt.date(2026,6,12): (0.00835, 0.00580, 0.02048),
    }
    for fri,(t,u,a) in known.items():
        r = compute_week(fri, rt, bond, tl, usd, last_data, coupons)
        gt = r["portfolios"]["Temkinli"]["return"]
        gu = r["portfolios"]["Temkinli USD"]["return"]
        ga = r["portfolios"]["Agresif"]["return"]
        def cmp(g,e): return f"{g:.5f} (exp {e:.5f} {'OK' if g is not None and abs(g-e)<1e-4 else 'FARK'})"
        print(f"{fri}: Temk {cmp(gt,t)} | USD {cmp(gu,u)} | Agr {cmp(ga,a)}")
