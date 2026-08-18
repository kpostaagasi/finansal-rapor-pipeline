# Otomatik Rapor ve Mail Otomasyonları

Yahoo Finance, ABD Hazinesi ve TEFAS verisinden dört finansal rapor üreten
otomasyonların kaynak kodu. Mail ve yayın birbirinden bağımsız güvenlik kapılarıyla
kapalı tutulabilir.

Proje Python 3.14 venv'iyle doğrulanmıştır. Kurulum:

```bash
python3.14 -m venv .venv
env -u PYTHONPATH .venv/bin/python -m pip install -r requirements.txt
env -u PYTHONPATH .venv/bin/python verify_runtime.py
```

Üçüncü parti bağımlılık yalnızca `requests`, `matplotlib`, `openpyxl`, `certifi` —
gerisi standart kütüphane.

---

## Kurulum: üç ortak ayar

### 1. SMTP şifresi

Şifreler kodda tutulmuyor, macOS Keychain'den okunuyor. Gmail için normal şifre
değil **16 haneli App Password** gerekir:

```bash
security add-generic-password -s model_portfoy_smtp -a gonderen@example.com -w 'APP_PASSWORD'
```

Keychain servis adı config'lerdeki `keychain_service` alanıyla eşleşmeli.
macOS dışında çalıştıracaksanız scriptlerdeki `security find-generic-password`
çağrısını ortam değişkeni okumasıyla değiştirmeniz yeterli.

### 2. Alıcılar ve adresler

`mail_config.ornek.json` dosyalarını `mail_config.json` olarak kopyalayıp
doldurun. `KULLANICI/DEPO` ve `/MUTLAK/YOL/...` yer tutucularını kendi
değerlerinizle değiştirin. İlk kurulumda `allow_publish` ve `allow_send`
değerlerini `false` bırakın; doğrulama bittikten sonra ayrı ayrı açın.

### 3. Rapor yayınlama (isteğe bağlı)

