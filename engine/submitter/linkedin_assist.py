"""LinkedIn Easy Apply assist — the ceiling is opening the page.

Guardrail (spec section 8): no automated actions inside linkedin.com. Automated
clicking through Easy Apply violates LinkedIn ToS and risks account restriction;
the candidate's LinkedIn is a business asset. This module only opens the posting in
her default browser so she applies in her own logged-in session.
"""
from __future__ import annotations

import webbrowser


def open_posting(url: str) -> None:
    if "linkedin.com" not in url:
        raise SystemExit("not a LinkedIn URL")
    webbrowser.open(url)
    print("Opened in your browser. Apply manually — the engine never clicks inside LinkedIn.")
