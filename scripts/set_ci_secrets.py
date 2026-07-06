#!/usr/bin/env python
"""Push scan credentials from .env to GitHub Actions repo secrets.

Run it yourself (it writes secrets to your repo — deliberate action):

    .venv\\Scripts\\python.exe scripts\\set_ci_secrets.py

Requires the gh CLI to be logged in (it is, if `git push` works).
Values are piped to `gh secret set`, never printed. SEARCHAPI_KEY is
deliberately NOT pushed — the 2 break-glass credits stay local-only.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

REPO = Path(__file__).resolve().parents[1]
CI_SECRETS = [
    "TURSO_DATABASE_URL",
    "TURSO_AUTH_TOKEN",
    "RAPIDAPI_KEY",
    "TRAVELPAYOUTS_TOKEN",
    "SERPAPI_KEY",
    "NTFY_TOPIC",
]


def main() -> int:
    env = dotenv_values(REPO / ".env")
    failures = 0
    for name in CI_SECRETS:
        value = (env.get(name) or "").strip()
        if not value:
            print(f"{name}: not in .env - SKIPPED (add it and re-run)")
            continue
        r = subprocess.run(["gh", "secret", "set", name],
                           input=value, text=True, capture_output=True)
        if r.returncode == 0:
            print(f"{name}: set ({len(value)} chars)")
        else:
            failures += 1
            print(f"{name}: FAILED - {r.stderr.strip()[:150]}")
    print("\nVerify at: https://github.com/carlospintod/flight_scans/"
          "settings/secrets/actions")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
