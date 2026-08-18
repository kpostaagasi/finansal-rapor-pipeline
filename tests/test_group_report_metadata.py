import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "2_tefas_altin_akis" / "tefas_akis.py"


def load_module():
    spec = importlib.util.spec_from_file_location("group_report_metadata_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class GroupReportMetadataTests(unittest.TestCase):
    def test_group_report_embeds_latest_fund_counts_and_data_date(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = root / "template.html"
            output = root / "report.html"
            desktop = root / "desktop.html"
            template.write_text(
                "const REPORT_META = __REPORT_META__; const RAW = __RAW__; "
                "__YF_N__ __EYF_N__ __YF0_N__ __EYF0_N__ __ILK_TARIH__",
                encoding="utf-8",
            )
            module.TEMPLATE = str(template)
            module.OUT = str(output)
            module.DESKTOP_COPY = str(desktop)
            cache = {
                "d": ["2026-08-14", "2026-08-17"],
                "yf": [100, 200],
                "eyf": [10, 20],
                "yf_n": {"2026-08-14": 48, "2026-08-17": 48},
                "eyf_n": {"2026-08-14": 16, "2026-08-17": 16},
                "yf_expected_n": {"2026-08-17": 48},
                "eyf_expected_n": {"2026-08-17": 16},
                "yf_calc_n": {"2026-08-17": 48},
                "eyf_calc_n": {"2026-08-17": 16},
                "yf_missing": {"2026-08-17": []},
                "eyf_missing": {"2026-08-17": []},
            }

            module.html_uret(cache)

            rendered = output.read_text(encoding="utf-8")
            match = re.search(r"const REPORT_META = (\{.*?\});", rendered)
            self.assertIsNotNone(match)
            meta = json.loads(match.group(1))
            self.assertEqual(meta["data_end_date"], "2026-08-17")
            self.assertEqual(meta["expected_count"], 64)
            self.assertEqual(meta["found_count"], 64)
            self.assertEqual(meta["missing"], [])
            self.assertEqual(meta["count_label"], "fon")
            self.assertEqual(meta["source"], "TEFAS")

    def test_group_report_marks_latest_partial_coverage(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = root / "template.html"
            output = root / "report.html"
            template.write_text(
                "const REPORT_META = __REPORT_META__; const RAW = __RAW__; "
                "__YF_N__ __EYF_N__ __YF0_N__ __EYF0_N__ __ILK_TARIH__",
                encoding="utf-8",
            )
            module.TEMPLATE = str(template)
            module.OUT = str(output)
            module.DESKTOP_COPY = str(root / "desktop.html")
            cache = {
                "d": ["2026-08-14", "2026-08-17"],
                "yf": [100, None], "eyf": [10, 20],
                "yf_n": {"2026-08-14": 48, "2026-08-17": 47},
                "eyf_n": {"2026-08-14": 16, "2026-08-17": 16},
                "yf_expected_n": {"2026-08-17": 48},
                "eyf_expected_n": {"2026-08-17": 16},
                "yf_calc_n": {"2026-08-17": 47},
                "eyf_calc_n": {"2026-08-17": 16},
                "yf_missing": {"2026-08-17": ["AAA"]},
                "eyf_missing": {"2026-08-17": []},
            }

            module.html_uret(cache)
            rendered = output.read_text(encoding="utf-8")
            meta = json.loads(re.search(r"const REPORT_META = (\{.*?\});", rendered).group(1))

            self.assertEqual(meta["expected_count"], 64)
            self.assertEqual(meta["found_count"], 63)
            self.assertEqual(meta["missing"], ["AAA"])
            self.assertEqual(meta["uncomputed_count"], 1)

    def test_production_template_embeds_report_metadata(self):
        template = (MODULE_PATH.parent / "tefas_template.html").read_text(encoding="utf-8")
        self.assertIn("const REPORT_META = __REPORT_META__;", template)


if __name__ == "__main__":
    unittest.main()
