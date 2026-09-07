import importlib.util
import json
import re
import shutil
import ssl
import subprocess
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


STALE_ESIK_GUN = 5  # emtia_report.fetch_quote: age_days > STALE_ESIK_GUN eşiği
# (bkz. HTML metni: "beş günden eski fiyatlar eğriden çıkarılır")
NODE_AVAILABLE = shutil.which("node") is not None


def _quote_response(price, age_days, fixed_now):
    ts = fixed_now - age_days * 86400
    return json.dumps(
        {"chart": {"result": [{"meta": {"regularMarketPrice": price, "regularMarketTime": ts}}]}}
    )


def _extract_badge_expression(html):
    """M14: gömülü <script>'teki 'complete' kapısını (found_count===requested_count)
    ve onu tüketen badge ifadesini kaynaktan çıkarır."""
    match = re.search(
        r"^\s*const complete = c\.found_count === c\.requested_count;\s*\n"
        r"\s*const badge = (.+);$",
        html,
        re.MULTILINE,
    )
    assert match, "complete/badge JS ifadesi HTML şablonunda bulunamadı"
    return match.group(1)


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

    def test_fetch_quote_rejects_non_positive_or_non_numeric_prices(self):
        """Regresyon: 0/negatif fiyat "geçerli gözlem" sayılıp HTML tablosunda
        sıfıra bölme yüzünden "+∞" değişim yüzdesi üretiyordu; string tip ise
        sonraki round() çağrısını TypeError ile TÜM raporu çökertiyordu.
        TEFAS tarafındaki eşlenik hata: 2_tefas_altin_akis/tefas_akis.py
        gecerli_gozlem (F1)."""
        invalid_prices = [0.0, -5.25, "N/A", True, float("nan"), float("inf"), None]
        for price in invalid_prices:
            with self.subTest(price=price):
                response = {
                    "chart": {
                        "result": [{
                            "meta": {
                                "regularMarketPrice": price,
                                "regularMarketTime": 100_000,
                            }
                        }]
                    }
                }
                with patch.object(self.module, "http_get", return_value=json.dumps(response)):
                    quote = self.module.fetch_quote("TEST.CMX", now_ts=1_000_000)
                self.assertIsNone(quote)

        response = {
            "chart": {
                "result": [{
                    "meta": {
                        "regularMarketPrice": 42.5,
                        "regularMarketTime": 100_000,
                    }
                }]
            }
        }
        with patch.object(self.module, "http_get", return_value=json.dumps(response)):
            quote = self.module.fetch_quote("TEST.CMX", now_ts=1_000_000)
        self.assertEqual(quote["value"], 42.5)

    def test_build_data_survives_treasury_failure_with_all_commodities_healthy(self):
        """Regresyon: fetch_treasury istisna fırlattığında data["rates"] None
        kalıyor, report_meta hesaplanırken data.get("rates", {}).get("points")
        None.get(...) ile AttributeError'a düşüp 6/6 emtia sağlıklı olsa bile
        TÜM raporu (emtia_futures.html) kaybettiriyordu."""
        def fq(sym, now_ts=None):
            return {"value": 55.5, "timestamp": 1_700_000_000, "age_days": 1.0, "stale": False}

        with (
            patch.object(self.module, "fetch_quote", side_effect=fq),
            patch.object(self.module, "fetch_treasury", side_effect=RuntimeError("erisilemedi")),
        ):
            data = self.module.build_data()

        self.assertEqual(len(data["curves"]), len(self.module.COMMODITIES))
        self.assertEqual(data["rates"], {})
        self.assertIn("Hazine getiri eğrisi alınamadı", data["warnings"])
        self.assertEqual(data["report_meta"]["treasury_found_count"], 0)

    def test_build_data_isolates_commodity_whose_every_contract_is_invalid(self):
        """Regresyon: bir emtianın TÜM vadeleri geçersiz kotasyon döndürdüğünde
        (ör. Yahoo sürekli 0/negatif fiyat veriyor) build_data yine üretilmeli;
        yalnız o eğri kapsam dışı kalmalı, diğer beş eğri etkilenmemeli."""
        def fq(sym, now_ts=None):
            if sym.startswith("GC"):
                return None
            return {"value": 10.0, "timestamp": 1_700_000_000, "age_days": 1.0, "stale": False}

        with (
            patch.object(self.module, "fetch_quote", side_effect=fq),
            patch.object(
                self.module, "fetch_treasury", return_value={"date": "01/02/2027", "points": []}
            ),
        ):
            data = self.module.build_data()

        titles = {c["title"] for c in data["curves"]}
        self.assertNotIn("Altın", titles)
        self.assertEqual(len(data["curves"]), len(self.module.COMMODITIES) - 1)
        altin_contracts = self.module.gen_contracts("GC", "CMX", [2, 4, 6, 8, 10, 12], 8)
        altin_symbols = {sym.split(".")[0] for sym, _, _ in altin_contracts}
        self.assertTrue(altin_symbols.issubset(set(data["report_meta"]["missing"])))
        self.assertTrue(any(w.startswith("Altın: 0/") for w in data["warnings"]))
        for curve in data["curves"]:
            self.assertEqual(curve["found_count"], curve["candidate_count"])

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

    def test_fetch_quote_without_timestamp_is_never_considered_fresh(self):
        """M13b bulgu notu: regularMarketTime alanı yok/None/0 olduğunda
        age_days hesaplanamıyor (None kalıyor); böyle bir kotasyon TAZE
        sayılırsa likidite filtresini atlayıp yaşı bilinmeyen bir fiyat
        eğriye girer. ÖLÇÜM: kaynak bugün üç senaryoda da (anahtar yok,
        None, 0) zaten stale=True döndürüyor (age_days is None -> True) —
        bu bir hata DEĞİL, mevcut fail-closed davranış. Sözleşme ihlali
        olmadığından expectedFailure kullanılmadı; bu test yalnızca
        ölçülen doğru davranışı kilitler."""
        base_meta = {"regularMarketPrice": 50.0}
        scenarios = {
            "anahtar_yok": dict(base_meta),
            "None_degeri": {**base_meta, "regularMarketTime": None},
            "sifir_degeri": {**base_meta, "regularMarketTime": 0},
        }
        for label, meta in scenarios.items():
            with self.subTest(senaryo=label):
                response = {"chart": {"result": [{"meta": meta}]}}
                with patch.object(self.module, "http_get", return_value=json.dumps(response)):
                    quote = self.module.fetch_quote("TEST.CMX", now_ts=1_000_000)
                self.assertIsNone(quote["age_days"])
                self.assertTrue(quote["stale"])

    def test_build_data_locks_stale_threshold_boundary_at_five_days(self):
        """M13c: test_build_data_excludes_stale_quotes_from_liquid_curve
        fetch_quote'u tamamen mocklayıp 'stale' alanını literal veriyor;
        gerçek age_days > STALE_ESIK_GUN karşılaştırması hiç çalışmıyor,
        eşik 5..10.4 gün arasında herhangi bir değere kayabilirdi (fikstür
        10.4 gün kullanıyordu). Burada yalnızca http_get mocklanıyor;
        gerçek fetch_quote eşiği hesaplıyor. Eşiğin hemen altı (4.9 gün,
        eğriye GİRER) ve hemen üstü (5.1 gün, excluded_stale_quotes'a
        GİRER) literal olarak kilitleniyor."""
        commodity = {
            "key": "test", "title": "Test Emtia", "unit": "$/unit",
            "root": "TT", "suffix": "CMX", "cycle": [1], "count": 4, "dec": 2,
        }
        contracts = [
            ("FRESH.CMX", 2027, 1),
            ("STALE.CMX", 2028, 1),
            ("THIRD.CMX", 2029, 1),
            ("FOURTH.CMX", 2030, 1),
        ]
        fixed_now = 1_800_000_000.0
        responses = {
            "FRESH.CMX": _quote_response(10.0, STALE_ESIK_GUN - 0.1, fixed_now),
            "STALE.CMX": _quote_response(11.0, STALE_ESIK_GUN + 0.1, fixed_now),
            "THIRD.CMX": _quote_response(12.0, 1.0, fixed_now),
            "FOURTH.CMX": _quote_response(13.0, 2.0, fixed_now),
        }

        def http_get_stub(url):
            for sym, payload in responses.items():
                if sym in url:
                    return payload
            raise AssertionError(f"beklenmeyen url: {url}")

        real_fetch_quote = self.module.fetch_quote
        with (
            patch.object(self.module, "COMMODITIES", [commodity]),
            patch.object(self.module, "gen_contracts", return_value=contracts),
            patch.object(self.module, "http_get", side_effect=http_get_stub),
            patch.object(
                self.module,
                "fetch_quote",
                side_effect=lambda sym, now_ts=None: real_fetch_quote(sym, now_ts=fixed_now),
            ),
            patch.object(
                self.module, "fetch_treasury", return_value={"date": "01/02/2027", "points": []}
            ),
        ):
            data = self.module.build_data()

        self.assertEqual(len(data["curves"]), 1)
        curve = data["curves"][0]
        self.assertEqual({p["sym"] for p in curve["points"]}, {"FRESH", "THIRD", "FOURTH"})
        self.assertEqual(
            [(q["symbol"], q["age_days"]) for q in curve["excluded_stale_quotes"]],
            [("STALE", STALE_ESIK_GUN + 0.1)],
        )

    def test_build_data_publishes_curve_only_at_three_or_more_points(self):
        """N: build_data yalnızca len(pts) >= 3 olduğunda eğriyi
        data['curves']'e ekliyor; aksi halde stderr'e uyarı basıp eğriyi
        TAMAMEN düşürüyor. Sınırın iki yanı: 2 geçerli kotasyon -> eğri
        YOK; 3 geçerli kotasyon -> eğri VAR."""

        def run(n_valid):
            commodity = {
                "key": "test", "title": "Test Emtia", "unit": "$/unit",
                "root": "TT", "suffix": "CMX", "cycle": [1], "count": n_valid, "dec": 2,
            }
            contracts = [(f"S{i}.CMX", 2027 + i, 1) for i in range(n_valid)]
            quotes = {
                f"S{i}.CMX": {
                    "value": 10.0 + i, "timestamp": 1_700_000_000 + i,
                    "age_days": 1.0, "stale": False,
                }
                for i in range(n_valid)
            }
            with (
                patch.object(self.module, "COMMODITIES", [commodity]),
                patch.object(self.module, "gen_contracts", return_value=contracts),
                patch.object(self.module, "fetch_quote", side_effect=lambda sym: quotes[sym]),
                patch.object(
                    self.module, "fetch_treasury", return_value={"date": "01/02/2027", "points": []}
                ),
            ):
                return self.module.build_data()

        self.assertEqual(run(2)["curves"], [])

        data_three = run(3)
        self.assertEqual(len(data_three["curves"]), 1)
        self.assertEqual(data_three["curves"][0]["found_count"], 3)

    def test_build_data_requested_count_excludes_stale_from_candidate_count(self):
        """O: requested_count = candidate_count - len(excluded_stale_quotes)
        olmalı; bu çıkarma kaldırılırsa requested_count her zaman
        candidate_count'a eşit çıkar ve HTML/JS'teki 'complete' kapısı
        (found_count===requested_count, bkz. M14) bayat vade varken bile
        yanlışlıkla True dönebilir. Literal: aday(candidate) 5,
        bayat(stale) 2, hedef(requested) 3, bulunan(found) 3."""
        commodity = {
            "key": "test", "title": "Test Emtia", "unit": "$/unit",
            "root": "TT", "suffix": "CMX", "cycle": [1], "count": 5, "dec": 2,
        }
        contracts = [(f"S{i}.CMX", 2027 + i, 1) for i in range(5)]
        quotes = {
            "S0.CMX": {"value": 10.0, "timestamp": 100, "stale": False, "age_days": 1.0},
            "S1.CMX": {"value": 11.0, "timestamp": 200, "stale": False, "age_days": 1.0},
            "S2.CMX": {"value": 12.0, "timestamp": 300, "stale": False, "age_days": 1.0},
            "S3.CMX": {"value": 13.0, "timestamp": 400, "stale": True, "age_days": 6.0},
            "S4.CMX": {"value": 14.0, "timestamp": 500, "stale": True, "age_days": 7.0},
        }
        with (
            patch.object(self.module, "COMMODITIES", [commodity]),
            patch.object(self.module, "gen_contracts", return_value=contracts),
            patch.object(self.module, "fetch_quote", side_effect=lambda sym: quotes[sym]),
            patch.object(
                self.module, "fetch_treasury", return_value={"date": "01/02/2027", "points": []}
            ),
        ):
            data = self.module.build_data()

        curve = data["curves"][0]
        self.assertEqual(curve["candidate_count"], 5)
        self.assertEqual(len(curve["excluded_stale_quotes"]), 2)
        self.assertEqual(curve["requested_count"], 3)
        self.assertEqual(curve["found_count"], 3)

    @unittest.skipUnless(NODE_AVAILABLE, "node bulunamadı; JS davranış testi atlandı")
    def test_badge_gate_and_trend_labels_via_real_node_execution(self):
        """M14: gömülü <script>'teki 'complete' kapısı (found_count===
        requested_count -> !complete durumunda badge='Kaynakta eksik')
        hiçbir testte ÇALIŞTIRILMIYORDU; yalnızca metin varlığı
        pinlenmişti (assertIn). Burada ifade kaynaktan regex ile çıkarılıp
        gerçek Node.js'te sentetik curve/point girdileriyle çalıştırılıyor;
        kapı kaldırılır/gevşetilirse çıktı beklenenle uyuşmaz ve test
        kırmızıya düşer."""
        badge_expr = _extract_badge_expression(self.module.HTML)
        script = (
            "const cases = ["
            "[{found_count: 2, requested_count: 3}, {value: 100}, {value: 100}],"
            "[{found_count: 3, requested_count: 3}, {value: 100}, {value: 110}],"
            "[{found_count: 3, requested_count: 3}, {value: 100}, {value: 90}],"
            "[{found_count: 3, requested_count: 3}, {value: 100}, {value: 100}],"
            "];\n"
            "for (const [c, f, l] of cases) {\n"
            "  const complete = c.found_count === c.requested_count;\n"
            f"  const badge = {badge_expr};\n"
            "  console.log(badge);\n"
            "}\n"
        )
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, timeout=10
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.strip().splitlines(),
            ["Kaynakta eksik", "Contango", "Backwardation", "Yatay"],
        )

    @unittest.skipIf(NODE_AVAILABLE, "node bulunduğu için davranışsal test tercih edildi")
    def test_badge_expression_structurally_gates_on_complete_when_node_unavailable(self):
        """M14 SINIRLILIK: node yoksa gerçek JS çalıştırılamıyor; bu test
        yalnızca ifadenin literal yapısını (complete kapısı hemen ardından
        '!complete ? Kaynakta eksik' dalı) doğrular — çalışma zamanı
        davranışını KANITLAMAZ. node mevcut olduğunda yukarıdaki
        davranışsal test tercih edilir."""
        badge_expr = _extract_badge_expression(self.module.HTML)
        self.assertTrue(badge_expr.startswith("!complete ? 'Kaynakta eksik' :"))


if __name__ == "__main__":
    unittest.main()
