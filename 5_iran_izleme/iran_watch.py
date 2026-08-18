#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
İran–ABD savaşı izleme botu — ateşkes / saldırı / yaptırım / Hürmüz Boğazı.

Her çalıştığında (LaunchAgent ile 5 dakikada bir) şu kaynakları tarar:
  1. Google News RSS — hedefli sorgular (Reuters, AP, AFP, Bloomberg vb. hepsini
     toplar; haber ajanslarına en hızlı erişim yolu).
  2. Doğrudan RSS — Al Jazeera, BBC Middle East, Jerusalem Post,
     Iran International, Mehr News (İran devlet ajansı), AA, TRT Haber.
  3. OFAC Recent Actions — yaptırım kararlarının birincil kaynağı
     (haberlerden önce buraya düşer).

Yeni ve konuyla ilgili bir başlık bulursa ANINDA mail atar. Görülen başlıklar
state.json'da tutulur, aynı haber ikinci kez gönderilmez.

SMTP şifresi repoda tutulmaz — macOS Keychain'den okunur
(model_portfoy_smtp servisi, diğer otomasyonlarla aynı Gmail App Password).

Kullanım:
  python3 iran_watch.py              # normal tarama (LaunchAgent bunu çağırır)
  python3 iran_watch.py --seed       # ilk kurulum: mevcut haberleri "görüldü"
                                     # işaretle, mail atma
  python3 iran_watch.py --test       # şu an ne bulduysa zorla mail at (state'e
                                     # dokunmaz)
  python3 iran_watch.py --dry-run    # mail atma, bulunanları ekrana yaz
