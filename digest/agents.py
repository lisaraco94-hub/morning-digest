import json
import re
import urllib.parse
from pathlib import Path

import feedparser

from .trackers import CostTracker, CoverageTracker
from .research import parallel_web_research


def _load_vault_context(cfg: dict) -> str:
    vault_path = cfg.get("output", {}).get("obsidian_vault", "")
    if not vault_path:
        return ""
    notes = cfg.get("output", {}).get("obsidian_context_notes", [])
    if not notes:
        return ""

    domain_keywords = cfg.get("vault_context_keywords", [
        "automation", "competitor", "market", "acquisition", "product", "launch",
    ])
    kw_pattern = re.compile(
        "|".join(re.escape(k) for k in domain_keywords), re.IGNORECASE
    )

    chunks = []
    for rel in notes:
        p = Path(vault_path) / rel
        try:
            text = p.read_text(encoding="utf-8")
            text = re.sub(r"^---.*?---\n", "", text, flags=re.DOTALL)
            text = re.sub(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", r"\1", text)
            lines = []
            for line in text.splitlines():
                s = line.strip()
                if not s or len(s) < 6 or s.startswith("```"):
                    continue
                is_heading = s.startswith("#")
                is_bullet  = s.startswith(("- ", "* ", "+ ")) or re.match(r"^\d+\.", s)
                if is_heading or is_bullet or kw_pattern.search(s):
                    lines.append(re.sub(r"\*+", "", s).strip()[:130])
                    if len(lines) >= 35:
                        break
            chunks.append(f"[{Path(rel).stem}]\n" + "\n".join(lines))
        except Exception:
            pass
    return "\n\n".join(chunks)


def _validate_queries(queries: list[dict]) -> list[dict]:
    """Discard all queries that return no results on Google News RSS."""
    valid = []
    for q_cfg in queries:
        q_enc = urllib.parse.quote_plus(f"{q_cfg['q']} when:1d")
        url   = f"https://news.google.com/rss/search?q={q_enc}&hl=en&gl=US&ceid=US:en"
        try:
            if not feedparser.parse(url).entries:
                print(f"    [skip] no results: '{q_cfg['q']}'")
                continue
        except Exception:
            pass
        valid.append(q_cfg)
    return valid


def plan_search(
    cfg: dict, client, today: str, tracker: CostTracker
) -> list[dict]:
    user_name = cfg.get("user_name", "the user")
    seed = "\n".join(
        f"  {q['q']}  [{q['section']}]" for q in cfg["news_queries"]
    )

    vault_ctx = _load_vault_context(cfg)
    vault_block = (
        f"\nADDITIONAL CONTEXT FROM KNOWLEDGE BASE (use to generate extra queries):\n"
        f"{vault_ctx[:1200]}\n"
        if vault_ctx else ""
    )

    prompt = f"""You are a news search strategist for Google News. Date: {today}.

CONTEXT — what {user_name} monitors:
{cfg['profile'][:1600]}
{vault_block}
REFERENCE QUERIES (working examples from config — use as style guide):
{seed}

Generate 14-18 Google News search queries in English.

CRITICAL RULES FOR QUERY STYLE:
- Write queries like a journalist would write a headline or search — SHORT, journalistic language
- GOOD: "Siemens Healthineers diagnostics 2026", "Roche lab automation news"
- BAD: "cobas lysis-multianalyte platform throughput optimization hospital workflow" ← too technical, 0 results
- Max 6 words per query, use company names + 1-2 topic words
- Add "2026" only if it fits naturally

DISTRIBUTION: 7-8 IVD & Lab Automation, 3-4 Markets LATAM & APAC, 3-4 Biotech & Digital Health
For Biotech: AI only if clearly applied to diagnostics/laboratory — no generic AI news.

JSON ONLY (no other text):
[{{"q":"...", "section":"IVD & Lab Automation|Markets LATAM & APAC|Biotech & Digital Health"}}]"""

    for model in ["claude-haiku-4-5-20251001", cfg["model"]]:
        try:
            r = client.messages.create(
                model=model, max_tokens=1200,
                messages=[{"role": "user", "content": prompt}],
            )
            tracker.add(r.usage, model)
            m = re.search(r"\[.*\]", r.content[0].text, re.DOTALL)
            if m:
                plan = json.loads(m.group())
                print(f"  Raw plan: {len(plan)} queries ({model.split('-')[1]})")
                plan = _validate_queries(plan)
                existing = {q["q"].lower() for q in plan}
                for sq in cfg["news_queries"]:
                    if sq["q"].lower() not in existing:
                        plan.append(sq)
                print(f"  Final plan: {len(plan)} queries (validated)")
                return plan
        except Exception as e:
            print(f"  [warn] plan/{model}: {e}")

    return cfg["news_queries"]


def agentic_followup(
    articles:         list[dict],
    cfg:              dict,
    client,
    coverage:         CoverageTracker,
    tracker:          CostTracker,
    already_searched: list[str],
    max_steps:        int = 4,
) -> list[dict]:
    """
    After the parallel collection phase, Claude identifies coverage gaps
    and runs up to max_steps rounds of targeted follow-up searches.
    Stops early when no new articles are found or coverage is sufficient.
    """
    user_name = cfg.get("user_name", "the user")

    for step in range(max_steps):
        by_section: dict[str, list[str]] = {}
        for a in articles:
            by_section.setdefault(a["section"], []).append(a["title"])

        summary = "\n".join(
            f"{sec} ({len(titles)} articles):\n"
            + "\n".join(f"  - {t[:90]}" for t in titles[:8])
            for sec, titles in by_section.items()
        )
        already_txt = "\n".join(f"  - {q}" for q in already_searched[-30:])

        prompt = f"""You are a research editor. You have collected {len(articles)} articles for {user_name}'s briefing.

PROFILE (brief): {cfg['profile'][:400]}

QUERIES ALREADY RUN (do not repeat anything similar):
{already_txt}

ARTICLES ALREADY COLLECTED:
{summary}

Is there a RELEVANT gap not covered — a key competitor with no news, a geographic market absent,
an important event (deal, approval, launch) that might be circulating today?

RESPOND JSON ONLY:
- Gap found (max 2 NEW queries DIFFERENT from those already done): {{"follow_up": [{{"q":"...","section":"..."}}]}}
- Sufficient coverage: {{"follow_up": []}}

Queries: journalistic style, 3-5 words, DIFFERENT from everything already searched."""

        try:
            r = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            )
            tracker.add(r.usage, "haiku")
            m = re.search(r"\{.*\}", r.content[0].text, re.DOTALL)
            if not m:
                break
            queries = json.loads(m.group()).get("follow_up", [])
        except Exception as e:
            print(f"  [warn] followup step {step + 1}: {e}")
            break

        if not queries:
            print(f"  ✓ Step {step + 1}: no gaps — search complete")
            break

        print(f"  🔎 Step {step + 1}: {len(queries)} follow-up queries", end="", flush=True)
        new_arts = parallel_web_research(queries, coverage, cfg, max_fetch=6)
        already_searched.extend(q["q"] for q in queries)
        articles.extend(new_arts)
        print(f" → +{len(new_arts)} new articles")
        if not new_arts:
            print("  ✓ 0 new articles — early stop")
            break

    return articles


