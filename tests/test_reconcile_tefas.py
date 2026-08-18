import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "reconcile_tefas.py"


def load_module():
    spec = importlib.util.spec_from_file_location("reconcile_tefas_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ReconcileTefasTests(unittest.TestCase):
    def test_compare_reports_population_and_historical_value_changes(self):
        module = load_module()
        baseline = {
            "fon": {
                "AAA": {
                    "2026-08-14": [100, 1.0],
                    "2026-08-17": [110, 1.1],
                },
                "OLD": {"2026-08-14": [50, 2.0]},
            }
        }
        current = {
            "fon": {
                "AAA": {
                    "2026-08-14": [101, 1.0],
                    "2026-08-17": [110, 1.1],
                },
                "NEW": {"2026-08-17": [60, 2.0]},
            }
        }

        result = module.compare_caches(baseline, current)

        self.assertEqual(result["funds_added"], ["NEW"])
        self.assertEqual(result["funds_removed"], ["OLD"])
        self.assertEqual(result["changed_count"], 1)
        self.assertEqual(
            result["changed"][0],
            {
                "fund": "AAA",
                "date": "2026-08-14",
                "baseline": [100, 1.0],
                "current": [101, 1.0],
            },
        )

    def test_normalize_accepts_group_cache_shape(self):
        module = load_module()
        cache = {
            "yf": {"YF1": {"2026-08-17": [100, 1.0]}},
            "eyf": {"EYF1": {"2026-08-17": [200, 2.0]}},
        }

        normalized = module.normalize_cache(cache)

        self.assertEqual(set(normalized), {"YF1", "EYF1"})


if __name__ == "__main__":
    unittest.main()
