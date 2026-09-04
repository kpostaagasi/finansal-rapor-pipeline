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
import os
import tempfile
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

    def test_precious_metals_scope_is_a_superset_of_the_gold_universe(self):
        """Kıymetli maden evreni altını kapsamalı.

        TEFAS altın katılım fonlarını "Katılım Şemsiye Fonu", gümüş fonlarını
        "Fon Sepeti"/"Serbest" altında sınıflıyor. Şemsiye türü tek başına
        kullanıldığında rapor 49 altın fonunun 23'ünü kaçırıyordu.
        """
        haritalar = {
            "YAT": {
                "AFO": "Kıymetli Madenler Şemsiye Fonu",
                "KZL": "Katılım Şemsiye Fonu",       # Kuveyt Türk altın katılım
                "ZCE": "Serbest Şemsiye Fonu",        # Ziraat türk altın serbest
                "GUM": "Fon Sepeti Şemsiye Fonu",     # Ak gümüş fon sepeti
                "TLY": "Serbest Şemsiye Fonu",        # ilgisiz
            },
            "EMK": {"BGL": "Altın Fonu", "KML": "Kıymetli Madenler"},
        }
        kapsam = self.module.TurKapsami(
            haritalar,
            {"YAT": ("Kıymetli Madenler Şemsiye Fonu",),
             "EMK": ("Kıymetli Madenler", "Altın Fonu", "Altın Katılım Fonu")},
            self.module.kiymetli_maden_unvani,
        )
        self.assertTrue(kapsam("AFO", "AK PORTFÖY ALTIN FONU", "YAT"))
        self.assertTrue(kapsam("KZL", "KUVEYT TÜRK PORTFÖY ALTIN KATILIM FONU", "YAT"))
        self.assertTrue(kapsam("ZCE", "ZİRAAT PORTFÖY TÜRK ALTIN SERBEST (TL) ÖZEL FON", "YAT"))
        self.assertTrue(kapsam("GUM", "AK PORTFÖY GÜMÜŞ FON SEPETI FONU", "YAT"))
        self.assertFalse(kapsam("TLY", "TERA PORTFÖY BİRİNCİ SERBEST FON", "YAT"))
        self.assertTrue(kapsam("BGL", "... ALTIN EMEKLİLİK YATIRIM FONU", "EMK"))
        self.assertTrue(kapsam("KML", "... KIYMETLİ MADENLER EMEKLİLİK YATIRIM FONU", "EMK"))

    def test_precious_metals_title_rule_rejects_lookalike_names(self):
        """Unvan tuzakları: TEFAS unvan listesinden doğrulanmış gerçek örnekler."""
        kural = self.module.kiymetli_maden_unvani
        for unvan in (
            "AK PORTFÖY GÜMÜŞ FON SEPETI FONU",
            "İŞ PORTFÖY GÜMÜŞ SERBEST FON",
            "YAPI KREDİ PORTFÖY KIYMETLİ MADENLER KATILIM FONU",
            "GOLDEN GLOBAL PORTFÖY ALTIN KATILIM FONU",
            # Türkçe İ'li yazımlar: tek noktalı normalizasyondan önce kaçıyordu.
            "İŞ PORTFÖY SİLVER SERBEST FON",
            "AK PORTFÖY PALLADİUM KATILIM FONU",
        ):
            with self.subTest(unvan=unvan):
                self.assertTrue(kural(unvan))
        for unvan in (
            # "Gümüşsuyu" semt adı — Yapı Kredi'nin semt adlı özel fonları
            "YAPI KREDİ PORTFÖY PY GÜMÜŞSUYU SERBEST (DÖVIZ) ÖZEL FON",
            # "Platinum" burada hizmet segmenti adı, kıymetli maden değil
            "TEB PORTFÖY ING BANK ÖZEL BANKACILIK VE PLATİNUM DEĞİŞKEN ÖZEL FON",
            "AK PORTFÖY ING ÖZEL BANKACILIK VE PLATINUM MUTLAK GETİRİ HEDEFLİ DEĞİŞKEN FON",
            # sıra sayısı ve kurucu adı tuzakları
            "AK PORTFÖY ALTINCI SERBEST(DÖVİZ) FON",
            "GOLDEN GLOBAL PORTFÖY PARA PİYASASI KATILIM FONU",
            "DENİZ PORTFÖY ENERJİ VE MADENCİLİK SEKTÖRÜ DEĞİŞKEN FON",
        ):
            with self.subTest(unvan=unvan):
                self.assertFalse(kural(unvan))

    def test_gold_title_rule_decisions_are_unchanged(self):
        """kiymetli_maden_unvani normalizasyonu altin_unvani'nin kararını değiştirmemeli."""
        kural = self.module.altin_unvani
        for unvan, beklenen in (
            ("AK PORTFÖY ALTIN FONU", True),
            ("GOLDEN GLOBAL PORTFÖY ALTIN KATILIM FONU", True),
            ("GOLD PORTFÖY BİRİNCİ FON", True),
            ("AK PORTFÖY ALTINCI SERBEST(DÖVİZ) FON", False),
            ("AK PORTFÖY ONALTINCI SERBEST (DÖVİZ-POUND) FON", False),
            ("GOLDEN GLOBAL PORTFÖY PARA PİYASASI KATILIM FONU", False),
        ):
            with self.subTest(unvan=unvan):
                self.assertEqual(kural(unvan), beklenen)

    def test_unknown_title_rule_fails_loudly(self):
        cfg = {
            "ad": "test",
            "kapsam": {"tip": "tur", "turler": {"YAT": ["X"]},
                       "unvan_kurali": "yok_boyle_bir_kural"},
            "fon_tipleri": ["YAT"],
        }
        with self.assertRaises(ValueError) as ctx:
            self.module.kapsam_kurallari(cfg)
        self.assertIn("bilinmeyen unvan kuralı", str(ctx.exception))

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

    def test_gold_scope_admits_via_title_or_pension_type_list(self):
        """AltinKapsami: unvan kuralı ∪ altın emeklilik tür listesi — biri yeterli."""
        kapsam = self.module.AltinKapsami({"EMY"})
        # Unvanında ALTIN geçiyor ama tür listesinde yok (henüz incelenmemiş yeni fon).
        self.assertTrue(kapsam("YENI", "YENİ ALTIN EMEKLİLİK YATIRIM FONU", "EMK"))
        # Tür listesinde var ama unvanında ALTIN geçmiyor (TEFAS "Kıymetli Madenler" sınıflıyor).
        self.assertTrue(kapsam("EMY", "GARANTİ EMEKLİLİK VE HAYAT KIYMETLİ MADENLER EYF", "EMK"))
        # İlgisiz fon: ne unvan ne tür listesi eşleşir.
        self.assertFalse(kapsam("XYZ", "GARANTİ EMEKLİLİK PARA PİYASASI FONU", "EMK"))
        # Tür listesi yalnız EMK tarafında geçerli; YAT tarafında unvan şart.
        self.assertFalse(kapsam("EMY", "GARANTİ EMEKLİLİK VE HAYAT KIYMETLİ MADENLER FONU", "YAT"))

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

    def test_fund_launched_on_latest_date_is_not_reported_as_uncomputed(self):
        """Son veri tarihinde ilk gözlemini veren fon 'uncomputed' değil, 'launched' sayılır."""
        cache = {
            "fon": {
                "OLD": {"2026-09-01": [100, 1.0], "2026-09-02": [110, 1.0], "2026-09-03": [120, 1.0]},
                "NEW": {"2026-09-03": [50, 1.0]},
            },
            "ad": {"OLD": "OLD FUND", "NEW": "NEW FUND"},
            "tip": {"OLD": "YAT", "NEW": "YAT"},
        }
        cfg = {
            "ad": "test",
            "baslik": "Test",
            "kapsam": {"tip": "tur", "turler": {"YAT": ["X"]}},
            "fon_tipleri": ["YAT"],
        }
        durum = self.module.kapsam_durumu(cfg, cache)
        self.assertEqual(durum["launched"], ["NEW"])
        self.assertNotIn("NEW", durum["uncomputed"])

        meta = self.render(cfg, lambda: self.module.html_uret(cfg, cache))
        self.assertEqual(meta["launched"], ["NEW"])
        self.assertEqual(meta["launched_count"], 1)
        self.assertNotIn("NEW", meta["uncomputed"])

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

    def test_group_total_is_null_on_every_day_the_gapped_member_stays_active(self):
        """BBB'nin gözlem aralığı (09-01, 09-03] hem 02.09'u hem 03.09'u kapsıyor;
        BBB o günlerde akış vermediği için grup toplamı sessizce kısmi kalmaz, null olur."""
        satirlar, bosluk, yakin, _ = self.satirlar(
            self.kaynak_onbellegi(ikinci_fon_bosluklu=True)
        )
        seri = satirlar[0]["seri"]
        self.assertIsNone(seri["2026-09-02"])   # BBB aktif ama o günün akışı yok
        self.assertIsNone(seri["2026-09-03"])   # BBB'nin boşluğu ertesi günü de etkiler
        self.assertEqual(bosluk, 1)
        self.assertEqual(yakin, 1)

    def test_group_total_survives_member_launch_and_closure(self):
        """Dönem içinde piyasaya çıkan/kapanan bir üye grup toplamını iptal etmemeli."""
        onbellek = {
            "fon": {
                "AAA": {
                    "2026-09-01": [100, 1.0], "2026-09-02": [110, 1.0],
                    "2026-09-03": [120, 1.0], "2026-09-04": [130, 1.0],
                },
                "NEW": {"2026-09-04": [50, 1.0]},          # o gün piyasaya çıktı
                "CLOSED": {"2026-09-01": [200, 1.0], "2026-09-02": [220, 1.0]},  # kapandı
            },
            "ad": {"AAA": "A", "NEW": "N", "CLOSED": "C"},
            "tip": {"AAA": "YAT", "NEW": "YAT", "CLOSED": "YAT"},
        }
        satirlar, bosluk, yakin, _ = self.satirlar(onbellek)
        seri = satirlar[0]["seri"]
        self.assertEqual(seri["2026-09-02"], 30)   # AAA(10) + CLOSED(20), CLOSED henüz aktif
        self.assertEqual(seri["2026-09-03"], 10)   # CLOSED artık aralık dışı, iptal etmiyor
        self.assertEqual(seri["2026-09-04"], 10)   # NEW ilk gözlem günü, iptal etmiyor
        self.assertEqual(bosluk, 0)

    def test_duplicate_fund_gap_across_sources_is_counted_once(self):
        """Aynı fon-gün boşluğu iki kaynak raporda da geçse (altın ⊂ kıymetli maden gibi)
        boşluk sayısı bir kez sayılmalı, kaynak sayısı kadar değil."""
        with tempfile.TemporaryDirectory() as d:
            p1 = os.path.join(d, "r1.json")
            p2 = os.path.join(d, "r2.json")
            data = {
                "fon": {
                    "AAA": {"2026-09-01": [100, 1.0], "2026-09-03": [120, 1.0]},
                    "REF": {"2026-09-01": [500, 1.0], "2026-09-02": [510, 1.0],
                            "2026-09-03": [520, 1.0]},
                },
                "ad": {"AAA": "A", "REF": "R"},
                "tip": {"AAA": "YAT", "REF": "YAT"},
            }
            for yol in (p1, p2):
                with open(yol, "w", encoding="utf-8") as f:
                    json.dump(data, f)
            cfg = {
                "ad": "gruplar",
                "kapsam": {"tip": "toplam", "kaynaklar": [
                    {"kod": "S1", "grup": "G1", "rapor": "r1"},
                    {"kod": "S2", "grup": "G2", "rapor": "r2"},
                ]},
            }

            def sahte_rapor_yukle(ad):
                return {"cache": p1 if ad == "r1" else p2, "fon_tipleri": ["YAT"]}

            with patch.object(self.module, "rapor_yukle", sahte_rapor_yukle):
                satirlar, bosluk, yakin, _ = self.module.toplam_satirlari(cfg)
        self.assertEqual(len(satirlar), 2)
        self.assertEqual(bosluk, 1)
        self.assertEqual(yakin, 1)

    def test_missing_source_is_reported_separately_from_uncomputed_group(self):
        """Kaynak önbelleğinde son tarih hiç yoksa 'missing', varsa ama seri null ise
        'uncomputed' — build_site aynı grubu iki kez göstermesin diye ayrık kalmalı."""
        with tempfile.TemporaryDirectory() as d:
            p_good = os.path.join(d, "good.json")
            p_gap = os.path.join(d, "gap.json")
            p_stale = os.path.join(d, "stale.json")
            good = {"fon": {"Z": {"2026-09-01": [100, 1.0], "2026-09-02": [110, 1.0],
                                   "2026-09-03": [120, 1.0]}},
                    "ad": {"Z": "Z"}, "tip": {"Z": "YAT"}}
            gap = {"fon": {
                       "REF": {"2026-09-01": [500, 1.0], "2026-09-02": [510, 1.0],
                               "2026-09-03": [520, 1.0]},
                       "Y": {"2026-09-01": [10, 1.0], "2026-09-03": [30, 1.0]},
                   },
                   "ad": {"REF": "R", "Y": "Y"}, "tip": {"REF": "YAT", "Y": "YAT"}}
            stale = {"fon": {"W": {"2026-09-01": [1, 1.0], "2026-09-02": [2, 1.0]}},
                     "ad": {"W": "W"}, "tip": {"W": "YAT"}}
            for yol, veri in ((p_good, good), (p_gap, gap), (p_stale, stale)):
                with open(yol, "w", encoding="utf-8") as f:
                    json.dump(veri, f)

            cfg = {
                "ad": "gruplar",
                "kapsam": {"tip": "toplam", "kaynaklar": [
                    {"kod": "SG", "grup": "Good", "rapor": "good"},
                    {"kod": "SU", "grup": "Gapped", "rapor": "gap"},
                    {"kod": "SB", "grup": "Stale", "rapor": "stale"},
                ]},
            }

            def sahte_rapor_yukle(ad):
                yol = {"good": p_good, "gap": p_gap, "stale": p_stale}[ad]
                return {"cache": yol, "fon_tipleri": ["YAT"]}

            yakalanan = {}

            def sahte_yaz(cfg_, raw, report_meta, fon_ozet, eksik_not, gunler):
                yakalanan["meta"] = report_meta

            with (
                patch.object(self.module, "rapor_yukle", sahte_rapor_yukle),
                patch.object(self.module, "html_yaz", sahte_yaz),
            ):
                self.module.toplam_html_uret(cfg)

        meta = yakalanan["meta"]
        self.assertEqual(meta["missing"], ["SB-YAT"])
        self.assertEqual(meta["uncomputed"], ["SU-YAT"])
        self.assertNotIn("SU-YAT", meta["missing"])
        self.assertNotIn("SB-YAT", meta["uncomputed"])

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


