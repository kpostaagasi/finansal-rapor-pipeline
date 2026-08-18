import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "3_tefas_fon_akis_maili" / "secili_yenile.py"


def load_module():
    spec = importlib.util.spec_from_file_location("secili_refresh_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class RefreshSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def test_allow_publish_false_keeps_refresh_local(self):
        report = {"ad": "secili", "html": "/tmp/secili.html"}
        with (
            patch.object(self.module, "load_config", return_value={"allow_publish": False}),
            patch.object(self.module, "rapor_configleri", return_value=[report]),
            patch.object(self.module, "generate_report", return_value=True) as generate,
            patch.object(self.module, "html_hash", return_value="new"),
            patch.object(self.module, "onceki_hash", return_value="old"),
            patch.object(self.module, "push_to_github") as publish,
            patch.object(sys, "argv", ["secili_yenile.py"]),
        ):
            result = self.module.main()

        self.assertEqual(result, 0)
        generate.assert_called_once_with(report)
        publish.assert_not_called()

    def test_no_push_keeps_refresh_local_even_when_publish_is_allowed(self):
        report = {"ad": "secili", "html": "/tmp/secili.html"}
        with (
            patch.object(self.module, "load_config", return_value={"allow_publish": True}),
            patch.object(self.module, "rapor_configleri", return_value=[report]),
            patch.object(self.module, "generate_report", return_value=True),
            patch.object(self.module, "push_to_github") as publish,
            patch.object(sys, "argv", ["secili_yenile.py", "--no-push"]),
        ):
            result = self.module.main()

        self.assertEqual(result, 0)
        publish.assert_not_called()

    def test_changed_report_publish_updates_dashboard(self):
        report = {"ad": "secili", "html": "/tmp/secili.html"}
        with tempfile.TemporaryDirectory() as tmp:
            marker = str(Path(tmp) / ".last_push_secili")
            with (
                patch.object(self.module, "load_config", return_value={"allow_publish": True}) as config,
                patch.object(self.module, "rapor_configleri", return_value=[report]),
                patch.object(self.module, "generate_report", return_value=True),
                patch.object(self.module, "html_hash", return_value="new"),
                patch.object(self.module, "onceki_hash", return_value="old"),
                patch.object(self.module, "hash_dosyasi", return_value=marker),
                patch.object(self.module, "push_to_github", return_value=True),
                patch.object(self.module, "publish_dashboard", return_value=True, create=True) as dashboard,
                patch.object(sys, "argv", ["secili_yenile.py"]),
            ):
                result = self.module.main()

        self.assertEqual(result, 0)
        dashboard.assert_called_once_with(config.return_value)


if __name__ == "__main__":
    unittest.main()
