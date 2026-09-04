"""Dashboard artifact sözleşmesi: başlıklar kapsam değişince yanlışa dönmemeli.

`Seçili 15 Fon Net Akış` başlığı fon listesi 25'e çıktıktan sonra da yayında
kalmıştı; sayı başlığa gömülü olduğu için sessizce yanlışa döndü. Gerçek kapsam
`metadata.expected_count`'ta bulunur; bu test başlıkların sayı taşımadığını ve
kapsamın metadata'dan geldiğini doğrular.
"""

import importlib.util
import json
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "build_research_artifacts.py"


def load_module():
    spec = importlib.util.spec_from_file_location("research_artifacts_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


DATES = ["2026-09-02", "2026-09-03"]


def report_html(raw: dict, expected: int) -> str:
    meta = {
        "data_end_date": DATES[-1],
        "expected_count": expected,
        "found_count": expected,
        "missing": [],
        "source": "TEFAS",
    }
    return (
        "<html><script>"
        f"const REPORT_META = {json.dumps(meta)};\n"
        f"const RAW = {json.dumps(raw, ensure_ascii=False)};"
        "</script></html>"
    )


def build(module, selected_funds: dict[str, list[float]]):
    group = report_html({"d": DATES, "yf": [1.0, 2.0], "eyf": [3.0, 4.0]}, 65)
    gold = report_html(
        {"d": DATES, "f": {"AFO": [1.0, 2.0]}, "ad": {"AFO": "AK PORTFÖY ALTIN FONU"}},
        65,
    )
    selected = report_html(
        {
            "d": DATES,
            "f": selected_funds,
            "ad": {code: f"{code} FONU" for code in selected_funds},
        },
        len(selected_funds),
    )
    return module.build_fund_artifact(
        group, gold, selected, today=date.fromisoformat(DATES[-1])
    )


class ResearchArtifactTests(unittest.TestCase):
    def test_report_titles_do_not_hardcode_fund_counts(self):
        module = load_module()
        artifact = build(module, {"TLY": [1.0, 2.0], "THF": [3.0, 4.0]})
        for key, report in artifact["data"]["reports"].items():
            with self.subTest(report=key):
                self.assertNotRegex(
                    report["title"],
                    r"\d",
                    "başlığa sayı gömülmüş: liste değişince sessizce yanlışa döner",
                )

    def test_coverage_comes_from_report_metadata(self):
        module = load_module()
        artifact = build(module, {f"F{i:02d}": [1.0, 2.0] for i in range(25)})
        selected = artifact["data"]["reports"]["selected_funds"]
        self.assertEqual(selected["metadata"]["expected_count"], 25)
        self.assertEqual(selected["metadata"]["found_count"], 25)
        self.assertEqual(selected["status"], "ready")
        self.assertEqual(len(selected["series"]["f"]), 25)

    def test_published_titles_match_the_dashboard_wording(self):
        module = load_module()
        artifact = build(module, {"TLY": [1.0, 2.0]})
        titles = {k: v["title"] for k, v in artifact["data"]["reports"].items()}
        self.assertEqual(
            titles,
            {
                "gold_total": "Altın Fonları Toplam Net Akış",
                "gold_by_fund": "Altın Fonları Fon Bazında Net Akış",
                "selected_funds": "Seçili Fonlara Net Akış",
            },
        )


if __name__ == "__main__":
    unittest.main()
