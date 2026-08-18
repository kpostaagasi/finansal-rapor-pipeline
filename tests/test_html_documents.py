import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class HtmlDocumentTests(unittest.TestCase):
    def assert_html5_document(self, document: str):
        normalized = document.lstrip().lower()
        self.assertTrue(normalized.startswith("<!doctype html>"))
        self.assertIn('<html lang="tr">', normalized)
        self.assertIn("<head>", normalized)
        self.assertIn('<meta charset="utf-8">', normalized)
        self.assertIn('<meta name="viewport" content="width=device-width, initial-scale=1">', normalized)
        self.assertIn("<body>", normalized)
        self.assertTrue(normalized.rstrip().endswith("</html>"))

    def test_commodity_report_is_complete_html5_document(self):
        module = load_module(
            "commodity_html_document_test",
            ROOT / "1_emtia_tahvil_maili" / "emtia_report.py",
        )
        self.assert_html5_document(module.HTML)

    def test_tefas_templates_are_complete_html5_documents(self):
        paths = [
            ROOT / "2_tefas_altin_akis" / "tefas_template.html",
            ROOT / "3_tefas_fon_akis_maili" / "secili_template.html",
        ]
        for path in paths:
            with self.subTest(path=path.name):
                self.assert_html5_document(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
