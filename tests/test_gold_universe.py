"""Altın fon evreninin sessizce kaymasını engelleyen kilit.

Evren bir kod listesiyle değil, unvan kuralıyla belirlendiği için TEFAS'a çıkan
yeni bir fon rapora kendiliğinden girer. Bu istenen davranış; ancak kimin
girdiği/çıktığı gözden geçirilmeden değişmemeli. Buradaki testler:

  1. unvan kuralının doğru kararı verdiğini,
  2. önbellekteki gerçek evrenin (yatırım + emeklilik) incelenmiş kod
     kümesiyle örtüştüğünü,
  3. altın evreninin kıymetli maden raporunun bir alt kümesi olduğunu

doğrular. Kural bilinçli değiştirildiğinde ya da yeni bir fon incelendiğinde
aşağıdaki kümeler güncellenir.
"""

import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SELECTED_MODULE = ROOT / "3_tefas_fon_akis_maili" / "tefas_secili.py"
PRECIOUS_CACHE = ROOT / "3_tefas_fon_akis_maili" / "kiymetli_veri.json"

# 04.09.2026'da TEFAS unvan listesi üzerinden gözden geçirilmiş yatırım fonu
# evreni. GLL (GOLDEN GLOBAL PORTFÖY ALTIN KATILIM FONU) 20.08.2026'da
# piyasaya çıktı ve kurala uyduğu için eklendi — kurucu adındaki "GOLDEN"
# yalnızca "GOLD" dalını eler, unvandaki "ALTIN" bu fonu doğru biçimde alır.
REVIEWED_INVESTMENT_FUNDS = {
    "AFO", "AU1", "BAI", "BLT", "DBA", "FAK", "FAL", "FIB", "GGK", "GLL",
    "GOL", "GTA", "HAI", "HAM", "HBF", "IAY", "ICA", "KAN", "KCR", "KMF",
    "KZL", "KZU", "LKF", "MKG", "NAK", "NAU", "NJF", "OGD", "OIL", "OJK",
    "PA2", "PAF", "PEA", "PIR", "PKF", "PTN", "RBA", "RJG", "RPG", "TAL",
    "TCA", "TRO", "TTA", "TUA", "UP1", "VFO", "VLT", "YKT", "ZCE",
}

# Emeklilik tarafı: TEFAS fon türü (Altın Fonu / Altın Katılım Fonu) tek
# başına yetmiyor. EMY (GARANTİ EMEKLİLİK ALTIN EYF) "Kıymetli Madenler"
# olarak sınıflandığı için tür filtresinden düşüyordu; unvan kuralı onu
# 04.09.2026'da evrene geri getirdi.
REVIEWED_PENSION_FUNDS = {
    "AEA", "AGA", "AMZ", "BGL", "BNA", "CFA", "EAE", "EMY", "GEV", "GHA",
    "GRA", "HEA", "KEF", "MEA", "NHA", "NZA", "VGA",
}

# TEFAS unvanlarından alınmış gerçek örnekler: (unvan, evrene girmeli mi?)
TITLE_CASES = (
    ("AK PORTFÖY ALTIN FONU", True),
    ("AK PORTFÖY KÜLÇE ALTIN KATILIM FONU", True),
    ("GOLDEN GLOBAL PORTFÖY ALTIN KATILIM FONU", True),
    ("ZİRAAT PORTFÖY TÜRK ALTIN SERBEST (TL) ÖZEL FON", True),
    ("GOLD PORTFÖY BİRİNCİ FON", True),
    # "ALTINCI" sıra sayısıdır, kıymetli maden değil.
    ("AK PORTFÖY ALTINCI SERBEST(DÖVİZ) FON", False),
    ("AK PORTFÖY ONALTINCI SERBEST (DÖVİZ-POUND) FON", False),
    ("GARANTİ PORTFÖY YİRMİALTINCI SERBEST (DÖVİZ-AVRO) ÖZEL FON", False),
    ("BULLS PORTFÖY ALTINCI HİSSE SENEDİ SERBEST FON (HİSSE SENEDİ YOĞUN FON)", False),
    # Kurucu adı "GOLDEN GLOBAL"; fonun kendisi altın fonu değil.
    ("GOLDEN GLOBAL PORTFÖY PARA PİYASASI KATILIM FONU", False),
    ("GOLDEN GLOBAL PORTFÖY KISA VADELİ KİRA SERTİFİKALARI KATILIM FONU", False),
)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def cached_codes(cache: Path) -> dict[str, set[str]]:
    """Önbellekteki evreni {"YAT": {...}, "EMK": {...}} biçiminde döndürür."""
    data = json.loads(cache.read_text(encoding="utf-8"))
    return {
        tip: {kod for kod, t in data["tip"].items() if t == tip}
        for tip in ("YAT", "EMK")
    }


