import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import feedparser
import requests

from .trackers import CoverageTracker

try:
    import newspaper
    NEWSPAPER_OK = True
except ImportError:
    NEWSPAPER_OK = False

PRIORITY_SOURCES: set[str] = {
    "medtechdive", "darkdaily", "fiercebiotech", "fiercehealthcare",
    "biopharmaapac", "clinicallabproductsnews", "labmanager",
    "medscape", "healio", "mlo-online", "captodayonline",
    "diagnosticimaging", "healthcareitnews",
}


def resolve_url(url: str) -> str:
    if not url or "news.google.com" not in url:
        return url
    for method in ("HEAD", "GET"):
        try:
            fn = requests.head if method == "HEAD" else requests.get
            r  = fn(url, allow_redirects=True, timeout=7,
                    headers={"User-Agent": "Mozilla/5.0"})
            if "google.com" not in r.url:
                return r.url
        except Exception:
            pass
    return url


def extract_article(url: str, max_chars: int = 700) -> tuple[str, str]:
    """Return (text, canonical_url). Falls back to regex scrape if newspaper4k is unavailable."""
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
        t = re.sub(r"<script[^>]*>.*?</script>", " ", r.text, flags=re.DOTALL | re.IGNORECASE)
        t = re.sub(r"<style[^>]*>.*?</style>",  " ", t,      flags=re.DOTALL | re.IGNORECASE)
        t = re.sub(r"<[^>]+>", " ", t)
        return re.sub(r"\s+", " ", t).strip()[:max_chars], canonical
    except Exception as e:
        return f"[fetch failed: {e}]", url


def _is_article_url(url: str) -> bool:
    if not url or "google.com" in url:
        return False
    try:
        return len(urlparse(url).path.rstrip("/")) > 6
    except Exception:
        return False


def _search_one(query_cfg: dict, coverage: CoverageTracker) -> list[dict]:
    """Single Google News RSS search — called in thread pool."""
    q_enc = urllib.parse.quote_plus(f"{query_cfg['q']} when:1d")
    url   = f"https://news.google.com/rss/search?q={q_enc}&hl=en&gl=US&ceid=US:en"
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
        source  = src_obj.get("title", "") if isinstance(src_obj, dict) else ""
        snippet = re.sub(r"&\w+;", " ",
                  re.sub(r"<[^>]+>", " ", entry.get("summary", "")))[:250].strip()
        link    = entry.get("link", "")
        date    = pub.strftime("%Y-%m-%d") if pub else "recent"

        if not title or not coverage.register(title, link):
            continue

        results.append({
            "title":     title,
            "source":    source,
            "section":   query_cfg["section"],
            "snippet":   snippet,
            "link":      link,
            "date":      date,
            "full_text": "",
        })

    return results


def _fetch_one(art: dict) -> dict:
    art = art.copy()
    text, canonical = extract_article(art["link"], max_chars=700)
    art["full_text"] = text
    if _is_article_url(canonical):
        art["link"] = canonical
    return art


def _fetch_priority_score(
    art: dict, priority_sources: set[str], keywords: list[str]
) -> float:
    score = 0.0
    if any(s in art["source"].lower() for s in priority_sources):
        score += 4
    score += sum(1.5 for kw in keywords if kw.lower() in art["title"].lower())
    score += art.get("_freq", 1) * 0.5
    return score


def parallel_web_research(
    queries:   list[dict],
    coverage:  CoverageTracker,
    cfg:       dict,
    max_fetch: int = 25,
) -> list[dict]:
    """Search all queries in parallel, then fetch the highest-priority articles."""
    print(f"  🔍 Searching: {len(queries)} queries...", end="", flush=True)

    raw: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(len(queries), 10)) as pool:
        futures = [pool.submit(_search_one, q, coverage) for q in queries]
        for f in as_completed(futures):
            raw.extend(f.result() or [])

    print(f" {len(raw)} unique articles")

    for art in raw:
        art["_freq"] = coverage.frequency(art["link"])

    fetch_keywords   = cfg.get("fetch_priority_keywords", [
        "automation", "laboratory", "diagnostics", "acquisition", "FDA", "CE",
    ])
    priority_sources = set(cfg.get("priority_sources", list(PRIORITY_SOURCES)))

    to_fetch  = sorted(
        raw,
        key=lambda a: _fetch_priority_score(a, priority_sources, fetch_keywords),
        reverse=True,
    )[:max_fetch]
    fetch_ids = {art["link"] for art in to_fetch}

    print(f"  📄 Fetching: {len(to_fetch)} articles...", end="", flush=True)
    if not to_fetch:
        print(" nothing to fetch")
        return raw

    fetched_map: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=min(len(to_fetch), 12)) as pool:
        for f in as_completed([pool.submit(_fetch_one, art) for art in to_fetch]):
            result = f.result()
            fetched_map[result["link"]] = result["full_text"]

    for art in raw:
        if art["link"] in fetch_ids:
            art["full_text"] = fetched_map.get(art["link"], "")

    print(" done")
    return raw


def fetch_rss_feeds(feeds: list[dict], coverage: CoverageTracker) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=36)

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
                "title":     title,
                "source":    fd.feed.get("title", feed_cfg["url"])[:40],
                "section":   feed_cfg["section"],
                "snippet":   snippet,
                "link":      link,
                "date":      date,
                "full_text": "",
                "_freq":     1,
            })
        return items

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, len(feeds))) as pool:
        for batch in as_completed([pool.submit(_fetch_feed, f) for f in feeds]):
            results.extend(batch.result())
    return results


def fetch_arxiv(
    categories: list[str], lookback_days: int, max_papers: int
) -> list[dict]:
    items: list[dict] = []
    for cat in categories:
        try:
            feed = feedparser.parse(f"https://export.arxiv.org/rss/{cat}")
        except Exception:
            continue
        for e in feed.entries[:15]:
            title   = e.get("title", "").replace("\n", " ").strip()
            summary = re.sub(r"\s+", " ",
                      re.sub(r"<[^>]+>", " ", e.get("summary", ""))).strip()[:320]
            items.append({
                "title":    title,
                "source":   f"arXiv:{cat}",
                "abstract": summary,
                "link":     e.get("link", ""),
            })
            if len(items) >= max_papers:
                return items
    return items[:max_papers]


def fetch_pubmed(
    queries: list[str], lookback_days: int, max_results: int
) -> list[dict]:
    base   = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y/%m/%d")
    items: list[dict] = []

    for query in queries:
        try:
            ids = requests.get(f"{base}/esearch.fcgi", timeout=10, params={
                "db": "pubmed", "term": query,
                "mindate": cutoff, "maxdate": "3000",
                "datetype": "edat", "retmax": 4,
                "retmode": "json", "sort": "relevance",
            }).json().get("esearchresult", {}).get("idlist", [])
            if not ids:
                continue
            root = ET.fromstring(requests.get(f"{base}/efetch.fcgi", timeout=10,
                params={"db": "pubmed", "id": ",".join(ids),
                        "rettype": "abstract", "retmode": "xml"}).text)
            for art in root.findall(".//PubmedArticle"):
                t = art.find(".//ArticleTitle")
                a = art.find(".//AbstractText")
                p = art.find(".//PMID")
                title = (t.text or "").strip() if t is not None else ""
                if title:
                    items.append({
                        "title":    title,
                        "source":   "PubMed",
                        "abstract": (a.text or "")[:320] if a is not None else "",
                        "link":     f"https://pubmed.ncbi.nlm.nih.gov/{p.text}/"
                                    if p is not None else "",
                    })
        except Exception:
            pass
        if len(items) >= max_results:
            break

    return items[:max_results]
