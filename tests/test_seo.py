"""M4b: seo_pages gate schema + the seo intro template contract."""

import pytest

from lib.db import connect, ensure_schema
from lib.deals_db import ensure_deals_schema
from lib.drafting import DraftingError, build_user_prompt, load_template
from pathlib import Path

SEO_TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "seo_intro_es.md"


@pytest.fixture()
def conn(tmp_path):
    with connect(tmp_path / "t.db") as c:
        ensure_schema(c)
        ensure_deals_schema(c)
        yield c


def test_seo_pages_schema_and_gate_upsert(conn):
    conn.execute(
        "INSERT INTO seo_pages (origin, dest, status, last_generated) "
        "VALUES ('VLC', 'ROM', 'noindex', '2026-08-08T00:00:00Z') "
        "ON CONFLICT(origin, dest) DO UPDATE SET status = excluded.status")
    conn.execute(
        "INSERT INTO seo_pages (origin, dest, status, last_generated) "
        "VALUES ('VLC', 'ROM', 'published', '2026-08-09T00:00:00Z') "
        "ON CONFLICT(origin, dest) DO UPDATE SET status = excluded.status")
    row = conn.execute("SELECT * FROM seo_pages").fetchone()
    assert row["status"] == "published"
    assert conn.execute("SELECT COUNT(*) FROM seo_pages").fetchone()[0] == 1


def test_seo_template_loads_and_renders():
    t = load_template(SEO_TEMPLATE)
    assert t.version == "seo_intro_es v1"
    fields = {"origin": "VLC", "origin_city": "València", "dest": "ROM",
              "dest_city": "ROM", "normal": 68, "n_obs": 12,
              "best_price": 36, "best_month": "2026-10"}
    prompt = build_user_prompt(t, fields)
    assert "València" in prompt and "68" in prompt and "{" not in prompt


def test_seo_template_missing_field_fails():
    t = load_template(SEO_TEMPLATE)
    with pytest.raises(DraftingError, match="best_price"):
        build_user_prompt(t, {"origin": "VLC", "origin_city": "x",
                              "dest": "ROM", "dest_city": "y",
                              "normal": 68, "n_obs": 12,
                              "best_price": "", "best_month": "2026-10"})


def test_deal_template_placeholders_still_pin_required_fields():
    """The deal template and REQUIRED_FIELDS must not drift apart."""
    import re
    from lib.drafting import REQUIRED_FIELDS, load_template as lt
    t = lt()
    found = set(re.findall(r"{(\w+)}", t.user))
    assert found == set(REQUIRED_FIELDS)
