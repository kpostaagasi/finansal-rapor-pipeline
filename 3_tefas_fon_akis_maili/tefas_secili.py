#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TEFAS net akış raporu üreticisi (yapılandırılabilir kapsam).

Net akış = (o günün tedavüldeki pay sayısı − önceki işlem gününün pay sayısı)
           × o günün pay fiyatı        [her fon için ayrı hesaplanır]

Raporlar `raporlar/*.json` ile tanımlanır:
  secili — `fonlar.json`daki fon kodları (kullanıcının verdiği liste)
  altin  — TEFAS'ın iki altın filtresi:
             · Menkul Kıymet Yatırım Fonları + Fon Unvan Türü "Altın"  (unvanda ALTIN/GOLD)
             · Emeklilik Fonları + Fon Türü "Altın Fonu, Altın Katılım Fonu"
                                                         (fonTurAciklama ile, kod listesi her
                                                          çalışmada TEFAS'tan tazelenir)

Seri 31.12.2024'ten bugüne; ilk akış günü 31.12.2024 olsun diye veri bir önceki
işlem gününden (30.12.2024) itibaren çekilir, o gün yalnızca referanstır.

Veri rapora özel önbellekte birikir; her çalışmada yalnızca son ~12 gün yeniden
çekilip üstüne yazılır (TEFAS penceresi ≤28 gün, rate limit ~6 istek/dk).

Kullanım:
  python3 tefas_secili.py [--rapor secili|altin]   # artımlı güncelle + HTML üret
  python3 tefas_secili.py --rapor altin --bootstrap  # seriyi sıfırdan kur
  python3 tefas_secili.py --no-fetch                 # mevcut önbellekten üret
"""
import os, sys, json, time, tempfile, datetime as dt
import html as html_lib
from collections import defaultdict

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
RAPOR_DIZIN = os.path.join(HERE, "raporlar")
TEMPLATE = os.path.join(HERE, "secili_template.html")

REF_TARIH = dt.date(2024, 12, 30)   # referans gün (akışa girmez)
BAS_TARIH = dt.date(2024, 12, 31)   # serinin ilk akış günü
PENCERE = 25                        # gün; TEFAS sınırı 28
INCREMENTAL_GUN = 12                # her çalışmada yeniden çekilen kuyruk

API = "https://www.tefas.gov.tr/api/funds/fonGnlBlgSiraliGetir"
API_LISTE = "https://www.tefas.gov.tr/api/funds/fonYonetimBazliBilgiGetir"
HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://www.tefas.gov.tr",
    "Referer": "https://www.tefas.gov.tr/",
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
}
EMK_ALTIN_TURLERI = ("Altın Fonu", "Altın Katılım Fonu")


def log(msg):
    print(f"{dt.datetime.now():%H:%M:%S} — {msg}", file=sys.stderr, flush=True)


def script_json(value):
    """JSON'u HTML <script> bağlamını kapatamayacak biçimde kodlar."""
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("&", "\\u0026")
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029"))


def atomic_json_dump(path, value):
    """Tam JSON'u aynı dizinde yazıp fsync sonrası atomik olarak yer değiştirir."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=directory
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def rapor_yukle(ad="secili"):
    """raporlar/<ad>.json'u okur; yollar mutlak hale getirilir."""
    with open(os.path.join(RAPOR_DIZIN, f"{ad}.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["cache"] = os.path.join(HERE, cfg["cache"])
    cfg["html"] = os.path.join(HERE, cfg["html"])
    cfg["desktop"] = os.path.expanduser(cfg["desktop"])
    return cfg


# --- kapsam -----------------------------------------------------------------

def altin_unvani(unvan):
    """Unvan altın fonuna işaret ediyor mu? ('altıncı' sıra sayısı tuzağına dikkat)"""
    u = unvan.upper()
    temiz = u.replace("ON ALTINCI", " ").replace("ONALTINCI", " ").replace("ALTINCI", " ")
    if "ALTIN" in temiz:
        return True
    return "GOLD" in u and "GOLDEN" not in u


def emk_altin_kodlari():
    """Emeklilik tarafında fonTurAciklama'sı Altın (Katılım) Fonu olan kodlar."""
    body = {"fonTipi": "EMK", "fonKodu": None, "aramaMetni": None, "fonTurKod": None,
            "fonGrubu": None, "sfonTurKod": None, "basSira": 1, "bitSira": 100000,
            "fonTurAciklama": None, "dil": "TR", "kurucuKod": None, "islem": None}
    r = requests.post(API_LISTE, headers=HEADERS, json=body, timeout=120)
    r.raise_for_status()
    return {x["fonKodu"] for x in (r.json().get("resultList") or [])
            if x.get("fonTurAciklama") in EMK_ALTIN_TURLERI}


def kapsam_kurallari(cfg):
    """(kod, unvan, tip) -> rapora girsin mi? sorusunu yanıtlayan fonksiyon döndürür."""
    k = cfg["kapsam"]
    if k["tip"] == "liste":
        with open(os.path.join(HERE, k["dosya"]), encoding="utf-8") as f:
            kodlar = set(json.load(f)["fonlar"])
        return lambda kod, unvan, tip: kod in kodlar
    if k["tip"] == "altin":
        emk = emk_altin_kodlari()
        log(f"  altın emeklilik fonu: {len(emk)} kod")
        return lambda kod, unvan, tip: (altin_unvani(unvan) if tip == "YAT" else kod in emk)
    raise ValueError(f"bilinmeyen kapsam tipi: {k['tip']}")


def istenen_kodlar(cfg):
    """Listeye dayalı raporlarda beklenen kod listesi (eksik uyarısı için)."""
    k = cfg["kapsam"]
    if k["tip"] != "liste":
        return []
    with open(os.path.join(HERE, k["dosya"]), encoding="utf-8") as f:
        return list(dict.fromkeys(json.load(f)["fonlar"]))


# --- veri -------------------------------------------------------------------

def fetch(fon_tipi, bas, bit, deneme=3):
    body = {"fonTipi": fon_tipi, "fonKodu": None, "aramaMetni": None, "fonTurKod": None,
            "fonGrubu": None, "sfonTurKod": None,
            "basTarih": bas.strftime("%Y%m%d"), "bitTarih": bit.strftime("%Y%m%d"),
            "basSira": 1, "bitSira": 100000, "fonTurAciklama": None,
            "dil": "TR", "kurucuKod": None}
    for i in range(1, deneme + 1):
        try:
            r = requests.post(API, headers=HEADERS, json=body, timeout=180)
            r.raise_for_status()
            return r.json()["resultList"] or []
        except Exception as e:
            log(f"  {fon_tipi} {bas}–{bit} deneme {i}/{deneme} hata: {str(e)[:120]}")
            if i == deneme:
                raise
            time.sleep(20)


def topla(cfg, bas, bit, adlar, tipler, pencere_bitti=None):
    """[bas, bit] için {kod: {tarih: (pay, fiyat)}} döndürür.

    Her pencere sonunda `pencere_bitti(veri)` çağrılır (araya girip kaydetmek için)."""
    kapsamda = kapsam_kurallari(cfg)
    veri = defaultdict(dict)
    pencere_bas = bas
    while pencere_bas <= bit:
        pencere_bit = min(pencere_bas + dt.timedelta(days=PENCERE), bit)
        for tip in cfg["fon_tipleri"]:
            for x in fetch(tip, pencere_bas, pencere_bit):
                if not kapsamda(x["fonKodu"], x["fonUnvan"], tip):
                    continue
                if x["fiyat"] is None or x["tedPaySayisi"] is None:
                    continue
                veri[x["fonKodu"]][x["tarih"]] = (x["tedPaySayisi"], x["fiyat"])
                adlar[x["fonKodu"]] = x["fonUnvan"]
                tipler[x["fonKodu"]] = tip
            time.sleep(8)   # rate limit ~6 istek/dk
        log(f"  çekildi {pencere_bas} – {pencere_bit} ({len(veri)} fon)")
        if pencere_bitti:
            pencere_bitti(veri)
        pencere_bas = pencere_bit + dt.timedelta(days=1)
    return veri


def veri_guncelle(cfg, tam=False):
    onbellek = {"fon": {}, "ad": {}, "tip": {}}
    if not tam and os.path.exists(cfg["cache"]):
        with open(cfg["cache"], encoding="utf-8") as f:
            onbellek = json.load(f)

    bugun = dt.date.today()
    tarihler = sorted({t for s in onbellek["fon"].values() for t in s})
    if tarihler:
        son = dt.date.fromisoformat(tarihler[-1])
        bas = max(REF_TARIH, son - dt.timedelta(days=INCREMENTAL_GUN))
    else:
        bas = REF_TARIH
    log(f"[{cfg['ad']}] veri çekiliyor: {bas} → {bugun}")

    adlar, tipler = dict(onbellek.get("ad", {})), dict(onbellek.get("tip", {}))

    def kaydet(yeni):
        """Ara kayıt: uzun çekim yarıda kalırsa ilerleme kaybolmasın."""
        for kod, seri in yeni.items():
            g = onbellek["fon"].setdefault(kod, {})
            for tarih, (pay, fiyat) in seri.items():
                g[tarih] = [pay, fiyat]
        onbellek["ad"], onbellek["tip"] = adlar, tipler
        onbellek["guncelleme"] = dt.datetime.now().isoformat(timespec="seconds")
        atomic_json_dump(cfg["cache"], onbellek)

    yeni = topla(cfg, bas, bugun, adlar, tipler, pencere_bitti=kaydet)
    if not yeni:
        raise RuntimeError("TEFAS'tan veri gelmedi")
    kaydet(yeni)
    return onbellek


def akis_serisi(onbellek):
    """Akışları yalnız ardışık TEFAS veri tarihleri arasında hesaplar.

    Bir fon evrenin bir önceki gerçek veri tarihinde gözlem vermediyse değer
    üretilmez. Böylece haftalar süren değişim tek bir güne yazılmaz.
    """
    fonlar = onbellek["fon"]
    tarihler = sorted({t for s in fonlar.values() for t in s})
    akis = {}
    bosluklar = {}
    onceki_rapor = {t: tarihler[i - 1] for i, t in enumerate(tarihler) if i}
    for kod, seri in fonlar.items():
        fon_tarihleri = sorted(seri)
        onceki_mevcut = {t: fon_tarihleri[i - 1] for i, t in enumerate(fon_tarihleri) if i}
        for t in fon_tarihleri:
            beklenen = onceki_rapor.get(t)
            if beklenen is None:
                continue
            if beklenen not in seri:
                if t == fon_tarihleri[0]:
                    continue  # fonun ilk gözlemi (piyasaya çıkış) gap değildir
                bosluklar.setdefault(t, {})[kod] = {
                    "expected_previous": beklenen,
                    "previous_available": onceki_mevcut.get(t),
                }
                akis.setdefault(t, {})
                continue
            akis.setdefault(t, {})[kod] = (seri[t][0] - seri[beklenen][0]) * seri[t][1]
    gunler = [t for t in tarihler if t >= BAS_TARIH.isoformat()]
    return akis, gunler, tarihler, bosluklar


KESINLESME_SAATI = 16  # TEFAS netleşmesi ~15:27; bu saatten önce bugünün satırı öncül kabul edilir


def kesin_tarihler(tarihler):
    """Netleşme saatinden önce bugünün (öncül) satırını seriden düşer."""
    simdi = dt.datetime.now()
    if simdi.hour < KESINLESME_SAATI and tarihler and tarihler[-1] == dt.date.today().isoformat():
        log(f"bugünün öncül satırı düşüldü ({dt.date.today()} — netleşme {KESINLESME_SAATI}:00 öncesi)")
        return tarihler[:-1]
    return tarihler


def kapsam_durumu(cfg, onbellek):
    """Son TEFAS tarihinde gerçek kapsam ve hesaplanabilirlik durumunu döndürür."""
    akis, gunler, tarihler, bosluklar = akis_serisi(onbellek)
    gunler = kesin_tarihler(gunler)
    if not gunler:
        raise RuntimeError("raporlanabilir TEFAS tarihi yok")
    son = gunler[-1]
    onceki = tarihler[tarihler.index(son) - 1] if tarihler.index(son) else None
    mevcut = {k for k, seri in onbellek["fon"].items() if son in seri}
    onceki_mevcut = ({k for k, seri in onbellek["fon"].items() if onceki in seri}
                     if onceki else set())
    istenen = istenen_kodlar(cfg)
    beklenen = set(istenen) if istenen else (mevcut | onceki_mevcut)
    bulunan = mevcut & beklenen
    hesaplanan = set(akis.get(son, {})) & beklenen
    if beklenen:
        bosluklar = {
            t: {k: v for k, v in kodlar.items() if k in beklenen}
            for t, kodlar in bosluklar.items()
        }
    kesim = (dt.date.fromisoformat(son) - dt.timedelta(days=89)).isoformat()
    yakin_bosluk_n = sum(
        len(kodlar) for tarih, kodlar in bosluklar.items() if tarih >= kesim
    )
    return {
        "akis": akis,
        "gunler": gunler,
        "tarihler": tarihler,
        "bosluklar": bosluklar,
        "son": son,
        "expected_codes": sorted(beklenen),
        "found_codes": sorted(bulunan),
        "missing": sorted(beklenen - mevcut),
        "uncomputed": sorted(bulunan - hesaplanan),
        "recent_gap_count": yakin_bosluk_n,
    }


# --- çıktı ------------------------------------------------------------------

GRUP_AD = {"YAT": "Yatırım fonları", "EMK": "Emeklilik fonları"}


def html_uret(cfg, onbellek):
    durum = kapsam_durumu(cfg, onbellek)
    akis, gunler = durum["akis"], durum["gunler"]
    kodlar = sorted(onbellek["fon"],
                    key=lambda k: -sum(abs(akis.get(t, {}).get(k, 0)) for t in gunler))
    istenen = set(istenen_kodlar(cfg))
    if istenen:
        kodlar = [k for k in kodlar if k in istenen]
    istenen = istenen_kodlar(cfg)
    eksik = durum["missing"]
    istenen_n = len(durum["expected_codes"])
    bulunan_n = len(durum["found_codes"])
    fon_ozet = f"son veri tarihinde {bulunan_n}/{istenen_n} fon bulundu"
    eksik_not = ("<span class=\"missing-note\">Eksik kodlar: "
                 + html_lib.escape(", ".join(eksik)) + "</span>") if eksik else ""
    bosluk_n = sum(len(x) for x in durum["bosluklar"].values())
    if bosluk_n:
        eksik_not += ("<span class=\"missing-note\">Ardışık TEFAS gözlemi olmayan "
                      f"{bosluk_n} fon-gün akışı hesaplanmadı.</span>")
    raw = {
        "d": gunler,
        "ad": {k: onbellek["ad"].get(k, k) for k in kodlar},
        "tip": {k: onbellek.get("tip", {}).get(k, "YAT") for k in kodlar},
        "grup": GRUP_AD,
        "f": {k: [round(akis[t][k]) if k in akis.get(t, {}) else None
                  for t in gunler] for k in kodlar},
        "gap_count": bosluk_n,
    }
    report_meta = {
        "last_successful_run": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "data_end_date": durum["son"],
        "expected_count": istenen_n,
        "found_count": bulunan_n,
        "missing": eksik,
        "uncomputed_count": len(durum["uncomputed"]),
        "uncomputed": durum["uncomputed"],
        "gap_count": bosluk_n,
        "recent_gap_count": durum["recent_gap_count"],
        "gap_codes": sorted({k for x in durum["bosluklar"].values() for k in x}),
        "count_label": "fon",
        "source": "TEFAS",
    }
    with open(TEMPLATE, encoding="utf-8") as f:
        html = f.read()
    tr = lambda iso: "{2}.{1}.{0}".format(*iso.split("-"))
    html = (html.replace("__RAW__", script_json(raw))
                .replace("__REPORT_META__", script_json(report_meta))
                .replace("__BASLIK__", cfg["baslik"])
                .replace("__FON_N__", str(bulunan_n))
                .replace("__ISTENEN_N__", str(istenen_n))
                .replace("__FON_OZET__", fon_ozet)
                .replace("__EKSIK_NOT__", eksik_not)
                .replace("__ILK_TARIH__", tr(gunler[0]))
                .replace("__SON_TARIH__", tr(gunler[-1])))
    for yol in (cfg["html"], cfg["desktop"]):
        try:
            with open(yol, "w", encoding="utf-8") as f:
                f.write(html)
        except OSError as e:
            log(f"{yol} yazılamadı: {e}")
    return bulunan_n, len(gunler)


def main():
    argv = sys.argv[1:]
    ad = "secili"
    if "--rapor" in argv:
        ad = argv[argv.index("--rapor") + 1]
    cfg = rapor_yukle(ad)

    if "--no-fetch" in argv:
        with open(cfg["cache"], encoding="utf-8") as f:
            onbellek = json.load(f)
    else:
        onbellek = veri_guncelle(cfg, tam="--bootstrap" in argv)

    fon, gun = html_uret(cfg, onbellek)
    durum = kapsam_durumu(cfg, onbellek)
    eksik = durum["missing"]
    hesaplanamayan = durum["uncomputed"]
    print(f"OK: {gun} gün × {fon} fon → {cfg['html']}")
    if eksik:
        print("Son TEFAS tarihinde bulunamayan kodlar: " + ", ".join(eksik))
    if hesaplanamayan:
        print("Önceki TEFAS gözlemi olmadığı için hesaplanamayan kodlar: "
              + ", ".join(hesaplanamayan))
    if eksik or hesaplanamayan:
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
