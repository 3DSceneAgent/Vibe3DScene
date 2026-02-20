#!/usr/bin/env python3
"""
Manual test entrypoint for prompt validation.

This wrapper forwards to scripts/validate_prompt.py so the tool is available
under tests/manual as requested.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    project_root = Path(__file__).resolve().parents[2]
    script_path = project_root / "scripts" / "validate_prompt.py"
    if not script_path.exists():
        print(f"validate_prompt script not found: {script_path}", file=sys.stderr)
        return 1
    os.execv(
        sys.executable,
        [sys.executable, str(script_path), *sys.argv[1:]],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
