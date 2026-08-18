import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_gunluk_mail():
    path = ROOT / "1_emtia_tahvil_maili" / "gunluk_mail.py"
    spec = importlib.util.spec_from_file_location("gunluk_mail_content_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class MailContentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_gunluk_mail()

    def test_message_uses_new_dashboard_without_previous_owner_reference(self):
        cfg = {
            "subject": "Test",
            "sender": "sender@example.com",
            "sender_name": "Kamil",
            "recipients": ["recipient@example.com"],
            "emtia_url": "https://kpostaagasi.github.io/finansal-raporlar/emtia_futures.html",
            "tahvil_url": "https://kpostaagasi.github.io/finansal-raporlar/",
            "tefas_url": "https://kpostaagasi.github.io/finansal-raporlar/tefas_net_akis.html",
        }
        message = self.module.build_message(cfg)
        rendered = message.get_body(preferencelist=("plain",)).get_content()
        rendered += message.get_body(preferencelist=("html",)).get_content()
        self.assertIn("Rapor Ana Sayfası", rendered)
        self.assertNotIn("arhanatav", rendered)


if __name__ == "__main__":
    unittest.main()
