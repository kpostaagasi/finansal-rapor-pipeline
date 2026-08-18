import importlib.util
import tempfile
import unittest
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "verify_runtime.py"


def load_module():
    spec = importlib.util.spec_from_file_location("verify_runtime_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class VerifyRuntimeTests(unittest.TestCase):
    def test_runtime_dependencies_are_exactly_pinned(self):
        lines = [
            line.strip()
            for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertTrue(lines)
        for line in lines:
            with self.subTest(requirement=line):
                self.assertRegex(line, r"^[A-Za-z0-9_.-]+==[^=<>~!]+$")

    def test_runtime_rejects_interpreter_outside_project_venv(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".venv" / "bin").mkdir(parents=True)
            errors = module.check_runtime(root=root, executable=Path("/usr/bin/python3"))

        self.assertEqual(len(errors), 1)
        self.assertIn(".venv/bin/python", errors[0])


if __name__ == "__main__":
    unittest.main()
