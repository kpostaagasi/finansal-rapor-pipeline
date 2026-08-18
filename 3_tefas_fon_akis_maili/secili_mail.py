#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hafta içi 10:30 — TEFAS net akış raporları maili.

mail_config.json'daki her rapor için (varsayılan: altin + secili):
  1. tefas_secili.py --rapor <ad> ile HTML'i tazeler.
     Zorunlu raporlardan biri üretilemezse yayın ve mail durur.
  2. HTML'i config'teki GitHub Pages reposuna yükler.
  3. Tüm raporların linklerini tek mailde alıcılara gönderir.

SMTP şifresi REPODA TUTULMAZ — macOS Keychain'den okunur (model_portfoy_smtp,
emtia/model portföy otomasyonlarıyla aynı Gmail App Password).

Kullanım:
  python3 secili_mail.py            # tam akış (günde bir kez; kilit .last_sent)
  python3 secili_mail.py --force    # bugünkü kilidi yok say, tekrar gönder
  python3 secili_mail.py --dry-run  # yerelde üret; yükleme/mail yok
  python3 secili_mail.py --example  # SADECE gönderene [ÖRNEK] maili at
  python3 secili_mail.py --no-push  # yerelde üret; yükleme/mail yok
"""
import os, sys, json, ssl, base64, smtplib, subprocess, shutil, tempfile, datetime as dt
from email.message import EmailMessage
from email.utils import formataddr, formatdate

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
CONFIG = os.path.join(HERE, "mail_config.json")
RAPOR_SCRIPT = os.path.join(HERE, "tefas_secili.py")
BUILD_SITE = os.path.join(ROOT, "build_site.py")
SITE_DIR = os.path.join(ROOT, "site")
SENT_MARK = os.path.join(HERE, "output", ".last_sent")
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


def rapor_configleri(cfg):
    import tefas_secili as ts
    return [ts.rapor_yukle(ad) for ad in cfg.get("raporlar", ["secili"])]


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


def generate_report(rapor):
    """tefas_secili.py --rapor <ad> çalıştırır; başarı = 'OK:' satırı."""
    r = subprocess.run([sys.executable, RAPOR_SCRIPT, "--rapor", rapor["ad"]],
                       capture_output=True, text=True,
                       env=dict(os.environ, TZ="Europe/Istanbul"), timeout=3600)
    out = (r.stdout or "").strip()
    if r.returncode == 0 and out.startswith("OK:"):
        log(f"[{rapor['ad']}] rapor üretildi: {out.splitlines()[0]}")
        return True
    log(f"[{rapor['ad']}] rapor HATASI: {out} {r.stderr.strip()[-300:]}")
    return False


def push_to_github(cfg, rapor):
    """Raporun HTML'ini Pages reposuna PUT eder (varsa sha ile günceller)."""
    with open(rapor["html"], encoding="utf-8") as f:
        html = f.read()
    if rapor["github_path"].endswith(".html") and "<!doctype" not in html.lower():
        bas = '<!doctype html><html lang="tr">'
        if "<meta charset" not in html.lower():
            bas += ('<meta charset="utf-8">'
                    '<meta name="viewport" content="width=device-width, initial-scale=1">')
        html = bas + html + "</html>"
    api_path = f"repos/{cfg['github_repo']}/contents/{rapor['github_path']}"
    sha = None
    r = subprocess.run([GH, "api", api_path, "--jq", ".sha"], capture_output=True, text=True)
    if r.returncode == 0:
        sha = r.stdout.strip()
    payload = {"message": f"{rapor['github_path']} {today_tag()}",
               "content": base64.b64encode(html.encode()).decode()}
    if sha:
        payload["sha"] = sha
    pf = os.path.join(HERE, "output", f"gh_payload_{rapor['ad']}.json")
    os.makedirs(os.path.dirname(pf), exist_ok=True)
    with open(pf, "w") as f:
        json.dump(payload, f)
    r = subprocess.run([GH, "api", "-X", "PUT", api_path, "--input", pf],
                       capture_output=True, text=True)
    os.remove(pf)
    if r.returncode != 0:
        log(f"[{rapor['ad']}] GitHub yükleme HATASI: {r.stderr.strip()[:300]}")
        return False
    log(f"[{rapor['ad']}] GitHub Pages güncellendi: {rapor['github_path']}")
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
    dosyalar = [
        {"ad": "dashboard", "html": os.path.join(SITE_DIR, "index.html"),
         "github_path": "index.html"},
        {"ad": "status", "html": os.path.join(SITE_DIR, "report_status.json"),
         "github_path": "report_status.json"},
    ]
    sonuclar = [push_to_github(cfg, rapor) for rapor in dosyalar]
    return all(sonuclar)


def fmt_tl(v):
    a, isaret = abs(v), "−" if v < 0 else "+"
    if a >= 1e9:
        return f"{isaret}{a/1e9:,.2f} mlr TL".replace(",", ".")
    if a >= 1e6:
        return f"{isaret}{a/1e6:,.1f} mn TL".replace(",", ".")
    return f"{isaret}{a:,.0f} TL".replace(",", ".")


