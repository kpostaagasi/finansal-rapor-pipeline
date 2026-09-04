"""Fon grubu raporları: tür bazlı kapsam ve grup toplamı sözleşmesi.

İki yeni kapsam tipi test ediliyor:

  `tur`    — kapsam TEFAS fon türünden (`fonTurAciklama`) gelir. Tür yalnızca
             yönetim bilgisi ucunda var ve o uç günlük veri veren her fonu
             kapsamıyor; türü bilinmeyen fon sessizce elenmez, ifşa edilir.
  `toplam` — grup raporu veri çekmez, kaynak raporların önbelleklerini toplar.
             Gruptaki bir fonun akışı hesaplanamıyorsa o günün grup toplamı
             null bırakılır: kısmi toplam tam sonuç gibi gösterilmez.
"""

import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import mock_open, patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "3_tefas_fon_akis_maili" / "tefas_secili.py"
RAPOR_DIZIN = ROOT / "3_tefas_fon_akis_maili" / "raporlar"


def load_module():
    spec = importlib.util.spec_from_file_location("fund_group_reports_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TypeScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def kapsam(self, yat_turleri=("Para Piyasası Şemsiye Fonu",),
               emk_turleri=("Para Piyasası Fonu",)):
        haritalar = {
            "YAT": {
                "PRY": "Para Piyasası Şemsiye Fonu",
                "TLY": "Serbest Şemsiye Fonu",
                "AFO": "Kıymetli Madenler Şemsiye Fonu",
            },
            "EMK": {"AH1": "Para Piyasası Fonu", "BGL": "Altın Fonu"},
        }
        istenen = {"YAT": tuple(yat_turleri), "EMK": tuple(emk_turleri)}
        return self.module.TurKapsami(haritalar, istenen)

    def test_scope_admits_only_the_requested_fund_types(self):
        kapsam = self.kapsam()
        self.assertTrue(kapsam("PRY", "PUSULA PORTFÖY PARA PİYASASI (TL) FONU", "YAT"))
        self.assertFalse(kapsam("TLY", "TERA PORTFÖY BİRİNCİ SERBEST FON", "YAT"))
        self.assertFalse(kapsam("AFO", "AK PORTFÖY ALTIN FONU", "YAT"))
        self.assertTrue(kapsam("AH1", "... PARA PİYASASI EMEKLİLİK YATIRIM FONU", "EMK"))
        self.assertFalse(kapsam("BGL", "... ALTIN EMEKLİLİK YATIRIM FONU", "EMK"))

    def test_fund_types_are_not_mixed_across_fund_type_sides(self):
        """EMK türü YAT tarafında, YAT türü EMK tarafında kapsam açmamalı."""
        kapsam = self.kapsam(yat_turleri=("Para Piyasası Fonu",),
                             emk_turleri=("Para Piyasası Şemsiye Fonu",))
        self.assertFalse(kapsam("PRY", "...", "YAT"))
        self.assertFalse(kapsam("AH1", "...", "EMK"))

    def test_fund_with_unknown_type_is_disclosed_not_silently_dropped(self):
        kapsam = self.kapsam()
        # VKR günlük veri veriyor ama yönetim bilgisi ucunda yok (tür bilinmiyor).
        self.assertFalse(kapsam("VKR", "ALBARAKA PORTFÖY İKİNCİ PARA PİYASASI KATILIM FONU", "YAT"))
        self.assertEqual(kapsam.bilinmeyen, {"VKR"})

    def test_unknown_type_reaches_report_metadata(self):
        cache = {
            "fon": {"PRY": {"2026-09-02": [100, 1.0], "2026-09-03": [110, 2.0]}},
            "ad": {"PRY": "PUSULA PORTFÖY PARA PİYASASI (TL) FONU"},
            "tip": {"PRY": "YAT"},
            "turu_bilinmeyen": ["VKR"],
        }
        cfg = {
            "ad": "para_piyasasi",
            "baslik": "Test",
            "kapsam": {"tip": "tur", "turler": {"YAT": ["Para Piyasası Şemsiye Fonu"]}},
            "fon_tipleri": ["YAT"],
        }
        meta = self.render(cfg, lambda: self.module.html_uret(cfg, cache))
        self.assertEqual(meta["untyped_count"], 1)
        self.assertEqual(meta["untyped"], ["VKR"])
        self.assertEqual(meta["count_label"], "fon")

    def render(self, cfg, call):
        """html_yaz'ı yakalayıp üretilen REPORT_META'yı döndürür."""
        yakalanan = {}

        def sahte(cfg_, raw, report_meta, fon_ozet, eksik_not, gunler):
            yakalanan.update(meta=report_meta, raw=raw, ozet=fon_ozet, not_=eksik_not)

        with patch.object(self.module, "html_yaz", sahte):
            call()
        return yakalanan["meta"]


class GroupTotalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def kaynak_onbellegi(self, ikinci_fon_bosluklu=False):
        """İki fonlu bir kaynak önbelleği; istenirse ikinci fonda gözlem boşluğu."""
        a = {"2026-09-01": [100, 1.0], "2026-09-02": [110, 1.0], "2026-09-03": [120, 1.0]}
        if ikinci_fon_bosluklu:
            b = {"2026-09-01": [200, 1.0], "2026-09-03": [260, 1.0]}
        else:
            b = {"2026-09-01": [200, 1.0], "2026-09-02": [230, 1.0], "2026-09-03": [260, 1.0]}
        return {
            "fon": {"AAA": a, "BBB": b},
            "ad": {"AAA": "A FONU", "BBB": "B FONU"},
            "tip": {"AAA": "YAT", "BBB": "YAT"},
        }

    def satirlar(self, onbellek):
        cfg = {
            "ad": "gruplar",
            "kapsam": {"tip": "toplam",
                       "kaynaklar": [{"kod": "TST", "grup": "Test grubu", "rapor": "test"}]},
        }
        alt = {"cache": "/tmp/olmayan.json", "fon_tipleri": ["YAT"]}
        with (
            patch.object(self.module, "rapor_yukle", return_value=alt),
            patch.object(self.module.os.path, "exists", return_value=True),
            patch("builtins.open", mock_open(read_data=json.dumps(onbellek))),
        ):
            return self.module.toplam_satirlari(cfg)

    def test_group_total_sums_member_fund_flows(self):
        satirlar, bosluk, yakin, bilinmeyen = self.satirlar(self.kaynak_onbellegi())
        self.assertEqual(len(satirlar), 1)
        seri = satirlar[0]["seri"]
        # 02.09: (110-100)*1 + (230-200)*1 = 40 ; 03.09: 10 + 30 = 40
        self.assertEqual(seri["2026-09-02"], 40)
        self.assertEqual(seri["2026-09-03"], 40)
        self.assertEqual((bosluk, yakin, bilinmeyen), (0, 0, []))
        self.assertEqual(satirlar[0]["fon_sayisi"], 2)

    def test_group_total_is_null_when_a_member_flow_is_uncomputable(self):
        satirlar, bosluk, yakin, _ = self.satirlar(
            self.kaynak_onbellegi(ikinci_fon_bosluklu=True)
        )
        seri = satirlar[0]["seri"]
        # BBB 02.09'da gözlem vermedi: 03.09 akışı hesaplanamaz, grup toplamı null.
        self.assertIsNone(seri["2026-09-03"])
        self.assertEqual(seri["2026-09-02"], 10)   # yalnız AAA hesaplanabildi
        self.assertEqual(bosluk, 1)
        self.assertEqual(yakin, 1)

    def test_missing_source_cache_fails_loudly(self):
        cfg = {
            "ad": "gruplar",
            "kapsam": {"tip": "toplam",
                       "kaynaklar": [{"kod": "TST", "grup": "Test", "rapor": "yok"}]},
        }
        alt = {"cache": "/tmp/kesinlikle-olmayan-onbellek.json", "fon_tipleri": ["YAT"]}
        with patch.object(self.module, "rapor_yukle", return_value=alt):
            with self.assertRaises(RuntimeError) as ctx:
                self.module.toplam_satirlari(cfg)
        self.assertIn("önbelleği yok", str(ctx.exception))

    def test_derived_report_runs_after_its_sources(self):
        """Grup raporu kaynak önbelleklerini okur: config sırası ne olursa olsun sonda."""
        spec = importlib.util.spec_from_file_location(
            "secili_mail_order_test", ROOT / "3_tefas_fon_akis_maili" / "secili_mail.py"
        )
        mail = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mail)
        sirali = mail.rapor_configleri(
            {"raporlar": ["gruplar", "hisse", "para_piyasasi"]}
        )
        self.assertEqual([r["ad"] for r in sirali][-1], "gruplar")
        self.assertEqual({r["ad"] for r in sirali[:-1]}, {"hisse", "para_piyasasi"})


