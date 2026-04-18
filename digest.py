#!/usr/bin/env python3
"""Morning Digest — AI-powered daily briefing for niche professionals.

Run:
    python3 digest.py

Architecture:
  1. Plan     — Haiku generates optimized search queries from your profile
  2. Research — ThreadPoolExecutor: web search + article fetch in parallel
  3. Followup — Agentic loop: Haiku identifies coverage gaps, runs targeted searches
  4. Score    — Sonnet scores every article and writes the full report
  5. Render   — Newspaper-style HTML with relevance indicators
"""

from digest.pipeline import run

if __name__ == "__main__":
    run()
