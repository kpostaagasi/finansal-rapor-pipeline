"""secili_mail.rapor_ozeti: türetilmiş grup raporunun (kapsam.tip == "toplam") kendi
önbelleği yok, özet üretilmiş HTML'deki REPORT_META'dan okunmalı; `cache` alanına
koşulsuz erişilip KeyError'a düşmemeli.
"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "3_tefas_fon_akis_maili" / "secili_mail.py"


def load_module():
    spec = importlib.util.spec_from_file_location("secili_mail_summary_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def report_html(meta: dict) -> str:
    return f"<html><script>\nconst REPORT_META = {json.dumps(meta)};\n</script></html>"


class GroupSummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def test_group_summary_reads_report_meta_without_touching_cache(self):
        """kapsam.tip == 'toplam' dalı REPORT_META'dan üretir; `rapor["cache"]` hiç yok —
        eski kod bu alana koşulsuz erişip KeyError'a düşer, except bloğu jenerik
        yedek metni döndürürdü. Bu test gerçek özetin üretildiğini doğrular."""
        with tempfile.TemporaryDirectory() as d:
            html_yolu = f"{d}/gruplar.html"
            meta = {
                "data_end_date": "2026-09-03",
                "expected_count": 8,
                "found_count": 8,
                "missing": [],
                "count_label": "grup",
                "source": "TEFAS",
            }
            with open(html_yolu, "w", encoding="utf-8") as f:
                f.write(report_html(meta))
            rapor = {
                "ad": "gruplar",
                "kapsam": {"tip": "toplam", "kaynaklar": []},
                "html": html_yolu,
            }
            self.assertNotIn("cache", rapor)
            ozet = self.module.rapor_ozeti(rapor)
        self.assertEqual(
            ozet,
            "8/8 grup · 03.09.2026 · gruplar arası toplam gösterilmez (tematik, örtüşüyor)",
        )
        self.assertNotEqual(ozet, "Günlük / haftalık / aylık net giriş-çıkış")

    def test_group_summary_reflects_partial_coverage_from_metadata(self):
        """found_count < expected_count olduğunda özet bunu REPORT_META'dan yansıtmalı."""
        with tempfile.TemporaryDirectory() as d:
            html_yolu = f"{d}/gruplar.html"
            meta = {
                "data_end_date": "2026-09-02",
                "expected_count": 8,
                "found_count": 6,
                "missing": ["ALT-YAT", "PAR-YAT"],
                "count_label": "grup",
                "source": "TEFAS",
            }
            with open(html_yolu, "w", encoding="utf-8") as f:
                f.write(report_html(meta))
            rapor = {
                "ad": "gruplar",
                "kapsam": {"tip": "toplam", "kaynaklar": []},
                "html": html_yolu,
            }
            ozet = self.module.rapor_ozeti(rapor)
        self.assertTrue(ozet.startswith("6/8 grup · 02.09.2026"))


class FundSummaryTests(unittest.TestCase):
    """secili_mail.rapor_ozeti fon bazlı dal (kapsam.tip != 'toplam'): son TEFAS
    veri gününün gerçek toplam net akışını `tefas_secili.akis_serisi`'nden okur."""

    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    @staticmethod
    def _cache_yolu(tmp_dir):
        """AAA ve BBB için üç günlük pay/fiyat serisi. Akışlar (pay_t - pay_onceki) *
        fiyat_t: 2024-12-31 → AAA +100, BBB 0 (toplam +100); 2025-01-02 → AAA +200,
        BBB +100 (toplam +300). İlk ve son günün toplamı bilerek FARKLI tutuldu."""
        onbellek = {
            "fon": {
                "AAA": {
                    "2024-12-30": [100, 10],
                    "2024-12-31": [110, 10],
                    "2025-01-02": [130, 10],
                },
                "BBB": {
                    "2024-12-30": [50, 5],
                    "2024-12-31": [50, 5],
                    "2025-01-02": [70, 5],
                },
            }
        }
        cache_yolu = f"{tmp_dir}/secili.json"
        with open(cache_yolu, "w", encoding="utf-8") as f:
            json.dump(onbellek, f)
        return cache_yolu

    def test_fund_summary_reports_actual_net_flow_total(self):
        """H: `toplam = 0` mutasyonuna karşı — son günün (2025-01-02) gerçek toplam
        akışı (AAA +200, BBB +100 = +300 TL) literal olarak kilitlenir. Mutant
        `toplam = 0` altında metin '0 TL' olurdu, bu tam eşitlik onu yakalar."""
        with tempfile.TemporaryDirectory() as d:
            rapor = {"ad": "secili", "kapsam": {"tip": "liste"}, "cache": self._cache_yolu(d)}
            ozet = self.module.rapor_ozeti(rapor)
        self.assertEqual(
            ozet,
            "2 fon · son işlem günü 02.01.2025: +300 TL net giriş",
        )

    def test_fund_summary_uses_last_data_day_not_first_day(self):
        """H2: `son = gunler[0]` mutasyonuna karşı — özet son günü (2025-01-02,
        +300 TL) anlatır; ilk günün (2024-12-31, +100 TL) tarihi/değeri hiç
        geçmez. Mutant altında tarih '31.12.2024' ve tutar '+100 TL' olurdu."""
        with tempfile.TemporaryDirectory() as d:
            rapor = {"ad": "secili", "kapsam": {"tip": "liste"}, "cache": self._cache_yolu(d)}
            ozet = self.module.rapor_ozeti(rapor)
        self.assertIn("02.01.2025", ozet)
        self.assertIn("+300 TL", ozet)
        self.assertNotIn("31.12.2024", ozet)
        self.assertNotIn("+100 TL", ozet)


class FmtTlFormattingTests(unittest.TestCase):
    """fmt_tl: pano `_money` ile birebir aynı Türkçe biçim (binlik `.`, ondalık `,`, 0 işaretsiz)."""

    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def test_fmt_tl_matches_dashboard_money_reference_values(self):
        """Pano `_money`'nin ürettiği biçimle birebir eşleşen referans değerler.
        Regresyon: nokta ondalığa dönüş (ör. "+1.23 mlr TL") ya da 0 için
        işaretli "+0 TL" bu değerlerden en az birini kırar."""
        vakalar = [
            (1234567890, "+1,23 mlr TL"),
            (1234567, "+1,2 mn TL"),
            (-987654321, "−987,7 mn TL"),
            (-999, "−999 TL"),
            (0, "0 TL"),
            (12345, "+12.345 TL"),
        ]
        for deger, beklenen in vakalar:
            with self.subTest(deger=deger):
                self.assertEqual(self.module.fmt_tl(deger), beklenen)


if __name__ == "__main__":
    unittest.main()
