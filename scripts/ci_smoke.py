# -*- coding: utf-8 -*-
"""GitHub Actions runner erişim testi: TEFAS / Yahoo / ABD Hazine.

Üreticilerin kullandığı gerçek endpoint'lere bağlanır; yalnız bağlantı
doğrular (hesaplama yapmaz). Stdlib (urllib) kullanır — runner'da pip
kurulumu gerekmez. Tümü başarılıysa exit 0.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import urllib.request

TEFAS_API = "https://www.tefas.gov.tr/api/funds/fonGnlBlgSiraliGetir"
YAHOO_API = "https://query1.finance.yahoo.com/v8/finance/chart/CL%3DF?range=1d&interval=1d"
TREASURY_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    f"daily-treasury-rates.csv/{dt.date.today().year}/all"
    "?type=daily_treasury_yield_curve"
    f"&field_tdr_date_value={dt.date.today().year}&page&_format=csv"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    "Accept": "application/json,text/csv,*/*",
}


def http_get(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def http_post(url: str, body: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={**HEADERS, "Content-Type": "application/json",
                 "Origin": "https://www.tefas.gov.tr",
                 "Referer": "https://www.tefas.gov.tr/"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    ok = True
    # 1) TEFAS — 3 günlük pencere, YAT (yatırım) tipi
    try:
        bit = dt.date.today()
        bas = bit - dt.timedelta(days=3)
        body = {
            "fonTipi": "YAT", "fonKodu": None, "aramaMetni": None,
            "fonTurKod": None, "fonGrubu": None, "sfonTurKod": None,
            "basTarih": bas.strftime("%Y%m%d"), "bitTarih": bit.strftime("%Y%m%d"),
            "basSira": 1, "bitSira": 100000, "fonTurAciklama": None,
            "dil": "TR", "kurucuKod": None,
        }
        sonuc = http_post(TEFAS_API, body)
        adet = len(sonuc.get("resultList") or [])
        durum = f"OK ({adet} fon)" if adet else "UYARI (boş sonuç)"
        print(f"TEFAS    : {durum}")
        ok = ok and adet > 0
    except Exception as e:
        print(f"TEFAS    : HATA — {str(e)[:140]}")
        ok = False

    # 2) Yahoo — CL=F (WTI) 1 gün
    try:
        d = json.loads(http_get(YAHOO_API))
        meta = d["chart"]["result"][0]["meta"]
        print(f"Yahoo    : OK (WTI {meta.get('regularMarketPrice')} {meta.get('currency')})")
    except Exception as e:
        print(f"Yahoo    : HATA — {str(e)[:140]}")
        ok = False

    # 3) ABD Hazine CSV
    try:
        csv = http_get(TREASURY_URL)
        baslik = csv.splitlines()[0][:80] if csv.strip() else "(boş)"
        print(f"Hazine   : OK ({len(csv.splitlines())} satır, başlık: {baslik}...)")
        ok = ok and len(csv.splitlines()) >= 2
    except Exception as e:
        print(f"Hazine   : HATA — {str(e)[:140]}")
        ok = False

    print("SONUÇ    : " + ("TAMAM" if ok else "BAŞARISIZ"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
