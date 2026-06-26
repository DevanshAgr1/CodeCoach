#!/usr/bin/env python3
"""
CodeCoach — entry-point script.
Run from the project root:  python run.py
"""
import sys
import pathlib
import os
ROOT = pathlib.Path(__file__).parent
BACKEND = ROOT / "backend"

# backend/*.py use bare imports ("from models import ...", "import crud") rather than
# package-qualified ones, so backend/ itself must be on sys.path -- not just the
# project root -- or "ModuleNotFoundError: No module named 'models'" is raised the
# moment uvicorn imports backend.main.
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT))

import uvicorn

if __name__ == "__main__":
    print("\n╔══════════════════════════════════════╗")
    print("║        CodeCoach is starting…        ║")
    print("╚══════════════════════════════════════╝")
    print("  Open: http://localhost:8000\n")
uvicorn.run(
    "backend.main:app",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", 8000)),
    reload=False
)
