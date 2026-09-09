import importlib.util
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "build_site.py"


def load_module():
    spec = importlib.util.spec_from_file_location("build_site_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class BuildSiteTests(unittest.TestCase):
    def test_failure_arguments_are_parsed_without_losing_message_spaces(self):
        module = load_module()
        failures = module._parse_failures([
            "--failure=tefas_altin_fon_bazinda.html=TEFAS veri üretimi başarısız",
            "--ignored",
        ])
        self.assertEqual(
            failures,
            {"tefas_altin_fon_bazinda.html": "TEFAS veri üretimi başarısız"},
        )

    def test_build_site_copies_ready_reports_and_marks_missing_ones(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            emtia = root / "1_emtia_tahvil_maili" / "emtia_futures.html"
            altin = root / "3_tefas_fon_akis_maili" / "tefas_altin_akis.html"
            emtia.parent.mkdir(parents=True)
            altin.parent.mkdir(parents=True)
            emtia.write_text("<html>emtia</html>", encoding="utf-8")
            altin.write_text("<html>altin</html>", encoding="utf-8")

            site = root / "site"
            result = module.build_site(root=root, site_dir=site)

            self.assertEqual(result["ready"], 2)
            # Rapor sayısı büyüdükçe kaymasın: hazır olmayan her kaynak eksik sayılır.
            self.assertEqual(result["missing"], len(module.REPORTS) - 2)
            self.assertEqual((site / "emtia_futures.html").read_text(), "<html>emtia</html>")
            self.assertEqual((site / "tefas_altin_fon_bazinda.html").read_text(), "<html>altin</html>")
            index = (site / "index.html").read_text(encoding="utf-8")
            self.assertIn("Emtia Futures ve ABD Hazine Eğrisi", index)
            self.assertIn('href="emtia_futures.html"', index)
            self.assertIn("TEFAS Seçili Fonlara Net Akış", index)
            self.assertIn("Henüz hazır değil", index)
            self.assertNotIn('href="tefas_secili_akis.html"', index)

    def test_build_site_rejects_report_outside_project_root(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            site = root / "site"
            original = module.REPORTS
            module.REPORTS = ({
                "title": "Dış dosya",
                "description": "test",
                "source": "../secret.html",
                "target": "secret.html",
            },)
            try:
                with self.assertRaises(ValueError):
                    module.build_site(root=root, site_dir=site)
            finally:
                module.REPORTS = original

    def test_dashboard_uses_report_metadata_for_coverage_source_and_staleness(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = dt.date(2026, 8, 17)
            emtia = root / "1_emtia_tahvil_maili" / "emtia_futures.html"
            altin = root / "3_tefas_fon_akis_maili" / "tefas_altin_akis.html"
            emtia.parent.mkdir(parents=True)
            altin.parent.mkdir(parents=True)
            emtia_meta = {
                "last_successful_run": "2026-08-17T14:00:00+03:00",
                "data_end_date": "2026-08-17",
                "expected_count": 6,
                "found_count": 5,
                "missing": ["Alüminyum"],
                "count_label": "emtia",
                "source": "Yahoo Finance + ABD Hazinesi",
            }
            altin_meta = {
                "last_successful_run": "2026-08-10T10:00:00+03:00",
                "data_end_date": "2026-08-10",
                "expected_count": 64,
                "found_count": 64,
                "missing": [],
                "count_label": "fon",
                "source": "TEFAS",
            }
            emtia.write_text(
                f"<script>const REPORT_META = {json.dumps(emtia_meta)};</script>",
                encoding="utf-8",
            )
            altin.write_text(
                f"<script>const REPORT_META = {json.dumps(altin_meta)};</script>",
                encoding="utf-8",
            )

            site = root / "site"
            module.build_site(root=root, site_dir=site, now=current)

            index = (site / "index.html").read_text(encoding="utf-8")
            self.assertIn("5/6 emtia", index)
            self.assertIn("Eksik: Alüminyum", index)
            self.assertIn("Yahoo Finance + ABD Hazinesi", index)
            self.assertIn("Eksik veri", index)
            self.assertIn("Veri güncel değil", index)
            self.assertIn("10.08.2026", index)

            statuses = json.loads((site / "report_status.json").read_text())
            self.assertEqual(statuses["emtia_futures.html"]["status"], "partial")
            self.assertEqual(statuses["tefas_altin_fon_bazinda.html"]["status"], "stale")

    def test_dashboard_marks_preserved_report_failed_when_generation_fails(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "3_tefas_fon_akis_maili" / "tefas_altin_akis.html"
            report.parent.mkdir(parents=True)
            report.write_text("<html>last known good</html>", encoding="utf-8")

            site = root / "site"
            module.build_site(
                root=root,
                site_dir=site,
                failures={"tefas_altin_fon_bazinda.html": "TEFAS veri üretimi başarısız"},
            )

            index = (site / "index.html").read_text(encoding="utf-8")
            self.assertIn("Başarısız", index)
            self.assertIn("TEFAS veri üretimi başarısız", index)
            self.assertIn('href="tefas_altin_fon_bazinda.html"', index)
            statuses = json.loads((site / "report_status.json").read_text())
            self.assertEqual(statuses["tefas_altin_fon_bazinda.html"]["status"], "failed")
            self.assertEqual(
                statuses["tefas_altin_fon_bazinda.html"]["error_message"],
                "TEFAS veri üretimi başarısız",
            )

    def test_dashboard_shows_only_current_liquid_commodity_contracts(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "1_emtia_tahvil_maili" / "emtia_futures.html"
            report.parent.mkdir(parents=True)
            meta = {
                "last_successful_run": "2026-08-17T14:00:00+03:00",
                "data_end_date": "2026-08-17",
                "candidate_count": 52,
                "expected_count": 44,
                "found_count": 44,
                "missing": [],
                "excluded_stale_quotes": [
                    {"commodity": "Altın", "symbol": "GCV27", "age_days": 24.9}
                ],
                "treasury_expected_count": 12,
                "treasury_found_count": 12,
                "count_label": "kontrat",
                "source": "Yahoo Finance + ABD Hazinesi",
            }
            report.write_text(
                f"<script>const REPORT_META = {json.dumps(meta)};</script>",
                encoding="utf-8",
            )

            site = root / "site"
            module.build_site(root=root, site_dir=site, now=dt.date(2026, 8, 17))

            index = (site / "index.html").read_text(encoding="utf-8")
            self.assertIn("Güncel", index)
            self.assertIn("Kapsam: 44 güncel/likit kontrat", index)
            self.assertIn("ABD Hazine: 12/12 vade", index)
            self.assertIn("Filtrelenen seyrek vadeler: GCV27 (24,9 gün)", index)
            self.assertNotIn("Seyrek fiyat", index)
            self.assertNotIn("Eksik veri", index)
            statuses = json.loads((site / "report_status.json").read_text())
            self.assertEqual(statuses["emtia_futures.html"]["status"], "ready")

    def test_partial_report_stays_partial_while_unrelated_failure_message_flows_through(self):
        # B4 regresyonu: production.yml artık kısmi kapsamlı (exit 4) raporları
        # --failure OLMADAN build_site.py'ye bırakır (REPORT_META'dan otomatik
        # 'partial' çıkarımı için), yalnız gerçek üretim hatası yaşayan raporlar
        # için --failure=hedef=mesaj geçirir. Bu test iki durumun AYNI build_site
        # çağrısında birbirine karışmadığını ve workflow'un ürettiği mesaj
        # metninin report_status.json'a bozulmadan taşındığını kilitler.
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secili = root / "3_tefas_fon_akis_maili" / "tefas_secili_akis.html"
            emtia = root / "1_emtia_tahvil_maili" / "emtia_futures.html"
            secili.parent.mkdir(parents=True)
            emtia.parent.mkdir(parents=True)
            secili_meta = {
                "last_successful_run": "2026-09-04T14:00:00+03:00",
                "data_end_date": "2026-09-04",
                "expected_count": 25,
                "found_count": 25,
                "missing": [],
                "uncomputed_count": 1,
                "uncomputed": ["PRY"],
                "count_label": "fon",
                "source": "TEFAS",
            }
            secili.write_text(
                f"<script>const REPORT_META = {json.dumps(secili_meta)};</script>",
                encoding="utf-8",
            )
            emtia.write_text("<html>son bilinen iyi hal</html>", encoding="utf-8")

            # Workflow'un üretim adımının gerçek üretim hatası için ürettiği
            # --failure argümanı birebir bu biçimde.
            failure_arg = "--failure=emtia_futures.html=Emtia/ABD Hazine üretimi başarısız (exit 4)"
            failures = module._parse_failures([failure_arg])

            site = root / "site"
            module.build_site(
                root=root, site_dir=site, now=dt.date(2026, 9, 4), failures=failures,
            )

            statuses = json.loads((site / "report_status.json").read_text())
            self.assertEqual(statuses["tefas_secili_akis.html"]["status"], "partial")
            self.assertNotIn("error_message", statuses["tefas_secili_akis.html"])
            self.assertEqual(statuses["emtia_futures.html"]["status"], "failed")
            self.assertEqual(
                statuses["emtia_futures.html"]["error_message"],
                "Emtia/ABD Hazine üretimi başarısız (exit 4)",
            )


if __name__ == "__main__":
    unittest.main()
