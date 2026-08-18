import importlib.util
import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class GunlukMailSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module("gunluk_mail_test", "1_emtia_tahvil_maili/gunluk_mail.py")

    @staticmethod
    def config():
        return {
            "recipients": ["test@example.com"],
            "subject": "Test",
            "tefas_html": "/tmp/tefas.html",
            "tefas_github_path": "tefas.html",
        }

    def test_uses_installed_github_cli(self):
        installed = shutil.which("gh")
        self.assertIsNotNone(installed)
        self.assertEqual(self.module.GH, installed)

    def test_dry_run_generates_locally_without_publishing_or_sending(self):
        cfg = self.config()
        with (
            patch.object(self.module, "load_config", return_value=cfg),
            patch.object(self.module, "generate_report", return_value=True) as generate_emtia,
            patch.object(self.module, "generate_tefas", return_value=True) as generate_tefas,
            patch.object(self.module, "push_to_github") as publish,
            patch.object(self.module, "keychain_password") as keychain,
            patch.object(sys, "argv", ["gunluk_mail.py", "--dry-run"]),
        ):
            result = self.module.main()

        self.assertEqual(result, 0)
        generate_emtia.assert_called_once_with()
        generate_tefas.assert_called_once_with(cfg)
        publish.assert_not_called()
        keychain.assert_not_called()

    def test_dry_run_returns_failure_when_generation_fails(self):
        cfg = self.config()
        with (
            patch.object(self.module, "load_config", return_value=cfg),
            patch.object(self.module, "generate_report", return_value=False),
            patch.object(self.module, "generate_tefas", return_value=True),
            patch.object(self.module, "push_to_github") as publish,
            patch.object(self.module, "publish_dashboard") as dashboard,
            patch.object(sys, "argv", ["gunluk_mail.py", "--dry-run"]),
        ):
            result = self.module.main()

        self.assertEqual(result, 4)
        publish.assert_not_called()
        dashboard.assert_not_called()

    def test_no_push_still_generates_reports(self):
        cfg = self.config()
        with (
            patch.object(self.module, "load_config", return_value=cfg),
            patch.object(self.module, "generate_report", return_value=True) as generate_emtia,
            patch.object(self.module, "generate_tefas", return_value=True) as generate_tefas,
            patch.object(self.module, "push_to_github") as publish,
            patch.object(sys, "argv", ["gunluk_mail.py", "--no-push", "--dry-run"]),
        ):
            result = self.module.main()

        self.assertEqual(result, 0)
        generate_emtia.assert_called_once_with()
        generate_tefas.assert_called_once_with(cfg)
        publish.assert_not_called()

    def test_no_push_does_not_read_credentials_or_send_mail(self):
        cfg = self.config()
        with (
            patch.object(self.module, "load_config", return_value=cfg),
            patch.object(self.module, "generate_report", return_value=True),
            patch.object(self.module, "generate_tefas", return_value=True),
            patch.object(self.module, "push_to_github") as publish,
            patch.object(self.module, "keychain_password") as keychain,
            patch.object(sys, "argv", ["gunluk_mail.py", "--no-push"]),
        ):
            result = self.module.main()

        self.assertEqual(result, 0)
        publish.assert_not_called()
        keychain.assert_not_called()

    def test_live_command_is_blocked_when_config_safety_gates_are_closed(self):
        cfg = self.config() | {"allow_publish": False, "allow_send": False}
        with (
            patch.object(self.module, "load_config", return_value=cfg),
            patch.object(self.module, "generate_report", return_value=True),
            patch.object(self.module, "generate_tefas", return_value=True),
            patch.object(self.module, "push_to_github") as publish,
            patch.object(self.module, "keychain_password") as keychain,
            patch.object(sys, "argv", ["gunluk_mail.py"]),
        ):
            result = self.module.main()

        self.assertEqual(result, 3)
        publish.assert_not_called()
        keychain.assert_not_called()

    def test_generation_failure_blocks_partial_publish_and_mail(self):
        cfg = self.config() | {"allow_publish": True, "allow_send": True}
        with (
            patch.object(self.module, "load_config", return_value=cfg),
            patch.object(self.module, "generate_report", return_value=False),
            patch.object(self.module, "generate_tefas", return_value=True),
            patch.object(self.module, "push_to_github") as publish,
            patch.object(self.module, "publish_dashboard", return_value=True, create=True) as dashboard,
            patch.object(self.module, "keychain_password") as keychain,
            patch.object(sys, "argv", ["gunluk_mail.py"]),
        ):
            result = self.module.main()

        self.assertEqual(result, 4)
        publish.assert_not_called()
        dashboard.assert_called_once_with(
            cfg, {"emtia_futures.html": "Emtia/ABD Hazine veri üretimi başarısız"}
        )
        keychain.assert_not_called()

    def test_publish_failure_blocks_mail(self):
        cfg = self.config() | {"allow_publish": True, "allow_send": True}
        with (
            patch.object(self.module, "load_config", return_value=cfg),
            patch.object(self.module, "generate_report", return_value=True),
            patch.object(self.module, "generate_tefas", return_value=True),
            patch.object(self.module, "push_to_github", side_effect=[True, False]),
            patch.object(self.module, "keychain_password") as keychain,
            patch.object(sys, "argv", ["gunluk_mail.py"]),
        ):
            result = self.module.main()

        self.assertEqual(result, 5)
        keychain.assert_not_called()

    def test_successful_report_publish_updates_dashboard_before_send_gate(self):
        cfg = self.config() | {"allow_publish": True, "allow_send": False}
        with (
            patch.object(self.module, "load_config", return_value=cfg),
            patch.object(self.module, "generate_report", return_value=True),
            patch.object(self.module, "generate_tefas", return_value=True),
            patch.object(self.module, "push_to_github", return_value=True),
            patch.object(self.module, "publish_dashboard", return_value=True, create=True) as dashboard,
            patch.object(self.module, "keychain_password") as keychain,
            patch.object(sys, "argv", ["gunluk_mail.py"]),
        ):
            result = self.module.main()

        self.assertEqual(result, 3)
        dashboard.assert_called_once_with(cfg)
        keychain.assert_not_called()

    def test_dashboard_publish_failure_blocks_mail(self):
        cfg = self.config() | {"allow_publish": True, "allow_send": True}
        with (
            patch.object(self.module, "load_config", return_value=cfg),
            patch.object(self.module, "generate_report", return_value=True),
            patch.object(self.module, "generate_tefas", return_value=True),
            patch.object(self.module, "push_to_github", return_value=True),
            patch.object(self.module, "publish_dashboard", return_value=False, create=True),
            patch.object(self.module, "keychain_password") as keychain,
            patch.object(sys, "argv", ["gunluk_mail.py"]),
        ):
            result = self.module.main()

        self.assertEqual(result, 5)
        keychain.assert_not_called()


class SeciliMailSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module("secili_mail_test", "3_tefas_fon_akis_maili/secili_mail.py")

    @staticmethod
    def config():
        return {"recipients": ["test@example.com"], "subject": "Test"}

    @staticmethod
    def reports():
        return [
            {"ad": "altin", "github_path": "tefas_altin_fon_bazinda.html"},
            {"ad": "secili", "github_path": "tefas_secili_akis.html"},
        ]

    def test_uses_installed_github_cli(self):
        installed = shutil.which("gh")
        self.assertIsNotNone(installed)
        self.assertEqual(self.module.GH, installed)

    def test_dry_run_generates_locally_without_publishing_or_sending(self):
        cfg = self.config()
        reports = self.reports()
        with (
            patch.object(self.module, "load_config", return_value=cfg),
            patch.object(self.module, "rapor_configleri", return_value=reports),
            patch.object(self.module, "generate_report", return_value=True) as generate,
            patch.object(self.module, "push_to_github") as publish,
            patch.object(self.module, "keychain_password") as keychain,
            patch.object(sys, "argv", ["secili_mail.py", "--dry-run"]),
        ):
            result = self.module.main()

        self.assertEqual(result, 0)
        self.assertEqual(generate.call_count, 2)
        publish.assert_not_called()
        keychain.assert_not_called()

    def test_dry_run_returns_failure_when_any_report_generation_fails(self):
        cfg = self.config()
        reports = self.reports()
        with (
            patch.object(self.module, "load_config", return_value=cfg),
            patch.object(self.module, "rapor_configleri", return_value=reports),
            patch.object(self.module, "generate_report", side_effect=[True, False]),
            patch.object(self.module, "push_to_github") as publish,
            patch.object(self.module, "publish_dashboard") as dashboard,
            patch.object(sys, "argv", ["secili_mail.py", "--dry-run"]),
        ):
            result = self.module.main()

        self.assertEqual(result, 4)
        publish.assert_not_called()
        dashboard.assert_not_called()

    def test_no_push_still_generates_reports(self):
        cfg = self.config()
        reports = self.reports()
        with (
            patch.object(self.module, "load_config", return_value=cfg),
            patch.object(self.module, "rapor_configleri", return_value=reports),
            patch.object(self.module, "generate_report", return_value=True) as generate,
            patch.object(self.module, "push_to_github") as publish,
            patch.object(sys, "argv", ["secili_mail.py", "--no-push", "--dry-run"]),
        ):
            result = self.module.main()

        self.assertEqual(result, 0)
        self.assertEqual(generate.call_count, 2)
        publish.assert_not_called()

    def test_no_push_does_not_read_credentials_or_send_mail(self):
        cfg = self.config()
        reports = self.reports()
        with (
            patch.object(self.module, "load_config", return_value=cfg),
            patch.object(self.module, "rapor_configleri", return_value=reports),
            patch.object(self.module, "generate_report", return_value=True),
            patch.object(self.module, "push_to_github") as publish,
            patch.object(self.module, "keychain_password") as keychain,
            patch.object(sys, "argv", ["secili_mail.py", "--no-push"]),
        ):
            result = self.module.main()

        self.assertEqual(result, 0)
        publish.assert_not_called()
        keychain.assert_not_called()

    def test_live_command_is_blocked_when_config_safety_gates_are_closed(self):
        cfg = self.config() | {"allow_publish": False, "allow_send": False}
        reports = self.reports()
        with (
            patch.object(self.module, "load_config", return_value=cfg),
            patch.object(self.module, "rapor_configleri", return_value=reports),
            patch.object(self.module, "generate_report", return_value=True),
            patch.object(self.module, "push_to_github") as publish,
            patch.object(self.module, "keychain_password") as keychain,
            patch.object(sys, "argv", ["secili_mail.py"]),
        ):
            result = self.module.main()

        self.assertEqual(result, 3)
        publish.assert_not_called()
        keychain.assert_not_called()

    def test_generation_failure_blocks_partial_publish_and_mail(self):
        cfg = self.config() | {"allow_publish": True, "allow_send": True}
        reports = self.reports()
        with (
            patch.object(self.module, "load_config", return_value=cfg),
            patch.object(self.module, "rapor_configleri", return_value=reports),
            patch.object(self.module, "generate_report", side_effect=[True, False]),
            patch.object(self.module, "push_to_github") as publish,
            patch.object(self.module, "publish_dashboard", return_value=True, create=True) as dashboard,
            patch.object(self.module, "keychain_password") as keychain,
            patch.object(sys, "argv", ["secili_mail.py"]),
        ):
            result = self.module.main()

        self.assertEqual(result, 4)
        publish.assert_not_called()
        dashboard.assert_called_once_with(
            cfg, {"tefas_secili_akis.html": "secili veri üretimi başarısız"}
        )
        keychain.assert_not_called()

    def test_publish_failure_blocks_mail(self):
        cfg = self.config() | {"allow_publish": True, "allow_send": True}
        reports = self.reports()
        with (
            patch.object(self.module, "load_config", return_value=cfg),
            patch.object(self.module, "rapor_configleri", return_value=reports),
            patch.object(self.module, "generate_report", return_value=True),
            patch.object(self.module, "push_to_github", side_effect=[True, False]),
            patch.object(self.module, "keychain_password") as keychain,
            patch.object(sys, "argv", ["secili_mail.py"]),
        ):
            result = self.module.main()

        self.assertEqual(result, 5)
        keychain.assert_not_called()

    def test_successful_report_publish_updates_dashboard_before_send_gate(self):
        cfg = self.config() | {"allow_publish": True, "allow_send": False}
        reports = self.reports()
        with (
            patch.object(self.module, "load_config", return_value=cfg),
            patch.object(self.module, "rapor_configleri", return_value=reports),
            patch.object(self.module, "generate_report", return_value=True),
            patch.object(self.module, "push_to_github", return_value=True),
            patch.object(self.module, "publish_dashboard", return_value=True, create=True) as dashboard,
            patch.object(self.module, "keychain_password") as keychain,
            patch.object(sys, "argv", ["secili_mail.py"]),
        ):
            result = self.module.main()

        self.assertEqual(result, 3)
        dashboard.assert_called_once_with(cfg)
        keychain.assert_not_called()

    def test_dashboard_publish_failure_blocks_mail(self):
        cfg = self.config() | {"allow_publish": True, "allow_send": True}
        reports = self.reports()
        with (
            patch.object(self.module, "load_config", return_value=cfg),
            patch.object(self.module, "rapor_configleri", return_value=reports),
            patch.object(self.module, "generate_report", return_value=True),
            patch.object(self.module, "push_to_github", return_value=True),
            patch.object(self.module, "publish_dashboard", return_value=False, create=True),
            patch.object(self.module, "keychain_password") as keychain,
            patch.object(sys, "argv", ["secili_mail.py"]),
        ):
            result = self.module.main()

        self.assertEqual(result, 5)
        keychain.assert_not_called()


if __name__ == "__main__":
    unittest.main()
