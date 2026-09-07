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
YAKIN_PENCERE_GUN = 90              # "son 90 gün" penceresi: panonun gap_periods varsayılanıyla aynı

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

    TEFAS unvanlarında `İ` bazen İngilizce kelimelerde de kullanılıyor (ör.
    "PLATİNUM"). Bu yüzden unvan tek noktada `İ` → ASCII `I`'ya indirgenip
    yalnız ASCII anahtarlarla karşılaştırılıyor; PLATİN/PLATIN gibi çift
    dallara gerek kalmıyor (SILVER/PALLADIUM'un da Türkçe İ'li yazımı artık
    yakalanıyor).

    Unvan tuzakları (TEFAS unvan listesinden doğrulandı):
      · ALTINCI/ONALTINCI/YİRMİALTINCI — sıra sayısı (altin_unvani eler)
      · GOLDEN GLOBAL — kurucu adı (altin_unvani eler)
      · GÜMÜŞSUYU — Yapı Kredi'nin semt adlı özel fonu, gümüş değil
      · "ÖZEL BANKACILIK VE PLATİNUM" — hizmet segmenti adı, platin değil
    """
    u = unvan.upper().replace("İ", "I")
    if altin_unvani(u):
        return True
    temiz = u.replace("GÜMÜŞSUYU", " ")
    if any(s in temiz for s in ("GÜMÜŞ", "SILVER", "PALADYUM", "PALLADIUM",
                                "KIYMETLI MADEN")):
        return True
    return "PLATIN" in u and "BANKACILIK" not in u


# Tür bazlı kapsamların isteğe bağlı unvan kuralı: tür eşleşmese de unvan
# fonu evrene alabilir (raporlar/*.json içinde `kapsam.unvan_kurali`).
UNVAN_KURALLARI = {
    "altin": altin_unvani,
    "kiymetli_maden": kiymetli_maden_unvani,
}


def tur_haritasi(fon_tipi, deneme=3):
    """{fon kodu: fonTurAciklama} — TEFAS yönetim bilgisi ucundan.

    Günlük veri ucu fon türünü döndürmüyor; tür yalnızca bu uçtan gelir. Uç
    günlük veri veren her fonu kapsamıyor (bugün 2041 fonun 13'ü burada yok),
    bu yüzden türü bilinmeyen fonlar sessizce elenmez: `TurKapsami` onları
    toplayıp metadata'da ifşa eder.

    `fetch` gibi yeniden dener: sekiz rapor × iki fon tipi bu ucu 16 kez
    çağırıyor ve TEFAS yoğunlukta bağlantıyı yanıt vermeden kapatabiliyor
    (RemoteDisconnected). Tek kopma tüm koşuyu düşürmemeli.
    """
    body = {"fonTipi": fon_tipi, "fonKodu": None, "aramaMetni": None, "fonTurKod": None,
            "fonGrubu": None, "sfonTurKod": None, "basSira": 1, "bitSira": 100000,
            "fonTurAciklama": None, "dil": "TR", "kurucuKod": None, "islem": None}
    for i in range(1, deneme + 1):
        try:
            r = requests.post(API_LISTE, headers=HEADERS, json=body, timeout=120)
            r.raise_for_status()
            return {x["fonKodu"]: x.get("fonTurAciklama")
                    for x in (r.json().get("resultList") or [])}
        except Exception as e:
            log(f"  {fon_tipi} tür listesi deneme {i}/{deneme} hata: {str(e)[:120]}")
            if i == deneme:
                raise
            time.sleep(20)


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


def gecerli_gozlem(kayit):
    """TEFAS kaydı geçerli bir gözlem mi?

    Bozuk bir fetch bazen tedavüldeki pay sayısını (tedPaySayisi) ve fiyatı
    0 döndürüyor; pay×fiyat=0 matematiksel olarak geçerli bir 0 akış gibi
    görünüp fail-closed kontrollerini atlatıyor, "Hesaplandı" etiketiyle 0 TL
    yayınlanıyordu. tedPaySayisi<=0 veya fiyat<=0 olan kayıt gözlem SAYILMAZ
    — veri boşluğu olarak ele alınır.
    """
    pay, fiyat = kayit
    return pay is not None and fiyat is not None and pay > 0 and fiyat > 0


BOLUNME_PAY_ESIGI = 1.5         # pay oranı bu eşiği aşarsa/altına inerse bölünme adayı
BOLUNME_DEGER_TOLERANSI = 0.05  # pay×fiyat oranı 1'den bu kadar sapabilir


def pay_bolunmesi(onceki_kayit, simdi_kayit):
    """İki ardışık geçerli gözlem arasında pay bölünmesi/birleşmesi var mı?

    TEFAS bazen bir fonun pay bölünmesini/birleşmesini sıradan bir güncelleme
    gibi veriyor: pay sayısı binlerce kat sıçrıyor, fiyat ters orantılı düşüyor
    (ör. TI2 2025-01-20: pay ×9971, fiyat ÷9834). (pay_t - pay_onceki) × fiyat_t
    formülü bunu matematiksel olarak geçerli ama hayali, milyarlarca TL'lik bir
    akış gibi hesaplıyor. Pay oranı BOLUNME_PAY_ESIGI'yi aşıp/altına inip fon
    değeri (pay×fiyat) ~sabit kalıyorsa (BOLUNME_DEGER_TOLERANSI içinde) bu
    sıradan bir alım/satım değil, birim değişimidir — akış hesaplanamaz
    (fail-closed: bkz. akis_serisi).
    """
    onceki_pay, onceki_fiyat = onceki_kayit
    simdi_pay, simdi_fiyat = simdi_kayit
    pay_orani = simdi_pay / onceki_pay
    fiyat_orani = simdi_fiyat / onceki_fiyat
    deger_orani = pay_orani * fiyat_orani
    esik_asildi = pay_orani >= BOLUNME_PAY_ESIGI or pay_orani <= 1 / BOLUNME_PAY_ESIGI
    return esik_asildi and abs(deger_orani - 1) < BOLUNME_DEGER_TOLERANSI


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
                if not gecerli_gozlem((x["tedPaySayisi"], x["fiyat"])):
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

    def kaydet(yeni, bilinmeyen=None):
        """Ara kayıt (bilinmeyen=None): uzun çekim yarıda kalırsa ilerleme
        kaybolmasın, turu_bilinmeyen alanına dokunmaz. Son kayıt: bu koşunun
        kümesini (boş bile olsa) koşulsuz yazar — yoksa önceki koşudan kalan
        bayat liste önbellekte sürünür."""
        for kod, seri in yeni.items():
            g = onbellek["fon"].setdefault(kod, {})
            for tarih, (pay, fiyat) in seri.items():
                g[tarih] = [pay, fiyat]
        onbellek["ad"], onbellek["tip"] = adlar, tipler
        if bilinmeyen is not None:
            if bilinmeyen:
                onbellek["turu_bilinmeyen"] = sorted(bilinmeyen)
            else:
                onbellek.pop("turu_bilinmeyen", None)
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

    Önbellekte zaten birikmiş bozuk kayıtlar da olabilir (ör. [0, 0]) —
    `gecerli_gozlem` burada seriyi okurken de uygulanır: geçersiz kayıt o
    fon için o tarihte gözlem yokmuş gibi ele alınır, tarih 'bosluklar'a
    'invalid_observation' nedeniyle girer ve ilk-gözlem (piyasaya çıkış)
    istisnasını tetiklemez (istisna yalnız fonun HAM serideki en eski
    tarihinde, o kayıt geçerliyse uygulanır).

    İki ardışık geçerli gözlem arasında pay bölünmesi/birleşmesi tespit
    edilirse (bkz. `pay_bolunmesi`) o gün için de akış üretilmez: tarih
    'bosluklar'a 'unit_split' nedeniyle girer. Fail-closed — TEFAS bölünme
    oranını yayınlamadığı için tahminle akış hesaplanmaz.
    """
    fonlar = onbellek["fon"]
    tarihler = sorted({t for s in fonlar.values() for t in s})
    akis = {}
    bosluklar = {}
    onceki_rapor = {t: tarihler[i - 1] for i, t in enumerate(tarihler) if i}
    for kod, seri in fonlar.items():
        ham_tarihler = sorted(seri)
        ilk_ham_tarih = ham_tarihler[0]
        gecerli_tarihler = [t for t in ham_tarihler if gecerli_gozlem(seri[t])]
        gecerli = {t: seri[t] for t in gecerli_tarihler}
        onceki_mevcut = {t: gecerli_tarihler[i - 1] for i, t in enumerate(gecerli_tarihler) if i}
        son_gecerli = None
        for t in ham_tarihler:
            if t in gecerli:
                son_gecerli = t
                continue
            beklenen = onceki_rapor.get(t)
            bosluklar.setdefault(t, {})[kod] = {
                "reason": "invalid_observation",
                "expected_previous": beklenen,
                "previous_available": son_gecerli,
            }
            akis.setdefault(t, {})
        for t in gecerli_tarihler:
            beklenen = onceki_rapor.get(t)
            if beklenen is None:
                continue
            if beklenen not in gecerli:
                if t == ilk_ham_tarih:
                    continue  # fonun ilk gözlemi (piyasaya çıkış) gap değildir
                bosluklar.setdefault(t, {})[kod] = {
                    "expected_previous": beklenen,
                    "previous_available": onceki_mevcut.get(t),
                }
                akis.setdefault(t, {})
                continue
            if pay_bolunmesi(gecerli[beklenen], gecerli[t]):
                # Pay bölünmesi/birleşmesi: (pay_t - pay_onceki) × fiyat_t hayali
                # bir akış üretir (bkz. pay_bolunmesi). Fail-closed: akış
                # üretilmez, boşluk kaydına 'unit_split' nedeniyle girer.
                bosluklar.setdefault(t, {})[kod] = {
                    "reason": "unit_split",
                    "expected_previous": beklenen,
                    "previous_available": beklenen,
                }
                akis.setdefault(t, {})
                continue
            akis.setdefault(t, {})[kod] = (gecerli[t][0] - gecerli[beklenen][0]) * gecerli[t][1]
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
    # Son tarihte ilk gözlemini veren (piyasaya yeni çıkan) fon akış boşluğu
    # DEĞİLDİR: akis_serisi çıkış gününü kasıtlı atlar (bkz. akis_serisi).
    # Bu fonları "hesaplanamayan" sayıp zinciri son tarihte durdurmak yerine
    # ayrı bir 'launched' kümesiyle ifşa ediyoruz — sessizleştirme değil,
    # sınıflandırma düzeltmesi.
    launched = {k for k in bulunan if min(onbellek["fon"][k]) == son}
    hesaplanan |= launched
    if beklenen:
        bosluklar = {
            t: {k: v for k, v in kodlar.items() if k in beklenen}
            for t, kodlar in bosluklar.items()
        }
    # Yakın pencere: son veri gününden YAKIN_PENCERE_GUN (90) gün geriye — dahil.
    kesim = (dt.date.fromisoformat(son) - dt.timedelta(days=YAKIN_PENCERE_GUN)).isoformat()
    yakin_bosluk_n = sum(
        len(kodlar) for tarih, kodlar in bosluklar.items() if tarih >= kesim
    )
    split_codes = sorted({
        k for kodlar in bosluklar.values()
        for k, v in kodlar.items() if v.get("reason") == "unit_split"
    })
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
        "launched": sorted(launched),
        "recent_gap_count": yakin_bosluk_n,
        "split_codes": split_codes,
        "split_count": len(split_codes),
    }