"""
import os
import re
import sys
import ssl
import json
import html
import time
import hashlib
import smtplib
import subprocess
import datetime as dt
import urllib.parse
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage
from email.utils import formataddr, formatdate
from xml.etree import ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "watch_config.json")
STATE = os.path.join(HERE, "state.json")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# ---------------------------------------------------------------- sınıflandırma
# Bir başlığın gönderilmesi için: (a) bir OLAY kalıbı + (b) bir AKTÖR kalıbı
# eşleşmeli. Böylece "Iran football team" tarzı gürültü elenir.

EVENT_PATTERNS = [
    # (kategori, öncelik 0=en yüksek, regex)
    # Sadece OLAY haberleri — müzakere/yorum/piyasa haberi gönderilmez.
    ("HÜRMÜZ", 0, r"strait\s+of\s+hormuz|hormuz\s+strait|\bhormuz\b|hürmüz|hurmuz"),
    ("ATEŞKES BİTTİ", 0, r"(ceasefire|truce)\s*\w*\s*(violat|breach|collaps|broke|"
                         r"broken|shatter|over|ends?|ended|expire|fail)|"
                         r"(resum|restart|renew)\w*\s+(strikes?|attacks?|war|"
                         r"hostilities)|war\s+resumes?|back\s+to\s+war|"
                         r"ateşkes\s*\w*\s*(ihlal|bozul|sona|bitti|çöktü|delin)|"
                         r"savaş\s*(yeniden|tekrar)\s*başla|saldırılar\s+yeniden"),
    # Kalıplar arada kelime olmasına toleranslı: "Ceasefire between US and Iran
    # takes effect" gibi başlıklar kaçmasın. Kesinliği HARD_ACTION/UNREAL sağlıyor.
    ("ATEŞKES", 0, r"(cease[\-\s]?fire|ceasefire|truce|armistice|peace\s+deal|"
                   r"peace\s+agreement)[\w\s,'’\-–]{0,45}"
                   r"(reached|agreed|announced|declared|signed|takes?\s+effect|"
                   r"took\s+effect|begins?|began|holds?|in\s+effect|"
                   r"came\s+into\s+(force|effect))|"
                   r"(agree[sd]?|announce[sd]?|declare[sd]?|sign(s|ed)?|reach(es|ed)?)"
                   r"[\w\s,'’\-–]{0,25}(cease[\-\s]?fire|truce|peace\s+deal)|"
                   r"hostilities\s+end|end\s+to\s+the\s+war|"
                   r"ateşkes[\w\s,'’\-–]{0,30}"
                   r"(ilan|sağlan|imzalan|yürürlü|başla|kabul|varıl|girdi)|"
                   r"(ilan\s+edil|sağlan|imzalan)\w*[\w\s]{0,20}ateşkes|"
                   r"barış\s+anlaşması[\w\s]{0,20}(imzalan|sağlan|varıl)|"
                   r"savaş\s*(sona\s+erdi|bitti)|silah\s*bırak"),
    ("SALDIRI", 0, r"\b(air\s?strike|airstrikes?|strikes?\b|struck|"
                   r"attack(ed|s)\b|bomb(ed|ing|ard)|missile[s]?\s+(fired|launch|hit|"
                   r"strike|attack)|fire[sd]?\s+(\w+\s+){0,2}(missile|rocket|drone)s?|"
                   r"launch(ed|es)\s+(\w+\s+){0,2}(missile|drone|rocket|attack|strike|"
                   r"offensive|operation)s?|drone\s+(attack|strike)|shelling|retaliat\w*|"
                   r"assassinat\w*|killed\s+in\s+(a\s+)?strike|seiz(ed|es|ure)\s+"
                   r"(a\s+|the\s+)?(tanker|ship|vessel)|shot\s+down)\b|"
                   r"saldırdı|saldırı\s+düzenle|vurdu|bombalad|füze\s+(attı|fırlat|"
                   r"isabet)|hava\s+saldırısı|misilleme|suikast|el\s+koydu|düşürdü"),
    ("YAPTIRIM", 1, r"\b(impose[sd]?|announce[sd]?|new|fresh|lift(s|ed)?|ease[sd]?)\s+"
                    r"\w{0,12}\s?(sanction(s)?|embargo)\b|"
                    r"\bsanction(s|ed)\s+(iran|tehran|iranian)\b|"
                    r"\b(designat(ed|ion|ions)|blacklist(ed)?|asset\s+freeze|"
                    r"snapback|secondary\s+sanctions)\b|"
                    r"yaptırım\s*(uygula|getir|karar|paket|kaldır|açıkla)|"
                    r"(yeni|ek)\s+yaptırım|ambargo\s*(uygula|getir|kaldır)|"
                    r"kara\s+listeye|varlık\s+dondur"),
    ("PATLAMA/OLAY", 1, r"\b(explosion|blast|sabotage|fire\s+(at|breaks\s+out))\b|"
                        r"patlama|infilak|sabotaj|yangın\s+çıktı"),
    ("SAVAŞ/TIRMANIŞ", 1, r"\b(declares?\s+war|state\s+of\s+war|"
                          r"(general\s+)?mobiliz(ation|es|ed)|nuclear\s+(strike|test)|"
                          r"launch(es|ed)\s+(an\s+)?(offensive|operation))\b|"
                          r"savaş\s+ilan|seferberlik\s+ilan|nükleer\s+(deneme|saldırı)"),
]

# ÇEKİRDEK aktör: haber İran veya Hürmüz'le doğrudan ilgili olmalı.
# (Yoksa Gazze/Ukrayna gibi başka çatışmaların ateşkes haberleri de içeri sızıyor.)
CORE_RE = re.compile(
    r"\biran(ian|ians)?\b|\btehran\b|\bislamic\s+republic\b|\birgc\b|"
    r"\brevolutionary\s+guard\b|\bkhamenei\b|\bpezeshkian\b|\baraghchi\b|"
    r"\bstrait\s+of\s+hormuz\b|\bhormuz\b|"
    r"i̇ran|iran|tahran|hürmüz|hurmuz|humeyni|hamaney|devrim\s+muhafız",
    re.IGNORECASE)

# İkincil aktör: bunlar tek başına yetmez, ABD/gemi/üs bağlamıyla birlikte sayılır
SECONDARY_RE = re.compile(
    r"\bisrael(i)?\b|\bidf\b|\bnetanyahu\b|\bhouthi(s)?\b|\bhezbollah\b|"
    r"\byemen(i)?\b|\biraq(i)?\b|\blebanon\b|\bsyria\b|\bqatar\b|\bbahrain\b|"
    r"\bpersian\s+gulf\b|\bgulf\s+of\s+oman\b|\boman\b|\bred\s+sea\b|"
    r"i̇srail|israil|husi|hizbullah|yemen|irak|lübnan|suriye|katar|umman|"
    r"basra\s+körfez|kızıldeniz",
    re.IGNORECASE)

US_RE = re.compile(
    r"\b(u\.s\.|us|usa|united\s+states|america(n|ns)?|washington|pentagon|trump|"
    r"white\s+house|centcom|navy|warship|carrier|tanker|oil\s+tanker|"
    r"(military|air)\s+base|embassy)\b|abd|amerika|washington|beyaz\s+saray|"
    r"pentagon|tanker|savaş\s+gemisi|üs(sü)?\b",
    re.IGNORECASE)

# Spor/kültür + ansiklopedi/analiz/köşe yazısı gürültüsü — tamamen ele
NOISE_RE = re.compile(
    r"\b(football|soccer|volleyball|wrestling|world\s+cup|olympic|match|"
    r"box\s+office|film|movie|recipe|horoscope|celebrity|britannica|wikipedia|"
    r"explained|explainer|what\s+to\s+know|everything\s+you\s+need|timeline|"
    r"opinion|editorial|op-?ed|column|podcast|photos?\s+of\s+the|"
    r"here'?s\s+what|quiz|obituary)\b|"
    r"\b((in|on)\s+(the\s+|international\s+)?media|media\s+review|"
    r"press\s+review|roundup|round-?up|"
    r"newsletter|briefing\b|live\s+updates?|as\s+it\s+happened)\b|"
    r"futbol|voleybol|güreş|maç\b|dizi\b|burç|köşe\s+yazı|kim\s+kimdir|"
    r"ne\s+demek|nedir\?|kaç\s+kilometre|dünya\s+basınında|basın\s+turu|"
    r"canlı\s+anlatım|gün\s+gün\b",
    re.IGNORECASE)

# GERÇEKLEŞMİŞ HAREKET: haberde fiilen olmuş bir eylem olmalı.
# "İran Hürmüz'ü kapatabilir" değil, "İran Hürmüz'ü kapattı".
HARD_ACTION_RE = re.compile(
    r"\b(struck|strikes\s+(on|at|hit)?|hit|hits|attacked|attacks\b|bombed|"
    r"launched|fired|killed|kills|wounded|shot\s+down|downed|seized|seizes|"
    r"captured|detained|boarded|sank|sunk|damaged|exploded|intercepted|"
    r"closed|closes|shut|shuts|reopened|reopens|opened|opens|blocked|blocks|"
    r"mined|halted|halts|suspended|suspends|banned|bans|"
    r"signed|signs|took\s+effect|takes\s+effect|came\s+into\s+(force|effect)|"
    r"imposed|imposes|lifted|lifts|designated|designates|sanctioned|"
    r"began|begins|started|starts|collapsed|collapses|violated|violates|"
    r"breached|resumed|resumes|withdrew|withdraws|evacuated|"
    r"announced|announces|declared|declares|agreed|reached)\b|"
    r"\b(passed|passes|transit(s|ed|ing)?|crossed|crosses|sailed|sails|"
    r"entered|enters|departed|docked|arrived|arrives|resumed\s+traffic|"
    r"reopening|first\s+(ship|tanker|vessel)s?)\b|"
    r"\b(explosion|blast|fire\s+at|casualt(y|ies)|killed|dead|wounded|"
    r"closure|shutdown|strike\s+on|attack\s+on)\b|"
    r"vurdu|vuruldu|vurulan|saldırdı|saldırı\s+düzenle|bombalad|"
    r"attı|fırlattı|öldürdü|öldü|yaraland|düşürdü|düşürül|el\s+koydu|"
    r"alıkoydu|batırdı|hasar\s+gör|patlad|patlama|imzaland|imzaladı|"
    r"yürürlüğe\s+gir|kapattı|kapandı|kapatıld|açtı|açıldı|başladı|"
    r"sona\s+erdi|ihlal\s+etti|çöktü|uyguladı|getirdi|kaldırdı|"
    r"ilan\s+etti|duyurdu|açıkladı|karar\s+aldı|tahliye|"
    r"geçti|geçiş\s+başla|seyret|demirledi|ulaştı|vardı|girdi|çıktı|"
    r"gerçekleşti|yapıldı|düzenlendi|ilk\s+(gemi|tanker)|"
    # Türkçe belirli geçmiş zaman eki (-dı/-di/-du/-dü/-tı/-ti/-tu/-tü):
    # başlıkta olmuş bitmiş eylemin en güçlü işareti. Niyet/iddia kalıpları
    # UNREAL_RE'de ayrıca eleniyor.
    r"\b\w{3,}(dı|di|du|dü|tı|ti|tu|tü)\b",
    re.IGNORECASE)

# NİYET / İDDİA / İHTİMAL — henüz olmamış, gönderme.
UNREAL_RE = re.compile(
    r"\b(could|may|might|would|possibl(e|y)|potential(ly)?|likely|"
    r"plans?\s+to|planning\s+to|threaten(s|ed|ing)?\s+to|threat\s+of|"
    r"expected\s+to|set\s+to|due\s+to\s+\w+|about\s+to|ready\s+to|"
    r"prepar(es|ed|ing)\s+to|vow(s|ed)?\s+to|pledge[sd]?\s+to|"
    r"urge[sd]?|calls?\s+(for|on)|hope[sd]?|consider(s|ing)|weighs|mulls|"
    r"demand(s|ed)?|propose[sd]?|offer(s|ed)\s+to|seeks?|"
    r"deny|denies|denied|reject(s|ed)?|claim(s|ed)?|alleged(ly)?|"
    r"reportedly|rumou?r|speculat\w*|fears?\s+of|warns?\s+of|risk\s+of|"
    r"if\s+iran|would\s+be)\b|"
    r"olabilir|edebilir|abilir\b|tehdit\s+et|planlıyor|bekleniyor|"
    r"bekleniyordu|çağrı(sı|da|sında)?\b|umut|umuyor|iddia|iddias|yalanla|"
    r"reddet|hazırlanıyor|talep\s+et|önerdi|öneri\b|sinyal|olasılığ|"
    r"ihtimal|riski\b|endişe|korkusu|resti\b|uyardı|uyarısı|"
    r"gerekiyor|isteniyor|görüşülüyor|tartışıl",
    re.IGNORECASE)

# Piyasa tepkisi / yorum — olay değil, öncelik 2'ye düşür
MARKET_RE = re.compile(
    r"\b(oil\s+price|crude|brent|wti|stocks?|shares?|bourse|equit(y|ies)|"
    r"futures|etf|investors?|market(s)?\s+(rally|slip|slide|rise|fall|jump)|"
    r"rally|selloff|sell-off|gold\s+price|yields?|dollar\s+index|"
    r"analysts?\s+say|outlook)\b|"
    r"borsa|hisse|piyasa(lar)?|petrol\s+fiyat|altın\s+fiyat|dolar\s+kur|endeks",
    re.IGNORECASE)

PRIORITY_LABEL = {0: "🚨 KRİTİK", 1: "⚠️ ÖNEMLİ", 2: "ℹ️ TAKİP"}

try:
    from zoneinfo import ZoneInfo
    TR_TZ = ZoneInfo("Europe/Istanbul")
except Exception:                                   # tzdata yoksa sabit +03
    TR_TZ = dt.timezone(dt.timedelta(hours=3), "TRT")


def now_tr():
    return dt.datetime.now(TR_TZ)


def parse_when(item):
    """Haberin yayın zamanını Türkiye saatine çevirir. Çözülemezse None."""
    p = (item.get("published") or "").strip()
    if not p:
        return None
    if re.fullmatch(r"\d{8}", p):                   # OFAC: 20260803
        try:
            d = dt.datetime.strptime(p, "%Y%m%d")
            return d.replace(tzinfo=TR_TZ)
        except ValueError:
            return None
    try:
        from email.utils import parsedate_to_datetime
        d = parsedate_to_datetime(p)
    except Exception:
        return None
    if d is None:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(TR_TZ)


def age_minutes(item):
    """Haber kaç dakika önce yayınlandı? Bilinmiyorsa None.
    İleri tarihli (kaynak saat dilimini yanlış etiketlemiş) ise 0 sayılır."""
    w = parse_when(item)
    if w is None:
        return None
    return max(0.0, (now_tr() - w).total_seconds() / 60.0)


TR_SOURCES = ("Anadolu Ajansı", "TRT Haber")
_TR_CHARS = re.compile(r"[ğşıİĞŞ]")
_TR_WORDS = re.compile(r"\b(ve|ile|için|bir|bu|dedi|oldu|sonra|karşı|"
                       r"açıklama|boğazı|savaş)\b", re.IGNORECASE)


def is_turkish(item):
    if item["source"] in TR_SOURCES:
        return True
    if "Google News" in item["source"] and item.get("lang") == "tr":
        return True
    t = item["title"]
    return bool(_TR_CHARS.search(t) and _TR_WORDS.search(t))


def translate_tr(text, timeout=12):
    """Başlığı Türkçeye çevirir. Başarısızsa orijinali döndürür."""
    try:
        url = ("https://translate.googleapis.com/translate_a/single?client=gtx"
               "&sl=auto&tl=tr&dt=t&q=" + urllib.parse.quote(text))
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=ssl_context()) as r:
            data = json.load(r)
        out = "".join(seg[0] for seg in data[0] if seg and seg[0])
        return out.strip() or text
    except Exception as e:
        log(f"çeviri hatası: {type(e).__name__}")
        return text


def add_turkish_titles(hits):
    """Mail atılacak başlıkları Türkçeleştirir (sadece bunlar — az sayıda)."""
    todo = [h for h in hits if not is_turkish(h)]
    if not todo:
        return
    with ThreadPoolExecutor(max_workers=6) as ex:
        for h, tr in zip(todo, ex.map(lambda x: translate_tr(x["title"]), todo)):
            if tr and tr != h["title"]:
                h["title_tr"] = tr


def fmt_when(item):
    """Mailde gösterilecek Türkiye saati."""
    w = parse_when(item)
    if w is None:
        return ""
    if w.date() == now_tr().date():
        return f"bugün {w:%H:%M} TSİ"
    return f"{w:%d.%m %H:%M} TSİ"


def log(msg):
    print(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} — {msg}", flush=True)


def load_config():
    with open(CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    # Repo public olduğu için alıcı adresleri dosyada tutulmuyor; bulutta
    # MAIL_TO secret'ından, yerelde watch_config.json'dan gelir.
    env_to = os.environ.get("MAIL_TO", "").strip()
    if env_to:
        cfg["recipients"] = [a.strip() for a in env_to.split(",") if a.strip()]
    if not cfg.get("recipients"):
        raise SystemExit("HATA: alıcı yok — MAIL_TO ayarla veya watch_config.json'a ekle")
    return cfg


def ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def keychain_password(service, account):
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except subprocess.CalledProcessError:
        return None


# ------------------------------------------------------------------ state
def load_state():
    """state: {"stories": [[ts, [token,...]], ...], "last_mail": ts}"""
    base = {"stories": [], "last_mail": 0}
    if not os.path.exists(STATE):
        return base
    try:
        with open(STATE, encoding="utf-8") as f:
            s = json.load(f)
        base.update(s)
        return base
    except Exception:
        return base


def save_state(state, retention_days, max_stories=2500):
    cutoff = time.time() - retention_days * 86400
    st = [s for s in state["stories"] if s[0] > cutoff]
    state["stories"] = st[-max_stories:]
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, STATE)


def known_story(sig, sigsets):
    for s in sigsets:
        if same_story(sig, s):
            return True
    return False


STOPWORDS = set("""
about after again against all also amid among and are back been before being
between both but can could day days did does down each even every for from
gets had has have here how into its just like made make many more most much
must near new news now off only other our out over pass reports said say says
see set she should since some such than that the their them then there these
they this those through time told too under until update very was were what
when where which while who why will with would year years your
after amid new latest breaking live report analysis opinion video watch photos
bir bu ve ile için daha çok gibi olarak sonra kadar ancak ama dedi diyor oldu
olan olduğunu var yok üzere karşı önce göre son yeni haber canlı ise the
""".split())


def _tokens(title):
    """Başlığı anlamlı kelime kümesine indirger — aynı olayın farklı kaynaklardaki
    versiyonlarını eşleştirmek için."""
    t = re.sub(r"\s+[-–|]\s+[^-–|]{2,45}$", "", title)  # ' - Reuters' ekini at
    t = re.sub(r"[^a-z0-9ğüşöçı ]", " ", t.lower()
               .replace("i̇", "i").replace("İ", "i"))
    out = set()
    for w in t.split():
        if len(w) < 4 or w in STOPWORDS:
            continue
        if len(w) > 5:  # kaba kök: strike/strikes/striking -> strik
            w = re.sub(r"(ings?|ed|es|s|ler|lar|leri|ları|nin|nın)$", "", w)
        out.add(w)
    return frozenset(out)


def item_key(title):
    """Birebir aynı başlık için hızlı anahtar."""
    return hashlib.sha1(" ".join(sorted(_tokens(title))).encode("utf-8")).hexdigest()[:16]


def same_story(a, b, threshold=0.42):
    """Jaccard benzerliği — aynı olayın farklı başlıkları mı?"""
    if not a or not b:
        return False
    inter = len(a & b)
    if inter < 2:
        return False
    return inter / len(a | b) >= threshold


# ------------------------------------------------------------------ fetch
def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/rss+xml, application/xml, text/xml, text/html;q=0.9",
        "Accept-Language": "en-US,en;q=0.9,tr;q=0.8",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as r:
            return r.read()
    except Exception as e:
        log(f"fetch hatası ({url[:70]}): {type(e).__name__} {e}")
        return None


def strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


def parse_rss(raw, source):
    """RSS/Atom -> [{title, link, source, published}]"""
    out = []
    if not raw:
        return out
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        # bozuk XML: kaba regex fallback
        for m in re.finditer(r"<item>(.*?)</item>", raw.decode("utf-8", "ignore"), re.S):
            blk = m.group(1)
            t = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", blk, re.S)
            l = re.search(r"<link>(.*?)</link>", blk, re.S)
            if t:
                out.append({"title": strip_tags(t.group(1)),
                            "link": strip_tags(l.group(1)) if l else "",
                            "source": source, "published": ""})
        return out

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    nodes = root.iter("item")
    for it in nodes:
        title = strip_tags((it.findtext("title") or ""))
        link = strip_tags((it.findtext("link") or ""))
        pub = strip_tags((it.findtext("pubDate") or ""))
        if title:
            out.append({"title": title, "link": link, "source": source, "published": pub})
    if not out:  # Atom
        for it in root.findall("atom:entry", ns):
            title = strip_tags(it.findtext("atom:title", "", ns))
            le = it.find("atom:link", ns)
            link = le.get("href") if le is not None else ""
            pub = strip_tags(it.findtext("atom:updated", "", ns))
            if title:
                out.append({"title": title, "link": link, "source": source,
                            "published": pub})
    return out


def fetch_ofac():
    """OFAC Recent Actions — yaptırım kararlarının birincil kaynağı."""
    raw = fetch("https://ofac.treasury.gov/recent-actions")
    if not raw:
        return []
    page = raw.decode("utf-8", "ignore")
    out = []
    seen_links = set()
    for m in re.finditer(r'<a href="(/recent-actions/(\d{8}))"[^>]*>(.*?)</a>', page, re.S):
        path, datestr, title = m.group(1), m.group(2), strip_tags(m.group(3))
        if not title or path in seen_links:
            continue
        seen_links.add(path)
        out.append({
            "title": f"OFAC: {title} ({datestr[6:8]}.{datestr[4:6]}.{datestr[0:4]})",
            "link": "https://ofac.treasury.gov" + path,
            "source": "OFAC / US Treasury",
            "published": datestr,
            "force_category": ("YAPTIRIM", 0) if re.search(r"iran", title, re.I) else None,
        })
    return out


def gather(cfg):
    """Tüm kaynakları paralel çek."""
    jobs = []
    for cat, q in cfg["google_news_queries"]:
        tr = bool(re.search(r"[çğıöşüİ]", q))
        url = ("https://news.google.com/rss/search?q="
               + urllib.parse.quote(q)
               + ("&hl=tr&gl=TR&ceid=TR:tr" if tr else "&hl=en-US&gl=US&ceid=US:en"))
        jobs.append(("gnews", f"Google News ({cat})", url, "tr" if tr else "en"))
    for name, url in cfg["feeds"]:
        jobs.append(("rss", name, url, "tr" if name in TR_SOURCES else "en"))

    items = []
    with ThreadPoolExecutor(max_workers=12) as ex:
        futs = {ex.submit(fetch, url): (kind, name, lang)
                for kind, name, url, lang in jobs}
        futs[ex.submit(fetch_ofac_wrapper)] = ("ofac", "OFAC", "en")
        for fut, (kind, name, lang) in futs.items():
            try:
                res = fut.result(timeout=40)
            except Exception as e:
                log(f"kaynak hatası ({name}): {e}")
                continue
            if kind == "ofac":
                got = res or []
            elif kind == "gnews":
                # Google News alaka/tazelik sırasına göre verir; uzun kuyruğu alma
                got = parse_rss(res, name)[:40]
            else:
                got = parse_rss(res, name)
            for it in got:
                it["lang"] = lang
            items.extend(got)
    return items


def fetch_ofac_wrapper():
    return fetch_ofac()


# ------------------------------------------------------------------ eşleştirme
def classify(item):
    """(kategori, öncelik) döner; ilgisizse None."""
    forced = item.get("force_category")
    if forced:
        return tuple(forced)
    text = item["title"]
    if NOISE_RE.search(text):
        return None

    # İlgi kapısı: ya doğrudan İran/Hürmüz, ya da (bölge ülkesi + ABD/gemi/üs)
    if not CORE_RE.search(text):
        if not (SECONDARY_RE.search(text) and US_RE.search(text)):
            return None

    best = None
    for cat, prio, pat in EVENT_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            if best is None or prio < best[1]:
                best = (cat, prio)
    if best is None:
        return None

    # Piyasa tepkisi / analiz — olay değil, hiç gönderme
    if MARKET_RE.search(text):
        return None

    # GERÇEK HAREKET KAPISI: fiilen olmuş bir eylem yoksa gönderme.
    if not HARD_ACTION_RE.search(text):
        return None
    # Niyet/iddia/ihtimal kalıbı varsa gönderme ("kapatabilir", "tehdit etti").
    if UNREAL_RE.search(text):
        return None
    return best


def collect_new(cfg, state, ignore_state=False):
    """Tüm başlıkları toplar, ilgili olanları sınıflar, HABER BAZINDA tekilleştirir.
    Aynı olayı 20 farklı gazeteden yakaladığımız için tek başlığa indiriyoruz."""
    items = gather(cfg)
    log(f"{len(items)} başlık toplandı")

    old_sigs = [] if ignore_state else [frozenset(s[1]) for s in state["stories"]]
    max_age = cfg.get("max_age_minutes", 90)
    batch_sigs = []
    hits = []
    stale = unknown = 0
    for it in items:
        res = classify(it)
        if not res:
            continue
        # Tazelik: sadece YENİ çıkan haber gönderilir. Yayın saati bilinmiyorsa
        # eski olup olmadığını doğrulayamayız — göndermiyoruz.
        age = age_minutes(it)
        if age is None:
            unknown += 1
            continue
        if age > max_age:
            stale += 1
            continue
        it["age_min"] = age
        sig = _tokens(it["title"])
        if len(sig) < 3:
            continue
        if known_story(sig, batch_sigs):      # bu turda zaten var
            continue
        if known_story(sig, old_sigs):        # daha önce mail atıldı
            continue
        batch_sigs.append(sig)
        it["category"], it["priority"] = res
        it["sig"] = sig
        hits.append(it)
    if stale or unknown:
        log(f"{stale} haber {max_age} dk'dan eski, {unknown} haber tarihsiz — elendi")
    hits.sort(key=lambda x: (x["priority"], x.get("age_min", 999)))
    return items, hits, batch_sigs


# ------------------------------------------------------------------ mail
def build_message(cfg, hits, tag=""):
    top = hits[0]
    cats = []
    for h in hits:
        if h["category"] not in cats:
            cats.append(h["category"])
    tnow = now_tr()
    flag = "🚨" if top["priority"] == 0 else ("⚠️" if top["priority"] == 1 else "ℹ️")
    subject = (f"{tag}{flag} {' · '.join(cats[:3])} | "
               f"{len(hits)} yeni gelişme — {tnow:%H:%M} TSİ")

    lines = [f"{tnow:%d.%m.%Y %H:%M} TSİ — {len(hits)} yeni gelişme\n"]
    for h in hits:
        baslik = h.get("title_tr") or h["title"]
        lines.append(f"[{PRIORITY_LABEL[h['priority']]} · {h['category']}] {baslik}")
        if h.get("title_tr"):
            lines.append(f"   ({h['title']})")
        when = fmt_when(h)
        lines.append(f"   {h['source']}{' · ' + when if when else ''} — {h['link']}\n")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((cfg.get("sender_name", ""), cfg["sender"]))
    msg["To"] = ", ".join(cfg["recipients"])
    msg["Date"] = formatdate(localtime=True)
    if top["priority"] == 0:
        msg["X-Priority"] = "1"
        msg["Importance"] = "high"
    msg.set_content("\n".join(lines))

    colors = {0: "#c62828", 1: "#e65100", 2: "#546e7a"}
    blocks = []
    current = None
    for h in hits:
        if h["category"] != current:
            current = h["category"]
            blocks.append(
                f'<h3 style="margin:22px 0 8px;font-size:14px;letter-spacing:.4px;'
                f'color:{colors[h["priority"]]};border-bottom:1px solid #e0e0e0;'
                f'padding-bottom:5px">{PRIORITY_LABEL[h["priority"]]} · {current}</h3>')
        link = html.escape(h["link"] or "#")
        baslik = h.get("title_tr") or h["title"]
        orij = (f'<span style="color:#9e9e9e;font-size:12px;font-style:italic">'
                f'{html.escape(h["title"])}</span><br>' if h.get("title_tr") else "")
        blocks.append(
            f'<p style="margin:0 0 12px;line-height:1.45">'
            f'<a href="{link}" style="color:#0b57d0;text-decoration:none;'
            f'font-weight:600">{html.escape(baslik)}</a><br>{orij}'
            f'<span style="color:#808080;font-size:12px">{html.escape(h["source"])}'
            f'{" · " + html.escape(fmt_when(h)) if fmt_when(h) else ""}'
            f'{" · %d dk önce" % round(h["age_min"]) if h.get("age_min") is not None else ""}'
            f'</span></p>')

    msg.add_alternative(f"""\