1. ve 3. otomasyon üretilen HTML'i GitHub Pages'e yükleyip mailde link olarak
gönderiyor — alıcı ek indirmeden tarayıcıda açabiliyor. Bunun için
[`gh` CLI](https://cli.github.com) kurulu ve `gh auth login` yapılmış olmalı.
Script `gh` ikilisini sistem `PATH`'inden bulur; bulunamazsa eski
`~/.local/bin/gh` yoluna bakar.

Link istemiyorsanız `--no-push` ile çalıştırın, raporlar yalnızca yerelde üretilir.

---

## Otomasyonlar

Hepsinde ortak bayraklar: `--dry-run` (yerelde rapor üretir; yayın ve mail yok),
`--example` (yalnızca gönderene örnek mail), `--force` (günlük kilidi yok sayar),
`--no-push` (yerelde üretir; yayın ve mail yok).

### 1_emtia_tahvil_maili

`gunluk_mail.py` günlük akışı yönetir: raporları üretir → Pages'e yükler → mailler.

`emtia_report.py` veriyi çekip tek dosyalık HTML üretir: 6 emtianın vadeli işlem
eğrisi (WTI `CL`, altın `GC`, gümüş `SI`, platin `PL`, bakır `HG`, alüminyum `ALI`
— Yahoo Finance `v8/finance/chart` kontrat sembolleri) + ABD Hazine getiri eğrisi
(treasury.gov `daily-treasury-rates.csv`) + vadeler arası faiz diferansiyeli.
5 günden eski fiyatlı kontratlar (vadesi geçmiş/likiditesiz) eğriden düşülür.

```bash
env -u PYTHONPATH .venv/bin/python 1_emtia_tahvil_maili/gunluk_mail.py --dry-run
```

Rapor üretimi veya Pages yayını başarısız olursa eski rapor korunur, dashboard hata
durumunu gösterir ve mail **gönderilmez**.

### 2_tefas_altin_akis — 1. maddedeki mailin üçüncü linki

`tefas_akis.py` altın fonlarına net giriş/çıkışı hesaplar.

**Hesap:** net akış = (o günün `tedPaySayisi` − önceki işlem gününün
`tedPaySayisi`) × **o günün** fiyatı; fon bazında hesaplanıp gruplanır. Fiyat
hareketinden gelen büyüklük değişimi hesaba girmez.

“Önceki işlem günü”, rapor evrenindeki bir önceki gerçek TEFAS veri tarihidir.
Bir fon bu iki tarihten birinde gözlem vermediyse akış `0` yapılmaz, ileri taşınmaz
ve sonraki güne yığılmaz; hücre **hesaplanamadı** olarak bırakılır. Grup veya dönem
toplamı eksik hücre içeriyorsa kısmi tutar tam sonuç gibi gösterilmez. Son veri
tarihinde eksik ya da hesaplanamayan fon varsa üretici hata koduyla çıkar ve
yayın/mail zinciri durur.

**Fon evreni kuralı (kritik):** Yatırım fonu tarafı "adında ALTIN geçenler" —
ancak **"ALTINCI"/"ON ALTINCI"** (sıra sayısı) elenir, **"GOLD"** geçenler eklenir
ama **"GOLDEN ..."** elenir. Bu kural 48 fon veriyor ve geçmiş seriyi %0,005
sapmayla yeniden üretiyor. Emeklilik tarafı `eyf_fonlar.json`'daki 16 fon.

```bash
env -u PYTHONPATH .venv/bin/python 2_tefas_altin_akis/tefas_akis.py --bootstrap
env -u PYTHONPATH .venv/bin/python 2_tefas_altin_akis/tefas_akis.py
env -u PYTHONPATH .venv/bin/python 2_tefas_altin_akis/tefas_akis.py --no-fetch
```

Çıktı: `tefas_net_akis.html` + `~/Documents/TEFAS_Altin_Fonlari_Akis.xlsx`
(sayfalar: Özet / Fiyat / Tedavüldeki Pay Sayısı / Net Akış).

### 3_tefas_fon_akis_maili

Hesap yöntemi 2. maddeyle **birebir aynı**; farkı, sonucu grup toplamı yerine fon
bazında göstermesi. Rapor tanımları `raporlar/*.json` içinde, motor
yapılandırılabilir:

```bash
env -u PYTHONPATH .venv/bin/python 3_tefas_fon_akis_maili/tefas_secili.py --rapor altin --bootstrap
env -u PYTHONPATH .venv/bin/python 3_tefas_fon_akis_maili/tefas_secili.py --rapor secili
env -u PYTHONPATH .venv/bin/python 3_tefas_fon_akis_maili/secili_mail.py --dry-run
env -u PYTHONPATH .venv/bin/python 3_tefas_fon_akis_maili/secili_yenile.py --no-push
```

Tüm yerel çıktıları tek bir yayın klasöründe ve ana sayfada toplamak için:

```bash
env -u PYTHONPATH .venv/bin/python build_site.py
```

Dashboard her raporun gerçek veri tarihi, son başarılı üretimi, veri kaynağı,
beklenen/bulunan son-gün kapsamını, hesaplanamayan akışları ve `Güncel` / `Eksik
veri` / `Veri güncel değil` / `Başarısız` durumunu gösterir. Aynı bilgiler
`site/report_status.json` içinde de makinece okunabilir biçimde bulunur.

TEFAS'ın 12 günlük artımlı çekim penceresinden eski revizyonları manuel kontrol etmek
için önce kabul edilmiş cache'i ayrı bir dosyada saklayın, sonra tam çekimle oluşan
cache'i read-only karşılaştırın:

```bash
env -u PYTHONPATH .venv/bin/python reconcile_tefas.py \
  /path/to/kabul-edilmis-cache.json /path/to/tam-cekim-cache.json
```

Komut cache'leri değiştirmez; fark yoksa `0`, fon/gözlem/revizyon farkı varsa `1`
ile çıkar ve ayrıntıları JSON olarak stdout'a yazar.

- `raporlar/altin.json` — TEFAS'ın iki altın filtresinin birebir karşılığı
  (YAT tarafı unvan kuralı 48 fon, EMK tarafı `fonTurAciklama ∈ {Altın Fonu,
  Altın Katılım Fonu}` 16 fon). Eski grup bazlı raporla ortak 395 günde
  **%0,000 sapmayla** aynı sonucu veriyor.
- `raporlar/secili.json` + `fonlar.json` — elle seçilmiş fon listesi.

## Zamanlama

Bu proje için şu anda cron, LaunchAgent veya GitHub Actions zamanlayıcısı kurulu
değildir. Kesin iş günleri/saatleri, eski otomasyonların kapalı olduğu ve test maili
onaylandıktan sonra ayrıca belirlenmelidir. O zamana kadar komutlar manuel çalışır.

---

## TEFAS API notu

TEFAS eski `tefas.gov.tr/api/DB/BindHistoryInfo` uçlarını 2026'da kapattı.
Kullanılan uçlar `https://www.tefas.gov.tr/api/funds/*` — **POST**, JSON gövde,
`Origin`/`Referer` başlıkları `tefas.gov.tr` olmalı, tarayıcı `User-Agent`
gerekiyor. Tarayıcıdan (GET) açılınca 404 döner, bu normaldir.

- `fonGnlBlgSiraliGetir` — fiyat, `tedPaySayisi`, `kisiSayisi`, `portfoyBuyukluk`.
  Gövde TÜM alanları içermeli, yoksa "Index 0 out of bounds" döner.
- `fonYonetimBazliBilgiGetir` — fon türü/yönetim ücreti.

Tek istekte en çok ~28 günlük pencere çekilebiliyor, rate limit ~6 istek/dakika.
Bu yüzden `--bootstrap` uzun sürüyor ve pencere pencere ilerliyor.