def rapor_ozeti(rapor):
    """'64 fon · son işlem günü 31.07.2026: +1,2 mlr TL net giriş' satırı."""
    try:
        import tefas_secili as ts
        with open(rapor["cache"], encoding="utf-8") as f:
            ob = json.load(f)
        akis, gunler, _, _ = ts.akis_serisi(ob)
        son = gunler[-1]
        toplam = sum(akis.get(son, {}).values())
        g, a, y = son.split("-")[2], son.split("-")[1], son.split("-")[0]
        return (f"{len(ob['fon'])} fon · son işlem günü {g}.{a}.{y}: "
                f"{fmt_tl(toplam)} net {'giriş' if toplam >= 0 else 'çıkış'}")
    except Exception as e:
        log(f"[{rapor['ad']}] özet çıkarılamadı: {e}")
        return "Günlük / haftalık / aylık net giriş-çıkış"


def build_message(cfg, raporlar):
    msg = EmailMessage()
    msg["Subject"] = cfg["subject"]
    msg["From"] = formataddr((cfg.get("sender_name", ""), cfg["sender"]))
    msg["To"] = ", ".join(cfg["recipients"])
    msg["Date"] = formatdate(localtime=True)

    ozetler = [(r["mail_ad"], r["url"], rapor_ozeti(r)) for r in raporlar]
    msg.set_content("\n".join(f"{ad}: {url}\n{ozet}\n" for ad, url, ozet in ozetler)
                    + "\n\n\n\nSaygılarımla,")
    govde = "".join(
        f'<p><a href="{url}">{ad}</a><br>'
        f'<span style="color:#808080;font-size:12px">{ozet}</span></p>'
        for ad, url, ozet in ozetler)
    msg.add_alternative(f"""\
<html><body style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;font-size:14px;color:#222">
{govde}
<p style="color:#808080;font-size:12px">Net akış = (o günün tedavüldeki pay sayısı − önceki işlem gününün pay sayısı) × o günün pay fiyatı.
Fiyat hareketinden gelen büyüklük değişimi tutara girmez.</p>
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
    raporlar = rapor_configleri(cfg)

    if example:
        cfg = dict(cfg)
        cfg["recipients"] = [cfg["sender"]]
        cfg["subject"] = "[ÖRNEK] " + cfg["subject"]

    # Hafta sonu koruması: launchd kaçırılan cuma işini cumartesi açılışta tetikleyebiliyor.
    if dt.date.today().weekday() >= 5 and not force and not example:
        log(f"bugün hafta sonu ({today_tag()}), mail gönderilmiyor. Zorlamak için: --force")
        return 0

    if already_sent_today() and not force and not dry and not example:
        log(f"bugün ({today_tag()}) zaten gönderilmiş, atlanıyor. Tekrar için: --force")
        return 0

    # Önce bütün zorunlu raporları üret. Kısmi başarıda sabit URL'lerdeki eski
    # içerik güncelmiş gibi dağıtılmasın diye yayın ve mail fail-closed çalışır.
    uretim = [(rapor, generate_report(rapor)) for rapor in raporlar]

    basarisiz = [rapor["ad"] for rapor, ok in uretim if not ok]
    if basarisiz:
        log("HATA: zorunlu raporlar tazelenemedi; yayın/mail engellendi: "
            + ", ".join(basarisiz))
        failures = {
            rapor["github_path"]: f"{rapor['ad']} veri üretimi başarısız"
            for rapor, ok in uretim if not ok
        }
        if not (dry or no_push) and allow_publish and not publish_dashboard(cfg, failures):
            log("HATA: başarısızlık durumu dashboard'a yayımlanamadı")
        return 4

    if dry or no_push:
        mod = "DRY-RUN" if dry else "NO-PUSH"
        log(f"[{mod}] yükleme/mail yok. Alıcılar: {', '.join(cfg['recipients'])} | "
            f"Konu: {cfg['subject']} | Raporlar: {', '.join(r['ad'] for r in raporlar)}")
        return 0

    if not allow_publish and not example:
        log("GÜVENLİK: allow_publish=false; GitHub yayını kapalı")
        return 3

    if not example:
        yayin_sonuclari = [push_to_github(cfg, rapor) for rapor in raporlar]
        if not all(yayin_sonuclari):
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

        msg = build_message(cfg, raporlar)
        with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=60) as s:
            s.starttls(context=ssl_context())
            s.login(cfg["sender"], pw)
            s.send_message(msg)
        if not example:
            mark_sent()
    finally:
        if claimed:
            release_send_claim()
    log(f"{'ÖRNEK mail' if example else 'mail gönderildi'}: "
        f"{len(cfg['recipients'])} alıcı → {', '.join(cfg['recipients'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
