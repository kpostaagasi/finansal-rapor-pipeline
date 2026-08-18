#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hafta içi 09:45 emtia + tahvil + TEFAS mail otomasyonu.

Akış:
  1. emtia_report.py'yi çalıştırır -> emtia_futures.html.
  2. tefas_akis.py'yi çalıştırır -> tefas_net_akis.html.
     Zorunlu üretimlerden biri başarısızsa yayın ve mail durur.
  3. Her iki HTML'i config'teki GitHub Pages reposuna yükler; sayfalar alıcıların
     tarayıcısında girişsiz açılır.
  4. 4 alıcıya üç linki içeren maili SMTP ile gönderir.

SMTP şifresi REPODA TUTULMAZ — macOS Keychain'den okunur (model_portfoy_smtp,
Model Portföy otomasyonuyla aynı Gmail App Password).

Kullanım:
  python3 gunluk_mail.py            # tam akış (günde bir kez; kilit .last_sent)
  python3 gunluk_mail.py --force    # bugünkü kilidi yok say, tekrar gönder
  python3 gunluk_mail.py --dry-run  # yerelde üret; yükleme/mail yok
  python3 gunluk_mail.py --example  # SADECE gönderene [ÖRNEK] maili at; kilidi işaretlemez
  python3 gunluk_mail.py --no-push  # yerelde üret; yükleme/mail yok
"""
import os, sys, json, ssl, base64, smtplib, subprocess, shutil, tempfile, datetime as dt
from email.message import EmailMessage
from email.utils import formataddr, formatdate

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONFIG = os.path.join(HERE, "mail_config.json")
HTML_PATH = os.path.join(HERE, "emtia_futures.html")
BUILD_SITE = os.path.join(ROOT, "build_site.py")
SITE_DIR = os.path.join(ROOT, "site")
SENT_MARK = os.path.join(HERE, "output", ".last_sent")  # aynı gün tekrar göndermeyi engeller
SEND_CLAIM = os.path.join(HERE, "output", ".send_claim")
GH = shutil.which("gh") or os.path.expanduser("~/.local/bin/gh")


def log(msg):
    print(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} — {msg}", flush=True)


def ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def load_config():
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)


def keychain_password(service, account):
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True, text=True, check=True,
        )
        return out.stdout.strip()
    except subprocess.CalledProcessError:
        return None


def today_tag():
    return f"{dt.date.today():%Y-%m-%d}"


def already_sent_today():
    if not os.path.exists(SENT_MARK):
        return False
    with open(SENT_MARK, encoding="utf-8") as f:
        return f.read().strip() == today_tag()


def mark_sent():
    os.makedirs(os.path.dirname(SENT_MARK), exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".last_sent.", dir=os.path.dirname(SENT_MARK))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(today_tag())
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, SENT_MARK)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def acquire_send_claim():
    """Aynı gün yalnız bir sürecin SMTP aşamasına girmesini sağlar."""
    os.makedirs(os.path.dirname(SEND_CLAIM), exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(SEND_CLAIM, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                with open(SEND_CLAIM, encoding="utf-8") as f:
                    if f.read().strip() == today_tag():
                        return False
                os.unlink(SEND_CLAIM)
                continue
            except (FileNotFoundError, OSError):
                return False
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(today_tag())
            f.flush()
            os.fsync(f.fileno())
        return True
    return False


def release_send_claim():
    try:
        os.unlink(SEND_CLAIM)
    except FileNotFoundError:
        pass


def generate_report():
    """emtia_report.py'yi çalıştırır; başarı = 'OK:' satırı + en az 5 emtia + faiz var."""
    env = dict(os.environ, TZ="Europe/Istanbul")
    for attempt in range(1, 4):
        r = subprocess.run([sys.executable, os.path.join(HERE, "emtia_report.py")],
                           capture_output=True, text=True, env=env, timeout=600)
        out = (r.stdout or "").strip()
        if r.returncode == 0 and "OK:" in out and "faiz: var" in out:
            try:
                n = int(out.split("—")[1].split("emtia")[0].strip())
            except Exception:
                n = 0
            if n >= 5:
                log(f"rapor üretildi: {out}")
                return True
        log(f"rapor denemesi {attempt}/3 başarısız: {out} {r.stderr.strip()[:200]}")
        import time; time.sleep(90)
    return False