<html><body style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;
font-size:14px;color:#222;max-width:640px">
<p style="margin:0 0 4px;font-size:12px;color:#808080">
İran–ABD savaş izleme · {tnow:%d.%m.%Y %H:%M} TSİ · {len(hits)} yeni gelişme</p>
{''.join(blocks)}
<p style="margin-top:26px;font-size:11px;color:#9e9e9e;border-top:1px solid #eee;
padding-top:10px">Kaynaklar: Google News (Reuters/AP/AFP/Bloomberg), Al Jazeera,
BBC, Jerusalem Post, Iran International, Mehr News, AA, TRT Haber,
OFAC Recent Actions. 5 dakikada bir taranır; sadece son
{cfg.get("max_age_minutes", 90)} dakika içinde yayınlanmış olay haberleri gönderilir.
Tüm saatler Türkiye saati (TSİ).</p>
</body></html>""", subtype="html")
    return msg


def smtp_password(cfg):
    """Bulutta (GitHub Actions) ortam değişkeni, yerelde Keychain."""
    env = os.environ.get("SMTP_PASSWORD")
    if env:
        return env.strip()
    return keychain_password(cfg["keychain_service"], cfg["sender"])


def send(cfg, msg):
    pw = smtp_password(cfg)
    if not pw:
        log("HATA: SMTP şifresi yok — ne SMTP_PASSWORD ortam değişkeni "
            f"ne de Keychain ({cfg['keychain_service']})")
        return False
    with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=60) as s:
        s.starttls(context=ssl_context())
        s.login(cfg["sender"], pw)
        s.send_message(msg)
    log(f"mail gönderildi → {', '.join(cfg['recipients'])}")
    return True


# ------------------------------------------------------------------ main
def main():
    seed = "--seed" in sys.argv
    test = "--test" in sys.argv
    dry = "--dry-run" in sys.argv
    cfg = load_config()
    state = load_state()

    items, hits, batch_sigs = collect_new(cfg, state, ignore_state=(seed or test))
    log(f"{len(hits)} yeni haber (tekilleştirilmiş)")

    now = time.time()

    if seed:
        for sig in batch_sigs:
            state["stories"].append([now, sorted(sig)])
        state["last_mail"] = now
        save_state(state, cfg["state_retention_days"])
        log(f"seed tamam: {len(state['stories'])} haber 'görüldü' işaretlendi, "
            f"mail atılmadı")
        return 0

    if not hits:
        log("yeni gelişme yok")
        return 0

    if dry:
        for h in hits[:40]:
            print(f"  [{h['priority']}·{h['category']}] {h['title']}  <{h['source']}>")
        return 0

    # Sessizlik penceresi: kritik (öncelik 0) haber her zaman anında gider;
    # daha düşük öncelikliler için art arda mail atmamak adına bekleme uygulanır.
    critical = any(h["priority"] == 0 for h in hits)
    cooldown = cfg.get("cooldown_minutes", 25) * 60
    if not test and not critical and (now - state.get("last_mail", 0)) < cooldown:
        left = int((cooldown - (now - state["last_mail"])) / 60)
        log(f"kritik haber yok ve son mailden {left} dk daha var — bu tur bekleniyor")
        return 0

    cap = cfg.get("max_items_per_mail", 20)
    shown = hits[:cap]
    if len(hits) > cap:
        log(f"{len(hits)} haber bulundu, ilk {cap} tanesi maillenecek")

    add_turkish_titles(shown)
    msg = build_message(cfg, shown, tag="[TEST] " if test else "")
    ok = send(cfg, msg)
    if ok and not test:
        for h in hits:
            state["stories"].append([now, sorted(h["sig"])])
        state["last_mail"] = now
        save_state(state, cfg["state_retention_days"])
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
