"""Claude drafting for Vuelazo deal alerts (Anthropic API).

The Spanish copy is product surface (CLAUDE.md #8): the prompt lives in
templates/deal_draft_es.md (versioned; edits are commits), this module
only fills placeholders and calls the API. The client is wrapped in a
GuardedClient by the runner — `draft` is the metered method (1 unit).

Uses the official `anthropic` SDK. Model + max_tokens come from
routes/vuelazo.yaml (drafting section).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

LOG = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = REPO / "templates" / "deal_draft_es.md"
SOURCE_ID = "anthropic"

_VERSION_RE = re.compile(r"<!--\s*template:\s*(\S+)\s+(v\S+)\s*-->")


class DraftingError(RuntimeError):
    """Raised when the template or the API response is unusable."""


@dataclass(frozen=True)
class Template:
    version: str          # e.g. "deal_draft_es v1"
    system: str
    user: str


@dataclass(frozen=True)
class DraftResult:
    text: str
    template_version: str
    model: str


def load_template(path: Path = TEMPLATE_PATH) -> Template:
    raw = Path(path).read_text(encoding="utf-8")
    m = _VERSION_RE.search(raw)
    if not m:
        raise DraftingError(f"{path}: missing '<!-- template: name vN -->' header")
    version = f"{m.group(1)} {m.group(2)}"
    parts = re.split(r"^## (system|user)\s*$", raw, flags=re.MULTILINE)
    sections: dict[str, str] = {}
    for name, body in zip(parts[1::2], parts[2::2]):
        sections[name] = body.strip()
    if "system" not in sections or "user" not in sections:
        raise DraftingError(f"{path}: needs '## system' and '## user' sections")
    return Template(version=version, system=sections["system"],
                    user=sections["user"])


# The deal template's DATOS fields (pinned by tests; other templates
# carry their own placeholders and build_user_prompt derives the
# requirement from the template itself).
REQUIRED_FIELDS = (
    "origin", "dest", "is_round_trip", "price", "currency",
    "depart_date", "return_date", "baseline_line", "carrier",
    "deal_class", "verification_line", "booking_url",
)

_PLACEHOLDER_RE = re.compile(r"{(\w+)}")


def build_user_prompt(template: Template, fields: dict) -> str:
    """Fill the template. EVERY placeholder the template declares must
    arrive non-empty — a half-filled prompt never reaches the API (the
    draft would otherwise invent the gap)."""
    needed = sorted(set(_PLACEHOLDER_RE.findall(template.user)))
    missing = [f for f in needed if fields.get(f) in (None, "")]
    if missing:
        raise DraftingError(f"draft fields missing: {missing}")
    try:
        return template.user.format(**fields)
    except KeyError as exc:
        raise DraftingError(f"template placeholder without a field: {exc}") from exc


class AnthropicDraftClient:
    """Thin drafting client with the repo's adapter conventions: from_env
    construction, typed results, defensive response handling. `draft` is
    the single metered method."""

    source_id = SOURCE_ID

    def __init__(self, api_key: str, *, model: str, max_tokens: int = 1200,
                 inner=None):
        if not api_key:
            raise ValueError("api_key is required")
        if inner is None:
            import anthropic
            inner = anthropic.Anthropic(api_key=api_key)
        self._client = inner
        self._model = model
        self._max_tokens = max_tokens

    @classmethod
    def from_env(cls, *, model: str, max_tokens: int = 1200,
                 var: str = "ANTHROPIC_API_KEY") -> "AnthropicDraftClient":
        key = os.environ.get(var, "").strip()
        if not key:
            raise RuntimeError(
                f"{var} is not set. Drafting needs an Anthropic API key "
                "(infra secret: .env / Actions secrets only, never /ops).")
        return cls(api_key=key, model=model, max_tokens=max_tokens)

    def draft(self, *, fields: dict,
              template: Template | None = None) -> DraftResult:
        template = template or load_template()
        user = build_user_prompt(template, fields)
        LOG.info("anthropic draft %s->%s model=%s template=%s",
                 fields.get("origin"), fields.get("dest"), self._model,
                 template.version)
        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=template.system,
            messages=[{"role": "user", "content": user}],
        )
        # Defensive: check the stop reason BEFORE touching content —
        # safety classifiers can decline with an empty content list.
        if getattr(response, "stop_reason", None) == "refusal":
            raise DraftingError("anthropic declined the draft request (refusal)")
        text = "".join(
            block.text for block in (response.content or [])
            if getattr(block, "type", None) == "text").strip()
        if not text:
            raise DraftingError(
                f"anthropic returned no text (stop_reason="
                f"{getattr(response, 'stop_reason', None)!r})")
        return DraftResult(text=text, template_version=template.version,
                           model=self._model)