class TuruBilinmeyenPersistenceTests(unittest.TestCase):
    """kaydet(): ara kayıt turu_bilinmeyen'e dokunmaz, son kayıt koşulsuz yazar/siler."""

    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def _cfg_ve_onbellek(self, d, baslangic_bilinmeyen):
        cache_path = os.path.join(d, "cache.json")
        baslangic = {
            "fon": {"AAA": {"2026-09-01": [100, 1.0]}},
            "ad": {"AAA": "A"},
            "tip": {"AAA": "YAT"},
        }
        if baslangic_bilinmeyen is not None:
            baslangic["turu_bilinmeyen"] = baslangic_bilinmeyen
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(baslangic, f)
        return {"cache": cache_path, "ad": "test", "fon_tipleri": ["YAT"]}, cache_path

    def test_final_save_with_empty_set_clears_stale_untyped_list(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, cache_path = self._cfg_ve_onbellek(d, ["OLD1", "OLD2"])

            class SahteKapsam:
                bilinmeyen = set()

            def sahte_topla(cfg_, bas, bit, adlar, tipler, pencere_bitti=None):
                return {"AAA": {"2026-09-05": (100, 1.0)}}, SahteKapsam()

            with patch.object(self.module, "topla", sahte_topla):
                sonuc = self.module.veri_guncelle(cfg)
            self.assertNotIn("turu_bilinmeyen", sonuc)
            with open(cache_path, encoding="utf-8") as f:
                self.assertNotIn("turu_bilinmeyen", json.load(f))

    def test_interim_save_does_not_touch_untyped_list(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, cache_path = self._cfg_ve_onbellek(d, ["OLD1", "OLD2"])

            def sahte_topla(cfg_, bas, bit, adlar, tipler, pencere_bitti=None):
                # Uzun çekim ortasında bir pencere kaydı (bilinmeyen=None, ara kayıt).
                pencere_bitti({"AAA": {"2026-09-02": (105, 1.0)}})
                raise RuntimeError("ağ hatası ortasında")

            with patch.object(self.module, "topla", sahte_topla):
                with self.assertRaises(RuntimeError):
                    self.module.veri_guncelle(cfg)
            with open(cache_path, encoding="utf-8") as f:
                onbellek = json.load(f)
            self.assertEqual(onbellek["turu_bilinmeyen"], ["OLD1", "OLD2"])
            self.assertIn("2026-09-02", onbellek["fon"]["AAA"])


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
                    if ad == "kiymetli_maden":
                        self.assertEqual(cfg["kapsam"].get("unvan_kurali"), "kiymetli_maden")
                        modul = load_module()
                        self.assertIn("kiymetli_maden", modul.UNVAN_KURALLARI)
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

    def test_group_report_sources_are_fund_level_reports(self):
        gruplar = json.loads((RAPOR_DIZIN / "gruplar.json").read_text(encoding="utf-8"))
        kodlar = set()
        for kaynak in gruplar["kapsam"]["kaynaklar"]:
            with self.subTest(kaynak=kaynak["rapor"]):
                yol = RAPOR_DIZIN / f"{kaynak['rapor']}.json"
                self.assertTrue(yol.exists())
                alt = json.loads(yol.read_text(encoding="utf-8"))
                # Kaynak fon bazında olmalı; türetilmiş rapor kaynak olamaz.
                self.assertIn(alt["kapsam"]["tip"], ("tur", "altin", "liste"))
                self.assertNotEqual(alt["kapsam"]["tip"], "toplam")
                self.assertTrue(alt.get("cache"))
                self.assertNotIn(kaynak["kod"], kodlar, "grup kodu tekrarı")
                kodlar.add(kaynak["kod"])

    def test_group_report_declares_that_its_rows_overlap(self):
        """Tematik satırlar ayrık değil; sayfa bunu bilmeli ve toplam iddia etmemeli."""
        module = load_module()
        cfg = {
            "ad": "gruplar",
            "kapsam": {"tip": "toplam", "kaynaklar": []},
        }
        satirlar = [
            {"anahtar": "ALT-YAT", "tip": "YAT", "kodlar": {"AFO", "KZL"}},
            {"anahtar": "KIY-YAT", "tip": "YAT", "kodlar": {"AFO", "KZL", "GUM"}},
            {"anahtar": "PAR-YAT", "tip": "YAT", "kodlar": {"PRY"}},
            {"anahtar": "ALT-EMK", "tip": "EMK", "kodlar": {"BGL"}},
        ]
        ortak = module.ortak_fonlar(satirlar)
        self.assertEqual(ortak["ALT-YAT"], {"KIY-YAT": 2})
        self.assertEqual(ortak["KIY-YAT"], {"ALT-YAT": 2})
        self.assertNotIn("PAR-YAT", ortak)          # ayrık satır
        self.assertNotIn("ALT-EMK", ortak)          # farklı fon tipi karışmaz


if __name__ == "__main__":
    unittest.main()
