import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "3_tefas_fon_akis_maili" / "fonlar.json"


class SelectedFundConfigTests(unittest.TestCase):
    def test_selected_funds_match_the_verified_watchlist(self):
        configured = json.loads(CONFIG.read_text(encoding="utf-8"))["fonlar"]
        # Kriter: net akış büyüklüğü yüksek fonlar (yön fark etmez) + izlenen
        # kurucuların butik fonları. PDR/RTA 18.08.2026'da çıkarıldı; son 10
        # kod 04.09.2026'da eklendi (izlenen kurucuların akışta ilk 50'ye
        # giren eksik fonları).
        expected = {
            "TLY", "PHE", "TP2", "PBR", "ZFB", "ZFZ", "RBH",
            "BGP", "PPB", "DLY", "PNU", "PRY", "TGR", "ZBJ", "PRD",
            "THF", "ODN", "PAL", "PCS", "PPJ", "TMV", "PUR", "YOZ",
            "TZL", "ZPR",
        }
        self.assertEqual(len(configured), 25)
        self.assertEqual(len(set(configured)), len(configured), "kod tekrarı var")
        self.assertEqual(set(configured), expected)


if __name__ == "__main__":
    unittest.main()
