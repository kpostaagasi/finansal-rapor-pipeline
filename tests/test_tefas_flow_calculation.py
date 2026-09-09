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

    def test_selected_fund_zero_pay_and_price_record_is_not_published_as_zero_flow(self):
        """Bozuk TEFAS kaydı ([0, 0]) gözlem sayılmaz; akış hesaplanmaz.

        Eski davranışta (0 - önceki_pay) * 0 == 0 sessizce yayınlanıyordu —
        gerçek 0 akış ile "veri boşluğu"nu ayırt edemiyordu.
        """
        cache = {
            "fon": {
                "PRY": {
                    "2026-09-03": [1_000_000, 10.0],
                    "2026-09-04": [0, 0],
                    "2026-09-05": [1_000_000, 10.0],
                },
            }
        }

        flows, _, _, gaps = self.selected.akis_serisi(cache)

        self.assertNotIn("PRY", flows.get("2026-09-04", {}))
        self.assertEqual(gaps["2026-09-04"]["PRY"]["reason"], "invalid_observation")
        self.assertEqual(gaps["2026-09-04"]["PRY"]["previous_available"], "2026-09-03")
        # Bozuk günün etrafındaki geçiş de hesaplanamaz (önceki geçerli gözlem
        # 09-04 değil 09-03; beklenen önceki TEFAS tarihi 09-04'te yok).
        self.assertNotIn("PRY", flows.get("2026-09-05", {}))

    def test_selected_fund_flow_unchanged_for_valid_consecutive_observations(self):
        """Kör filtreleme regresyonu: geçerli pozitif kayıtlarda hesap değişmez."""
        cache = {
            "fon": {
                "ABC": {
                    "2026-09-03": [1000, 10.0],
                    "2026-09-04": [1050, 10.5],
                },
            }
        }

        flows, _, _, gaps = self.selected.akis_serisi(cache)

        self.assertEqual(flows["2026-09-04"]["ABC"], (1050 - 1000) * 10.5)
        self.assertEqual(gaps, {})

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

    def test_pay_bolunmesi_boundary_cases(self):
        """pay_bolunmesi: pay oranı eşiği + değer toleransı sınır vakaları.

        Regresyon: TI2/GA1 gibi gerçek bölünmeler yanlış negatif, sıradan
        büyük alım/satımlar yanlış pozitif vermemeli.
        """
        vakalar = [
            ("TI2 gerçek bölünme (pay x9971, fiyat /9834)",
             (7_453_535, 1003.618235), (74_320_597_890, 0.102052), True),
            ("GA1 gerçek bölünme (pay x993)",
             (1_000_000, 10.0), (993_000_000, 10.0 / 993), True),
            ("normal büyük akış (pay x2, fiyat sabit -> değer x2)",
             (1000, 10.0), (2000, 10.0), False),
            ("eşik altı (pay x1.4)",
             (1000, 10.0), (1400, 10.0), False),
            ("ters yön birleşme (pay /10, fiyat x10)",
             (10_000, 1.0), (1_000, 10.0), True),
            ("değer %10 sapmış (tolerans dışı)",
             (1000, 10.0), (10_000, 1.1), False),
        ]
        for ad, onceki, simdi, beklenen in vakalar:
            with self.subTest(vaka=ad):
                self.assertEqual(self.selected.pay_bolunmesi(onceki, simdi), beklenen)

    def test_selected_fund_unit_split_day_flow_is_not_published(self):
        """Regresyon: eski davranışta pay bölünmesi günü hesaplanabilir bir
        akış gibi görünüp (TI2 2025-01-20 örneğinde +7,58 mlr TL) fail-closed
        kontrollerini atlatıyordu. Bölünme günü artık akış üretmez, izleyen
        gün normal hesaplanmaya devam eder."""
        cache = {
            "fon": {
                "SPL": {
                    "2026-09-01": [100, 10.0],
                    "2026-09-02": [110, 10.0],
                    "2026-09-03": [11_000, 0.1],   # pay x100, fiyat /100, değer sabit
                    "2026-09-04": [11_050, 0.1],
                },
            }
        }

        flows, _, _, gaps = self.selected.akis_serisi(cache)

        self.assertNotIn("SPL", flows.get("2026-09-03", {}))
        self.assertEqual(
            gaps["2026-09-03"]["SPL"],
            {"reason": "unit_split", "expected_previous": "2026-09-02",
             "previous_available": "2026-09-02"},
        )
        self.assertEqual(flows["2026-09-04"]["SPL"], (11_050 - 11_000) * 0.1)

    def test_report_meta_includes_split_codes_and_note(self):
        """report_meta'ya split_codes/split_count eklenir, eksik notuna Contract
        madde 2'deki cümle yazılır — regresyon: bölünme günü sessizce
        "Hesaplandı" (F1 ile aynı sınıf hata) olarak görünmemeli."""
        cache = {
            "fon": {
                "SPL": {
                    "2026-09-01": [100, 10.0],
                    "2026-09-02": [110, 10.0],
                    "2026-09-03": [11_000, 0.1],
                },
            },
            "ad": {"SPL": "S FONU"},
            "tip": {"SPL": "YAT"},
        }
        cfg = {
            "ad": "test",
            "baslik": "Test",
            "kapsam": {"tip": "tur", "turler": {"YAT": ["X"]}},
            "fon_tipleri": ["YAT"],
        }
        yakalanan = {}

        def sahte_yaz(cfg_, raw, meta, ozet, eksik_not, gunler):
            yakalanan.update(meta=meta, eksik_not=eksik_not)

        original = self.selected.html_yaz
        self.selected.html_yaz = sahte_yaz
        try:
            self.selected.html_uret(cfg, cache)
        finally:
            self.selected.html_yaz = original

        meta = yakalanan["meta"]
        self.assertEqual(meta["split_codes"], ["SPL"])
        self.assertEqual(meta["split_count"], 1)
        self.assertIn(
            "Pay bölünmesi nedeniyle akış hesaplanamayan 1 fon: SPL",
            yakalanan["eksik_not"],
        )


if __name__ == "__main__":
    unittest.main()
