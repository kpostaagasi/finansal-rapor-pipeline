import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "3_tefas_fon_akis_maili" / "fonlar.json"


class SelectedFundConfigTests(unittest.TestCase):
    def test_selected_funds_match_the_verified_watchlist(self):
        configured = json.loads(CONFIG.read_text(encoding="utf-8"))["fonlar"]
        # Kriter: net akış büyüklüğü yüksek fonlar (yön fark etmez) + izlenen
        # kurucuların butik fonları. PDR/RTA 18.08.2026'da çıkarıldı; 10 kod
        # 04.09.2026'da eklendi (izlenen kurucuların akışta ilk 50'ye giren
        # eksik fonları). PHE/PBR/THF/TMV 18.09.2026'da çıkarıldı: SPK Bülteni
        # 2026/60 Tera ve Pusula fonlarını TEFAS'ta işleme kapattı, bu dördü
        # tasfiye listesinde olmadığı için veri basmaya devam edip akışı
        # kalıcı sıfırda kalacak. Tasfiye listesindeki TLY/TP2/PRY/PNU/PCS
        # bilerek bırakıldı — tasfiye çıkışı raporun ölçtüğü akış olayıdır.
        # ZFZ 18.09.2026'da çıkarıldı; bültenle ilgisi yok, fon TEFAS'tan
        # tamamen silindi (uç geçmiş tarihler için de kayıt döndürmüyor).
        expected = {
            "TLY", "TP2", "ZFB", "RBH",
            "BGP", "PPB", "DLY", "PNU", "PRY", "TGR", "ZBJ", "PRD",
            "ODN", "PAL", "PCS", "PPJ", "PUR", "YOZ",
            "TZL", "ZPR",
        }
        self.assertEqual(len(configured), 20)
        self.assertEqual(len(set(configured)), len(configured), "kod tekrarı var")
        self.assertEqual(set(configured), expected)


if __name__ == "__main__":
    unittest.main()
