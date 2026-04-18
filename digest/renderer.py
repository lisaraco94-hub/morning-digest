import re
from datetime import datetime
from pathlib import Path

_DEFAULT_COLORS: dict[str, str] = {
    "primary":        "#00B3E3",
    "dark":           "#0074A2",
    "darker":         "#004A66",
    "accent":         "#EA8651",
    "text":           "#333333",
    "text_secondary": "#686869",
    "light_blue":     "#B1DEF1",
    "light_blue2":    "#EAF6FC",
    "green":          "#1A7A4A",
}

_SCORE_CONFIG: dict[int, dict] = {
    3: {"label": "HIGH PRIORITY", "dot": "●", "show": True},
    2: {"label": "",              "dot": "",  "show": False},
    1: {"label": "",              "dot": "",  "show": False},
}

_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday",
             "Friday", "Saturday", "Sunday"]
_MONTHS   = ["", "January", "February", "March", "April", "May", "June",
             "July", "August", "September", "October", "November", "December"]


# ── Color helpers ─────────────────────────────────────────────────────────────

def _resolve_colors(cfg: dict) -> dict[str, str]:
    c = dict(_DEFAULT_COLORS)
    b = cfg.get("branding", {})
    for cfg_key, color_key in {
        "primary_color": "primary",
        "dark_color":    "dark",
        "darker_color":  "darker",
        "accent_color":  "accent",
        "success_color": "green",
    }.items():
        if b.get(cfg_key):
            c[color_key] = b[cfg_key]
    return c


def _section_colors(title: str, colors: dict) -> tuple[str, str]:
    """Return (foreground_color, background_light) for a section title."""
    mapping = {
        "IVD & Lab Automation":     (colors["dark"],    colors["light_blue2"]),
        "Markets LATAM & APAC":     (colors["darker"],  "#EBF4F8"),
        "Biotech & Digital Health": (colors["primary"], colors["light_blue2"]),
        "Scientific Literature":    (colors["accent"],  "#FDF3ED"),
        "Ideas to keep":            ("#9A7A1A",         "#FDF8E8"),
    }
    key = next((k for k in mapping if k in title), "")
    return mapping.get(key, (colors["dark"], colors["light_blue2"]))


# ── CSS ───────────────────────────────────────────────────────────────────────