def score_and_write(
    articles: list[dict],
    arxiv:    list[dict],
    pubmed:   list[dict],
    cfg:      dict,
    client,
    tracker:  CostTracker,
    today:    str,
) -> tuple[str, list[dict]]:
    """
    Single Sonnet call: score every article for relevance, then write the full report.
    Returns (markdown_report, scored_articles).
    """
    user_name = cfg.get("user_name", "the user")

    items_txt = ""
    for i, art in enumerate(articles):
        items_txt += f"[{i}] {art['section']} | {art['source']} | {art['date']}\n"
        items_txt += f"T: {art['title']}\n"
        if art.get("snippet"):
            items_txt += f"S: {art['snippet'][:180]}\n"
        if art.get("full_text") and len(art["full_text"]) > 80:
            items_txt += f"X: {art['full_text'][:350]}\n"
        items_txt += "\n"

    lit_txt = ""
    for p in (arxiv + pubmed):
        lit_txt += (
            f"[LIT] {p['source']} — {p['title']}\n"
            f"  {p.get('abstract', '')[:250]}\n"
            f"  {p['link']}\n\n"
        )

    prompt = f"""You are the best executive assistant in the world — you prepare a daily briefing for {user_name}.

=== WHO IS {user_name.upper()} ===
{cfg['profile']}

=== TODAY: {today} ===

=== ARTICLES TO EVALUATE ({len(articles)}) ===
{items_txt}

=== PRE-LOADED SCIENTIFIC LITERATURE ===
{lit_txt}

=== TASK ===
1. For each article assign a relevance score. Be VERY selective with score 3:

   - score 3 ★ MARKET IMPACT — max 3-5 articles per day in total, only for:
     • Acquisitions, mergers, spin-offs announced
     • CE/FDA approval of a direct competitor product
     • Competitor exit/failure/market withdrawal
     • Regulatory change impacting the sector in key geographies
     • Product launch that DIRECTLY competes with the user's core solutions
     • Strategic deal that repositions a competitor in target markets
     If nothing at this level today → score 3 can be 0.

   - score 2 — relevant sector news for the user's role

   Discard score 1 and irrelevant articles: do NOT include them in JSON or report.
   Include in the report ONLY score 2 and score 3 articles.

2. For scientific papers: include those touching lab automation, pre-analytics, TAT, AI in IVD.

3. Write the full report in Obsidian Markdown (see format below).

=== OUTPUT ===
Reply with TWO blocks separated by ---JSON_END---:

BLOCK 1: JSON of selected articles:
[{{"idx":0,"score":3,"summary":"2 sentences: key facts","why_it_matters":"1 sentence connecting to user role/products/competitors"}},...]

---JSON_END---

BLOCK 2: Full Obsidian Markdown report:
---
created: {today}
tags: [daily, digest]
type: morning-digest
---

# 📰 Morning Digest — {today}

## TL;DR
- **[short title](article_url)** — [one sentence] ← max 8 bullets, score 3 first then best score 2. URL required.

---

## 🔬 IVD & Lab Automation
### [Title]
*[Source] · [date]* `score:N`
[2-sentence summary]
**Why it matters:** [why it matters to the user]
[Read →](url)
---

IMPORTANT: on the source/date line ALWAYS write `score:N` where N is 2 or 3 (backticks included).
Example: *MedTech Dive · {today}* `score:3`

[sections: 🌎 Markets LATAM & APAC, 🧬 Biotech & Digital Health, 📚 Scientific Literature]

## 📌 Ideas to keep
- [concrete actionable idea for the user]

=== REPORT RULES ===
- At least 3 articles per main section
- EVERY article MUST have `score:N` on the source/date line — N matches the JSON score. NEVER omit this tag.
- Literature: include 3-5 relevant papers
- "Why it matters": explicitly connect to the user's products, competitors, target markets
- NO generic AI: Biotech includes AI only if clearly tied to diagnostics/laboratory
"""

    response = client.messages.create(
        model=cfg["model"],
        max_tokens=9000,
        messages=[{"role": "user", "content": prompt}],
    )
    tracker.add(response.usage, cfg["model"])
    raw = response.content[0].text

    if "---JSON_END---" in raw:
        json_part, md_part = raw.split("---JSON_END---", 1)
    else:
        json_part, md_part = "", raw

    scored: list[dict] = []
    m = re.search(r"\[.*\]", json_part, re.DOTALL)
    if m:
        try:
            scored = json.loads(m.group())
            for s in scored:
                idx = s.get("idx")
                if idx is not None and 0 <= idx < len(articles):
                    s.update({
                        k: articles[idx][k]
                        for k in ("title", "source", "section", "link", "date")
                        if k in articles[idx]
                    })
        except json.JSONDecodeError:
            pass

    md_part = md_part.strip()
    if md_part.startswith("```"):
        md_part = re.sub(r"^```\w*\n?", "", md_part)
        md_part = re.sub(r"\n?```$", "", md_part)

    return md_part, scored
