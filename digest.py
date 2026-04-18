#!/usr/bin/env python3
"""Morning Digest — AI-powered daily briefing for niche professionals.

Architecture (inspired by gpt-researcher + DeepAgents):
  1. PLAN   — Haiku generates optimized search queries from your profile
  2. PARALLEL RESEARCH — ThreadPoolExecutor: web search + fetch simultaneously
  3. SCORE & WRITE — Sonnet: relevance scoring + full report in one call
  4. HTML   — Newspaper-style HTML with relevance indicators
"""

import os, sys, json, re, urllib.parse, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml, feedparser, requests, anthropic
from xml.etree import ElementTree as ET

try:
    import newspaper
    NEWSPAPER_OK = True
except ImportError:
    NEWSPAPER_OK = False

# ── DEFAULT BRANDING COLORS ───────────────────────────────────────────────────
_DEFAULT_COLORS = {
    "primary":   "#00B3E3",
    "dark":      "#0074A2",
    "darker":    "#004A66",
    "accent":    "#EA8651",
    "text":      "#333333",
    "text_secondary": "#686869",
    "light_blue":     "#B1DEF1",
    "light_blue2":    "#EAF6FC",
    "green":     "#1A7A4A",
}

def _load_colors(cfg: dict) -> dict:
    c = dict(_DEFAULT_COLORS)
    branding = cfg.get("branding", {})
    if branding.get("primary_color"):   c["primary"]  = branding["primary_color"]
    if branding.get("dark_color"):      c["dark"]     = branding["dark_color"]
    if branding.get("darker_color"):    c["darker"]   = branding["darker_color"]
    if branding.get("accent_color"):    c["accent"]   = branding["accent_color"]
    if branding.get("success_color"):   c["green"]    = branding["success_color"]
    return c

SECTION_STYLE_TEMPLATE = {
    "IVD & Lab Automation":     {"emoji": "🔬"},
    "Mercati LATAM & APAC":     {"emoji": "🌎"},
    "Biotech & Digital Health": {"emoji": "🧬"},
    "Letteratura Scientifica":  {"emoji": "📚"},
    "Idee da tenere":           {"emoji": "📌"},
}

SCORE_STYLE_TEMPLATE = {
    3: {"label": "PRIORITÀ ALTA", "dot": "●", "show": True,  "outline": False},
    2: {"label": "",              "dot": "",  "show": False, "outline": False},
    1: {"label": "",              "dot": "",  "show": False, "outline": False},
}

# ── HIGH-QUALITY SOURCES (boost for fetch selection) ─────────────────────────
PRIORITY_SOURCES = {
    "medtechdive", "darkdaily", "fiercebiotech", "fiercehealthcare",
    "biopharmaapac", "clinicallabproductsnews", "labmanager",
    "medscape", "healio", "mlo-online", "captodayonline",
    "diagnosticimaging", "healthcareitnews",
}

# ── CONFIG ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    p = Path(__file__).parent / "config.yaml"
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)

# ── COST TRACKER ─────────────────────────────────────────────────────────────

class CostTracker:
    IN_COST  = 3.0  / 1_000_000
    OUT_COST = 15.0 / 1_000_000
    HAIKU_IN  = 0.80 / 1_000_000
    HAIKU_OUT = 4.0  / 1_000_000

    def __init__(self, limit: float = 0.80):
        self.limit = limit
        self._in  = 0; self._out = 0; self._cost = 0.0
        self._lock = threading.Lock()

    def add(self, usage, model: str = "sonnet"):
        c = (usage.input_tokens  * (self.HAIKU_IN  if "haiku" in model else self.IN_COST) +
             usage.output_tokens * (self.HAIKU_OUT if "haiku" in model else self.OUT_COST))
        with self._lock:
            self._in  += usage.input_tokens
            self._out += usage.output_tokens
            self._cost += c

    @property
    def cost(self): return self._cost
    @property
    def exceeded(self): return self._cost >= self.limit
    def summary(self):
        return f"${self._cost:.4f}/{self.limit:.2f} ({self._in:,}in+{self._out:,}out)"

# ── COVERAGE TRACKER (thread-safe) ───────────────────────────────────────────

class CoverageTracker:
    def __init__(self):
        self._lock        = threading.Lock()
        self.seen_titles  : set[str] = set()
        self.seen_urls    : set[str] = set()
        self.freq         : dict[str, int] = {}

    def register(self, title: str, url: str) -> bool:
        """Returns True if new, False if duplicate. Thread-safe."""
        tk = re.sub(r"\W+", "", title.lower())[:55]
        uk = url.rstrip("/").lower()[:100]
        with self._lock:
            self.freq[uk] = self.freq.get(uk, 0) + 1
            if tk in self.seen_titles or uk in self.seen_urls:
                return False
            self.seen_titles.add(tk)
            self.seen_urls.add(uk)
        return True

    def frequency(self, url: str) -> int:
        uk = url.rstrip("/").lower()[:100]
        return self.freq.get(uk, 1)

# ── URL / ARTICLE EXTRACTION ─────────────────────────────────────────────────

def resolve_url(url: str) -> str:
    if not url or "news.google.com" not in url:
        return url
    for method in ("HEAD", "GET"):
        try:
            fn = requests.head if method == "HEAD" else requests.get
            r  = fn(url, allow_redirects=True, timeout=7,
                    headers={"User-Agent": "Mozilla/5.0"})
            final = r.url
            if "google.com" not in final:
                return final
        except Exception:
            pass
    return url


def extract_article(url: str, max_chars: int = 700) -> tuple[str, str]:
    """Returns (text, canonical_url). Falls back to regex scrape if newspaper4k fails."""
    resolved = resolve_url(url)
    if NEWSPAPER_OK:
        for u in (resolved, url):
            try:
                art  = newspaper.article(u)
                text = art.text.strip()
                if len(text) > 150:
                    prefix = ""
                    if art.publish_date:
                        prefix = f"[{art.publish_date.strftime('%Y-%m-%d')}] "
                    canonical = getattr(art, "url", "") or u
                    return (prefix + text)[:max_chars], canonical
            except Exception:
                pass
    try:
        r = requests.get(resolved, timeout=9, headers={"User-Agent": "Mozilla/5.0"})
        canonical = r.url if ("google.com" not in r.url and "/" in r.url[8:]) else url
        t = re.sub(r"<script[^>]*>.*?</script>", " ", r.text,
                   flags=re.DOTALL | re.IGNORECASE)
        t = re.sub(r"<style[^>]*>.*?</style>",  " ", t,
                   flags=re.DOTALL | re.IGNORECASE)
        t = re.sub(r"<[^>]+>", " ", t)
        return re.sub(r"\s+", " ", t).strip()[:max_chars], canonical
    except Exception as e:
        return f"[fetch failed: {e}]", url