class GoldTitleRuleTests(unittest.TestCase):
    def test_title_rule_matches_reviewed_examples(self):
        selected = load_module("gold_rule_selected", SELECTED_MODULE)
        for title, expected in TITLE_CASES:
            with self.subTest(title=title):
                self.assertEqual(selected.altin_unvani(title), expected)


class GoldUniverseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # tek kaynak: raporlar/altin.json üzerinden tefas_secili.py'nin
        # kendi çözdüğü önbellek yolu (hardcode edilmiş bir yol değil).
        selected = load_module("gold_universe_selected", SELECTED_MODULE)
        cls.cache = Path(selected.rapor_yukle("altin")["cache"])

    def test_investment_universe_matches_the_reviewed_fund_set(self):
        codes = cached_codes(self.cache)["YAT"]
        added = sorted(codes - REVIEWED_INVESTMENT_FUNDS)
        dropped = sorted(REVIEWED_INVESTMENT_FUNDS - codes)
        self.assertEqual(
            (added, dropped),
            ([], []),
            f"altın fon evreni değişti — yeni {added}, düşen {dropped}. "
            "Unvanları TEFAS'ta doğrula, sonra REVIEWED_INVESTMENT_FUNDS ve "
            "README'deki fon sayısını güncelle.",
        )

    def test_pension_universe_matches_the_reviewed_fund_set(self):
        """Emeklilik evreni artık tek üreticiden (tefas_secili.py) gelir:

        her çalışmada TEFAS'tan tazelenen `altin_veri.json` önbelleği,
        burada elle gözden geçirilmiş REVIEWED_PENSION_FUNDS'tan
        ayrışmamalı."""
        codes = cached_codes(self.cache)["EMK"]
        added = sorted(codes - REVIEWED_PENSION_FUNDS)
        dropped = sorted(REVIEWED_PENSION_FUNDS - codes)
        self.assertEqual(
            (added, dropped),
            ([], []),
            f"altın emeklilik evreni değişti — yeni {added}, düşen {dropped}. "
            "Unvanı ve fon türünü TEFAS'ta doğrula, sonra REVIEWED_PENSION_FUNDS "
            "ve README'yi güncelle.",
        )

    def test_gold_universe_is_contained_in_the_precious_metals_report(self):
        """Altın fonları kıymetli maden raporunun alt kümesi olmalı.

        TEFAS altın katılım fonlarını "Katılım Şemsiye Fonu", gümüş fonlarını
        "Fon Sepeti"/"Serbest" altında sınıflıyor; kıymetli maden evreni bu
        yüzden şemsiye türüne değil tür ∪ unvan kuralına dayanır. Aksi halde
        "kıymetli maden" raporu kendi içindeki altın fonlarını kaçırıyordu.
        """
        altin = cached_codes(self.cache)
        kiymetli = cached_codes(PRECIOUS_CACHE)
        for tip in ("YAT", "EMK"):
            with self.subTest(tip=tip):
                eksik = sorted(altin[tip] - kiymetli[tip])
                self.assertEqual(
                    eksik, [],
                    f"kıymetli maden raporu şu altın fonlarını kaçırıyor: {eksik}",
                )


if __name__ == "__main__":
    unittest.main()