# --- çıktı ------------------------------------------------------------------

# Şablon (secili_template.html ~569-591) grup düğmelerini RAW.tip'teki tekil
# değerlerden, etiketleri de RAW.grup'tan (bu sözlük) üretiyor; yeni bir tip
# eklemek şablonda değişiklik gerektirmeden otomatik üçüncü düğme olarak çıkar.
GRUP_AD = {
    "YAT": "Yatırım fonları",
    "EMK": "Emeklilik fonları",
    "TUM": "Yatırım + emeklilik",
}


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
    if durum["launched"]:
        eksik_not += ("<span class=\"missing-note\">Son veri tarihinde piyasaya yeni "
                      f"çıkan {len(durum['launched'])} fon: "
                      + html_lib.escape(", ".join(durum["launched"])) + "</span>")
    if durum["split_codes"]:
        eksik_not += ("<span class=\"missing-note\">Pay bölünmesi nedeniyle akış hesaplanamayan "
                      f"{durum['split_count']} fon: "
                      + html_lib.escape(", ".join(durum["split_codes"])) + "</span>")
    # null = "hesaplanamadı" (ardışık TEFAS gözlemi yok). Fonun piyasaya
    # çıkışından önceki / kapanışından sonraki günler ile çıkış gününün kendisi
    # boşluk değildir: akışa 0 katkı verir ve dönem toplamını iptal etmez.
    bosluk_kodlari = {t: set(x) for t, x in durum["bosluklar"].items()}
    raw = {
        "d": gunler,
        "ad": {k: onbellek["ad"].get(k, k) for k in kodlar},
        "tip": {k: onbellek.get("tip", {}).get(k, "YAT") for k in kodlar},
        # Fon bazlı raporlar TUM tipini hiç üretmez (bkz. toplam_satirlari);
        # GRUP_AD'ın yalnızca burada fiilen kullanılan alt kümesini gömerek bu
        # raporların çıktısı grup toplamı raporundan bağımsız, değişmez kalır.
        "grup": {t: GRUP_AD[t] for t in ("YAT", "EMK")},
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
        "launched_count": len(durum["launched"]),
        "launched": durum["launched"],
        "gap_count": bosluk_n,
        "recent_gap_count": durum["recent_gap_count"],
        "split_count": durum["split_count"],
        "split_codes": durum["split_codes"],
        "gap_codes": sorted({k for x in durum["bosluklar"].values() for k in x}),
        "untyped_count": len(bilinmeyen),
        "untyped": list(bilinmeyen),
        "count_label": "fon",
        "source": "TEFAS",
    }
    html_yaz(cfg, raw, report_meta, fon_ozet, eksik_not, gunler)
    return bulunan_n, len(gunler)