# ── PARALLEL WEB RESEARCH ────────────────────────────────────────────────────

def _real_url_from_entry(entry) -> str:
    """Returns the Google News redirect link (entry.link).
    We avoid source.href because it's the publisher homepage, not the article."""
    return entry.get("link", "")


def _search_one(q_cfg: dict, coverage: CoverageTracker) -> list[dict]:
    """Single Google News RSS search — called in thread pool."""
    query   = q_cfg["q"]
    sezione = q_cfg["sezione"]
    q_enc   = urllib.parse.quote_plus(f"{query} when:1d")
    url     = f"https://news.google.com/rss/search?q={q_enc}&hl=en&gl=US&ceid=US:en"

    try:
        feed = feedparser.parse(url)
    except Exception:
        return []

    cutoff  = datetime.now(timezone.utc) - timedelta(hours=36)
    results = []

    for entry in feed.entries[:12]:
        pub = None
        for attr in ("published_parsed", "updated_parsed"):
            v = getattr(entry, attr, None)
            if v:
                pub = datetime(*v[:6], tzinfo=timezone.utc)
                break
        if pub and pub < cutoff:
            continue

        title   = re.sub(r"<[^>]+>", " ", entry.get("title", "")).strip()
        src_obj = entry.get("source", {})
        source  = (src_obj.get("title", "") if isinstance(src_obj, dict) else "")
        snippet = re.sub(r"&\w+;", " ",
                  re.sub(r"<[^>]+>", " ", entry.get("summary", "")))[:250].strip()
        link    = _real_url_from_entry(entry)
        date    = pub.strftime("%Y-%m-%d") if pub else "recent"

        if not title or not coverage.register(title, link):
            continue

        results.append({
            "titolo":  title,
            "fonte":   source,
            "sezione": sezione,
            "snippet": snippet,
            "link":    link,
            "data":    date,
            "full_text": "",
        })

    return results


def _is_article_url(url: str) -> bool:
    """True if the URL has a specific path (not just homepage or root)."""
    if not url or "google.com" in url:
        return False
    try:
        from urllib.parse import urlparse
        path = urlparse(url).path.rstrip("/")
        return len(path) > 6
    except Exception:
        return False


def _fetch_one(art: dict) -> dict:
    """Fetch full article text + resolve canonical article URL."""
    art = art.copy()
    text, canonical = extract_article(art["link"], max_chars=700)
    art["full_text"] = text
    if _is_article_url(canonical):
        art["link"] = canonical
    return art


def _article_fetch_priority(art: dict, priority_sources: set, keywords: list) -> float:
    """Score 0-10 to decide which articles to fetch — no API call."""
    score = 0.0
    source_lower = art["fonte"].lower()
    if any(s in source_lower for s in priority_sources):
        score += 4
    title_lower = art["titolo"].lower()
    score += sum(1.5 for kw in keywords if kw.lower() in title_lower)
    score += art.get("_freq", 1) * 0.5
    return score


def parallel_web_research(queries: list[dict],
                           coverage: CoverageTracker,
                           cfg: dict,
                           max_fetch: int = 25) -> list[dict]:
    """
    Phase 2: parallel research across all topics simultaneously.
    Step A — Google News RSS in parallel (ThreadPoolExecutor)
    Step B — Fetch priority articles in parallel
    """
    print(f"  🔍 Parallel search: {len(queries)} queries...", end="", flush=True)

    raw: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(len(queries), 10)) as ex:
        futures = [ex.submit(_search_one, q, coverage) for q in queries]
        for f in as_completed(futures):
            raw.extend(f.result() or [])

    print(f" {len(raw)} unique articles found")

    for art in raw:
        art["_freq"] = coverage.frequency(art["link"])

    fetch_keywords = cfg.get("fetch_priority_keywords", [
        "automation", "laboratory", "diagnostics", "acquisition", "FDA", "CE",
    ])
    priority_sources = set(cfg.get("priority_sources", list(PRIORITY_SOURCES)))

    to_fetch = sorted(raw,
                      key=lambda a: _article_fetch_priority(a, priority_sources, fetch_keywords),
                      reverse=True)[:max_fetch]
    fetch_ids = {art["link"] for art in to_fetch}

    print(f"  📄 Parallel fetch: {len(to_fetch)} articles...", end="", flush=True)
    fetched_map: dict[str, str] = {}
    if not to_fetch:
        print(" nothing to fetch")
        return raw
    with ThreadPoolExecutor(max_workers=max(1, min(len(to_fetch), 12))) as ex:
        futures = {ex.submit(_fetch_one, art): art for art in to_fetch}
        for f in as_completed(futures):
            result = f.result()
            fetched_map[result["link"]] = result["full_text"]

    for art in raw:
        if art["link"] in fetch_ids:
            art["full_text"] = fetched_map.get(art["link"], "")
    print(f" done")

    return raw


