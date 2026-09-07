#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TEFAS akış raporlarını tazeler — TEFAS yeni veri yayımladığında yakalamak için
gün içinde saat başı çalışır (LaunchAgent: com.user.tefas.seciliyenile, 08:00–22:00).

mail_config.json'daki her rapor (altin, secili) için: tefas_secili.py son ~12 günü
yeniden çeker, HTML ve masaüstü kopyası güncellenir. GitHub Pages'e **yalnızca veri
değiştiyse** yüklenir (HTML hash'i `output/.last_push_<rapor>` ile karşılaştırılır).

Mail göndermez; mail işi ayrı (secili_mail.py, hafta içi 10:30).

Kullanım:
  python3 secili_yenile.py            # tazele, değişen raporu Pages'e yükle
  python3 secili_yenile.py --force    # değişmese de yükle
  python3 secili_yenile.py --no-push  # sadece yerel çıktıları tazele
"""
import os, sys, hashlib, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from secili_mail import (generate_report, load_config, log,  # noqa: E402
                         publish_dashboard, push_to_github, rapor_configleri)


def hash_dosyasi(ad):
    return os.path.join(HERE, "output", f".last_push_{ad}")


def html_hash(yol):
    with open(yol, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def onceki_hash(ad):
    yol = hash_dosyasi(ad)
    if not os.path.exists(yol):
        return None
    with open(yol, encoding="utf-8") as f:
        return f.read().strip()


def kapi_acik_mi(cfg, alan):
    """`allow_send`/`allow_publish` gibi güvenlik kapılarını okur. Yalnız gerçek `True`
    (bool) değeri kapıyı açar; başka her değer (dize, 0/1, None, liste…) KAPALI sayılır
    ve stderr'e uyarı yazılır (B5: `cfg.get(alan, False)` truthiness ile "false" gibi
    boş olmayan dizeleri de açık sayıyordu)."""
    deger = cfg.get(alan, False)
    if deger is True:
        return True
    print(f"UYARI: {alan} boolean değil ({deger!r}); kapı kapalı sayıldı", file=sys.stderr)
    return False


def main():
    force = "--force" in sys.argv
    no_push = "--no-push" in sys.argv
    cfg = load_config()
    allow_publish = kapi_acik_mi(cfg, "allow_publish")
    hata = 0
    yayinlandi = False

    for rapor in rapor_configleri(cfg):
        ad = rapor["ad"]
        if not generate_report(rapor):
            log(f"[{ad}] tazelenemedi; mevcut sayfa korunuyor")
            hata = 1
            continue
        if no_push or not allow_publish:
            if not allow_publish:
                log(f"[{ad}] allow_publish=false; yalnızca yerel çıktı güncellendi")
            continue

        yeni = html_hash(rapor["html"])
        if yeni == onceki_hash(ad) and not force:
            log(f"[{ad}] veri değişmemiş, GitHub yüklemesi atlandı")
            continue
        if push_to_github(cfg, rapor):
            os.makedirs(os.path.join(HERE, "output"), exist_ok=True)
            with open(hash_dosyasi(ad), "w", encoding="utf-8") as f:
                f.write(yeni)
            yayinlandi = True
            log(f"[{ad}] yayınlandı ({dt.datetime.now():%H:%M})")
        else:
            hata = 2
    if yayinlandi and not publish_dashboard(cfg):
        log("Dashboard yayını tamamlanamadı")
        hata = 2
    return hata


if __name__ == "__main__":
    sys.exit(main())
