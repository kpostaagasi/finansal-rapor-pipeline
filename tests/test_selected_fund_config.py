import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "3_tefas_fon_akis_maili" / "fonlar.json"


class SelectedFundConfigTests(unittest.TestCase):
    def test_selected_funds_match_the_verified_legacy_watchlist(self):
        configured = json.loads(CONFIG.read_text(encoding="utf-8"))["fonlar"]
        expected = {
            "TLY", "PHE", "TP2", "PBR", "ZFB", "ZFZ", "RBH",
            "BGP", "PPB", "DLY", "PNU", "PRY", "TGR", "ZBJ", "PRD",
        }
        self.assertEqual(len(configured), 15)
        self.assertEqual(set(configured), expected)


if __name__ == "__main__":
    unittest.main()