class ReportConfigTests(unittest.TestCase):
    """Rapor tanımları ile yayın/dashboard tablosu birbirinden kaymamalı."""

    def test_every_report_config_is_complete(self):
        beklenen = {"altin", "secili", "kiymetli_maden", "para_piyasasi",
                    "borclanma", "katilim", "hisse", "gruplar"}
        bulunan = {p.stem for p in RAPOR_DIZIN.glob("*.json")}
        self.assertEqual(bulunan, beklenen)
        for ad in sorted(bulunan):
            with self.subTest(rapor=ad):
                cfg = json.loads((RAPOR_DIZIN / f"{ad}.json").read_text(encoding="utf-8"))
                for alan in ("ad", "baslik", "kapsam", "html", "github_path", "url"):
                    self.assertIn(alan, cfg)
                self.assertEqual(cfg["ad"], ad)
                self.assertTrue(cfg["url"].endswith(cfg["github_path"]))
                if cfg["kapsam"]["tip"] == "tur":
                    self.assertTrue(cfg["kapsam"]["turler"]["YAT"])
                    self.assertTrue(cfg["cache"])
                if cfg["kapsam"]["tip"] == "toplam":
                    # Türetilmiş rapor: kendi önbelleği yok, kaynakları var.
                    self.assertNotIn("cache", cfg)
                    self.assertTrue(cfg["kapsam"]["kaynaklar"])

    def test_dashboard_publishes_every_report_under_its_configured_name(self):
        spec = importlib.util.spec_from_file_location(
            "build_site_group_test", ROOT / "build_site.py"
        )
        build_site = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(build_site)
        hedefler = {r["target"] for r in build_site.REPORTS}
        kaynaklar = {r["source"] for r in build_site.REPORTS}
        self.assertEqual(len(hedefler), len(build_site.REPORTS), "yayın adı tekrarı var")
        for ad in ("kiymetli_maden", "para_piyasasi", "borclanma", "katilim",
                   "hisse", "gruplar", "altin", "secili"):
            cfg = json.loads((RAPOR_DIZIN / f"{ad}.json").read_text(encoding="utf-8"))
            with self.subTest(rapor=ad):
                self.assertIn(cfg["github_path"], hedefler)
                self.assertIn(f"3_tefas_fon_akis_maili/{cfg['html']}", kaynaklar)

    def test_group_report_sources_exist_and_are_fund_level(self):
        gruplar = json.loads((RAPOR_DIZIN / "gruplar.json").read_text(encoding="utf-8"))
        kodlar = set()
        for kaynak in gruplar["kapsam"]["kaynaklar"]:
            with self.subTest(kaynak=kaynak["rapor"]):
                yol = RAPOR_DIZIN / f"{kaynak['rapor']}.json"
                self.assertTrue(yol.exists())
                alt = json.loads(yol.read_text(encoding="utf-8"))
                self.assertEqual(alt["kapsam"]["tip"], "tur")
                self.assertNotIn(kaynak["kod"], kodlar, "grup kodu tekrarı")
                kodlar.add(kaynak["kod"])


if __name__ == "__main__":
    unittest.main()