# --- grup bazlı toplam raporu -----------------------------------------------

def _satir(anahtar, ad, tip, kodlar, akis, gunler, fonlar):
    """(anahtar, ad, tip, kodlar) için tek bir toplam satırı üretir.

    YAT, EMK ve birleşik (TUM) satırlar aynı mantığı paylaşır: gözlem aralığı
    (ilk gözlemden SONRA, son gözleme KADAR) t'yi kapsayan her üye fonun
    akis[t]'te değeri OLMALI; biri eksikse (o gün TEFAS gözlemi vermemiş)
    toplam sessizce kısmi kalmasın, null olsun. Henüz piyasaya çıkmamış (t <=
    ilk gözlem) ya da kapanmış (t > son gözlem) fon bu şarta girmez, toplamı
    iptal etmez.
    """
    araliklar = {k: (min(fonlar[k]), max(fonlar[k])) for k in kodlar}
    seri = {}
    for t in gunler:
        gunun = akis.get(t, {})
        aktif = {k for k in kodlar if araliklar[k][0] < t <= araliklar[k][1]}
        seri[t] = None if (not aktif or (aktif - set(gunun))) else sum(gunun[k] for k in aktif)
    return {
        "anahtar": anahtar,
        "ad": ad,
        "tip": tip,
        "kodlar": kodlar,
        "fon_sayisi": len(kodlar),
        "seri": seri,
    }


