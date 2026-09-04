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
    """raporlar/<ad>.json'u okur; yollar mutlak hale getirilir.

    Türetilmiş raporların (grup toplamı) kendi önbelleği yoktur.
    """
    with open(os.path.join(RAPOR_DIZIN, f"{ad}.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    if cfg.get("cache"):
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


def kiymetli_maden_unvani(unvan):
    """Unvan kıymetli maden fonuna işaret ediyor mu?

    Şemsiye türü tek başına yetmiyor: TEFAS altın katılım fonlarını "Katılım
    Şemsiye Fonu", gümüş fonlarını çoğunlukla "Fon Sepeti"/"Serbest" altında
    sınıflıyor. Bu yüzden kıymetli maden evreni tür VE unvan birleşimidir.

    Unvan tuzakları (TEFAS unvan listesinden doğrulandı):
      · ALTINCI/ONALTINCI/YİRMİALTINCI — sıra sayısı (altin_unvani eler)
      · GOLDEN GLOBAL — kurucu adı (altin_unvani eler)
      · GÜMÜŞSUYU — Yapı Kredi'nin semt adlı özel fonu, gümüş değil
      · "ÖZEL BANKACILIK VE PLATİNUM" — hizmet segmenti adı, platin değil
    """
    u = unvan.upper()
    if altin_unvani(u):
        return True
    temiz = u.replace("GÜMÜŞSUYU", " ")
    if any(s in temiz for s in ("GÜMÜŞ", "SILVER", "PALADYUM", "PALLADIUM",
                                "KIYMETLİ MADEN", "KIYMETLI MADEN")):
        return True
    return ("PLATİN" in u or "PLATIN" in u) and "BANKACILIK" not in u


# Tür bazlı kapsamların isteğe bağlı unvan kuralı: tür eşleşmese de unvan
# fonu evrene alabilir (raporlar/*.json içinde `kapsam.unvan_kurali`).
UNVAN_KURALLARI = {
    "altin": altin_unvani,
    "kiymetli_maden": kiymetli_maden_unvani,
}


def tur_haritasi(fon_tipi):
    """{fon kodu: fonTurAciklama} — TEFAS yönetim bilgisi ucundan.

    Günlük veri ucu fon türünü döndürmüyor; tür yalnızca bu uçtan gelir. Uç
    günlük veri veren her fonu kapsamıyor (bugün 2041 fonun 13'ü burada yok),
    bu yüzden türü bilinmeyen fonlar sessizce elenmez: `TurKapsami` onları
    toplayıp metadata'da ifşa eder.
    """
    body = {"fonTipi": fon_tipi, "fonKodu": None, "aramaMetni": None, "fonTurKod": None,
            "fonGrubu": None, "sfonTurKod": None, "basSira": 1, "bitSira": 100000,
            "fonTurAciklama": None, "dil": "TR", "kurucuKod": None, "islem": None}
    r = requests.post(API_LISTE, headers=HEADERS, json=body, timeout=120)
    r.raise_for_status()
    return {x["fonKodu"]: x.get("fonTurAciklama")
            for x in (r.json().get("resultList") or [])}


class TurKapsami:
    """Fon türüne (istenirse tür VEYA unvan kuralına) dayalı kapsam.

    Türü TEFAS yönetim bilgisi ucunda görünmeyen ve unvan kuralına da uymayan
    fonlar sessizce elenmez: `bilinmeyen` kümesinde toplanıp metadata'da ifşa
    edilir.
    """

    def __init__(self, haritalar, istenen, unvan_kurali=None):
        self.haritalar = haritalar
        self.istenen = istenen
        self.unvan_kurali = unvan_kurali
        self.bilinmeyen = set()

    def __call__(self, kod, unvan, tip):
        tur = self.haritalar.get(tip, {}).get(kod)
        if tur is not None and tur in self.istenen.get(tip, ()):
            return True
        if self.unvan_kurali is not None and self.unvan_kurali(unvan):
            return True
        if tur is None:
            self.bilinmeyen.add(kod)
        return False


class ListeKapsami:
    """Elle seçilmiş kod listesi."""

    def __init__(self, kodlar):
        self.kodlar = kodlar
        self.bilinmeyen = set()

    def __call__(self, kod, unvan, tip):
        return kod in self.kodlar


class AltinKapsami:
    """Altın fonu evreni: unvan kuralı ∪ altın emeklilik fon türleri.

    Yalnızca fon türüne bakmak yetmiyor: Garanti Emeklilik'in ALTIN EMEKLİLİK
    YATIRIM FONU'nu (EMY) TEFAS "Kıymetli Madenler" olarak sınıflıyor, tür
    filtresi bu fonu kaçırıyordu. Yalnızca unvana bakmak da yetmez; emeklilik
    tarafında tür listesi kuralın kapsamını doğrulayan ikinci kaynaktır.
    """

    def __init__(self, emk_kodlari):
        self.emk = emk_kodlari
        self.bilinmeyen = set()

    def __call__(self, kod, unvan, tip):
        if altin_unvani(unvan):
            return True
        return tip == "EMK" and kod in self.emk


def kapsam_kurallari(cfg):
    """(kod, unvan, tip) -> rapora girsin mi? sorusunu yanıtlayan nesne döndürür."""
    k = cfg["kapsam"]
    if k["tip"] == "liste":
        with open(os.path.join(HERE, k["dosya"]), encoding="utf-8") as f:
            return ListeKapsami(set(json.load(f)["fonlar"]))
    if k["tip"] == "altin":
        emk = {kod for kod, tur in tur_haritasi("EMK").items()
               if tur in EMK_ALTIN_TURLERI}
        log(f"  altın emeklilik fonu: {len(emk)} kod")
        return AltinKapsami(emk)
    if k["tip"] == "tur":
        kural_adi = k.get("unvan_kurali")
        if kural_adi is not None and kural_adi not in UNVAN_KURALLARI:
            raise ValueError(f"bilinmeyen unvan kuralı: {kural_adi}")
        kural = UNVAN_KURALLARI.get(kural_adi)
        haritalar, istenen = {}, {}
        for tip in cfg["fon_tipleri"]:
            haritalar[tip] = tur_haritasi(tip)
            istenen[tip] = tuple(k["turler"].get(tip, ()))
            kapsamda = sum(1 for t in haritalar[tip].values() if t in istenen[tip])
            log(f"  {tip}: türle {kapsamda} fon, {len(istenen[tip])} tür"
                + (f", ek olarak '{kural_adi}' unvan kuralı" if kural else ""))
        return TurKapsami(haritalar, istenen, kural)
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
    """[bas, bit] için ({kod: {tarih: (pay, fiyat)}}, kapsam) döndürür.

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
    return veri, kapsamda


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

    def kaydet(yeni, bilinmeyen=()):
        """Ara kayıt: uzun çekim yarıda kalırsa ilerleme kaybolmasın."""
        for kod, seri in yeni.items():
            g = onbellek["fon"].setdefault(kod, {})
            for tarih, (pay, fiyat) in seri.items():
                g[tarih] = [pay, fiyat]
        onbellek["ad"], onbellek["tip"] = adlar, tipler
        if bilinmeyen:
            onbellek["turu_bilinmeyen"] = sorted(bilinmeyen)
        onbellek["guncelleme"] = dt.datetime.now().isoformat(timespec="seconds")
        atomic_json_dump(cfg["cache"], onbellek)

    yeni, kapsamda = topla(cfg, bas, bugun, adlar, tipler, pencere_bitti=kaydet)
    if not yeni:
        raise RuntimeError("TEFAS'tan veri gelmedi")
    # Türü TEFAS yönetim bilgisi ucunda görünmeyen fonlar kapsam dışı kaldı;
    # sessizce düşmesinler diye önbelleğe yazılıp metadata'da raporlanır.
    kaydet(yeni, kapsamda.bilinmeyen)
    if kapsamda.bilinmeyen:
        log(f"  türü bilinmeyen {len(kapsamda.bilinmeyen)} fon kapsam dışı: "
            + ", ".join(sorted(kapsamda.bilinmeyen)))
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


def html_yaz(cfg, raw, report_meta, fon_ozet, eksik_not, gunler):
    """Şablonu doldurup rapor HTML'ini yerel çıktılara yazar."""
    with open(TEMPLATE, encoding="utf-8") as f:
        html = f.read()
    tr = lambda iso: "{2}.{1}.{0}".format(*iso.split("-"))
    html = (html.replace("__RAW__", script_json(raw))
                .replace("__REPORT_META__", script_json(report_meta))
                .replace("__BASLIK__", cfg["baslik"])
                .replace("__FON_N__", str(report_meta["found_count"]))
                .replace("__ISTENEN_N__", str(report_meta["expected_count"]))
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


def html_uret(cfg, onbellek):
    durum = kapsam_durumu(cfg, onbellek)
    akis, gunler = durum["akis"], durum["gunler"]
    kodlar = sorted(onbellek["fon"],
                    key=lambda k: -sum(abs(akis.get(t, {}).get(k, 0)) for t in gunler))
    istenen = set(istenen_kodlar(cfg))
    if istenen:
        kodlar = [k for k in kodlar if k in istenen]
    eksik = durum["missing"]
    istenen_n = len(durum["expected_codes"])
    bulunan_n = len(durum["found_codes"])
    bilinmeyen = onbellek.get("turu_bilinmeyen", [])
    fon_ozet = f"son veri tarihinde {bulunan_n}/{istenen_n} fon bulundu"
    eksik_not = ("<span class=\"missing-note\">Eksik kodlar: "
                 + html_lib.escape(", ".join(eksik)) + "</span>") if eksik else ""
    bosluk_n = sum(len(x) for x in durum["bosluklar"].values())
    if bosluk_n:
        eksik_not += ("<span class=\"missing-note\">Ardışık TEFAS gözlemi olmayan "
                      f"{bosluk_n} fon-gün akışı hesaplanmadı.</span>")
    if bilinmeyen:
        eksik_not += ("<span class=\"missing-note\">TEFAS fon türünü açıklamadığı için "
                      f"kapsam dışı kalan {len(bilinmeyen)} fon: "
                      + html_lib.escape(", ".join(bilinmeyen)) + "</span>")
    # null = "hesaplanamadı" (ardışık TEFAS gözlemi yok). Fonun piyasaya
    # çıkışından önceki / kapanışından sonraki günler ile çıkış gününün kendisi
    # boşluk değildir: akışa 0 katkı verir ve dönem toplamını iptal etmez.
    bosluk_kodlari = {t: set(x) for t, x in durum["bosluklar"].items()}
    raw = {
        "d": gunler,
        "ad": {k: onbellek["ad"].get(k, k) for k in kodlar},
        "tip": {k: onbellek.get("tip", {}).get(k, "YAT") for k in kodlar},
        "grup": GRUP_AD,
        "f": {k: [round(akis[t][k]) if k in akis.get(t, {})
                  else (None if k in bosluk_kodlari.get(t, ()) else 0)
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
        "untyped_count": len(bilinmeyen),
        "untyped": list(bilinmeyen),
        "count_label": "fon",
        "source": "TEFAS",
    }
    html_yaz(cfg, raw, report_meta, fon_ozet, eksik_not, gunler)
    return bulunan_n, len(gunler)


# --- grup bazlı toplam raporu -----------------------------------------------

def toplam_satirlari(cfg):
    """Kaynak raporların önbelleklerinden (grup, fon tipi) satırları üretir.

    Yeni veri çekilmez: her kaynak rapor kendi önbelleğini kendi koşusunda
    tazeler, bu rapor yalnızca onları toplar. Bir grubun bir günkü toplamı, o
    gruptaki fonlardan birinin akışı hesaplanamıyorsa (ardışık TEFAS gözlemi
    yok) null bırakılır — kısmi toplam tam sonuç gibi gösterilmez.
    """
    satirlar = []
    bosluk_n = 0
    yakin_bosluk_n = 0
    bilinmeyen = set()
    for kaynak in cfg["kapsam"]["kaynaklar"]:
        alt = rapor_yukle(kaynak["rapor"])
        if not os.path.exists(alt["cache"]):
            raise RuntimeError(
                f"{kaynak['rapor']} önbelleği yok ({alt['cache']}); "
                "grup raporu kaynak raporlardan sonra çalışır"
            )
        with open(alt["cache"], encoding="utf-8") as f:
            onbellek = json.load(f)
        akis, gunler, _, bosluklar = akis_serisi(onbellek)
        bosluk_n += sum(len(x) for x in bosluklar.values())
        if gunler:
            kesim = (dt.date.fromisoformat(gunler[-1]) - dt.timedelta(days=89)).isoformat()
            yakin_bosluk_n += sum(len(x) for t, x in bosluklar.items() if t >= kesim)
        bilinmeyen |= set(onbellek.get("turu_bilinmeyen", []))
        tipler = onbellek.get("tip", {})
        for tip in alt["fon_tipleri"]:
            kodlar = {k for k, t in tipler.items() if t == tip}
            if not kodlar:
                continue
            seri = {}
            for t in gunler:
                gunun = akis.get(t, {})
                if kodlar & set(bosluklar.get(t, {})):
                    seri[t] = None          # gruptaki bir fon hesaplanamadı
                    continue
                hesaplanan = kodlar & set(gunun)
                seri[t] = sum(gunun[k] for k in hesaplanan) if hesaplanan else None
            satirlar.append({
                "anahtar": f"{kaynak['kod']}-{tip}",
                "ad": f"{kaynak['grup']} — {GRUP_AD[tip]}",
                "tip": tip,
                "kodlar": kodlar,
                "fon_sayisi": len(kodlar),
                "seri": seri,
            })
    return satirlar, bosluk_n, yakin_bosluk_n, sorted(bilinmeyen)


def ortak_fonlar(satirlar):
    """{satır: {diğer satır: ortak fon sayısı}} — satırlar tematik, ayrık değil.

    Altın fonlarının tamamı kıymetli maden satırında, altın/gümüş katılım
    fonları hem kıymetli maden hem katılım satırında yer alır. Örtüşen satırlar
    birlikte seçilirse toplamları anlamsızdır; sayfa bunu bu haritayla bilir.
    """
    ortak = {}
    for a in satirlar:
        for b in satirlar:
            if a is b or a["tip"] != b["tip"]:
                continue
            kesisim = a["kodlar"] & b["kodlar"]
            if kesisim:
                ortak.setdefault(a["anahtar"], {})[b["anahtar"]] = len(kesisim)
    return ortak


def toplam_html_uret(cfg):
    satirlar, bosluk_n, yakin_bosluk_n, bilinmeyen = toplam_satirlari(cfg)
    if not satirlar:
        raise RuntimeError("grup raporunda satır yok")
    gunler = kesin_tarihler(sorted({t for s in satirlar for t in s["seri"]}))
    if not gunler:
        raise RuntimeError("raporlanabilir TEFAS tarihi yok")
    son = gunler[-1]
    bulunan = [s for s in satirlar if s["seri"].get(son) is not None]
    eksik = [s["anahtar"] for s in satirlar if s["seri"].get(son) is None]
    sirali = sorted(satirlar,
                    key=lambda s: -sum(abs(s["seri"].get(t) or 0) for t in gunler))
    fon_sayisi = sum(s["fon_sayisi"] for s in satirlar)
    tekil_fon = len({k for s in satirlar for k in s["kodlar"]})
    ortak = ortak_fonlar(satirlar)
    ortak_ciftler = sorted(
        {(min(a, b), max(a, b), n)
         for a, komsular in ortak.items() for b, n in komsular.items()},
        key=lambda x: -x[2],
    )
    fon_ozet = (f"son veri tarihinde {len(bulunan)}/{len(satirlar)} grup toplandı "
                f"({tekil_fon} tekil fon)")
    eksik_not = ("<span class=\"missing-note\">Son veri tarihinde toplanamayan gruplar: "
                 + html_lib.escape(", ".join(eksik)) + "</span>") if eksik else ""
    if bosluk_n:
        eksik_not += ("<span class=\"missing-note\">Kaynak raporlarda ardışık TEFAS "
                      f"gözlemi olmayan {bosluk_n} fon-gün; o günün grup toplamı "
                      "boş bırakıldı.</span>")
    if ortak_ciftler:
        ozet = ", ".join(f"{a}∩{b}: {n} fon" for a, b, n in ortak_ciftler[:6])
        eksik_not += ("<span class=\"missing-note\">Gruplar tematik, ayrık değil "
                      f"({ozet}). Örtüşen gruplar birlikte seçilirse dönem toplamı "
                      "gösterilmez — çift sayım olurdu.</span>")
    if bilinmeyen:
        eksik_not += ("<span class=\"missing-note\">TEFAS fon türünü açıklamadığı için "
                      f"kapsam dışı kalan {len(bilinmeyen)} fon: "
                      + html_lib.escape(", ".join(bilinmeyen)) + "</span>")
    raw = {
        "d": gunler,
        "ad": {s["anahtar"]: s["ad"] for s in sirali},
        "tip": {s["anahtar"]: s["tip"] for s in sirali},
        "grup": GRUP_AD,
        "f": {s["anahtar"]: [None if s["seri"].get(t) is None else round(s["seri"][t])
                             for t in gunler] for s in sirali},
        "gap_count": bosluk_n,
        "ortak": ortak,
    }
    report_meta = {
        "last_successful_run": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "data_end_date": son,
        "expected_count": len(satirlar),
        "found_count": len(bulunan),
        "missing": eksik,
        "uncomputed_count": len(eksik),
        "uncomputed": eksik,
        "gap_count": bosluk_n,
        "recent_gap_count": yakin_bosluk_n,
        "gap_codes": [],
        "fund_count": tekil_fon,
        "row_fund_count": fon_sayisi,
        "overlap_pairs": [{"a": a, "b": b, "fon": n} for a, b, n in ortak_ciftler],
        "additive": not ortak_ciftler,
        "untyped_count": len(bilinmeyen),
        "untyped": bilinmeyen,
        "count_label": "grup",
        "source": "TEFAS",
    }
    html_yaz(cfg, raw, report_meta, fon_ozet, eksik_not, gunler)
    return len(bulunan), len(gunler), eksik


def main():
    argv = sys.argv[1:]
    ad = "secili"
    if "--rapor" in argv:
        ad = argv[argv.index("--rapor") + 1]
    cfg = rapor_yukle(ad)

    if cfg["kapsam"]["tip"] == "toplam":
        # Grup raporu veri çekmez; kaynak raporların önbelleklerini toplar.
        grup, gun, eksik = toplam_html_uret(cfg)
        print(f"OK: {gun} gün × {grup} grup → {cfg['html']}")
        if eksik:
            print("Son veri tarihinde toplanamayan gruplar: " + ", ".join(eksik))
            return 4
        return 0

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