def _build_css(colors: dict) -> str:
    c = colors
    return f""":root{{
  --blue:{c['primary']};--dkblue:{c['dark']};--dkblue2:{c['darker']};
  --orange:{c['accent']};--dark:{c['text']};--lgrey:{c['text_secondary']};
  --lblue:{c['light_blue']};--lblue2:{c['light_blue2']};--green:{c['green']};
  --bg:#F4F6F8;--white:#FFFFFF;--border:#DDE3EA;
  --radius:8px;--shadow:0 2px 12px rgba(0,0,0,.08);
  --shadow-hover:0 6px 24px rgba(0,0,0,.14);
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Montserrat',sans-serif;background:var(--bg);color:var(--dark);line-height:1.6}}
.masthead{{background:var(--dkblue2);padding:0}}
.masthead-inner{{display:flex;justify-content:space-between;align-items:stretch;max-width:1320px;margin:0 auto;padding:24px 40px 0}}
.logo-area{{display:flex;flex-direction:column;justify-content:flex-end;padding-bottom:20px}}
.logo-eyebrow{{font-size:.7rem;letter-spacing:3px;color:var(--blue);text-transform:uppercase;font-weight:600;margin-bottom:4px}}
.logo-title{{font-size:clamp(2rem,4vw,3rem);font-weight:700;color:white;line-height:1.1;letter-spacing:-.5px}}
.logo-title span{{color:var(--blue)}}
.masthead-right{{display:flex;flex-direction:column;align-items:flex-end;justify-content:flex-end;padding-bottom:20px;gap:6px}}
.masthead-date{{color:white;font-size:1rem;font-weight:600}}
.masthead-sub{{color:rgba(255,255,255,.6);font-size:.75rem}}
.masthead-bar{{background:linear-gradient(90deg,var(--blue),var(--dkblue),var(--orange));height:4px}}
.stats-bar{{background:var(--white);border-bottom:1px solid var(--border);padding:10px 40px;display:flex;gap:24px;align-items:center;font-size:.78rem;color:var(--lgrey)}}
.stats-bar strong{{color:var(--dark)}}
.stat-pill{{background:var(--lblue2);border:1px solid var(--lblue);border-radius:12px;padding:3px 10px;font-size:.72rem;font-weight:600;color:var(--dkblue)}}
.legend-sep{{color:var(--border);margin:0 4px}}
.legend-item{{display:flex;align-items:center;gap:5px;font-size:.75rem;color:var(--lgrey)}}
.tldr-section{{background:var(--dkblue2);border-bottom:1px solid rgba(255,255,255,.08);padding:20px 40px}}
.tldr-header{{display:flex;align-items:center;gap:10px;margin-bottom:12px}}
.tldr-tag{{background:var(--orange);color:white;font-size:.65rem;font-weight:700;letter-spacing:2px;text-transform:uppercase;padding:3px 10px;border-radius:3px}}
.tldr-section ul{{list-style:none;display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:6px 32px}}
.tldr-section li{{color:rgba(255,255,255,.88);font-size:.85rem;padding-left:12px;position:relative}}
.tldr-section li::before{{content:"▸";color:var(--blue);position:absolute;left:0;font-weight:700}}
.tldr-link{{color:white;text-decoration:none;border-bottom:1px solid rgba(255,255,255,.3)}}
.tldr-link:hover{{border-bottom-color:var(--blue);color:var(--blue)}}
.container{{max-width:1320px;margin:0 auto;padding:36px 40px}}
.digest-section{{margin-bottom:52px}}
.section-header{{display:flex;align-items:center;gap:12px;border-top:3px solid;padding-top:14px;margin-bottom:20px}}
.section-title{{font-family:'Montserrat',sans-serif;font-size:1.15rem;font-weight:700;flex:1}}
.section-badges{{display:flex;gap:8px;align-items:center}}
.section-count{{color:white;font-size:.68rem;font-weight:700;padding:3px 10px;border-radius:12px}}
.feat-badge{{font-size:.68rem;font-weight:700;padding:3px 10px;border-radius:12px}}
.featured-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:20px;margin-bottom:20px}}
.featured-card{{box-shadow:var(--shadow-hover)!important}}
.articles-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px}}
.article-card{{background:var(--white);border-radius:var(--radius);box-shadow:var(--shadow);padding:20px;display:flex;flex-direction:column;gap:10px;transition:box-shadow .2s,transform .2s;position:relative}}
.article-card:hover{{box-shadow:var(--shadow-hover);transform:translateY(-2px)}}
.card-header{{display:flex;align-items:center;gap:8px}}
.score-badge{{font-size:.67rem;font-weight:700;letter-spacing:.5px;padding:3px 9px;border-radius:10px;font-family:'Montserrat',sans-serif}}
.card-title{{font-size:.97rem;font-weight:700;line-height:1.4;font-family:'Montserrat',sans-serif}}
.card-meta{{font-size:.73rem;color:var(--lgrey);font-family:'Montserrat',sans-serif}}
.card-body{{font-family:'Merriweather',serif;font-size:.85rem;line-height:1.65;color:#444;flex:1}}
.card-why{{padding:10px 14px;border-radius:5px;font-size:.82rem;font-family:'Montserrat',sans-serif}}
.why-label{{display:block;font-size:.65rem;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--lgrey);margin-bottom:4px}}
.read-link{{font-size:.78rem;font-weight:700;text-decoration:none;font-family:'Montserrat',sans-serif;margin-top:4px;display:inline-flex;align-items:center;gap:4px}}
.read-link:hover{{text-decoration:underline}}
.ideas-list{{list-style:none;padding:16px 20px;border-radius:var(--radius);display:flex;flex-direction:column;gap:10px;font-size:.9rem;font-family:'Merriweather',serif}}
.ideas-list li{{padding-left:16px;position:relative}}
.ideas-list li::before{{content:"→";font-weight:700;position:absolute;left:0;font-family:'Montserrat',sans-serif}}
.footer{{text-align:center;padding:28px 40px;font-size:.73rem;color:var(--lgrey);border-top:1px solid var(--border);margin-top:20px;font-family:'Montserrat',sans-serif}}
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
#import-bar{{position:fixed;bottom:0;left:0;right:0;background:var(--dkblue2);padding:14px 40px;display:flex;align-items:center;gap:16px;box-shadow:0 -4px 20px rgba(0,0,0,.25);transform:translateY(100%);transition:transform .25s ease;z-index:999;font-family:'Montserrat',sans-serif}}
#import-bar.visible{{transform:translateY(0)}}
#import-count{{color:white;font-size:.9rem;font-weight:600;flex:1}}
#import-count span{{color:var(--blue)}}
.import-btn{{background:var(--blue);color:white;border:none;padding:10px 22px;border-radius:6px;font-family:'Montserrat',sans-serif;font-size:.85rem;font-weight:700;cursor:pointer;letter-spacing:.3px;transition:background .15s}}
.import-btn:hover{{background:var(--dkblue)}}
.clear-btn{{background:transparent;color:rgba(255,255,255,.6);border:1px solid rgba(255,255,255,.2);padding:10px 16px;border-radius:6px;font-family:'Montserrat',sans-serif;font-size:.82rem;cursor:pointer}}
.clear-btn:hover{{color:white;border-color:rgba(255,255,255,.5)}}
@media print{{
  .masthead,.tldr-section{{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
  .article-card{{break-inside:avoid}}
  #import-bar,.card-select{{display:none}}
}}"""


