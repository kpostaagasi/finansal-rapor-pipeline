# Otomatik Rapor Üretimi ve Yayını

Yahoo Finance, ABD Hazinesi ve TEFAS verisinden toplam 9 rapor sayfası üreten
otomasyonların kaynak kodu (yayın adları `build_site.py`'deki `REPORTS`
tablosunda tanımlı, tek kaynak). Raporlar GitHub Actions'ta üretilip
`kpostaagasi/finansal-raporlar` deposuna push edilerek GitHub Pages'te
yayınlanır.

Proje Python 3.14 venv'iyle doğrulanmıştır. Kurulum:

```bash
python3.14 -m venv .venv
env -u PYTHONPATH .venv/bin/python -m pip install -r requirements.txt
env -u PYTHONPATH .venv/bin/python verify_runtime.py
```

Üçüncü parti bağımlılık yalnızca `requests` ve `certifi` — gerisi standart
kütüphane.

---

## Otomasyonlar

### 1_emtia_tahvil_maili

`emtia_report.py` veriyi çekip tek dosyalık HTML üretir: 6 emtianın vadeli işlem
eğrisi (WTI `CL`, altın `GC`, gümüş `SI`, platin `PL`, bakır `HG`, alüminyum `ALI`
— Yahoo Finance `v8/finance/chart` kontrat sembolleri) + ABD Hazine getiri eğrisi
(treasury.gov `daily-treasury-rates.csv`) + vadeler arası faiz diferansiyeli.
5 günden eski fiyatlı kontratlar (vadesi geçmiş/likiditesiz) eğriden düşülür.

```bash
env -u PYTHONPATH .venv/bin/python 1_emtia_tahvil_maili/emtia_report.py
```

`main()` fail-closed bir eşik uygular: en az 5 emtia eğrisi ve faiz eğrisi
yoksa 3 deneme sonunda HTML'i **yazmadan** `1` ile çıkar — eski rapor korunur
ve dashboard o kartı `Başarısız` gösterir.

### 3_tefas_fon_akis_maili

`tefas_secili.py` tüm TEFAS raporlarının tek motoru; rapor tanımları
`raporlar/*.json` içinde:

```bash
env -u PYTHONPATH .venv/bin/python 3_tefas_fon_akis_maili/tefas_secili.py --rapor altin --bootstrap
env -u PYTHONPATH .venv/bin/python 3_tefas_fon_akis_maili/tefas_secili.py --rapor secili
env -u PYTHONPATH .venv/bin/python 3_tefas_fon_akis_maili/tefas_secili.py --rapor gruplar --no-fetch
env -u PYTHONPATH .venv/bin/python 3_tefas_fon_akis_maili/tefas_secili.py --rapor altin --yerel-kopya
```

**Hesap:** net akış = (o günün `tedPaySayisi` − önceki işlem gününün
`tedPaySayisi`) × **o günün** fiyatı; fon bazında hesaplanır, grup raporunda
gruplanır. Fiyat hareketinden gelen büyüklük değişimi hesaba girmez.

“Önceki işlem günü”, rapor evrenindeki bir önceki gerçek TEFAS veri tarihidir.
Bir fon bu iki tarihten birinde gözlem vermediyse akış `0` yapılmaz, ileri taşınmaz
ve sonraki güne yığılmaz; hücre **hesaplanamadı** olarak bırakılır. Grup veya dönem
toplamı eksik hücre içeriyorsa kısmi tutar tam sonuç gibi gösterilmez.