def generate_tefas(cfg):
    """tefas_akis.py'yi çalıştırır (TEFAS'tan son günleri çekip HTML'i tazeler)."""
    script = cfg.get("tefas_script")
    if not script or not os.path.exists(script):
        log("UYARI: tefas_akis.py bulunamadı, TEFAS raporu atlanıyor")
        return False
    r = subprocess.run([sys.executable, script], capture_output=True, text=True,
                       env=dict(os.environ, TZ="Europe/Istanbul"), timeout=1800)
    out = (r.stdout or "").strip()
    if r.returncode == 0 and out.startswith("OK:"):
        log(f"TEFAS raporu üretildi: {out}")
        return True
    log(f"TEFAS raporu HATASI: {out} {r.stderr.strip()[-300:]}")
    return False


def push_to_github(cfg, local_path=None, repo_path=None):
    """Bir HTML'i Pages reposuna PUT eder (varsa sha ile günceller)."""
    local_path = local_path or HTML_PATH
    repo_path = repo_path or cfg["github_path"]
    with open(local_path, encoding="utf-8") as f:
        html = f.read()
    # Pages'te doğrudan servis edildiği için tam belge sarmalayıcısı ekle
    if repo_path.endswith(".html") and "<!doctype" not in html.lower():
        bas = '<!doctype html><html lang="tr">'
        if "<meta charset" not in html.lower():
            bas += ('<meta charset="utf-8">'
                    '<meta name="viewport" content="width=device-width, initial-scale=1">')
        html = bas + html + "</html>"
    api_path = f"repos/{cfg['github_repo']}/contents/{repo_path}"
    sha = None
    r = subprocess.run([GH, "api", api_path, "--jq", ".sha"], capture_output=True, text=True)
    if r.returncode == 0:
        sha = r.stdout.strip()
    payload = {"message": f"{repo_path} {today_tag()}",
               "content": base64.b64encode(html.encode()).decode()}
    if sha:
        payload["sha"] = sha
    pf = os.path.join(HERE, "output", "gh_payload.json")
    os.makedirs(os.path.dirname(pf), exist_ok=True)
    with open(pf, "w") as f:
        json.dump(payload, f)
    r = subprocess.run([GH, "api", "-X", "PUT", api_path, "--input", pf],
                       capture_output=True, text=True)
    os.remove(pf)
    if r.returncode != 0:
        log(f"GitHub yükleme HATASI ({repo_path}): {r.stderr.strip()[:300]}")
        return False
    log(f"GitHub Pages güncellendi: {repo_path}")
    return True


def publish_dashboard(cfg, failures=None):
    """Site paketini yeniden üretir; ana sayfa ve sağlık JSON'unu yayımlar."""
    cmd = [sys.executable, BUILD_SITE]
    for target, message in (failures or {}).items():
        cmd.append(f"--failure={target}={message}")
    r = subprocess.run(
        cmd, capture_output=True, text=True,
        env=dict(os.environ, TZ="Europe/Istanbul"), timeout=120,
    )
    if r.returncode != 0:
        log(f"Dashboard üretim HATASI: {(r.stderr or r.stdout).strip()[-300:]}")
        return False
    index_ok = push_to_github(cfg, os.path.join(SITE_DIR, "index.html"), "index.html")
    status_ok = push_to_github(
        cfg, os.path.join(SITE_DIR, "report_status.json"), "report_status.json"
    )
    return index_ok and status_ok


def build_message(cfg):
    msg = EmailMessage()
    msg["Subject"] = cfg["subject"]
    msg["From"] = formataddr((cfg.get("sender_name", ""), cfg["sender"]))
    msg["To"] = ", ".join(cfg["recipients"])
    msg["Date"] = formatdate(localtime=True)
    emtia, dashboard = cfg["emtia_url"], cfg["tahvil_url"]
    tefas = cfg["tefas_url"]
    msg.set_content(
        f"Emtia Futures Eğrileri: {emtia}\n"
        f"Rapor Ana Sayfası: {dashboard}\n"
        f"TEFAS Altın Fonlarına Net Akış: {tefas}\n\n\n\n\nSaygılarımla,"
    )
    msg.add_alternative(f"""\
<html><body style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;font-size:14px;color:#222">
<p><a href="{emtia}">Emtia Futures Eğrileri</a><br>
<span style="color:#808080;font-size:12px">Petrol, altın, gümüş, platin, bakır, alüminyum vadeli eğrileri + ABD getiri eğrisi</span></p>
<p><a href="{dashboard}">Rapor Ana Sayfası</a><br>
<span style="color:#808080;font-size:12px">Emtia ve TEFAS raporları</span></p>
<p><a href="{tefas}">TEFAS Altın Fonlarına Net Akış</a><br>
<span style="color:#808080;font-size:12px">Altın yatırım ve altın emeklilik fonlarına günlük / haftalık / aylık net giriş-çıkış</span></p>
<br><br><br><br>
<p>Saygılarımla,</p>
</body></html>""", subtype="html")
    return msg


