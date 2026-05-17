"""Shared rate-limit graceful-exit wrapper for systemd-driven pollers.

When a poller hits a GitHub rate-limit (REST 429 or GraphQL "rate limit
already exceeded"), we want it to exit 0 — not 1 — so:

  1. systemd doesn't surface the failure (timer keeps running)
  2. journalctl noise stays low (clear one-liner instead of full traceback)
  3. The next scheduled tick will try again, by which point the per-app
     installation token may have rotated and the limit cleared.

Other RuntimeErrors / CalledProcessErrors still bubble up as failures.

Usage:
    from stokowski.pollers._rate_limit_guard import run_with_rate_limit_guard

    def main():
        ...

    def cli():
        run_with_rate_limit_guard(main)

    if __name__ == "__main__":
        cli()
"""

from __future__ import annotations

import subprocess
import sys
from typing import Callable

# Phrases that indicate a GitHub rate-limit response (REST or GraphQL).
# Match case-insensitively; check against both message and stderr.
_RATE_LIMIT_SIGNALS = (
    "rate limit",
    "rate-limit",
    "secondary rate limit",
    "abuse detection",
    "api rate limit exceeded",
    " 429",
    "http 429",
    "x-ratelimit",
)


def _is_rate_limit(text: str) -> bool:
    t = (text or "").lower()
    return any(sig in t for sig in _RATE_LIMIT_SIGNALS)


def run_with_rate_limit_guard(main_fn: Callable[[], None]) -> None:
    """Run main_fn(); exit 0 if a rate-limit error escapes; re-raise otherwise."""
    try:
        main_fn()
    except subprocess.CalledProcessError as exc:
        combined = f"{exc} {exc.stderr or ''} {exc.stdout or ''}"
        if _is_rate_limit(combined):
            print(
                f"⏳ rate-limited, will retry on next tick: {combined.strip()[:300]}",
                file=sys.stderr,
            )
            sys.exit(0)
        raise
    except RuntimeError as exc:
        if _is_rate_limit(str(exc)):
            print(
                f"⏳ rate-limited, will retry on next tick: {str(exc)[:300]}",
                file=sys.stderr,
            )
            sys.exit(0)
        raise