def fetch_rss_feeds(feeds: list[dict], coverage: CoverageTracker) -> list[dict]:
    """Fetch specialized RSS feeds."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=36)
    results = []

    def _fetch_feed(feed_cfg: dict) -> list[dict]:
        items = []
        try:
            fd = feedparser.parse(feed_cfg["url"])
        except Exception:
            return items
        for entry in fd.entries[:15]:
            pub = None
            for attr in ("published_parsed", "updated_parsed"):
                v = getattr(entry, attr, None)
                if v:
                    pub = datetime(*v[:6], tzinfo=timezone.utc)
                    break
            if pub and pub < cutoff:
                continue
            title   = re.sub(r"<[^>]+>", " ", entry.get("title", "")).strip()
            snippet = re.sub(r"<[^>]+>", " ", entry.get("summary", ""))[:250].strip()
            link    = entry.get("link", "")
            date    = pub.strftime("%Y-%m-%d") if pub else "recent"
            if not title or not coverage.register(title, link):
                continue
            items.append({
                "titolo":    title,
                "fonte":     fd.feed.get("title", feed_cfg["url"])[:40],
                "sezione":   feed_cfg["sezione"],
                "snippet":   snippet,
                "link":      link,
                "data":      date,
                "full_text": "",
                "_freq":     1,
            })
        return items

    with ThreadPoolExecutor(max_workers=max(1, len(feeds))) as ex:
        for batch in as_completed([ex.submit(_fetch_feed, f) for f in feeds]):
            results.extend(batch.result())

    return results

# ── SCIENTIFIC LITERATURE ─────────────────────────────────────────────────────

def fetch_arxiv(categories: list, giorni: int, max_papers: int) -> list[dict]:
    items = []
    for cat in categories:
        try:
            feed = feedparser.parse(f"https://export.arxiv.org/rss/{cat}")
        except Exception:
            continue
        for e in feed.entries[:15]:
            title   = e.get("title", "").replace("\n", " ").strip()
            summary = re.sub(r"\s+", " ",
                      re.sub(r"<[^>]+>", " ", e.get("summary", ""))).strip()[:320]
            items.append({"titolo": title, "fonte": f"arXiv:{cat}",
                          "abstract": summary, "link": e.get("link", "")})
            if len(items) >= max_papers:
                return items
    return items[:max_papers]


def fetch_pubmed(queries: list, giorni: int, max_results: int) -> list[dict]:
    items = []
    base   = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    cutoff = (datetime.now() - timedelta(days=giorni)).strftime("%Y/%m/%d")
    for query in queries:
        try:
            ids = requests.get(f"{base}/esearch.fcgi", timeout=10, params={
                "db": "pubmed", "term": query,
                "mindate": cutoff, "maxdate": "3000",
                "datetype": "edat", "retmax": 4,
                "retmode": "json", "sort": "relevance"
            }).json().get("esearchresult", {}).get("idlist", [])
            if not ids:
                continue
            root = ET.fromstring(requests.get(f"{base}/efetch.fcgi", timeout=10,
                params={"db":"pubmed","id":",".join(ids),
                        "rettype":"abstract","retmode":"xml"}).text)
            for art in root.findall(".//PubmedArticle"):
                t = art.find(".//ArticleTitle")
                a = art.find(".//AbstractText")
                p = art.find(".//PMID")
                title = (t.text or "").strip() if t is not None else ""
                if title:
                    items.append({
                        "titolo":   title,
                        "fonte":    "PubMed",
                        "abstract": ((a.text or "")[:320]) if a is not None else "",
                        "link":     f"https://pubmed.ncbi.nlm.nih.gov/{p.text}/"
                                    if p is not None else "",
                    })
        except Exception:
            pass
        if len(items) >= max_results:
            break
    return items[:max_results]

# ── PLAN STEP (Haiku) ────────────────────────────────────────────────────────

def _validate_queries(queries: list[dict], max_test: int = 5) -> list[dict]:
    """Discard queries that return 0 results on Google News RSS."""
    valid = []
    tested = 0
    for q_cfg in queries:
        q     = q_cfg["q"]
        q_enc = urllib.parse.quote_plus(f"{q} when:1d")
        url   = f"https://news.google.com/rss/search?q={q_enc}&hl=en&gl=US&ceid=US:en"
        if tested < max_test:
            try:
                feed = feedparser.parse(url)
                tested += 1
                if not feed.entries:
                    print(f"    [skip] 0 results: '{q}'")
                    continue
            except Exception:
                pass
        valid.append(q_cfg)
    return valid


def _load_vault_context(cfg: dict) -> str:
    """
    Reads configured Obsidian notes and extracts a compact block of proper nouns
    (competitors, products, topics) useful for Haiku to generate queries.
    Strategy: headings + bullets + lines matching domain keywords.
    """
    vault_path = cfg.get("output", {}).get("obsidian_vault", "")
    if not vault_path:
        return ""

    # Note paths configurable via output.obsidian_context_notes
    notes = cfg.get("output", {}).get("obsidian_context_notes", [])
    if not notes:
        return ""

    domain_keywords = cfg.get("vault_context_keywords", [
        "automation", "competitor", "market", "acquisition", "product", "launch",
    ])
    _kw = re.compile("|".join(re.escape(k) for k in domain_keywords), re.IGNORECASE)

    chunks = []
    for rel in notes:
        p = Path(vault_path) / rel
        try:
            text = p.read_text(encoding="utf-8")
            text = re.sub(r"^---.*?---\n", "", text, flags=re.DOTALL)
            text = re.sub(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", r"\1", text)
            lines = []
            for l in text.splitlines():
                s = l.strip()
                if not s or len(s) < 6 or s.startswith("```"):
                    continue
                is_heading = s.startswith("#")
                is_bullet  = s.startswith(("- ", "* ", "+ ")) or re.match(r"^\d+\.", s)
                has_keyword = bool(_kw.search(s))
                if is_heading or is_bullet or has_keyword:
                    clean = re.sub(r"\*+", "", s).strip()[:130]
                    lines.append(clean)
                    if len(lines) >= 35:
                        break
            chunks.append(f"[{Path(rel).stem}]\n" + "\n".join(lines))
        except Exception:
            pass
    return "\n\n".join(chunks)


def plan_search(cfg: dict, client, data_oggi: str) -> list[dict]:
    user_name = cfg.get("user_name", "the user")
    seed = "\n".join(f"  {q['q']}  [{q['sezione']}]" for q in cfg["news_queries"])

    vault_ctx = _load_vault_context(cfg)
    vault_block = (f"\nADDITIONAL CONTEXT FROM KNOWLEDGE BASE (use to generate extra queries):\n{vault_ctx[:1200]}\n"
                   if vault_ctx else "")

    prompt = f"""You are a news search strategist for Google News. Date: {data_oggi}.

CONTEXT — what {user_name} monitors:
{cfg['chi_sono'][:1600]}
{vault_block}
REFERENCE QUERIES (working examples from config — use as style guide):
{seed}

Generate 14-18 Google News search queries in English.

CRITICAL RULES FOR QUERY STYLE:
- Write queries like a journalist would write a headline or search — SHORT, journalistic language
- GOOD: "Siemens Healthineers diagnostics 2026", "Roche lab automation news"
- BAD: "cobas lysis-multianalyte platform throughput optimization hospital workflow" ← too technical, 0 results
- GOOD: "Beckman Coulter laboratory acquisition", "bioMerieux microbiology launch"
- BAD: "VITEK automated antimicrobial susceptibility testing clinical performance" ← too specific
- Max 6 words per query, use company names + 1-2 topic words
- Add "2026" only if it fits naturally

DISTRIBUTION: 7-8 IVD & Lab Automation, 3-4 Mercati LATAM & APAC, 3-4 Biotech & Digital Health
For Biotech: AI only if clearly applied to diagnostics/laboratory — no generic AI news.