def toplam_satirlari(cfg):
    """Kaynak raporların önbelleklerinden (grup, fon tipi) satırları üretir.

    Yeni veri çekilmez: her kaynak rapor kendi önbelleğini kendi koşusunda
    tazeler, bu rapor yalnızca onları toplar. Bir grubun bir günkü toplamı, o
    gruptaki fonlardan birinin akışı hesaplanamıyorsa (ardışık TEFAS gözlemi
    yok) null bırakılır — kısmi toplam tam sonuç gibi gösterilmez. Kaynakta
    hem YAT hem EMK varsa üçüncü bir birleşik (TUM) satır da üretilir; TUM
    satırı YAT+EMK toplamı olarak değil, aynı null-propagasyon mantığıyla
    doğrudan tüm fon kümesi üzerinden hesaplanır.
    """
    satirlar = []
    bosluk_gunler = set()       # (kod, tarih) — kaynaklar arası aynı fon-gün
    yakin_bosluk_gunler = set() # birden çok kaynakta (altın⊂kıymetli maden vb.)
    split_kodlari = set()       # pay bölünmesi görülen fonlar (tüm seri boyunca)
    bilinmeyen = set()          # tekrar sayılmasın diye çift değil küme
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
        for t, kodlar in bosluklar.items():
            bosluk_gunler.update((k, t) for k in kodlar)
            split_kodlari.update(
                k for k, v in kodlar.items() if v.get("reason") == "unit_split"
            )
        if gunler:
            # Yakın pencere: kaynağın son veri gününden YAKIN_PENCERE_GUN (90) gün geriye — dahil.
            kesim = (dt.date.fromisoformat(gunler[-1]) - dt.timedelta(days=YAKIN_PENCERE_GUN)).isoformat()
            for t, kodlar in bosluklar.items():
                if t >= kesim:
                    yakin_bosluk_gunler.update((k, t) for k in kodlar)
        bilinmeyen |= set(onbellek.get("turu_bilinmeyen", []))
        tipler = onbellek.get("tip", {})
        tip_kodlari = {}
        for tip in alt["fon_tipleri"]:
            kodlar = {k for k, t in tipler.items() if t == tip}
            if not kodlar:
                continue
            tip_kodlari[tip] = kodlar
            satirlar.append(_satir(
                f"{kaynak['kod']}-{tip}", f"{kaynak['grup']} — {GRUP_AD[tip]}",
                tip, kodlar, akis, gunler, onbellek["fon"],
            ))
        # Birleşik (TUM) satır yalnızca kaynakta hem YAT hem EMK varsa üretilir;
        # tek tip olsaydı YAT satırının birebir kopyası olurdu (gereksiz
        # örtüşen satır).
        if "YAT" in tip_kodlari and "EMK" in tip_kodlari:
            tum_kodlar = tip_kodlari["YAT"] | tip_kodlari["EMK"]
            satirlar.append(_satir(
                f"{kaynak['kod']}-TUM", f"{kaynak['grup']} — {GRUP_AD['TUM']}",
                "TUM", tum_kodlar, akis, gunler, onbellek["fon"],
            ))
    return (satirlar, len(bosluk_gunler), len(yakin_bosluk_gunler),
            sorted(bilinmeyen), sorted(split_kodlari))


