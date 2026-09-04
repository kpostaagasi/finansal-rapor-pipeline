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


if __name__ == "__main__":
    unittest.main()