JSON ONLY (no other text):
[{{"q":"...", "sezione":"IVD & Lab Automation|Mercati LATAM & APAC|Biotech & Digital Health"}}]"""

    for model in ["claude-haiku-4-5-20251001", cfg["modello"]]:
        try:
            r = client.messages.create(
                model=model, max_tokens=1200,
                messages=[{"role": "user", "content": prompt}])
            tracker.add(r.usage, model)
            m = re.search(r"\[.*\]", r.content[0].text, re.DOTALL)
            if m:
                plan = json.loads(m.group())
                print(f"  Raw plan: {len(plan)} queries ({model.split('-')[1]})")
                plan = _validate_queries(plan, max_test=6)
                seed_set = {q["q"].lower() for q in plan}
                for sq in cfg["news_queries"]:
                    if sq["q"].lower() not in seed_set:
                        plan.append(sq)
                print(f"  Final plan: {len(plan)} queries (validated)")
                return plan
        except Exception as e:
            print(f"  [warn] plan/{model}: {e}")

    return cfg["news_queries"]

# ── MINI AGENTIC LOOP ────────────────────────────────────────────────────────

def agentic_followup(articles: list[dict], cfg: dict,
                     client, coverage: CoverageTracker,
                     already_searched: list[str],
                     max_steps: int = 4) -> list[dict]:
    """
    After parallel collection, Claude sees what's been found and can request
    1-2 targeted searches to fill important gaps. Stops early if no gaps found.
    """
    user_name = cfg.get("user_name", "the user")

    for step in range(max_steps):
        by_section: dict[str, list] = {}
        for a in articles:
            by_section.setdefault(a["sezione"], []).append(a["titolo"])

        summary_lines = []
        for sec, titles in by_section.items():
            summary_lines.append(f"{sec} ({len(titles)} articles):")
            for t in titles[:8]:
                summary_lines.append(f"  - {t[:90]}")

        summary = "\n".join(summary_lines)
        already_txt = "\n".join(f"  - {q}" for q in already_searched[-30:])

        prompt = f"""You are a research editor. You have collected {len(articles)} articles for {user_name}'s briefing.

PROFILE (brief): {cfg['chi_sono'][:400]}

QUERIES ALREADY RUN (do not repeat anything similar):
{already_txt}

ARTICLES ALREADY COLLECTED:
{summary}

Is there a RELEVANT gap not covered — a key competitor with no news, a geographic market absent,
an important event (deal, approval, launch) that might be circulating today?

RESPOND JSON ONLY:
- Gap found (max 2 NEW queries DIFFERENT from those already done): {{"follow_up": [{{"q":"...","sezione":"..."}}]}}
- Sufficient coverage: {{"follow_up": []}}

Queries: journalistic style, 3-5 words, DIFFERENT from everything already searched."""

        try:
            r = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}]
            )
            tracker.add(r.usage, "haiku")
            m = re.search(r"\{.*\}", r.content[0].text, re.DOTALL)
            if not m:
                break
            data = json.loads(m.group())
            queries = data.get("follow_up", [])
        except Exception as e:
            print(f"  [warn] followup step {step+1}: {e}")
            break

        if not queries:
            print(f"  ✓ Step {step+1}: no gaps — search complete")
            break

        print(f"  🔎 Step {step+1}: {len(queries)} follow-up queries", end="", flush=True)
        new_arts = parallel_web_research(queries, coverage, cfg, max_fetch=6)
        already_searched.extend(q["q"] for q in queries)
        articles.extend(new_arts)
        print(f" → +{len(new_arts)} new articles")
        if len(new_arts) == 0:
            print(f"  ✓ 0 new articles — early stop")
            break

    return articles


# ── SCORE & WRITE (Sonnet) ────────────────────────────────────────────────────

def score_and_write(articles: list[dict],
                    arxiv: list[dict], pubmed: list[dict],
                    cfg: dict, client,
                    data_oggi: str) -> tuple[str, list[dict]]:
    """
    Single Sonnet call: evaluate relevance, write the full report.
    Returns (markdown_report, scored_articles).
    """
    user_name = cfg.get("user_name", "the user")

    items_txt = ""
    for i, art in enumerate(articles):
        items_txt += f"[{i}] {art['sezione']} | {art['fonte']} | {art['data']}\n"
        items_txt += f"T: {art['titolo']}\n"
        if art.get("snippet"):
            items_txt += f"S: {art['snippet'][:180]}\n"
        if art.get("full_text") and len(art["full_text"]) > 80:
            items_txt += f"X: {art['full_text'][:350]}\n"
        items_txt += "\n"

    lit_txt = ""
    for p in (arxiv + pubmed):
        lit_txt += f"[LIT] {p['fonte']} — {p['titolo']}\n  {p.get('abstract','')[:250]}\n  {p['link']}\n\n"

    prompt = f"""You are the best executive assistant in the world — you prepare a daily briefing for {user_name}.

=== WHO IS {user_name.upper()} ===
{cfg['chi_sono']}

=== TODAY: {data_oggi} ===

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
[{{"idx":0,"score":3,"riassunto":"2 sentences IT: key facts","perche":"1 sentence connecting to user's role/products/competitors"}},...]

---JSON_END---

BLOCK 2: Full Obsidian Markdown report:
---
created: {data_oggi}
tags: [quotidiano, digest]
tipo: morning-digest
---

# 📰 Morning Digest — {data_oggi}

## TL;DR
- **[short title](article_url)** — [one sentence] ← max 8 bullets, score 3 first then best score 2. URL required.

---

## 🔬 IVD & Lab Automation
### [Title]
*[Source] · [date]* `score:N`
[2-sentence summary]
**Perché ti riguarda:** [why it matters to the user]
[Leggi →](url)
---

IMPORTANT: on the line with source and date ALWAYS write `score:N` where N is 1, 2 or 3 (backticks included).
Example: *MedTech Dive · {data_oggi}* `score:3`

[sections: 🌎 Mercati LATAM & APAC, 🧬 Biotech & Digital Health, 📚 Letteratura Scientifica]

## 📌 Idee da tenere
- [concrete actionable idea for the user]