# ── Markdown parsing ──────────────────────────────────────────────────────────

def _sanitize_url(url: str) -> str:
    """Block javascript:, data:, and vbscript: URIs."""
    if not url:
        return ""
    if url.strip().lower().startswith(("javascript:", "data:", "vbscript:")):
        return ""
    return url


def _parse_tldr(md_content: str) -> list[dict]:
    items = []
    m = re.search(r"## TL;DR\n(.*?)(?:\n---|$)", md_content, re.DOTALL)
    if not m:
        return items
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        text   = line[2:]
        linked = re.search(r"\*\*\[([^\]]+)\]\(([^)]+)\)\*\*\s*[—\-]\s*(.*)", text)
        if linked:
            link_title, url, desc = linked.group(1), linked.group(2), linked.group(3)
            url = _sanitize_url(url)
            html_item = (
                f'<a href="{url}" target="_blank" class="tldr-link">'
                f"<strong>{link_title}</strong></a>"
                + (f" — {desc}" if desc else "")
            )
        else:
            html_item = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
            url = ""
        items.append({"html": html_item, "url": url})
    return items


def _parse_articles_md(section_body: str) -> list[dict]:
    articles = []
    for block in re.split(r"\n---+\n", section_body):
        block = block.strip()
        if not block:
            continue
        tm = re.match(r"###\s+(.+)", block)
        if not tm:
            continue
        title = re.sub(r"\s*\{#[^}]+\}", "", tm.group(1)).strip()

        meta_m = re.search(r"\*([^*\n]+)\*\s*(?:`score:(\d)`)?", block)
        meta   = meta_m.group(1).strip() if meta_m else ""
        score  = int(meta_m.group(2)) if (meta_m and meta_m.group(2)) else 1

        why_m = re.search(r"\*\*Why it matters:\*\*\s*(.+?)(?:\n|$)", block)
        why   = why_m.group(1).strip() if why_m else ""

        url_m = re.search(r"\[Read\s*[→>][^\]]*\]\(([^)]+)\)", block)
        url   = _sanitize_url(url_m.group(1)) if url_m else ""

        bs   = block.find("\n", block.find("*") + 1) if "*" in block else 0
        be   = block.find("**Why it matters") if "**Why it matters" in block else len(block)
        body = re.sub(r"^\[Read.*$", "", block[bs:be],
                      flags=re.MULTILINE).strip() if bs > 0 else ""

        articles.append({
            "title":          title,
            "meta":           meta,
            "body":           body,
            "why_it_matters": why,
            "url":            url,
            "score":          score,
        })
    return articles


def _parse_sections(md_content: str) -> list[dict]:
    sections = []
    sec_pat  = re.compile(
        r"## ([🔬🌎🧬📚📌][^\n]+)\n(.*?)(?=\n## (?:[🔬🌎🧬📚📌]|TL;DR)|\Z)",
        re.DOTALL,
    )
    for m in sec_pat.finditer(md_content):
        title = m.group(1).strip()
        body  = m.group(2).strip()
        if "Ideas to keep" in title:
            ideas = [
                line[2:].strip()
                for line in body.splitlines()
                if line.strip().startswith("- ") and len(line) > 4
            ]
            sections.append({"title": title, "ideas": ideas})
        else:
            arts = _parse_articles_md(body)
            if arts:
                sections.append({"title": title, "articles": arts})
    return sections


