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

    def test_external_fund_name_cannot_close_script_context(self):
        module = load_module()
        payload = {"name": "</script><script>window.auditXss=1</script>"}

        encoded = module.script_json(payload)

        self.assertNotIn("</script>", encoded.lower())
        self.assertEqual(json.loads(encoded), payload)


if __name__ == "__main__":
    unittest.main()