**Altın fon evreni kuralı (kritik):** Yatırım fonu tarafı "adında ALTIN
geçenler" — ancak **"ALTINCI"/"ON ALTINCI"** (sıra sayısı) elenir, **"GOLD"**
geçenler eklenir ama **"GOLDEN ..."** elenir. Bu kural 04.09.2026 itibarıyla
**49 fon** veriyor. Emeklilik tarafı **17 fon**: `fonTurAciklama ∈ {Altın Fonu,
Altın Katılım Fonu}` **birleşim** unvan kuralı. Birleşim gerekli çünkü TEFAS,
Garanti Emeklilik'in ALTIN EMEKLİLİK YATIRIM FONU'nu (`EMY`) "Kıymetli
Madenler" olarak sınıflıyor: tür filtresi tek başına bu fonu kaçırıyordu
(04.09.2026'da eklendi).

Evren kod listesiyle değil kuralla belirlendiği için TEFAS'a çıkan yeni bir fon
rapora kendiliğinden girer — GLL (GOLDEN GLOBAL PORTFÖY ALTIN KATILIM FONU)
20.08.2026'da böyle eklendi; kurucu adındaki "GOLDEN" yalnızca "GOLD" dalını
eler, unvandaki "ALTIN" fonu doğru biçimde alır. Sayının sessizce kaymasını
`tests/test_gold_universe.py` engeller: evren değişirse test yeni/düşen kodu
adıyla söyler, incelendikten sonra kilit güncellenir.

**`--yerel-kopya`**: raporun `~/Documents` altındaki yerel kopyasını da yazar.
Varsayılan koşu `~/Documents`'a hiç dokunmaz — rapor zaten repo içine, `site/`'a
ve panoya gidiyor.

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

### Rapor tanımları ve kapsam tipleri

Her rapor `raporlar/<ad>.json` dosyasıyla tanımlanır; `kapsam.tip` alanı fon
evreninin nasıl belirlendiğini söyler:

| `kapsam.tip` | Evren nereden gelir | Kullanan raporlar |
|---|---|---|
| `liste` | Elle seçilmiş kod listesi (`fonlar.json`) | `secili` (25 fon) |
| `altin` | Unvan kuralı ∪ altın emeklilik fon türleri | `altin` (49 + 17 fon) |
| `tur` | TEFAS fon türü (`fonTurAciklama`), istenirse ∪ `unvan_kurali` | `kiymetli_maden`, `para_piyasasi`, `borclanma`, `katilim`, `hisse` |
| `toplam` | Türetilmiş: kaynak raporların önbelleklerini toplar | `gruplar` |

- `raporlar/altin.json` — YAT tarafı unvan kuralı 49 fon, EMK tarafı 17 fon.
  Grup toplamı `gruplar` raporunun ALT-YAT/ALT-EMK satırlarında; ayrı bir
  altın-toplam üreticisi yok.
- `raporlar/secili.json` + `fonlar.json` — elle seçilmiş fon listesi.
- Fon grubu raporları (`tur`): **kıymetli maden 67+25=92**, para piyasası
  81+13=94, borçlanma araçları 85+45=130, katılım 109+85=194, hisse senedi
  192+42=234 fon (YAT+EMK, raporlanan/`expected_count`, 04.09.2026
  `report_status.json`). TEFAS'ın tür listesinde bundan yaklaşık 15 fon daha
  kayıtlı ama henüz günlük fiyat/`tedPaySayisi` vermiyor (piyasaya henüz
  çıkmamış ya da işlem görmüyor); `topla()` bu alanlardan biri `None` gelince
  fonu atlar (`tefas_secili.py`), bu yüzden raporlanan sayı tür listesinden
  düşük çıkar. Türler `kapsam.turler` içinde fon tipine göre ayrı listelenir;
  TEFAS'ın tür adı değişirse kapsam sessizce boşalmaz, rapor metadata'sındaki
  `expected_count` düşer ve dashboard `Eksik veri` gösterir.
- **Kıymetli maden evreni şemsiye türüyle tanımlanamaz** (`unvan_kurali:
  kiymetli_maden`). TEFAS altın katılım fonlarını "Katılım Şemsiye Fonu",
  gümüş fonlarını çoğunlukla "Fon Sepeti"/"Serbest" altında sınıflıyor: yalnız
  şemsiye türüne bakan ilk sürüm 27 fon veriyor ve **altın raporunun 49 fonundan
  23'ünü kaçırıyordu**. Kural artık tür ∪ unvan (ALTIN/GOLD, GÜMÜŞ/SILVER,
  PLATİN, PALADYUM, KIYMETLİ MADEN) ve altın evrenini kapsadığı testle
  kilitli. Unvan tuzakları: `GÜMÜŞSUYU` semt adıdır, `ÖZEL BANKACILIK VE
  PLATİNUM` hizmet segmentidir, `MADENCİLİK` sektör fonudur — üçü de elenir.