# ── HTML building blocks ──────────────────────────────────────────────────────

def _attr(s: str) -> str:
    """Escape a string for use inside an HTML attribute value."""
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("'", "&#39;")


def _render_card(
    art: dict, colors: dict, col: str, light: str, section_name: str = "",
    featured: bool = False,
) -> str:
    sc    = _SCORE_CONFIG.get(art.get("score", 2), _SCORE_CONFIG[2])
    badge = (
        f'<span class="score-badge" style="background:{colors["green"]};color:white">'
        f'{sc["dot"]} {sc["label"]}</span>'
        if sc["show"] else ""
    )
    body_esc = art["body"].replace("<", "&lt;").replace(">", "&gt;")
    why_esc  = art["why_it_matters"].replace("<", "&lt;").replace(">", "&gt;")
    link_html = (
        f'<a class="read-link" href="{art["url"]}" target="_blank" style="color:{col}">'
        f"Read article →</a>"
        if art["url"] else ""
    )
    feat_cls    = " featured-card" if featured else ""
    feat_style  = (
        f"border-top:4px solid {col};background:white;" if featured
        else f"border-top:2px solid {col};"
    )
    header_html = f'<div class="card-header">{badge}</div>' if badge else ""
    return (
        f'<div class="article-card{feat_cls}" style="{feat_style}"\n'
        f'  data-title="{_attr(art["title"])}" data-meta="{_attr(art["meta"])}"\n'
        f'  data-body="{_attr(art["body"])}" data-why="{_attr(art["why_it_matters"])}"\n'
        f'  data-url="{_sanitize_url(art.get("url", ""))}" data-section="{_attr(section_name)}">\n'
        f'  <label class="card-select" title="Select to import to Obsidian">\n'
        f'    <input type="checkbox" class="article-checkbox" onchange="updateBar()">\n'
        f'  </label>\n'
        f'  {header_html}\n'
        f'  <h3 class="card-title">{art["title"]}</h3>\n'
        f'  <div class="card-meta">{art["meta"]}</div>\n'
        f'  <p class="card-body">{body_esc}</p>\n'
        f'  <div class="card-why" style="background:{light};border-left:3px solid {col}">\n'
        f'    <span class="why-label">Why it matters</span>\n'
        f'    {why_esc}\n'
        f'  </div>\n'
        f'  {link_html}\n'
        f'</div>'
    )


def _render_ideas_section(title: str, ideas: list[str], col: str, light: str) -> str:
    items_html = "".join(f"<li>{i}</li>" for i in ideas)
    return (
        f'<section class="digest-section">\n'
        f'  <div class="section-header" style="border-color:{col}">\n'
        f'    <h2 class="section-title" style="color:{col}">{title}</h2>\n'
        f'  </div>\n'
        f'  <ul class="ideas-list" style="background:{light};border-left:4px solid {col}">'
        f'{items_html}</ul>\n'
        f'</section>'
    )


def _render_articles_section(
    title: str, articles: list[dict], colors: dict, col: str, light: str
) -> str:
    featured = [a for a in articles if a.get("score") == 3]
    regular  = [a for a in articles if a.get("score") != 3]

    featured_html = ""
    if featured:
        cards = "\n".join(
            _render_card(a, colors, col, light, section_name=title, featured=True)
            for a in featured
        )
        featured_html = f'<div class="featured-grid">{cards}</div>'

    regular_html = ""
    if regular:
        cards = "\n".join(
            _render_card(a, colors, col, light, section_name=title, featured=False)
            for a in regular
        )
        regular_html = f'<div class="articles-grid">{cards}</div>'

    count_badge = (
        f'<span class="section-count" style="background:{col}">'
        f'{len(articles)} articles</span>'
    )
    feat_badge = (
        f'<span class="feat-badge" style="background:{colors["green"]};color:white">'
        f'● {len(featured)} high priority</span>'
        if featured else ""
    )
    return (
        f'<section class="digest-section">\n'
        f'  <div class="section-header" style="border-color:{col}">\n'
        f'    <h2 class="section-title" style="color:{col}">{title}</h2>\n'
        f'    <div class="section-badges">{count_badge}{feat_badge}</div>\n'
        f'  </div>\n'
        f'  {featured_html}\n'
        f'  {regular_html}\n'
        f'</section>'
    )


