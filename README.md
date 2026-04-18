# Morning Digest

An AI-powered daily briefing tool for niche professionals who need to stay on top of a complex, fast-moving industry.

You describe your role, competitors, and markets in a plain-text config file. Every morning the tool researches the web, scores every article for relevance to **you specifically**, and renders a clean newspaper-style HTML digest.

> Built for IVD & Lab Automation professionals, but fully configurable for any sector.

---

## What it does

1. **Plans the search** — Claude Haiku reads your profile and Obsidian notes to generate optimized Google News queries
2. **Gathers data in parallel** via `ThreadPoolExecutor`:
   - Google News RSS (configurable queries across competitors, markets, regulations, tech)
   - Specialized RSS feeds (DarkDaily, FierceBiotech, or any feed you add)
   - PubMed (scientific literature, last 7 days)
   - arXiv (ML/AI papers applied to diagnostics and imaging)
3. **Agentic follow-up loop** — Claude identifies coverage gaps and runs targeted searches (up to 4 rounds)
4. **Scores & writes** — Claude Sonnet reads every article and assigns a relevance score (1–3) based on your profile:
   - **Score 3 / HIGH PRIORITY**: market-moving events (M&A, FDA/CE approvals, competitor launches, regulatory changes). Max 3–5 per day — if nothing qualifies, score 3 = 0.
   - **Score 2**: relevant sector news
   - Score 1 articles are discarded before the report is written
5. **Generates an HTML newspaper** with:
   - Featured cards for high-priority articles
   - Clickable TL;DR bullets at the top
   - "Why it matters" for every article
   - Obsidian import button (select articles → one click → saves to your vault)

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/morning-digest.git
cd morning-digest
pip install -r requirements.txt
```

### 2. Set your API key

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
# Add to ~/.bashrc or ~/.zshrc to make it permanent
```

On Windows (PowerShell):
```powershell
[System.Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY","sk-ant-...","User")
```

### 3. Configure

```bash
cp config.example.yaml config.yaml
```

Then edit `config.yaml` — the most important field is `profile`: describe your role, the products you work with, your competitors, and the markets you monitor. Claude uses this verbatim to score every article.

See `config.example.yaml` for detailed comments on every option.

### 4. Run

```bash
python3 digest.py
```

The HTML opens from the path set in `output.html_path` (default: `~/Desktop/digest_today.html`).

---

## Configuration reference

| Field | Description |
|-------|-------------|
| `user_name` | Your name — shown in the HTML header |
| `organization` | Your company — shown in the header/footer |
| `profile` | Your professional profile — **the most important field** |
| `branding` | Optional color overrides (primary, dark, accent, success colors) |
| `news_queries` | Google News queries with `section` assignment |
| `rss_feeds` | Specialized RSS feeds (each with a `section`) |
| `arxiv.categories` | arXiv categories to monitor |
| `arxiv.lookback_days` | How many days back to fetch arXiv papers |
| `pubmed.queries` | PubMed keyword queries |
| `pubmed.lookback_days` | How many days back to fetch PubMed results |
| `output.html_path` | Where to write the daily HTML file |
| `output.obsidian_vault` | Path to your Obsidian vault root (leave empty to disable) |
| `output.obsidian_import_folder` | Vault subfolder for imported articles |
| `output.obsidian_context_notes` | Vault notes to read for richer query generation |
| `model` | Claude model for scoring (default: `claude-sonnet-4-6`) |

---

## Scheduling (optional)

### Linux / macOS (cron)

```bash
# Run at 7:00 AM every weekday
0 7 * * 1-5 cd /path/to/morning-digest && python3 digest.py >> digest.log 2>&1
```

### Windows (Task Scheduler)

Create a `.bat` file:
```bat
@echo off
wsl bash -c "cd /path/to/morning-digest && python3 digest.py >> digest.log 2>&1"
```
Schedule it via Task Scheduler → Daily → 07:00.

To auto-open the HTML after generation, append to your run script:
```bash
# Linux
xdg-open "$HTML_PATH"

# macOS
open "$HTML_PATH"

# Windows WSL
WIN_PATH=$(wslpath -w "$HTML_PATH")
cmd.exe /c start chrome "file:///$WIN_PATH"
```

---

## Obsidian integration

If you set `output.obsidian_vault`, two things happen:
1. **Context enrichment**: notes listed in `output.obsidian_context_notes` are read (headings, bullets, keyword-matching lines) and fed to Haiku to generate richer search queries. Your vault is never modified automatically.
2. **Manual import**: the HTML includes a floating bar — select articles with checkboxes and click "Import to Obsidian". It opens Obsidian with a pre-filled note, ready to save.

Requires the `obsidian://` URI protocol (standard with Obsidian installed).

---

## Architecture

```
config.yaml + Obsidian notes (optional)
        │
        ▼
   [Haiku] plan_search()          ← generates optimized queries + context
        │
        ▼ (parallel, ThreadPoolExecutor)
   fetch_all_sources()
   ├── Google News RSS × N queries
   ├── Specialized RSS feeds
   ├── PubMed API
   └── arXiv API
        │
        ▼
   Agentic follow-up loop (max 4 iterations)
   ├── [Haiku] agentic_followup()  ← identifies gaps, runs targeted searches
   └── stops early if no new articles found
        │
        ▼
   [Sonnet] score_and_write()      ← scores articles + writes full MD report
        │
        ▼
   generate_html()                 ← renders HTML with embedded scores
```

**Typical cost**: $0.05–0.15 USD per run (varies with article count and length).

---

## Project structure

```
morning-digest/
├── digest/
│   ├── agents.py       # AI agents: plan, follow-up, score & write
│   ├── config.py       # Config loading
│   ├── pipeline.py     # Main orchestration
│   ├── renderer.py     # HTML generation
│   ├── research.py     # Web/RSS/arXiv/PubMed fetching
│   └── trackers.py     # CostTracker, CoverageTracker
├── digest.py           # Entry point
├── config.example.yaml
└── requirements.txt
```

---

## Customizing for your sector

The default sections (`IVD & Lab Automation`, `Markets LATAM & APAC`, `Biotech & Digital Health`, `Scientific Literature`) can be changed by editing `_section_colors()` in `digest/renderer.py` and updating the `section` values in `config.yaml`.

The scoring criteria in `score_and_write()` in `digest/agents.py` are written in plain English inside the prompt — edit them to match what "market-moving" means in your industry.

---

## Requirements

- Python 3.10+
- Anthropic API key ([get one here](https://console.anthropic.com))
- Optional: [newspaper4k](https://github.com/codelucas/newspaper) for better article extraction

---

## License

MIT
