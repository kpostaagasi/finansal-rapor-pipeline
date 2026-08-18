#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Model Portföy haftalık mailini gönderir.

mp_build.py çalıştıktan sonra output/ altında oluşan:
  - mail_preview.html  (mail gövdesi, içinde <img src="cid:chart">)
  - performans.png     (gömülü grafik)
dosyalarını okur, alıcı listesine (inputs/mail_config.json) SMTP ile yollar.

SMTP şifresi REPODA TUTULMAZ — macOS Keychain'den okunur:
  security add-generic-password -s model_portfoy_smtp -a gonderen@example.com -w '<APP_PASSWORD>'
(Gmail için normal şifre değil, 16 haneli "App Password" gerekir.)

Kullanım:
  python3 mp_send.py            # gerçek gönderim (config'teki tüm alıcılara)
  python3 mp_send.py --dry-run  # gönderme; kime/ne gideceğini yazdır
  python3 mp_send.py --example  # SADECE gönderene (kendine) örnek/önizleme maili at;
                                #   haftalık kilidi umursamaz, kilidi işaretlemez
"""
import os, sys, json, ssl, smtplib, subprocess, datetime as dt
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "inputs", "mail_config.json")
OUT_DIR = os.path.join(HERE, "output")
HTML_PATH = os.path.join(OUT_DIR, "mail_preview.html")
CHART_PATH = os.path.join(OUT_DIR, "performans.png")
SENT_MARK = os.path.join(OUT_DIR, ".last_sent")  # aynı haftada tekrar göndermeyi engeller
# Güncel workbook mail'e ek olarak iliştirilir (mp_build her çalıştığında yenilenir)
WB_PATH = os.path.join(os.path.expanduser("~"), "Documents", "Model Portföy Performans.xlsx")


def ssl_context():
    """macOS python.org Python sistem sertifika deposunu görmez;
    varsa certifi CA paketini kullan, yoksa varsayılana düş."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def load_config():
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)


def keychain_password(service, account):
    """macOS Keychain'den SMTP şifresini çeker."""
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True, text=True, check=True,
        )
        return out.stdout.strip()
    except subprocess.CalledProcessError:
        return None


def current_week_tag():
    """İçinde bulunduğumuz ISO haftası — 'sadece haftada bir gönder' kilidi için."""
    iso = dt.date.today().isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def already_sent_this_week():
    if not os.path.exists(SENT_MARK):
        return False
    with open(SENT_MARK, encoding="utf-8") as f:
        return f.read().strip() == current_week_tag()


def mark_sent():
    with open(SENT_MARK, "w", encoding="utf-8") as f:
        f.write(current_week_tag())


def build_message(cfg):
    with open(HTML_PATH, encoding="utf-8") as f:
        html = f.read()
    msg = EmailMessage()
    msg["Subject"] = cfg.get("subject", "Model Portföy Haftalık Performans")
    msg["From"] = formataddr((cfg.get("sender_name", ""), cfg["sender"]))
    msg["To"] = ", ".join(cfg["recipients"])
    msg["Date"] = formatdate(localtime=True)

    # Grafiği cid:chart olarak göm
    chart_cid = make_msgid(domain="modelportfoy")[1:-1]  # <...> parantezlerini at
    html = html.replace("cid:chart", f"cid:{chart_cid}")

    msg.set_content(
        "Model Portföy haftalık performans raporu. Bu mail HTML biçimindedir; "
        "görüntülemek için HTML destekleyen bir istemci kullanın."
    )
    msg.add_alternative(html, subtype="html")

    with open(CHART_PATH, "rb") as f:
        chart_bytes = f.read()
    html_part = msg.get_payload()[1]  # add_alternative ile eklenen html part
    html_part.add_related(chart_bytes, "image", "png", cid=f"<{chart_cid}>")

    # Güncel Excel'i ek olarak iliştir (tarihli dosya adıyla)
    if os.path.exists(WB_PATH):
        with open(WB_PATH, "rb") as f:
            wb_bytes = f.read()
        fname = f"Model Portfoy Performans_{dt.date.today():%Y%m%d}.xlsx"
        msg.add_attachment(
            wb_bytes,
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=fname,
        )
    return msg


def main():
    dry = "--dry-run" in sys.argv
    force = "--force" in sys.argv
    example = "--example" in sys.argv
    cfg = load_config()

    if example:
        # Örnek mail: gerçek alıcıları by-pass et, sadece gönderene at, kilidi işaretleme
        cfg = dict(cfg)
        cfg["recipients"] = [cfg["sender"]]
        cfg["subject"] = "[ÖRNEK] " + cfg.get("subject", "Model Portföy Haftalık Performans")

    for p in (HTML_PATH, CHART_PATH):
        if not os.path.exists(p):
            print(f"HATA: {p} yok. Önce mp_build.py çalıştırılmalı.", file=sys.stderr)
            return 1

    if already_sent_this_week() and not force and not dry and not example:
        print(f"Bu hafta ({current_week_tag()}) zaten gönderilmiş, atlanıyor. "
              f"Tekrar için: python3 mp_send.py --force")
        return 0

    if dry:
        print("[DRY-RUN] Gönderilmeyecek.")
        print("Gönderen :", cfg["sender"])
        print("Alıcılar :", ", ".join(cfg["recipients"]))
        print("Konu     :", cfg.get("subject"))
        print("Gövde    :", HTML_PATH, "(+ performans.png gömülü)")
        return 0

    pw = keychain_password(cfg["keychain_service"], cfg["sender"])
    if not pw:
        print("HATA: SMTP şifresi Keychain'de bulunamadı.\n"
              "Şu komutla ekleyin (Gmail App Password ile):\n"
              f"  security add-generic-password -s {cfg['keychain_service']} "
              f"-a {cfg['sender']} -w 'APP_PASSWORD'", file=sys.stderr)
        return 2

    msg = build_message(cfg)
    ctx = ssl_context()
    with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=60) as s:
        s.starttls(context=ctx)
        s.login(cfg["sender"], pw)
        s.send_message(msg)
    if not example:
        mark_sent()
    tag = "ÖRNEK mail" if example else "mail gönderildi"
    print(f"{dt.datetime.now():%Y-%m-%d %H:%M} — {tag}: "
          f"{len(cfg['recipients'])} alıcı → {', '.join(cfg['recipients'])} ({current_week_tag()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