def _render_section(sec: dict, colors: dict) -> str:
    title      = sec["title"]
    col, light = _section_colors(title, colors)
    if "ideas" in sec:
        return _render_ideas_section(title, sec["ideas"], col, light)
    return _render_articles_section(title, sec.get("articles", []), colors, col, light)


def _obsidian_js(vault: str, folder: str, today: str) -> str:
    vault_name = Path(vault).name if vault else ""
    return f"""const VAULT  = "{vault_name}";
const FOLDER = "{folder}";
const DATE   = "{today}";

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
  const selected = [];
  document.querySelectorAll('.article-card').forEach(card => {{
    if (card.querySelector('.article-checkbox')?.checked) {{
      selected.push({{
        title:   card.dataset.title   || '',
        meta:    card.dataset.meta    || '',
        body:    card.dataset.body    || '',
        why:     card.dataset.why     || '',
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
    if (a.why) md += `**Why it matters:** ${{a.why}}\\n\\n`;
    if (a.url)  md += `[Read →](${{a.url}})\\n\\n`;
    md += `---\\n\\n`;
  }});

  const file = FOLDER + "/" + DATE + "-saved";
  const uri  = "obsidian://new?vault=" + encodeURIComponent(VAULT)
             + "&file="    + encodeURIComponent(file)
             + "&content=" + encodeURIComponent(md);
  window.location.href = uri;
}}"""


# ── Public entry point ────────────────────────────────────────────────────────

def generate_html(
    md_content:      str,
    scored_articles: list[dict],
    today:           str,
    html_path:       str,
    cfg:             dict,
) -> str:
    colors = _resolve_colors(cfg)

    user_name    = cfg.get("user_name", "User")
    organization = cfg.get("organization", "")
    vault        = cfg.get("output", {}).get("obsidian_vault", "")
    folder       = cfg.get("output", {}).get("obsidian_import_folder", "Saved")

    tldr_items = _parse_tldr(md_content)
    sections   = _parse_sections(md_content)

    total_arts = sum(len(s.get("articles", [])) for s in sections if "articles" in s)
    org_label  = f" · {organization}" if organization else ""

    try:
        dt       = datetime.strptime(today, "%Y-%m-%d")
        date_str = f"{_WEEKDAYS[dt.weekday()]} {dt.day} {_MONTHS[dt.month]} {dt.year}"
    except Exception:
        date_str = today

    sections_html = "\n".join(_render_section(s, colors) for s in sections)
    tldr_html     = "".join(f'<li>{item["html"]}</li>' for item in tldr_items)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Morning Digest — {user_name} — {today}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;700&family=Merriweather:wght@400;700&display=swap" rel="stylesheet">
<style>
{_build_css(colors)}
</style>
</head>
<body>

<header class="masthead">
  <div class="masthead-inner">
    <div class="logo-area">
      <div class="logo-eyebrow">Morning Briefing{(' · ' + organization) if organization else ''}</div>
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
  <span>📊 <strong>{total_arts}</strong> articles selected</span>
  <span class="stat-pill">{len([s for s in sections if 'articles' in s])} sections</span>
  <span class="legend-sep">|</span>
  <span class="legend-item">
    <span style="background:{colors['green']};color:white;padding:1px 6px;border-radius:8px;font-size:.7rem;font-weight:700">● HIGH PRIORITY</span>
    = market-moving story
  </span>
</div>

<div class="tldr-section">
  <div class="tldr-header">
    <span class="tldr-tag">⚡ In brief</span>
    <span style="color:rgba(255,255,255,.5);font-size:.8rem">Today's must-read stories</span>
  </div>
  <ul>{tldr_html}</ul>
</div>

<main class="container">
{sections_html}
</main>

<footer class="footer">
  Auto-generated · {today} · Morning Digest · {user_name}{org_label}
</footer>

<div id="import-bar">
  <div id="import-count">Selected: <span id="sel-count">0</span> articles</div>
  <button class="clear-btn" onclick="clearSelection()">✕ Clear</button>
  <button class="import-btn" onclick="importToObsidian()">↗ Import to Obsidian</button>
</div>

<script>
{_obsidian_js(vault, folder, today)}
</script>

</body>
</html>"""

    Path(html_path).parent.mkdir(parents=True, exist_ok=True)
    Path(html_path).write_text(html, encoding="utf-8")
    return html_path
