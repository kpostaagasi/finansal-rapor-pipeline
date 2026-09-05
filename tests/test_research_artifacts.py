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
RRD_MODULE_PATH = ROOT / "scripts" / "research_report_data.py"
DASHBOARD_RRD_PATH = (
    Path.home()
    / "Documents"
    / "GitHub"
    / "bv-fon-dashboard"
    / "Güncellenecek Kodlar"
    / "research_report_data.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("research_artifacts_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_rrd_module(path: Path = RRD_MODULE_PATH, name: str = "research_report_data_test"):
    """`research_report_data.py`'yi bağımsız bir modül olarak yükler.

    `build_research_artifacts.py`'nin kendi `sys.path` içine eklediği kopyadan
    ayrı tutulur: amaç iki tarafı (üretici çıktısı ve sözleşme sabiti) birbirinden
    bağımsız okuyup karşılaştırmak.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


DATES = ["2026-09-02", "2026-09-03"]


def report_html(raw: dict, expected: int, count_label: str = "fon") -> str:
    meta = {
        "data_end_date": DATES[-1],
        "expected_count": expected,
        "found_count": expected,
        "missing": [],
        "count_label": count_label,
        "source": "TEFAS",
    }
    return (
        "<html><script>"
        f"const REPORT_META = {json.dumps(meta)};\n"
        f"const RAW = {json.dumps(raw, ensure_ascii=False)};"
        "</script></html>"
    )


def build(module, selected_funds: dict[str, list[float]]):
    """Sekiz raporun tamamını sentetik HTML'lerden kurar."""
    satirli = lambda satirlar, birim="fon": report_html(
        {
            "d": DATES,
            "f": satirlar,
            "ad": {kod: f"{kod} SATIRI" for kod in satirlar},
        },
        len(satirlar),
        birim,
    )
    sources = {}
    for key in ("gold_by_fund", "precious_metals", "money_market",
                "participation", "equity", "debt"):
        sources[key] = satirli({"AFO": [1.0, 2.0]})
    sources["selected_funds"] = satirli(selected_funds)
    sources["fund_groups"] = satirli({"PAR-YAT": [5.0, 6.0]}, birim="grup")
    return module.build_fund_artifact(
        sources, today=date.fromisoformat(DATES[-1])
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
                "gold_by_fund": "Altın Fonları Fon Bazında Net Akış",
                "selected_funds": "Seçili Fonlara Net Akış",
                "precious_metals": "Kıymetli Maden Fonlarına Net Akış",
                "money_market": "Para Piyasası Fonlarına Net Akış",
                "participation": "Katılım Fonlarına Net Akış",
                "equity": "Hisse Senedi Fonlarına Net Akış",
                "debt": "Borçlanma Araçları Fonlarına Net Akış",
                "fund_groups": "Fon Gruplarına Net Akış",
            },
        )

    def test_missing_report_source_fails_closed(self):
        module = load_module()
        sources = {
            "gold_by_fund": report_html(
                {"d": DATES, "f": {"AFO": [1.0, 2.0]}, "ad": {"AFO": "AFO SATIRI"}}, 1
            )
        }
        with self.assertRaises(ValueError) as ctx:
            module.build_fund_artifact(sources)
        self.assertIn("zorunlu rapor kaynağı eksik", str(ctx.exception))

    def test_fund_reports_table_dropped_the_gold_total_chain(self):
        """`gold_total` (Altın Toplam) research zincirinden tamamen kaldırıldı;
        FUND_REPORTS'a `2_tefas_altin_akis/` altından bir üretici tekrar
        eklenirse (grup serisi geri sızarsa) bu test yakalar."""
        module = load_module()
        keys = [key for key, _, _ in module.FUND_REPORTS]
        self.assertNotIn("gold_total", keys)
        self.assertEqual(len(module.FUND_REPORTS), 8)
        for _, _, (klasor, _dosya) in module.FUND_REPORTS:
            self.assertNotEqual(klasor, "2_tefas_altin_akis")

    def test_group_rows_keep_their_unit_label(self):
        module = load_module()
        artifact = build(module, {"TLY": [1.0, 2.0]})
        gruplar = artifact["data"]["reports"]["fund_groups"]
        self.assertEqual(gruplar["metadata"]["count_label"], "grup")
        self.assertEqual(
            artifact["data"]["reports"]["equity"]["metadata"]["count_label"], "fon"
        )

    def test_fund_artifact_schema_version_matches_contract_module(self):
        """Regresyon: `build_fund_artifact` çıktısındaki `schema_version` üretici
        içinde tekrar sabit bir sayıya (ör. eski `1`) döner ya da bump edilmesi
        unutulursa, bu değer `research_report_data.SCHEMA_VERSION`'dan sapar ve
        bu test kırılır — tam olarak 04.09.2026'da yaşanan uyuşmazlığın sınıfı."""
        module = load_module()
        rrd = load_rrd_module()
        artifact = build(module, {"TLY": [1.0, 2.0]})
        self.assertEqual(artifact["schema_version"], rrd.SCHEMA_VERSION)

    def test_schema_version_matches_dashboard_copy(self):
        """Regresyon: pipeline ve dashboard depolarındaki `research_report_data.py`
        kopyalarından biri bump edilip diğeri edilmezse (kod GitHub → Streamlit
        Cloud, veri Pages ile bağımsız dağıtıldığı için tam olarak böyle bir
        ayrışma üretimde yanıltıcı hataya yol açmıştı) bu test yakalar. Dashboard
        deposu bu makinede yoksa test atlanır, kırılmaz."""
        if not DASHBOARD_RRD_PATH.is_file():
            self.skipTest(f"dashboard deposu bulunamadı: {DASHBOARD_RRD_PATH}")
        local = load_rrd_module()
        dashboard = load_rrd_module(
            DASHBOARD_RRD_PATH, name="research_report_data_dashboard_test"
        )
        self.assertEqual(local.SCHEMA_VERSION, dashboard.SCHEMA_VERSION)

    def test_schema_version_mismatch_fails_closed(self):
        """Regresyon: artifact veya farklı sürümde üretilmiş bir manifest kod
        sürümünden sapan bir `schema_version` taşırsa (eski/yeni deploy karışımı),
        `validate_artifact` bunu sessizce kabul edip yanıltıcı 'zorunlu ... eksik'
        hatasına dönüşmemeli; `ArtifactSchemaMismatch` ile fail-closed düşmeli ve
        mesaj her iki sürümü de bildirmeli."""
        module = load_module()
        rrd = load_rrd_module()
        artifact = build(module, {"TLY": [1.0, 2.0]})
        for bad_version in (rrd.SCHEMA_VERSION - 1, rrd.SCHEMA_VERSION + 1):
            with self.subTest(bad_version=bad_version):
                mutated = dict(artifact)
                mutated["schema_version"] = bad_version
                with self.assertRaises(rrd.ArtifactSchemaMismatch) as ctx:
                    rrd.validate_artifact(mutated, "fund_flows")
                message = str(ctx.exception)
                self.assertIn(str(bad_version), message)
                self.assertIn(str(rrd.SCHEMA_VERSION), message)


if __name__ == "__main__":
    unittest.main()
