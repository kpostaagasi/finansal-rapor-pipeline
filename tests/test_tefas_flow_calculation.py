import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FundFlowCalculationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.selected = load_module(
            "tefas_selected_flow_test", "3_tefas_fon_akis_maili/tefas_secili.py"
        )
        cls.group = load_module(
            "tefas_group_flow_test", "2_tefas_altin_akis/tefas_akis.py"
        )

    def test_selected_fund_suppresses_flow_when_previous_report_date_is_missing(self):
        cache = {
            "fon": {
                "PDR": {
                    "2026-04-28": [0.01, 9],
                    "2026-06-05": [300_000_000, 1.002864],
                },
                "OTHER": {
                    "2026-06-04": [100, 1],
                    "2026-06-05": [100, 1],
                },
            }
        }

        flows, _, _, gaps = self.selected.akis_serisi(cache)

        self.assertNotIn("PDR", flows["2026-06-05"])
        self.assertEqual(
            gaps["2026-06-05"]["PDR"],
            {"expected_previous": "2026-06-04", "previous_available": "2026-04-28"},
        )
        self.assertNotIn("2026-04-28", flows)

    def test_group_flow_is_unavailable_when_fund_misses_previous_report_date(self):
        funds = {
            "PDR": {
                "2026-04-28": [0.01, 9],
                "2026-06-05": [300_000_000, 1.002864],
            },
            "OTHER": {
                "2026-06-04": [100, 1],
                "2026-06-05": [100, 1],
            },
        }
        dates = ["2026-04-28", "2026-06-04", "2026-06-05"]

        totals, counts, issues = self.group.akislari_hesapla(funds, dates)

        self.assertIsNone(totals["2026-06-05"])
        self.assertEqual(counts["2026-06-05"], 2)
        self.assertEqual(issues["2026-06-05"]["missing_previous"], ["PDR"])

    def test_group_flow_uses_cached_record_at_incremental_window_boundary(self):
        funds = {"AAA": {"2026-06-04": [100, 2], "2026-06-05": [110, 3]}}

        totals, counts, issues = self.group.akislari_hesapla(
            funds, ["2026-06-04", "2026-06-05"]
        )

        self.assertEqual(totals["2026-06-05"], 30)
        self.assertEqual(counts["2026-06-05"], 1)
        self.assertEqual(
            issues["2026-06-05"], {"missing_current": [], "missing_previous": []}
        )

    def test_report_cells_separate_launch_days_from_uncomputable_gaps(self):
        """null yalnızca "hesaplanamadı" demek; piyasaya çıkış öncesi 0 katkıdır.

        Aksi halde dönem içinde açılan tek bir fon (ör. 20.08.2026'da çıkan GLL)
        raporun dönem toplamını tümüyle iptal ediyordu.
        """
        cache = {
            "fon": {
                "ESKI": {
                    "2026-09-01": [100, 1.0],
                    "2026-09-02": [110, 1.0],
                    "2026-09-03": [120, 1.0],
                },
                "YENI": {"2026-09-02": [50, 1.0], "2026-09-03": [60, 1.0]},
                "BOSLUKLU": {"2026-09-01": [10, 1.0], "2026-09-03": [30, 1.0]},
            },
            "ad": {"ESKI": "A", "YENI": "B", "BOSLUKLU": "C"},
            "tip": {"ESKI": "YAT", "YENI": "YAT", "BOSLUKLU": "YAT"},
        }
        cfg = {
            "ad": "test",
            "baslik": "Test",
            "kapsam": {"tip": "tur", "turler": {"YAT": ["X"]}},
            "fon_tipleri": ["YAT"],
        }
        yakalanan = {}

        def sahte_yaz(cfg_, raw, meta, ozet, eksik_not, gunler):
            yakalanan.update(raw=raw, meta=meta)

        original = self.selected.html_yaz
        self.selected.html_yaz = sahte_yaz
        try:
            self.selected.html_uret(cfg, cache)
        finally:
            self.selected.html_yaz = original

        raw = yakalanan["raw"]
        gunler = raw["d"]
        i2 = gunler.index("2026-09-02")
        i3 = gunler.index("2026-09-03")
        # YENI 02.09'da piyasaya çıktı: çıkış günü akışa 0 katkı verir, null değil.
        self.assertEqual(raw["f"]["YENI"][i2], 0)
        self.assertEqual(raw["f"]["YENI"][i3], 10)
        # BOSLUKLU 02.09'da gözlem vermedi: 03.09 gerçek boşluk, null kalır.
        self.assertIsNone(raw["f"]["BOSLUKLU"][i3])
        self.assertEqual(yakalanan["meta"]["gap_codes"], ["BOSLUKLU"])
        self.assertEqual(raw["f"]["ESKI"][i3], 10)


if __name__ == "__main__":
    unittest.main()