def main():
    dry = "--dry-run" in sys.argv
    force = "--force" in sys.argv
    example = "--example" in sys.argv
    no_push = "--no-push" in sys.argv
    cfg = load_config()
    allow_publish = cfg.get("allow_publish", False)

    if example:
        cfg = dict(cfg)
        cfg["recipients"] = [cfg["sender"]]
        cfg["subject"] = "[ÖRNEK] " + cfg["subject"]

    # Hafta sonu koruması: launchd, kaçırılan cuma işini cumartesi açılışta tetikleyebiliyor.
    if dt.date.today().weekday() >= 5 and not force and not example:
        log(f"bugün hafta sonu ({today_tag()}), mail gönderilmiyor. Zorlamak için: --force")
        return 0

    if already_sent_today() and not force and not dry and not example:
        log(f"bugün ({today_tag()}) zaten gönderilmiş, atlanıyor. Tekrar için: --force")
        return 0

    # Önce bütün zorunlu raporları yerelde üret. Kısmi başarıda ne yayın ne mail
    # yapılır; sabit URL'lerin eski içeriği güncelmiş gibi dağıtılması engellenir.
    emtia_ok = generate_report()
    tefas_ok = generate_tefas(cfg)

    if not emtia_ok or not tefas_ok:
        eksik = []
        failures = {}
        if not emtia_ok:
            eksik.append("emtia/ABD Hazine")
            failures["emtia_futures.html"] = "Emtia/ABD Hazine veri üretimi başarısız"
        if not tefas_ok:
            eksik.append("TEFAS toplam altın")
            failures["tefas_net_akis.html"] = "TEFAS toplam altın veri üretimi başarısız"
        log("HATA: zorunlu raporlar tazelenemedi; yayın/mail engellendi: " + ", ".join(eksik))
        if not (dry or no_push) and allow_publish and not publish_dashboard(cfg, failures):
            log("HATA: başarısızlık durumu dashboard'a yayımlanamadı")
        return 4

    if dry or no_push:
        mod = "DRY-RUN" if dry else "NO-PUSH"
        log(f"[{mod}] yükleme/mail yok. Alıcılar: {', '.join(cfg['recipients'])} | Konu: {cfg['subject']}")
        return 0

    if not allow_publish and not example:
        log("GÜVENLİK: allow_publish=false; GitHub yayını kapalı")
        return 3

    if not example:
        yayin_ok = push_to_github(cfg)
        yayin_ok = (push_to_github(
            cfg, cfg["tefas_html"], cfg["tefas_github_path"]
        ) and yayin_ok)
        if not yayin_ok:
            log("HATA: GitHub yayını tamamlanamadı; mail engellendi")
            return 5

        if not publish_dashboard(cfg):
            log("HATA: dashboard yayını tamamlanamadı; mail engellendi")
            return 5

    if not cfg.get("allow_send", False):
        log("GÜVENLİK: allow_send=false; mail gönderimi kapalı")
        return 3

    claimed = False
    if not example:
        claimed = acquire_send_claim()
        if not claimed:
            log("GÜVENLİK: başka bir süreç bugünkü gönderimi üstlenmiş; mail atlandı")
            return 0
        if not force and already_sent_today():
            release_send_claim()
            log(f"bugün ({today_tag()}) başka süreç tarafından gönderilmiş; mail atlandı")
            return 0
    try:
        pw = keychain_password(cfg["keychain_service"], cfg["sender"])
        if not pw:
            log("HATA: SMTP şifresi Keychain'de yok")
            return 2

        msg = build_message(cfg)
        with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=60) as s:
            s.starttls(context=ssl_context())
            s.login(cfg["sender"], pw)
            s.send_message(msg)
        if not example:
            mark_sent()
    finally:
        if claimed:
            release_send_claim()
    log(f"{'ÖRNEK mail' if example else 'mail gönderildi'}: {len(cfg['recipients'])} alıcı → {', '.join(cfg['recipients'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
