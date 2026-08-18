#!/usr/bin/env python3
"""Otomasyonların proje venv'i ile çalıştırıldığını doğrular."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def check_runtime(root: Path = ROOT, executable: Path | None = None) -> list[str]:
    executable = executable or Path(sys.executable)
    expected = root / ".venv" / "bin" / "python"
    if executable.resolve() != expected.resolve():
        return [
            f"Yanlış Python: {executable}. Beklenen interpreter: {expected}"
        ]
    return []


def main() -> int:
    errors = check_runtime()
    if errors:
        for error in errors:
            print(f"HATA: {error}", file=sys.stderr)
        return 1
    print(f"Runtime hazır: {sys.executable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