def ortak_fonlar(satirlar):
    """{satır: {diğer satır: ortak fon sayısı}} — satırlar tematik, ayrık değil.

    Altın fonlarının tamamı kıymetli maden satırında, altın/gümüş katılım
    fonları hem kıymetli maden hem katılım satırında yer alır. Birleşik (TUM)
    satır da kendi YAT ve EMK satırlarını %100 kapsar — bu örtüşme de burada
    ifşa edilmeli, bu yüzden tip eşitliği şartı kaldırıldı; yalnızca kod
    kümesi kesişimine bakılıyor. Bir fonun TEFAS tipi (YAT/EMK) tektir, yani
    YAT ve EMK kod uzayları zaten ayrıktır (ör. PAR-YAT ∩ PAR-EMK boştur) —
    tip şartının kaldırılması bunlar arasında sahte kesişim doğurmaz.
    """
    ortak = {}
    for a in satirlar:
        for b in satirlar:
            if a is b:
                continue
            kesisim = a["kodlar"] & b["kodlar"]
            if kesisim:
                ortak.setdefault(a["anahtar"], {})[b["anahtar"]] = len(kesisim)
    return ortak


def toplam_html_uret(cfg):
    satirlar, bosluk_n, yakin_bosluk_n, bilinmeyen, split_kodlari = toplam_satirlari(cfg)
    if not satirlar:
        raise RuntimeError("grup raporunda satır yok")
    gunler = kesin_tarihler(sorted({t for s in satirlar for t in s["seri"]}))
    if not gunler:
        raise RuntimeError("raporlanabilir TEFAS tarihi yok")
    son = gunler[-1]
    bulunan = [s for s in satirlar if s["seri"].get(son) is not None]
    # "missing": kaynağın önbelleğinde son tarih hiç yok (kaynak bayat/geride
    # kaldı). "uncomputed": son tarih var ama o günün grup toplamı boşluk
    # yüzünden null. build_site aynı grubu iki kez göstermesin diye ayrık
    # tutuluyor; birleşimleri eski `eksik` bildirimiyle aynı kalıyor.
    missing, uncomputed, eksik = [], [], []
    for s in satirlar:
        if son not in s["seri"]:
            missing.append(s["anahtar"])
            eksik.append(s["anahtar"])
        elif s["seri"][son] is None:
            uncomputed.append(s["anahtar"])
            eksik.append(s["anahtar"])
    sirali = sorted(satirlar,
                    key=lambda s: -sum(abs(s["seri"].get(t) or 0) for t in gunler))
    # row_fund_count: satır başına fon sayılarının toplamı (birleşik TUM
    # satırları kendi YAT+EMK üyelerini tekrar sayar, bu yüzden tekil_fon'dan
    # büyüktür — alan adı ve anlamı aynı kalıyor, yalnızca değeri büyüdü).
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
        kalan_cift = len(ortak_ciftler) - 6
        if kalan_cift > 0:
            ozet += f" ve {kalan_cift} çift daha"
        eksik_not += ("<span class=\"missing-note\">Gruplar tematik, ayrık değil "
                      f"({ozet}). Örtüşen gruplar birlikte seçilirse dönem toplamı "
                      "gösterilmez — çift sayım olurdu.</span>")
    if bilinmeyen:
        eksik_not += ("<span class=\"missing-note\">TEFAS fon türünü açıklamadığı için "
                      f"kapsam dışı kalan {len(bilinmeyen)} fon: "
                      + html_lib.escape(", ".join(bilinmeyen)) + "</span>")
    if split_kodlari:
        eksik_not += ("<span class=\"missing-note\">Pay bölünmesi nedeniyle akış "
                      f"hesaplanamayan {len(split_kodlari)} fon: "
                      + html_lib.escape(", ".join(split_kodlari))
                      + "; o günün grup toplamı boş bırakıldı.</span>")
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
        "missing": missing,
        "uncomputed_count": len(uncomputed),
        "uncomputed": uncomputed,
        "gap_count": bosluk_n,
        "recent_gap_count": yakin_bosluk_n,
        "gap_codes": [],
        "split_count": len(split_kodlari),
        "split_codes": split_kodlari,
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
