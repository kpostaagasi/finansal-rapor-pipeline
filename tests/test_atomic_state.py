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
        cls.selected = load_module("selected_atomic_test", "3_tefas_fon_akis_maili/tefas_secili.py")

    def test_tefas_cache_helpers_replace_complete_json_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "cache.json"
            target.write_text('{"old":true}', encoding="utf-8")

            self.selected.atomic_json_dump(str(target), {"new": [1, 2, 3]})

            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"new": [1, 2, 3]})
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])

    def test_failed_atomic_write_keeps_previous_cache(self):
        """`atomic_json_dump` doğrudan yazıma indirgenirse (tmp+`os.replace`
        yerine hedefe direkt yazım) yarım/başarısız bir yazım önceki içeriği
        bozar. `os.replace` başarısız olacak şekilde `mock.patch` edilir; gerçek
        atomik uygulamada hedef dosya hiç dokunulmamış halde kalmalı ve `.tmp`
        artığı bırakılmamalı."""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "cache.json"
            target.write_text('{"old":true}', encoding="utf-8")
            with patch.object(self.selected.os, "replace", side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    self.selected.atomic_json_dump(str(target), {"new": True})

            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"old": True})
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
