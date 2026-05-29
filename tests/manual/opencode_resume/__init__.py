"""Sequential NEW + RESUME smoke harness for every OpenCode model.

OpenCode is backed by OpenRouter's free tier; the free-tier rate limit
(roughly 20 requests / minute per key) is the dominant pacing
constraint. This package walks the configured model list one entry at a
time, sleeps between calls, and stops early when the API reports a
429 / quota symptom so a rate-limited run is never silently passed.

The CLI entry point lives in :mod:`driver`; the thin shim at
``tests/manual/test_opencode_resume.py`` invokes it.
"""

from .driver import main

__all__ = ["main"]
