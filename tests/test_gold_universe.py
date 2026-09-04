"""Altın fon evreninin sessizce kaymasını engelleyen kilit.

Evren bir kod listesiyle değil, unvan kuralıyla belirlendiği için TEFAS'a çıkan
yeni bir fon rapora kendiliğinden girer. Bu istenen davranış; ancak kimin
girdiği/çıktığı gözden geçirilmeden değişmemeli. Buradaki testler:

  1. unvan kuralının iki üreticide de aynı kararı verdiğini,
  2. önbellekteki gerçek evrenin incelenmiş kod kümesiyle örtüştüğünü,
  3. emeklilik tarafındaki elle tutulan listenin, TEFAS'tan her çalışmada
     tazelenen evrenden ayrışmadığını

doğrular. Kural bilinçli değiştirildiğinde ya da yeni bir fon incelendiğinde
aşağıdaki kümeler güncellenir.
"""

import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GROUP_MODULE = ROOT / "2_tefas_altin_akis" / "tefas_akis.py"
SELECTED_MODULE = ROOT / "3_tefas_fon_akis_maili" / "tefas_secili.py"
GROUP_CACHE = ROOT / "2_tefas_altin_akis" / "fon_veri.json"
SELECTED_CACHE = ROOT / "3_tefas_fon_akis_maili" / "altin_veri.json"
PENSION_LIST = ROOT / "2_tefas_altin_akis" / "eyf_fonlar.json"

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
    if "tip" in data:  # fon bazında rapor önbelleği
        return {
            tip: {kod for kod, t in data["tip"].items() if t == tip}
            for tip in ("YAT", "EMK")
        }
    return {"YAT": set(data["yf"]), "EMK": set(data["eyf"])}


class GoldTitleRuleTests(unittest.TestCase):
    def test_both_engines_apply_the_same_title_rule(self):
        group = load_module("gold_rule_group", GROUP_MODULE)
        selected = load_module("gold_rule_selected", SELECTED_MODULE)
        for title, expected in TITLE_CASES:
            with self.subTest(title=title):
                self.assertEqual(group.altin_fonu(title), expected)
                self.assertEqual(selected.altin_unvani(title), expected)


class GoldUniverseTests(unittest.TestCase):
    def assert_investment_universe(self, cache: Path):
        codes = cached_codes(cache)["YAT"]
        added = sorted(codes - REVIEWED_INVESTMENT_FUNDS)
        dropped = sorted(REVIEWED_INVESTMENT_FUNDS - codes)
        self.assertEqual(
            (added, dropped),
            ([], []),
            f"{cache.name}: altın fon evreni değişti — yeni {added}, düşen {dropped}. "
            "Unvanları TEFAS'ta doğrula, sonra REVIEWED_INVESTMENT_FUNDS ve "
            "README'deki fon sayısını güncelle.",
        )

    def test_group_report_universe_matches_the_reviewed_fund_set(self):
        self.assert_investment_universe(GROUP_CACHE)

    def test_selected_report_universe_matches_the_reviewed_fund_set(self):
        self.assert_investment_universe(SELECTED_CACHE)

    def test_both_reports_cover_the_same_funds(self):
        group = cached_codes(GROUP_CACHE)
        selected = cached_codes(SELECTED_CACHE)
        self.assertEqual(group["YAT"], selected["YAT"])
        self.assertEqual(group["EMK"], selected["EMK"])

    def test_pension_list_matches_the_refreshed_pension_universe(self):
        """Emeklilik evreni 2. raporda elle, 3. raporda TEFAS'tan gelir.

        Yeni bir altın emeklilik fonu açıldığında fon bazında rapor onu
        kendiliğinden alır, grup raporu ise elle güncellenene kadar almaz. Bu
        test o ayrışmayı ilk üretimde görünür kılar.
        """
        listed = set(json.loads(PENSION_LIST.read_text(encoding="utf-8"))["fonlar"])
        refreshed = cached_codes(SELECTED_CACHE)["EMK"]
        missing = sorted(refreshed - listed)
        stale = sorted(listed - refreshed)
        self.assertEqual(
            (missing, stale),
            ([], []),
            f"eyf_fonlar.json güncel değil — eksik {missing}, fazla {stale}. "
            "TEFAS'ta fonTurAciklama'sı Altın (Katılım) Fonu olan emeklilik "
            "fonlarını yenileyip --bootstrap ile seriyi yeniden kur.",
        )


if __name__ == "__main__":
    unittest.main()
