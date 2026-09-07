import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "3_tefas_fon_akis_maili" / "tefas_secili.py"


def load_module():
    spec = importlib.util.spec_from_file_location("tefas_secili_completeness_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TefasReportCompletenessTests(unittest.TestCase):
    def test_production_template_embeds_report_metadata(self):
        template = (MODULE_PATH.parent / "secili_template.html").read_text(encoding="utf-8")
        self.assertIn("const REPORT_META = __REPORT_META__;", template)

    def test_list_report_discloses_missing_requested_fund_codes(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "fonlar.json").write_text(
                json.dumps({"fonlar": ["PHE", "RNP"]}), encoding="utf-8"
            )
            template = root / "template.html"
            template.write_text(
                "__BASLIK__|__FON_N__/__ISTENEN_N__ fon bulundu|__EKSIK_NOT__|"
                "__ILK_TARIH__|__SON_TARIH__|const REPORT_META = __REPORT_META__;|__RAW__",
                encoding="utf-8",
            )
            output = root / "report.html"
            module.HERE = str(root)
            module.TEMPLATE = str(template)
            cfg = {
                "baslik": "Seçili Fonlar",
                "kapsam": {"tip": "liste", "dosya": "fonlar.json"},
                "html": str(output),
                "desktop": str(root / "desktop.html"),
            }
            cache = {
                "fon": {"PHE": {"2024-12-30": [100, 1], "2024-12-31": [110, 1]}},
                "ad": {"PHE": "Pusula Portföy Hisse Senedi Fonu"},
                "tip": {"PHE": "YAT"},
            }

            module.html_uret(cfg, cache)
            rendered = output.read_text(encoding="utf-8")

            self.assertIn("1/2 fon bulundu", rendered)
            self.assertIn("Eksik kodlar: RNP", rendered)
            self.assertNotIn("__ISTENEN_N__", rendered)
            self.assertNotIn("__EKSIK_NOT__", rendered)
            match = re.search(r"const REPORT_META = (\{.*?\});", rendered)
            self.assertIsNotNone(match)
            meta = json.loads(match.group(1))
            self.assertEqual(meta["expected_count"], 2)
            self.assertEqual(meta["found_count"], 1)
            self.assertEqual(meta["missing"], ["RNP"])
            self.assertEqual(meta["data_end_date"], "2024-12-31")
            self.assertEqual(meta["source"], "TEFAS")

    def test_list_report_measures_coverage_on_latest_date_not_historical_cache(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "fonlar.json").write_text(
                json.dumps({"fonlar": ["AAA", "BBB"]}), encoding="utf-8"
            )
            template = root / "template.html"
            template.write_text(
                "__BASLIK__|__FON_N__/__ISTENEN_N__|__FON_OZET__|__EKSIK_NOT__|"
                "__ILK_TARIH__|__SON_TARIH__|const REPORT_META = __REPORT_META__;|"
                "const RAW = __RAW__;",
                encoding="utf-8",
            )
            output = root / "report.html"
            module.HERE = str(root)
            module.TEMPLATE = str(template)
            cfg = {
                "baslik": "Seçili Fonlar",
                "kapsam": {"tip": "liste", "dosya": "fonlar.json"},
                "html": str(output),
                "desktop": str(root / "desktop.html"),
            }
            cache = {
                "fon": {
                    "AAA": {"2026-08-14": [100, 1], "2026-08-17": [110, 1]},
                    "BBB": {"2026-08-14": [200, 1]},
                },
                "ad": {"AAA": "AAA", "BBB": "BBB"},
                "tip": {"AAA": "YAT", "BBB": "YAT"},
            }

            module.html_uret(cfg, cache)
            rendered = output.read_text(encoding="utf-8")
            match = re.search(r"const REPORT_META = (\{.*?\});", rendered)
            meta = json.loads(match.group(1))

            self.assertEqual(meta["expected_count"], 2)
            self.assertEqual(meta["found_count"], 1)
            self.assertEqual(meta["missing"], ["BBB"])
            self.assertIn("Eksik kodlar: BBB", rendered)

    def test_gap_count_and_note_reflect_the_actual_number_of_uncomputed_fund_days(self):
        """X: report_meta['gap_count'] (ve raw['gap_count']) gerçek boşluk
        sayısını yansıtmalı; eksik notundaki 'N fon-gün akışı hesaplanmadı'
        cümlesi de aynı sayıyı taşımalı. Mutant `gap_count = 0` hem sayacı
        sessizce sıfırlar hem notu hiç göstermez (bosluk_n falsy olur)."""
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "fonlar.json").write_text(
                json.dumps({"fonlar": ["AAA", "GAP1"]}), encoding="utf-8"
            )
            template = root / "template.html"
            template.write_text(
                "__BASLIK__|__FON_N__/__ISTENEN_N__|__FON_OZET__|__EKSIK_NOT__|"
                "__ILK_TARIH__|__SON_TARIH__|const REPORT_META = __REPORT_META__;|"
                "const RAW = __RAW__;",
                encoding="utf-8",
            )
            output = root / "report.html"
            module.HERE = str(root)
            module.TEMPLATE = str(template)
            cfg = {
                "baslik": "Test",
                "kapsam": {"tip": "liste", "dosya": "fonlar.json"},
                "html": str(output),
                "desktop": str(root / "desktop.html"),
            }
            cache = {
                "fon": {
                    "AAA": {"2026-09-01": [100, 1.0], "2026-09-02": [110, 1.0],
                            "2026-09-03": [120, 1.0]},
                    "GAP1": {"2026-09-01": [10, 1.0], "2026-09-03": [30, 1.0]},  # 09-02 boşluk
                },
                "ad": {"AAA": "A", "GAP1": "G"},
                "tip": {"AAA": "YAT", "GAP1": "YAT"},
            }

            module.html_uret(cfg, cache)
            rendered = output.read_text(encoding="utf-8")
            meta = json.loads(re.search(r"const REPORT_META = (\{.*?\});", rendered).group(1))
            raw = json.loads(re.search(r"const RAW = (\{.*?\});", rendered).group(1))

            self.assertEqual(meta["gap_count"], 1)
            self.assertEqual(raw["gap_count"], 1)
            self.assertIn(
                "Ardışık TEFAS gözlemi olmayan 1 fon-gün akışı hesaplanmadı.",
                rendered,
            )

    def test_raw_flow_cells_use_round_half_to_even_not_truncation(self):
        """V: raw['f'] hücreleri round() ile üretilir. Gerçek Python
        davranışı ölçüldü: round(3.5)=4 (bankacı yuvarlaması en yakın çift
        sayıya gider), int(3.5)=3; round(-2.7)=-3, int(-2.7)=-2 — int()
        kesmesine geçilirse bu hücreler sessizce farklı (yanlış) değer verir."""
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "fonlar.json").write_text(
                json.dumps({"fonlar": ["TIE", "NEG"]}), encoding="utf-8"
            )
            template = root / "template.html"
            template.write_text(
                "__BASLIK__|__FON_N__/__ISTENEN_N__|__FON_OZET__|__EKSIK_NOT__|"
                "__ILK_TARIH__|__SON_TARIH__|const REPORT_META = __REPORT_META__;|"
                "const RAW = __RAW__;",
                encoding="utf-8",
            )
            output = root / "report.html"
            module.HERE = str(root)
            module.TEMPLATE = str(template)
            cfg = {
                "baslik": "Test",
                "kapsam": {"tip": "liste", "dosya": "fonlar.json"},
                "html": str(output),
                "desktop": str(root / "desktop.html"),
            }
            cache = {
                "fon": {
                    # (107-100)*0.5 = 3.5 -> round: 4 (en yakın çift), int: 3
                    "TIE": {"2026-09-01": [100, 1.0], "2026-09-02": [107, 0.5]},
                    # (73-100)*0.1 = -2.7 -> round: -3, int: -2
                    "NEG": {"2026-09-01": [100, 1.0], "2026-09-02": [73, 0.1]},
                },
                "ad": {"TIE": "TIE", "NEG": "NEG"},
                "tip": {"TIE": "YAT", "NEG": "YAT"},
            }

            module.html_uret(cfg, cache)
            rendered = output.read_text(encoding="utf-8")
            raw = json.loads(re.search(r"const RAW = (\{.*?\});", rendered).group(1))

            self.assertEqual(raw["f"]["TIE"][-1], 4)
            self.assertEqual(raw["f"]["NEG"][-1], -3)

    def test_external_fund_name_cannot_close_script_context(self):
        module = load_module()
        payload = {"name": "</script><script>window.auditXss=1</script>"}

        encoded = module.script_json(payload)

        self.assertNotIn("</script>", encoded.lower())
        self.assertEqual(json.loads(encoded), payload)

    def _yerel_kopya_kosumu(self, yerel_kopya):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "fonlar.json").write_text(
                json.dumps({"fonlar": ["PHE"]}), encoding="utf-8"
            )
            template = root / "template.html"
            template.write_text(
                "__BASLIK__|__FON_N__/__ISTENEN_N__|__EKSIK_NOT__|__ILK_TARIH__|"
                "__SON_TARIH__|const REPORT_META = __REPORT_META__;|__RAW__",
                encoding="utf-8",
            )
            output = root / "report.html"
            desktop = root / "desktop.html"
            module.HERE = str(root)
            module.TEMPLATE = str(template)
            module.YEREL_KOPYA = yerel_kopya
            module.html_uret(
                {
                    "baslik": "Seçili Fonlar",
                    "kapsam": {"tip": "liste", "dosya": "fonlar.json"},
                    "html": str(output),
                    "desktop": str(desktop),
                },
                {
                    "fon": {"PHE": {"2024-12-30": [100, 1], "2024-12-31": [110, 1]}},
                    "ad": {"PHE": "Pusula Portföy Hisse Senedi Fonu"},
                    "tip": {"PHE": "YAT"},
                },
            )
            return output.exists(), desktop.exists()

    def test_default_run_does_not_write_the_local_documents_copy(self):
        """`~/Documents` kopyası opt-in; bayrak yoksa yalnız repo çıktısı yazılır."""
        repo, yerel = self._yerel_kopya_kosumu(yerel_kopya=False)

        self.assertTrue(repo)
        self.assertFalse(yerel)

    def test_flag_enables_the_local_documents_copy(self):
        """Bayrakla eski davranış (iki hedefe yazım) aynen geri gelir."""
        repo, yerel = self._yerel_kopya_kosumu(yerel_kopya=True)

        self.assertTrue(repo)
        self.assertTrue(yerel)


if __name__ == "__main__":
    unittest.main()
