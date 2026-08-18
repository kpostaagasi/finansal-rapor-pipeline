import importlib.util
import json
import tempfile
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


class AtomicJsonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.group = load_module("group_atomic_test", "2_tefas_altin_akis/tefas_akis.py")
        cls.selected = load_module("selected_atomic_test", "3_tefas_fon_akis_maili/tefas_secili.py")

    def test_tefas_cache_helpers_replace_complete_json_atomically(self):
        for module in (self.group, self.selected):
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / "cache.json"
                target.write_text('{"old":true}', encoding="utf-8")

                module.atomic_json_dump(str(target), {"new": [1, 2, 3]})

                self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"new": [1, 2, 3]})
                self.assertEqual(list(Path(tmp).glob("*.tmp")), [])

    def test_failed_atomic_write_keeps_previous_cache(self):
        module = self.selected
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "cache.json"
            target.write_text('{"old":true}', encoding="utf-8")
            with patch.object(module.os, "replace", side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    module.atomic_json_dump(str(target), {"new": True})

            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"old": True})
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])


class SendClaimTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.modules = [
            load_module("daily_claim_test", "1_emtia_tahvil_maili/gunluk_mail.py"),
            load_module("selected_claim_test", "3_tefas_fon_akis_maili/secili_mail.py"),
        ]

    def test_only_one_process_can_claim_same_day_send(self):
        for module in self.modules:
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory() as tmp:
                claim = str(Path(tmp) / ".send_claim")
                with patch.object(module, "SEND_CLAIM", claim):
                    self.assertTrue(module.acquire_send_claim())
                    self.assertFalse(module.acquire_send_claim())
                    module.release_send_claim()
                    self.assertTrue(module.acquire_send_claim())
                    module.release_send_claim()


if __name__ == "__main__":
    unittest.main()
