"""Drafting: template integrity, prompt build, defensive API handling."""

from types import SimpleNamespace

import pytest

from lib.drafting import (REQUIRED_FIELDS, AnthropicDraftClient,
                          DraftingError, build_user_prompt, load_template)

FIELDS = {
    "origin": "VLC", "dest": "MRS", "is_round_trip": "si",
    "price": 38, "currency": "EUR",
    "depart_date": "2026-09-10", "return_date": "2026-09-14",
    "baseline_line": "la mediana hoy de rutas comparables es 119 EUR",
    "carrier": "Volotea", "deal_class": "standard",
    "verification_line": "confirmado en vivo a 38 EUR",
    "booking_url": "https://www.google.com/travel/flights?hl=es&q=x",
}


def test_template_loads_with_version_and_sections():
    t = load_template()
    assert t.version == "deal_draft_es v1"
    assert "es-ES" in t.system or "de tú" in t.system
    # The voice spec's mandatory content (D7) is enforced by the template.
    for must in ("inventes", "low-cost", "mistake", "INCRE"):
        assert must in t.system


def test_user_prompt_renders_every_voice_spec_field():
    t = load_template()
    prompt = build_user_prompt(t, FIELDS)
    # route, dates, price vs normal, carrier, booking link, class
    for token in ("VLC", "MRS", "38", "EUR", "2026-09-10", "2026-09-14",
                  "119", "Volotea", "standard", "google.com/travel"):
        assert str(token) in prompt
    assert "{" not in prompt  # no unfilled placeholder survives


def test_missing_field_fails_loudly():
    t = load_template()
    broken = {**FIELDS, "carrier": ""}
    with pytest.raises(DraftingError, match="carrier"):
        build_user_prompt(t, broken)


def test_required_fields_match_template_placeholders():
    t = load_template()
    for f in REQUIRED_FIELDS:
        assert "{" + f + "}" in t.user


class _FakeMessages:
    def __init__(self, response):
        self._response = response
        self.seen_kwargs = None

    def create(self, **kwargs):
        self.seen_kwargs = kwargs
        return self._response


def _client_with(response):
    inner = SimpleNamespace(messages=_FakeMessages(response))
    return AnthropicDraftClient(api_key="k", model="claude-opus-5",
                                inner=inner), inner


def test_draft_returns_text_and_versions():
    resp = SimpleNamespace(stop_reason="end_turn", content=[
        SimpleNamespace(type="text", text="Valencia a Marsella por 38 EUR.")])
    client, inner = _client_with(resp)
    result = client.draft(fields=FIELDS)
    assert "38" in result.text
    assert result.template_version == "deal_draft_es v1"
    assert result.model == "claude-opus-5"
    assert inner.messages.seen_kwargs["model"] == "claude-opus-5"
    assert "DATOS" in inner.messages.seen_kwargs["messages"][0]["content"]


def test_draft_refusal_raises():
    resp = SimpleNamespace(stop_reason="refusal", content=[])
    client, _ = _client_with(resp)
    with pytest.raises(DraftingError, match="refusal"):
        client.draft(fields=FIELDS)


def test_draft_empty_content_raises():
    resp = SimpleNamespace(stop_reason="end_turn", content=[])
    client, _ = _client_with(resp)
    with pytest.raises(DraftingError, match="no text"):
        client.draft(fields=FIELDS)


def test_from_env_raises_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        AnthropicDraftClient.from_env(model="claude-opus-5")
