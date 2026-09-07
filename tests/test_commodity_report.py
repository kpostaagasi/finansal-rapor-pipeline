import importlib.util
import json
import ssl
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "1_emtia_tahvil_maili" / "emtia_report.py"


def load_module():
    spec = importlib.util.spec_from_file_location("commodity_report_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CommodityReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def test_certificate_error_fails_closed_without_unverified_retry(self):
        error = urllib.error.URLError(
            ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED")
        )
        with patch.object(
            self.module.urllib.request, "urlopen", side_effect=error
        ) as urlopen:
            with self.assertRaises(urllib.error.URLError):
                self.module.http_get("https://example.com/data")

        self.assertEqual(urlopen.call_count, 1)

    def test_stale_but_available_quote_is_kept_with_age_metadata(self):
        response = {
            "chart": {
                "result": [{
                    "meta": {
                        "regularMarketPrice": 123.45,
                        "regularMarketTime": 100_000,
                    }
                }]
            }
        }
        with patch.object(self.module, "http_get", return_value=json.dumps(response)):
            quote = self.module.fetch_quote("TEST.CMX", now_ts=1_000_000)

        self.assertEqual(quote["value"], 123.45)
        self.assertEqual(quote["timestamp"], 100_000)
        self.assertTrue(quote["stale"])
        self.assertAlmostEqual(quote["age_days"], 900_000 / 86_400)

    def test_aluminum_target_matches_yahoo_listed_ten_month_chain(self):
        aluminum = next(
            item for item in self.module.COMMODITIES if item["key"] == "aluminyum"
        )
        self.assertEqual(aluminum["count"], 10)

    def test_copper_unit_uses_turkish_libre_not_english_abbreviation(self):
        """Diğer emtia birimleri Türkçe ($/varil, $/ons, $/ton); bakır İngilizce
        "$/lb" kısaltmasıyla tutarsızdı. "$/libre" ile hizalanmalı."""
        bakir = next(item for item in self.module.COMMODITIES if item["key"] == "bakir")
        self.assertEqual(bakir["unit"], "$/libre")

    def test_build_data_records_requested_found_and_missing_contracts(self):
        commodity = {
            "key": "test",
            "title": "Test Emtia",
            "unit": "$/unit",
            "root": "TT",
            "suffix": "CMX",
            "cycle": [1],
            "count": 4,
            "dec": 2,
        }
        contracts = [
            ("TTF27.CMX", 2027, 1),
            ("TTF28.CMX", 2028, 1),
            ("TTF29.CMX", 2029, 1),
            ("TTF30.CMX", 2030, 1),
        ]
        quotes = {
            "TTF27.CMX": {"value": 10.0, "timestamp": 100},
            "TTF28.CMX": {"value": 11.0, "timestamp": 200},
            "TTF29.CMX": {"value": 12.0, "timestamp": 300},
            "TTF30.CMX": None,
        }
        with (
            patch.object(self.module, "COMMODITIES", [commodity]),
            patch.object(self.module, "gen_contracts", return_value=contracts),
            patch.object(self.module, "fetch_quote", side_effect=lambda sym: quotes[sym]),
            patch.object(
                self.module,
                "fetch_treasury",
                return_value={"date": "01/02/2027", "points": []},
            ),
        ):
            data = self.module.build_data()

        curve = data["curves"][0]
        self.assertEqual(curve["requested_count"], 4)
        self.assertEqual(curve["found_count"], 3)
        self.assertEqual(curve["missing_symbols"], ["TTF30"])
        self.assertEqual(curve["quote_time_min"], 100)
        self.assertEqual(curve["quote_time_max"], 300)
        self.assertIn("Test Emtia: 3/4", data["warnings"])
        meta = data["report_meta"]
        self.assertEqual(meta["expected_count"], 4)
        self.assertEqual(meta["found_count"], 3)
        self.assertEqual(meta["missing"], ["TTF30"])
        self.assertEqual(meta["count_label"], "kontrat")
        self.assertEqual(meta["data_end_date"], "2027-01-02")
        self.assertEqual(meta["source"], "Yahoo Finance + ABD Hazinesi")

    def test_build_data_excludes_stale_quotes_from_liquid_curve(self):
        commodity = {
            "key": "test",
            "title": "Test Emtia",
            "unit": "$/unit",
            "root": "TT",
            "suffix": "CMX",
            "cycle": [1],
            "count": 4,
            "dec": 2,
        }
        contracts = [
            ("TTF27.CMX", 2027, 1),
            ("TTF28.CMX", 2028, 1),
            ("TTF29.CMX", 2029, 1),
            ("TTF30.CMX", 2030, 1),
        ]
        quotes = {
            "TTF27.CMX": {"value": 10.0, "timestamp": 100, "stale": False, "age_days": 1.0},
            "TTF28.CMX": {"value": 11.0, "timestamp": 200, "stale": True, "age_days": 10.4},
            "TTF29.CMX": {"value": 12.0, "timestamp": 300, "stale": False, "age_days": 2.0},
            "TTF30.CMX": {"value": 13.0, "timestamp": 400, "stale": False, "age_days": 3.0},
        }
        with (
            patch.object(self.module, "COMMODITIES", [commodity]),
            patch.object(self.module, "gen_contracts", return_value=contracts),
            patch.object(self.module, "fetch_quote", side_effect=lambda sym: quotes[sym]),
            patch.object(
                self.module,
                "fetch_treasury",
                return_value={"date": "01/02/2027", "points": []},
            ),
        ):
            data = self.module.build_data()

        curve = data["curves"][0]
        self.assertEqual(curve["found_count"], 3)
        self.assertEqual(curve["missing_symbols"], [])
        self.assertEqual(
            curve["excluded_stale_quotes"],
            [{"symbol": "TTF28", "age_days": 10.4}],
        )
        self.assertEqual(
            [point["sym"] for point in curve["points"]],
            ["TTF27", "TTF29", "TTF30"],
        )
        self.assertEqual(data["report_meta"]["candidate_count"], 4)
        self.assertEqual(data["report_meta"]["expected_count"], 3)
        self.assertEqual(data["report_meta"]["found_count"], 3)
        self.assertEqual(
            data["report_meta"]["excluded_stale_quotes"],
            [{"commodity": "Test Emtia", "symbol": "TTF28", "age_days": 10.4}],
        )

    def test_report_describes_yahoo_values_without_close_or_settlement_claim(self):
        html = self.module.HTML

        self.assertIn("Yahoo Finance gecikmeli/son piyasa fiyatları", html)
        self.assertNotIn("kapanışları", html)
        self.assertNotIn("seans kapanışları", html)
        self.assertIn("found_count", html)
        self.assertIn("requested_count", html)
        self.assertIn("Likidite filtresi", html)
        self.assertIn("Filtrelenen seyrek vadeler:", html)
        self.assertNotIn("Seyrek işlem gören vadeler", html)
        self.assertNotIn("Eksik veri kapsamı", html)
        self.assertIn("const REPORT_META = __REPORT_META__;", html)


if __name__ == "__main__":
    unittest.main()