- `raporlar/gruplar.json` — grup bazında akış, **12 satır** (6 grup × YAT/EMK;
  altın, kıymetli maden, para piyasası, borçlanma, katılım, hisse senedi).
  **Veri çekmez**, kaynak raporların önbelleklerini toplar; bu yüzden onlardan
  **sonra** çalışmalıdır (workflow'da sıra böyle). Gruptaki bir fonun akışı
  hesaplanamıyorsa o günün grup toplamı boş bırakılır.

  **Satırlar tematik, ayrık değil — bu yüzden toplanamaz.** Altının 66 fonunun
  tamamı kıymetli maden satırında; altın/gümüş katılım fonları hem kıymetli
  maden hem katılım satırında; "Katılım Hisse Senedi Fonu" hem katılım hem
  hisse satırında. 12 satırda 810 satır-fon ama **703 tekil fon** var. Üretici
  örtüşme haritasını hesaplayıp `RAW.ortak` ve `metadata.overlap_pairs`
  alanlarına yazıyor; sayfa örtüşen satırlar birlikte seçildiğinde dönem
  toplamı yerine **"— örtüşen gruplar"** gösteriyor (ayrık seçimde normal
  toplam). `metadata.additive` alanı satırların toplanabilirliğini bildirir.
  Ayrık bir piyasa haritası isteniyorsa satırlar şemsiye türü bölümlemesine
  taşınmalı — o zaman altın satırı olamaz (altın bir şemsiye türü değil).

**Tür bilinmeyen fonlar:** fon türü yalnızca TEFAS yönetim bilgisi ucundan
  geliyor ve o uç günlük veri veren her fonu kapsamıyor (04.09.2026'da 2.041
  fonun 13'ü yok — çoğu Albaraka katılım serbest fonu). Bu 13 fonun büyüklüğü
  04.09.2026'da tek seferlik elle ölçülmüş: toplam ~101 mlr TL, evrenin ~%1'i —
  bu iki rakam kodda hesaplanmıyor, yalnızca o günkü manuel örneklemeye ait ve
  güncellenmez. Bu fonlar tür bazlı kapsamda sessizce elenmez: önbellekte
  `turu_bilinmeyen` alanına, rapor metadata'sında `untyped`/`untyped_count`
  alanlarına ve sayfanın altbaşlığına yazılır.

Yeni bir grup raporunu ilk kez kurmak (~13 dk; TEFAS pencere sınırı ve rate
limiti yüzünden):

```bash
env -u PYTHONPATH .venv/bin/python 3_tefas_fon_akis_maili/tefas_secili.py \
  --rapor para_piyasasi --bootstrap
```

## Zamanlama

`.github/workflows/production.yml` hafta içi (Pzt–Cuma) `cron: "7 8 * * 1-5"`
ile 08:07 UTC'de (11:07 TRT) otomatik tetiklenir: tüm üreticileri çalıştırır,
sırayla `build_site.py` ile site'ı ve `scripts/build_research_artifacts.py` ile
dashboard artifact'lerini üretir, sonra `kpostaagasi/finansal-raporlar`
reposuna push ederek GitHub Pages'e yayınlar. `smoke.yml` zamanlanmamıştır;
yalnızca elle (`workflow_dispatch`) tetiklenip kaynak erişimini test eder.

GitHub'ın `schedule` tetikleyicisi "best effort"tur: bu depoda gerçek
tetiklenme hedeften 4–12 saat gecikebiliyor (private repo, düşük öncelik).
Dakika hassasiyeti gerekirse tetik dışarıdan atılmalı
(`gh workflow run production.yml`); `workflow_dispatch` gecikmiyor.

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