=== REPORT RULES ===
- At least 3 articles per main section
- EVERY article MUST have `score:N` on the source/date line — N matches the JSON score. NEVER omit this tag.
- Literature: include 3-5 relevant papers
- "Perché ti riguarda": explicitly connect to the user's products, competitors, target markets
- NO generic AI: Biotech includes AI only if clearly tied to diagnostics/laboratory
"""

    response = client.messages.create(
        model=cfg["modello"],
        max_tokens=9000,
        messages=[{"role": "user", "content": prompt}]
    )
    tracker.add(response.usage, cfg["modello"])
    raw = response.content[0].text

    if "---JSON_END---" in raw:
        json_part, md_part = raw.split("---JSON_END---", 1)
    else:
        json_part = ""
        md_part   = raw

    scored: list[dict] = []
    m = re.search(r"\[.*\]", json_part, re.DOTALL)
    if m:
        try:
            scored = json.loads(m.group())
            for s in scored:
                idx = s.get("idx")
                if idx is not None and 0 <= idx < len(articles):
                    s.update({k: articles[idx][k] for k in
                               ("titolo","fonte","sezione","link","data")
                               if k in articles[idx]})
        except json.JSONDecodeError:
            pass

    md_part = md_part.strip()
    if md_part.startswith("```"):
        md_part = re.sub(r"^```\w*\n?", "", md_part)
        md_part = re.sub(r"\n?```$", "", md_part)

    return md_part, scored

# ── HTML NEWSPAPER ────────────────────────────────────────────────────────────

def _parse_articles_md(section_body: str) -> list[dict]:
    articles = []
    for block in re.split(r"\n---+\n", section_body):
        block = block.strip()
        if not block:
            continue
        tm = re.match(r"###\s+(.+)", block)
        if not tm:
            continue
        title    = re.sub(r"\s*\{#[^}]+\}", "", tm.group(1)).strip()
        meta_line_m = re.search(r"\*([^*\n]+)\*\s*(?:`score:(\d)`)?", block)
        meta     = meta_line_m.group(1).strip() if meta_line_m else ""
        score    = int(meta_line_m.group(2)) if (meta_line_m and meta_line_m.group(2)) else 1
        perche_m = re.search(r"\*\*Perché ti riguarda:\*\*\s*(.+?)(?:\n|$)", block)
        perche   = perche_m.group(1).strip() if perche_m else ""
        url_m    = re.search(r"\[Leggi\s*[→>][^\]]*\]\(([^)]+)\)", block)
        url      = url_m.group(1) if url_m else ""
        bs       = block.find("\n", block.find("*") + 1) if "*" in block else 0
        be       = block.find("**Perché") if "**Perché" in block else len(block)
        body     = re.sub(r"^\[Leggi.*$", "", block[bs:be],
                          flags=re.MULTILINE).strip() if bs > 0 else ""
        articles.append({"title": title, "meta": meta, "body": body,
                          "perche": perche, "url": url, "score": score})
    return articles


def generate_html_newspaper(md_content: str,
                             scored_articles: list[dict],
                             data_oggi: str,
                             html_path: str,
                             cfg: dict) -> str:

    colors = _load_colors(cfg)
    BLUE    = colors["primary"]
    DKBLUE  = colors["dark"]
    DKBLUE2 = colors["darker"]
    ORANGE  = colors["accent"]
    DARK    = colors["text"]
    LGREY   = colors["text_secondary"]
    LBLUE   = colors["light_blue"]
    LBLUE2  = colors["light_blue2"]
    GREEN   = colors["green"]

    user_name    = cfg.get("user_name", "User")
    organization = cfg.get("organization", "")
    obsidian_vault  = cfg.get("output", {}).get("obsidian_vault", "")
    obsidian_folder = cfg.get("output", {}).get("obsidian_import_folder", "Saved")

    SECTION_STYLE = {
        "IVD & Lab Automation":     {"color": DKBLUE,  "light": LBLUE2,  "emoji": "🔬"},
        "Mercati LATAM & APAC":     {"color": DKBLUE2, "light": "#EBF4F8","emoji": "🌎"},
        "Biotech & Digital Health": {"color": BLUE,    "light": LBLUE2,  "emoji": "🧬"},
        "Letteratura Scientifica":  {"color": ORANGE,  "light": "#FDF3ED","emoji": "📚"},
        "Idee da tenere":           {"color": "#9A7A1A","light": "#FDF8E8","emoji": "📌"},
    }

    SCORE_STYLE = {
        3: {"color": GREEN, "label": "PRIORITÀ ALTA", "dot": "●", "show": True,  "outline": False},
        2: {"color": LGREY, "label": "",              "dot": "",  "show": False, "outline": False},
        1: {"color": LGREY, "label": "",              "dot": "",  "show": False, "outline": False},
    }

    # Parse TL;DR
    tldr_items = []
    tldr_m = re.search(r"## TL;DR\n(.*?)(?:\n---|\n## )", md_content, re.DOTALL)
    if tldr_m:
        for line in tldr_m.group(1).splitlines():
            line = line.strip()
            if not line.startswith("- "):
                continue
            text = line[2:]
            linked = re.search(r"\*\*\[([^\]]+)\]\(([^)]+)\)\*\*\s*[—\-]\s*(.*)", text)
            if linked:
                title, url, desc = linked.group(1), linked.group(2), linked.group(3)
                html_item = (f'<a href="{url}" target="_blank" class="tldr-link">'
                             f'<strong>{title}</strong></a>'
                             + (f" — {desc}" if desc else ""))
            else:
                html_item = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
                url = ""
            tldr_items.append({"html": html_item, "url": url})

    # Parse sections
    sections = []
    sec_pat = re.compile(
        r"## ([🔬🌎🧬📚📌][^\n]+)\n(.*?)(?=\n## (?:[🔬🌎🧬📚📌🤖]|TL;DR)|\Z)",
        re.DOTALL)
    for m in sec_pat.finditer(md_content):
        title = m.group(1).strip()
        body  = m.group(2).strip()
        if "Idee da tenere" in title:
            idee = [l[2:].strip() for l in body.splitlines()
                    if l.strip().startswith("- ") and len(l) > 4]
            sections.append({"title": title, "idee": idee})
        else:
            arts = _parse_articles_md(body)
            if arts:
                sections.append({"title": title, "articles": arts})

    try:
        dt = datetime.strptime(data_oggi, "%Y-%m-%d")
        WD = ["Lunedì","Martedì","Mercoledì","Giovedì","Venerdì","Sabato","Domenica"]
        MO = ["","Gennaio","Febbraio","Marzo","Aprile","Maggio","Giugno",
              "Luglio","Agosto","Settembre","Ottobre","Novembre","Dicembre"]
        date_str = f"{WD[dt.weekday()]} {dt.day} {MO[dt.month]} {dt.year}"
    except Exception:
        date_str = data_oggi

    total_arts = sum(len(s.get("articles",[])) for s in sections if "articles" in s)
    high_count = sum(1 for s in scored_articles if s.get("score") == 3)
    org_label  = f"· {organization}" if organization else ""

    def render_section(sec: dict) -> str:
        title = sec["title"]
        key   = next((k for k in SECTION_STYLE if k in title), "")
        st    = SECTION_STYLE.get(key, {"color": DKBLUE, "light": LBLUE2, "emoji": "📄"})
        col, light = st["color"], st["light"]

        if "idee" in sec:
            items_html = "".join(f"<li>{i}</li>" for i in sec["idee"])
            return f'''<section class="digest-section">
  <div class="section-header" style="border-color:{col}">
    <h2 class="section-title" style="color:{col}">{title}</h2>
  </div>
  <ul class="ideas-list" style="background:{light};border-left:4px solid {col}">{items_html}</ul>
</section>'''

        arts    = sec.get("articles", [])
        featured = [a for a in arts if a.get("score") == 3]
        regular  = [a for a in arts if a.get("score") != 3]

        def card(art: dict, feat: bool = False) -> str:
            sc    = art.get("score", 2)
            ss    = SCORE_STYLE.get(sc, SCORE_STYLE[2])
            if not ss.get("show"):
                badge = ""
            elif ss.get("outline"):
                badge = (f'<span class="score-badge" '
                         f'style="background:transparent;color:{ss["color"]};'
                         f'border:1px solid {ss["color"]}">'
                         f'{ss["dot"]} {ss["label"]}</span>')
            else:
                badge = (f'<span class="score-badge" '
                         f'style="background:{ss["color"]};color:white">'
                         f'{ss["dot"]} {ss["label"]}</span>')
            body_esc   = art["body"].replace("<","&lt;").replace(">","&gt;")
            perche_esc = art["perche"].replace("<","&lt;").replace(">","&gt;")
            link_html  = (f'<a class="read-link" href="{art["url"]}" '
                          f'target="_blank" style="color:{col}">Leggi l\'articolo →</a>'
                          if art["url"] else "")
            feat_cls   = " featured-card" if feat else ""
            feat_style = (f'border-top:4px solid {col};background:white;'
                          if feat else f'border-top:2px solid {col};')
            header = f'<div class="card-header">{badge}</div>' if badge else ""
            sec_name  = title.replace('"', '&quot;')
            art_title = art["title"].replace('"','&quot;').replace("'","&#39;")
            art_meta  = art["meta"].replace('"','&quot;').replace("'","&#39;")
            art_body  = art["body"].replace('"','&quot;').replace("'","&#39;")
            art_perche= art["perche"].replace('"','&quot;').replace("'","&#39;")
            art_url   = art.get("url","")
            return f'''<div class="article-card{feat_cls}" style="{feat_style}"
  data-title="{art_title}" data-meta="{art_meta}"
  data-body="{art_body}" data-perche="{art_perche}"
  data-url="{art_url}" data-section="{sec_name}">
  <label class="card-select" title="Select to import to Obsidian">
    <input type="checkbox" class="article-checkbox" onchange="updateBar()">
  </label>
  {header}
  <h3 class="card-title">{art["title"]}</h3>
  <div class="card-meta">{art["meta"]}</div>
  <p class="card-body">{body_esc}</p>
  <div class="card-perche" style="background:{light};border-left:3px solid {col}">
    <span class="perche-label">Perché ti riguarda</span>
    {perche_esc}
  </div>
  {link_html}
</div>'''

        featured_html = ""
        if featured:
            cards_html = "\n".join(card(a, True) for a in featured)
            featured_html = f'<div class="featured-grid">{cards_html}</div>'

        regular_html = ""
        if regular:
            cards_html = "\n".join(card(a, False) for a in regular)
            regular_html = f'<div class="articles-grid">{cards_html}</div>'

        count_badge = (f'<span class="section-count" style="background:{col}">'
                       f'{len(arts)} articoli</span>')
        feat_badge  = (f'<span class="feat-badge" '
                       f'style="background:{GREEN};color:white">'
                       f'● {len(featured)} priorità alta</span>'
                       if featured else "")

        return f'''<section class="digest-section">
  <div class="section-header" style="border-color:{col}">
    <h2 class="section-title" style="color:{col}">{title}</h2>
    <div class="section-badges">{count_badge}{feat_badge}</div>
  </div>
  {featured_html}
  {regular_html}
</section>'''

    sections_html = "\n".join(render_section(s) for s in sections)
    tldr_html = "".join(f'<li>{item["html"]}</li>' for item in tldr_items)

    html = f"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Morning Digest — {user_name} — {data_oggi}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700&family=Merriweather:wght@400;700&display=swap" rel="stylesheet">
<style>
:root{{
  --blue:{BLUE};--dkblue:{DKBLUE};--dkblue2:{DKBLUE2};
  --orange:{ORANGE};--dark:{DARK};--lgrey:{LGREY};
  --lblue:{LBLUE};--lblue2:{LBLUE2};--green:{GREEN};
  --bg:#F4F6F8;--white:#FFFFFF;--border:#DDE3EA;
  --radius:8px;--shadow:0 2px 12px rgba(0,0,0,.08);
  --shadow-hover:0 6px 24px rgba(0,0,0,.14);
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Montserrat',sans-serif;background:var(--bg);color:var(--dark);line-height:1.6}}
.masthead{{background:var(--dkblue2);padding:0}}
.masthead-inner{{
  display:flex;justify-content:space-between;align-items:stretch;
  max-width:1320px;margin:0 auto;padding:24px 40px 0;
}}
.logo-area{{display:flex;flex-direction:column;justify-content:flex-end;padding-bottom:20px}}
.logo-eyebrow{{font-size:.7rem;letter-spacing:3px;color:var(--blue);
               text-transform:uppercase;font-weight:600;margin-bottom:4px}}
.logo-title{{font-size:clamp(2rem,4vw,3rem);font-weight:700;color:white;
             line-height:1.1;letter-spacing:-.5px}}
.logo-title span{{color:var(--blue)}}
.masthead-right{{
  display:flex;flex-direction:column;align-items:flex-end;
  justify-content:flex-end;padding-bottom:20px;gap:6px
}}
.masthead-date{{color:white;font-size:1rem;font-weight:600}}
.masthead-sub{{color:rgba(255,255,255,.6);font-size:.75rem}}
.masthead-bar{{
  background:var(--blue);height:4px;
  background:linear-gradient(90deg,var(--blue),var(--dkblue),var(--orange));
}}
.stats-bar{{
  background:var(--white);border-bottom:1px solid var(--border);
  padding:10px 40px;display:flex;gap:24px;align-items:center;
  font-size:.78rem;color:var(--lgrey);
}}
.stats-bar strong{{color:var(--dark)}}
.stat-pill{{
  background:var(--lblue2);border:1px solid var(--lblue);
  border-radius:12px;padding:3px 10px;font-size:.72rem;font-weight:600;
  color:var(--dkblue);
}}
.stat-pill.high{{background:#E8F5EE;border-color:#A8D5B8;color:var(--green)}}
.legend-sep{{color:var(--border);margin:0 4px}}
.legend-item{{display:flex;align-items:center;gap:5px;font-size:.75rem;color:var(--lgrey)}}
.tldr-section{{
  background:var(--dkblue2);
  border-bottom:1px solid rgba(255,255,255,.08);
  padding:20px 40px;
}}
.tldr-header{{display:flex;align-items:center;gap:10px;margin-bottom:12px}}
.tldr-tag{{
  background:var(--orange);color:white;
  font-size:.65rem;font-weight:700;letter-spacing:2px;
  text-transform:uppercase;padding:3px 10px;border-radius:3px;
}}
.tldr-section ul{{
  list-style:none;display:grid;
  grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:6px 32px;
}}
.tldr-section li{{color:rgba(255,255,255,.88);font-size:.85rem;padding-left:12px;position:relative}}
.tldr-section li::before{{content:"▸";color:var(--blue);position:absolute;left:0;font-weight:700}}
.tldr-link{{color:white;text-decoration:none;border-bottom:1px solid rgba(255,255,255,.3)}}
.tldr-link:hover{{border-bottom-color:var(--blue);color:var(--blue)}}
.container{{max-width:1320px;margin:0 auto;padding:36px 40px}}
.digest-section{{margin-bottom:52px}}
.section-header{{
  display:flex;align-items:center;gap:12px;
  border-top:3px solid;padding-top:14px;margin-bottom:20px;
}}
.section-title{{font-family:'Montserrat',sans-serif;font-size:1.15rem;font-weight:700;flex:1}}
.section-badges{{display:flex;gap:8px;align-items:center}}
.section-count{{color:white;font-size:.68rem;font-weight:700;padding:3px 10px;border-radius:12px}}
.feat-badge{{font-size:.68rem;font-weight:700;padding:3px 10px;border-radius:12px}}
.featured-grid{{
  display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));
  gap:20px;margin-bottom:20px;
}}
.featured-card{{box-shadow:var(--shadow-hover)!important}}
.articles-grid{{
  display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px;
}}
.article-card{{
  background:var(--white);border-radius:var(--radius);
  box-shadow:var(--shadow);padding:20px;
  display:flex;flex-direction:column;gap:10px;
  transition:box-shadow .2s,transform .2s;position:relative;
}}
.article-card:hover{{box-shadow:var(--shadow-hover);transform:translateY(-2px)}}
.card-header{{display:flex;align-items:center;gap:8px}}
.score-badge{{
  font-size:.67rem;font-weight:700;letter-spacing:.5px;
  padding:3px 9px;border-radius:10px;font-family:'Montserrat',sans-serif;
}}
.card-title{{font-size:.97rem;font-weight:700;line-height:1.4;font-family:'Montserrat',sans-serif}}
.card-meta{{font-size:.73rem;color:var(--lgrey);font-family:'Montserrat',sans-serif}}
.card-body{{font-family:'Merriweather',serif;font-size:.85rem;line-height:1.65;color:#444;flex:1}}
.card-perche{{padding:10px 14px;border-radius:5px;font-size:.82rem;font-family:'Montserrat',sans-serif}}
.perche-label{{
  display:block;font-size:.65rem;font-weight:700;
  text-transform:uppercase;letter-spacing:1px;color:var(--lgrey);margin-bottom:4px;
}}
.read-link{{
  font-size:.78rem;font-weight:700;text-decoration:none;
  font-family:'Montserrat',sans-serif;margin-top:4px;
  display:inline-flex;align-items:center;gap:4px;
}}
.read-link:hover{{text-decoration:underline}}
.ideas-list{{
  list-style:none;padding:16px 20px;border-radius:var(--radius);
  display:flex;flex-direction:column;gap:10px;
  font-size:.9rem;font-family:'Merriweather',serif;
}}
.ideas-list li{{padding-left:16px;position:relative}}
.ideas-list li::before{{content:"→";font-weight:700;position:absolute;left:0;font-family:'Montserrat',sans-serif}}
.footer{{
  text-align:center;padding:28px 40px;font-size:.73rem;color:var(--lgrey);
  border-top:1px solid var(--border);margin-top:20px;font-family:'Montserrat',sans-serif;
}}
.footer a{{color:var(--dkblue);text-decoration:none}}
@media(max-width:720px){{
  .masthead-inner,.stats-bar,.tldr-section,.container,.footer{{padding-left:16px;padding-right:16px}}
  .articles-grid,.featured-grid{{grid-template-columns:1fr}}
  .tldr-section ul{{grid-template-columns:1fr}}
  .masthead-right{{display:none}}
}}
.card-select{{position:absolute;top:10px;right:10px;z-index:2;cursor:pointer;display:flex;align-items:center}}
.article-checkbox{{width:17px;height:17px;cursor:pointer;accent-color:var(--dkblue);border-radius:3px}}
.article-card.selected{{outline:2px solid var(--dkblue);background:var(--lblue2)!important}}
#import-bar{{
  position:fixed;bottom:0;left:0;right:0;background:var(--dkblue2);
  padding:14px 40px;display:flex;align-items:center;gap:16px;
  box-shadow:0 -4px 20px rgba(0,0,0,.25);transform:translateY(100%);
  transition:transform .25s ease;z-index:999;font-family:'Montserrat',sans-serif;
}}
#import-bar.visible{{transform:translateY(0)}}
#import-count{{color:white;font-size:.9rem;font-weight:600;flex:1}}
#import-count span{{color:var(--blue)}}
.import-btn{{
  background:var(--blue);color:white;border:none;padding:10px 22px;border-radius:6px;
  font-family:'Montserrat',sans-serif;font-size:.85rem;font-weight:700;
  cursor:pointer;letter-spacing:.3px;transition:background .15s;
}}
.import-btn:hover{{background:var(--dkblue)}}
.clear-btn{{
  background:transparent;color:rgba(255,255,255,.6);
  border:1px solid rgba(255,255,255,.2);padding:10px 16px;
  border-radius:6px;font-family:'Montserrat',sans-serif;font-size:.82rem;cursor:pointer;
}}
.clear-btn:hover{{color:white;border-color:rgba(255,255,255,.5)}}
@media print{{
  .masthead,.tldr-section{{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
  .article-card{{break-inside:avoid}}
  #import-bar,.card-select{{display:none}}
}}
</style>
</head>
<body>

<header class="masthead">
  <div class="masthead-inner">
    <div class="logo-area">
      <div class="logo-eyebrow">Morning Briefing{(" · " + organization) if organization else ""}</div>
      <div class="logo-title">Morning Digest · <span>{user_name}</span></div>
    </div>
    <div class="masthead-right">
      <div class="masthead-date">{date_str}</div>
      <div class="masthead-sub">IVD &amp; Lab Automation · LATAM/APAC · Biotech</div>
    </div>
  </div>
  <div class="masthead-bar"></div>
</header>

<div class="stats-bar">
  <span>📊 <strong>{total_arts}</strong> articoli selezionati</span>
  <span class="stat-pill">{len([s for s in sections if "articles" in s])} sezioni</span>
  <span>🕐 <strong>{datetime.now().strftime("%H:%M")}</strong> · {data_oggi}</span>
  <span class="legend-sep">|</span>
  <span class="legend-item"><span class="legend-dot" style="background:{GREEN};color:white;padding:1px 6px;border-radius:8px;font-size:.7rem;font-weight:700">● PRIORITÀ ALTA</span> = notizia market-moving</span>
</div>

<div class="tldr-section">
  <div class="tldr-header">
    <span class="tldr-tag">⚡ In breve</span>
    <span style="color:rgba(255,255,255,.5);font-size:.8rem">Le notizie che non puoi perdere oggi</span>
  </div>
  <ul>{tldr_html}</ul>
</div>

<main class="container">
{sections_html}
</main>

<footer class="footer">
  Generato automaticamente · {data_oggi} · Morning Digest · {user_name}{org_label}
</footer>

<div id="import-bar">
  <div id="import-count">Selezionati: <span id="sel-count">0</span> articoli</div>
  <button class="clear-btn" onclick="clearSelection()">✕ Deseleziona</button>
  <button class="import-btn" onclick="importToObsidian()">↗ Importa in Obsidian</button>
</div>

<script>
const VAULT  = "{Path(obsidian_vault).name if obsidian_vault else ""}";
const FOLDER = "{obsidian_folder}";
const DATE   = "{data_oggi}";

function updateBar() {{
  const checked = document.querySelectorAll('.article-checkbox:checked');
  document.getElementById('sel-count').textContent = checked.length;
  document.getElementById('import-bar').classList.toggle('visible', checked.length > 0);
  document.querySelectorAll('.article-card').forEach(c => {{
    c.classList.toggle('selected', c.querySelector('.article-checkbox')?.checked);
  }});
}}

function clearSelection() {{
  document.querySelectorAll('.article-checkbox').forEach(cb => cb.checked = false);
  updateBar();
}}

function importToObsidian() {{
  const cards = document.querySelectorAll('.article-card');
  const selected = [];
  cards.forEach(card => {{
    if (card.querySelector('.article-checkbox')?.checked) {{
      selected.push({{
        title:   card.dataset.title   || '',
        meta:    card.dataset.meta    || '',
        body:    card.dataset.body    || '',
        perche:  card.dataset.perche  || '',
        url:     card.dataset.url     || '',
        section: card.dataset.section || '',
      }});
    }}
  }});
  if (!selected.length) return;

  let md = `---\\ncreated: ${{DATE}}\\ntags: [digest, saved]\\n---\\n\\n`;
  md += `# Saved articles — ${{DATE}}\\n\\n`;
  selected.forEach(a => {{
    md += `## ${{a.title}}\\n`;
    md += `*${{a.meta}}*\\n`;
    if (a.section) md += `Section: ${{a.section}}\\n`;
    md += `\\n${{a.body}}\\n\\n`;
    if (a.perche) md += `**Why it matters:** ${{a.perche}}\\n\\n`;
    if (a.url)    md += `[Read →](${{a.url}})\\n\\n`;
    md += `---\\n\\n`;
  }});

  const file = FOLDER + "/" + DATE + "-saved";
  const uri  = "obsidian://new?vault=" + encodeURIComponent(VAULT)
             + "&file="    + encodeURIComponent(file)
             + "&content=" + encodeURIComponent(md);
  window.location.href = uri;
}}
</script>

</body>
</html>"""

    Path(html_path).parent.mkdir(parents=True, exist_ok=True)
    Path(html_path).write_text(html, encoding="utf-8")
    return html_path

