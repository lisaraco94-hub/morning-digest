import os
import sys
from datetime import datetime
from pathlib import Path

import anthropic

from .agents import agentic_followup, plan_search, score_and_write
from .config import load_config
from .renderer import generate_html
from .research import (
    NEWSPAPER_OK,
    fetch_arxiv,
    fetch_pubmed,
    fetch_rss_feeds,
    parallel_web_research,
)
from .trackers import CostTracker, CoverageTracker


def run() -> None:
    print("🌅 Morning Digest — starting")
    cfg = load_config()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("❌ ANTHROPIC_API_KEY not set")
        sys.exit(1)

    client   = anthropic.Anthropic(api_key=api_key)
    tracker  = CostTracker(limit=0.80)
    coverage = CoverageTracker()
    today    = datetime.now().strftime("%Y-%m-%d")

    print(f"   newspaper4k: {'✅' if NEWSPAPER_OK else '❌ regex fallback'}")

    # ── Phase 1: Plan ──────────────────────────────────────────────────────────
    print("\n🗺️  Phase 1: Plan...")
    queries = plan_search(cfg, client, today, tracker)

    # ── Phase 2: Parallel research ─────────────────────────────────────────────
    print("\n🔄 Phase 2: Parallel Research...")
    articles = parallel_web_research(queries, coverage, cfg, max_fetch=28)

    if cfg.get("rss_feeds"):
        print(f"  📡 RSS feeds ({len(cfg['rss_feeds'])})...", end="", flush=True)
        rss_arts = fetch_rss_feeds(cfg["rss_feeds"], coverage)
        articles.extend(rss_arts)
        print(f" +{len(rss_arts)} articles")

    print("  📚 arXiv...", end="", flush=True)
    arxiv = fetch_arxiv(
        cfg["arxiv"]["categories"],
        cfg["arxiv"]["lookback_days"],
        cfg["arxiv"]["max_papers"],
    )
    print(f" {len(arxiv)} papers")

    print("  🧬 PubMed...", end="", flush=True)
    pubmed = fetch_pubmed(
        cfg["pubmed"]["queries"],
        cfg["pubmed"]["lookback_days"],
        cfg["pubmed"]["max_results"],
    )
    print(f" {len(pubmed)} papers")

    print(f"\n  📊 After parallel research: {len(articles)} unique articles")

    # ── Phase 2b: Agentic follow-up loop ───────────────────────────────────────
    print("\n🔁 Phase 2b: Agentic follow-up...")
    already_searched = [q["q"] for q in queries]
    articles = agentic_followup(
        articles, cfg, client, coverage, tracker, already_searched, max_steps=4
    )
    print(
        f"  📊 Final total: {len(articles)} articles | "
        f"Literature: {len(arxiv) + len(pubmed)} papers"
    )

    # ── Phase 3: Score & write ─────────────────────────────────────────────────
    print("\n🤖 Phase 3: Score & Write (Sonnet)...")
    md_report, scored = score_and_write(
        articles, arxiv, pubmed, cfg, client, tracker, today
    )

    high = sum(1 for s in scored if s.get("score") == 3)
    print(f"  Scoring: {len(scored)} articles selected, {high} high priority")
    print(f"  Cost:    {tracker.summary()}")

    if not md_report.strip():
        print("⚠️  Empty report — check logs")
        sys.exit(1)

    # ── Phase 4: Render HTML ───────────────────────────────────────────────────
    out_cfg     = cfg["output"]
    html_main   = Path(out_cfg["html_path"]).expanduser()
    archive_dir = Path(__file__).parent.parent / out_cfg.get("archive_dir", "archive")
    archive_dir.mkdir(exist_ok=True)
    html_archive = archive_dir / f"{today}.html"

    try:
        for path in (html_main, html_archive):
            generate_html(md_report, scored, today, str(path), cfg)
        print(f"\n✅ HTML: {html_main}")
        print(f"   archive: {html_archive}")
    except Exception as e:
        print(f"⚠️  HTML generation failed: {e}")
