"""Scrub personal data from saved trial records.

Two leaks are handled, for any researcher, not just the repo author:
  1. Literal email addresses (regex) — scrubbed everywhere, every provider.
  2. Identity tokens derived from the account email the harness injects. Claude Code puts the
     account email into every prompt (the ``# userEmail`` system-reminder), and models then weave
     the name into generated artifacts (e.g. an assume-role session name ``jane-doe-admin``). We
     derive the name tokens from whatever email the current account has and scrub those too.

The scrub runs in the runner on every saved record, so nothing reaches disk unredacted. Tokens are
derived at run time from the live account (see ClaudeAgentSDKProvider.setup), never hard-coded.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_HOME = str(Path.home())


def identity_tokens(email: str | None) -> list[str]:
    """Strings to scrub, derived from an email, longest first.

    For ``jane.doe.42@corp.com``: the full email, the local part ``jane.doe.42``, its separator
    variants (``janedoe``, ``jane.doe``, ``jane-doe``, ``jane_doe``) and the lone name tokens of
    length >= 4 (``jane``). Lone tokens shorter than 4 chars are skipped to avoid scrubbing common
    words, but the full email, the full local part and the name-joins are always scrubbed.
    """
    if not email or "@" not in email:
        return []
    local = email.strip().lower().split("@", 1)[0]
    tokens = {email.strip().lower(), local}
    parts = [p for p in re.split(r"[._\-+0-9]+", local) if p.isalpha() and len(p) >= 2]
    tokens.update(p for p in parts if len(p) >= 4)  # lone tokens only if >=4 (avoid common words)
    if len(parts) >= 2:  # name joins are distinctive, keep them even with a short part
        tokens.update({"".join(parts), ".".join(parts), "-".join(parts), "_".join(parts)})
    return sorted((t for t in tokens if len(t) >= 4), key=len, reverse=True)


def scrub_text(text: str, tokens: list[str] = ()) -> str:
    text = EMAIL.sub("<redacted-email>", text)
    if _HOME and _HOME not in ("/", ""):
        text = text.replace(_HOME, "~")
    for token in tokens:  # longest first, so the full name goes before its parts
        text = re.sub(re.escape(token), "<redacted-id>", text, flags=re.IGNORECASE)
    return text


def scrub(obj: Any, tokens: list[str] = ()) -> Any:
    """Recursively scrub emails, the home path and identity tokens from any JSON-like value."""
    if isinstance(obj, str):
        return scrub_text(obj, tokens)
    if isinstance(obj, list):
        return [scrub(v, tokens) for v in obj]
    if isinstance(obj, dict):
        return {k: scrub(v, tokens) for k, v in obj.items()}
    return obj
