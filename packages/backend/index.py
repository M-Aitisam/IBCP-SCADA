# packages/backend/index.py
# Vercel entrypoint. Must live next to app/ (not inside it) — @vercel/python
# adds the entrypoint's own directory to sys.path, so `app` is only
# importable as a package from here, not from within app/main.py itself.
# The explicit sys.path insert below is belt-and-suspenders: it makes the
# fix deterministic instead of relying on assumptions about exactly what
# Vercel's builder puts on sys.path.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.main import app  # noqa: E402,F401