# ── MAIN PIPELINE ─────────────────────────────────────────────────────────────

tracker: CostTracker = None

def main():
    global tracker
    print("🌅 Morning Digest — starting")
    cfg = load_config()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("❌ ANTHROPIC_API_KEY not set"); sys.exit(1)

    client   = anthropic.Anthropic(api_key=api_key)
    tracker  = CostTracker(limit=0.80)
    coverage = CoverageTracker()
    data_oggi = datetime.now().strftime("%Y-%m-%d")

    print(f"   newspaper4k: {'✅' if NEWSPAPER_OK else '❌ regex fallback'}")

    # ── PHASE 1: PLAN ─────────────────────────────────────────────────────────
    print("\n🗺️  Phase 1: Plan...")
    piano = plan_search(cfg, client, data_oggi)

    # ── PHASE 2: PARALLEL WEB RESEARCH ───────────────────────────────────────
    print("\n🔄 Phase 2: Parallel Research...")
    articles = parallel_web_research(piano, coverage, cfg, max_fetch=28)

    if cfg.get("rss_feeds"):
        print(f"  📡 RSS feeds ({len(cfg['rss_feeds'])})...", end="", flush=True)
        rss_arts = fetch_rss_feeds(cfg["rss_feeds"], coverage)
        articles.extend(rss_arts)
        print(f" +{len(rss_arts)} articles")

    print("  📚 arXiv...", end="", flush=True)
    arxiv = fetch_arxiv(cfg["arxiv"]["categories"],
                        cfg["arxiv"]["giorni_indietro"],
                        cfg["arxiv"]["max_papers"])
    print(f" {len(arxiv)} papers")

    print("  🧬 PubMed...", end="", flush=True)
    pubmed = fetch_pubmed(cfg["pubmed"]["queries"],
                          cfg["pubmed"]["giorni_indietro"],
                          cfg["pubmed"]["max_results"])
    print(f" {len(pubmed)} papers")

    print(f"\n  📊 After parallel research: {len(articles)} unique articles")

    # ── PHASE 2b: MINI AGENTIC LOOP ──────────────────────────────────────────
    print("\n🔁 Phase 2b: Agentic follow-up...")
    already_searched = [q["q"] for q in piano]
    articles = agentic_followup(articles, cfg, client, coverage,
                                already_searched, max_steps=4)
    print(f"  📊 Final total: {len(articles)} articles | "
          f"Literature: {len(arxiv)+len(pubmed)} papers")

    # ── PHASE 3: SCORE & WRITE ────────────────────────────────────────────────
    print("\n🤖 Phase 3: Score & Write (Sonnet)...")
    md_report, scored = score_and_write(
        articles, arxiv, pubmed, cfg, client, data_oggi)

    high = sum(1 for s in scored if s.get("score") == 3)
    print(f"  Scoring: {len(scored)} articles selected, {high} high priority")
    print(f"  Cost:    {tracker.summary()}")

    if not md_report.strip():
        print("⚠️  Empty report. Check digest.log"); sys.exit(1)

    # ── PHASE 4: HTML ─────────────────────────────────────────────────────────
    out_cfg      = cfg["output"]
    html_main    = Path(out_cfg["html_path"]).expanduser()
    archive_dir  = Path(__file__).parent / out_cfg.get("archive_dir", "archive")
    archive_dir.mkdir(exist_ok=True)
    html_archive = archive_dir / f"{data_oggi}.html"

    try:
        for path in (html_main, html_archive):
            generate_html_newspaper(md_report, scored, data_oggi, str(path), cfg)
        print(f"\n✅ HTML: {html_main}")
        print(f"   archive: {html_archive}")
    except Exception as e:
        print(f"⚠️  HTML generation failed: {e}")

if __name__ == "__main__":
    main()
