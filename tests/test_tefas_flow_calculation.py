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


if __name__ == "__main__":
    unittest.main()
