import os
import sys
from pathlib import Path

import pytest

# Ensure `lib/` and `tracker.py` are importable when running pytest from the repo root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _force_local_sqlite(monkeypatch):
    """Force `lib.db.connect()` onto the local-sqlite branch for every test.

    Rationale: `ui/_common.py` calls load_dotenv() at import time and pulls
    the developer's real TURSO_DATABASE_URL / TURSO_AUTH_TOKEN into
    os.environ. Any test that imports from ui or transitively triggers
    that load_dotenv would then run against the real cloud DB instead
    of the tmp_path SQLite fixture — inserting/reading from Turso and
    failing on unexpected row counts. Scrubbing per-test guarantees
    isolation regardless of import order.
    """
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
