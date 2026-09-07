import datetime as dt
import fcntl
import importlib.util
import json
import os
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
        """A: `atomic_json_dump` doğrudan yazıma indirgenirse (tmp+`os.replace`
        yerine hedefe direkt yazım) yarım/başarısız bir yazım önceki içeriği
        bozar. `os.replace` başarısız olacak şekilde `mock.patch` edilir; gerçek
        atomik uygulamada hedef dosya hiç dokunulmamış halde kalmalı ve `.tmp`
        artığı bırakılmamalı. Hem grup (`tefas_akis`) hem seçili (`tefas_secili`)
        üreticisi için eşlenik doğrulama."""
        for module in (self.group, self.selected):
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory() as tmp:
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

    def test_second_concurrent_claim_fails(self):
        """(a) B6 regresyonu: aynı gün art arda iki claim çağrısından yalnız ilki kazanmalı."""
        for module in self.modules:
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory() as tmp:
                claim = str(Path(tmp) / ".send_claim")
                with patch.object(module, "SEND_CLAIM", claim):
                    self.assertTrue(module.acquire_send_claim())
                    self.assertFalse(module.acquire_send_claim())
                    module.release_send_claim()

    def test_claim_fails_while_lock_file_exists_but_content_not_yet_written(self):
        """(b) B6 regresyonu: eski kod kilit dosyası oluşturulup içeriği HENÜZ
        yazılmadan ikinci süreci "bayat" sanıp os.unlink ile devralıyor, True
        dönüyordu (aynı gün 2 mail). flock içerikten bağımsız çalıştığı için
        ikinci süreç artık False almalı."""
        for module in self.modules:
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory() as tmp:
                claim = str(Path(tmp) / ".send_claim")
                os.makedirs(os.path.dirname(claim), exist_ok=True)
                # "1. süreç": dosyayı oluşturup flock aldı ama içeriği HENÜZ yazmadı.
                onceki_fd = os.open(claim, os.O_CREAT | os.O_RDWR, 0o600)
                fcntl.flock(onceki_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                try:
                    with patch.object(module, "SEND_CLAIM", claim):
                        self.assertFalse(module.acquire_send_claim())
                finally:
                    fcntl.flock(onceki_fd, fcntl.LOCK_UN)
                    os.close(onceki_fd)

    def test_stale_lock_from_previous_day_can_only_be_taken_over_once(self):
        """(c) Kalıcı kilit dosyası dünkü etiketi taşırken iki "süreç" onu neredeyse
        aynı anda devralmaya çalışsa da flock sayesinde yalnız biri kazanmalı."""
        dun = f"{dt.date.today() - dt.timedelta(days=1):%Y-%m-%d}"
        for module in self.modules:
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory() as tmp:
                claim = str(Path(tmp) / ".send_claim")
                os.makedirs(os.path.dirname(claim), exist_ok=True)
                with open(claim, "w", encoding="utf-8") as f:
                    f.write(dun)
                with patch.object(module, "SEND_CLAIM", claim):
                    self.assertTrue(module.acquire_send_claim())
                    self.assertFalse(module.acquire_send_claim())
                    module.release_send_claim()

    def test_force_ignores_todays_tag_but_never_a_live_lock(self):
        """(d) --force yalnız kalıcı kilitteki bugünün ETİKETİNİ yok sayar; başka
        bir sürecin şu an tuttuğu CANLI kilidi asla devralmaz."""
        for module in self.modules:
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory() as tmp:
                claim = str(Path(tmp) / ".send_claim")
                with patch.object(module, "SEND_CLAIM", claim):
                    self.assertTrue(module.acquire_send_claim())
                    self.assertFalse(module.acquire_send_claim(force=True))
                    module.release_send_claim()
                    self.assertFalse(module.acquire_send_claim())
                    self.assertTrue(module.acquire_send_claim(force=True))
                    module.release_send_claim()


if __name__ == "__main__":
    unittest.main()
