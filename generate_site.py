"""
generate_site.py
------------------
STEP 2 of the aggregator pipeline: turns the `site_listings` table (built
by prepare_site_data.py / site_data.py) into a real, browsable website -
plain HTML files, no server needed.

What it builds, inside a new "site/" folder:
    site/index.html              - homepage: listings grouped by town,
                                    cheapest first
    site/rent/<slug>.html        - one page per listing, with your own
                                    computed insight + a "View on Jiji"
                                    button (we link out for photos/full
                                    details rather than copying them -
                                    see the note in README.md about why)
    site/sitemap.xml             - tells Google every page that exists
    site/robots.txt              - standard "you're allowed to crawl me"
                                    file search engines check first
    site/style.css               - basic clean styling

Run this AFTER prepare_site_data.py / site_data.py has filled the
`site_listings` table. Safe to re-run any time - it fully regenerates
the site folder from whatever is currently in the database.

Usage:
    python generate_site.py
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

DB_PATH = "listings.db"
SITE_DIR = Path("site")
RENT_DIR = SITE_DIR / "rent"

# Set this once you have a real AdSense account approved. Leave as None
# until then - the ad slots simply won't render anything without it.
ADSENSE_CLIENT_ID: str | None = None  # e.g. "ca-pub-1234567890123456"

# Change this once you actually register a domain / know your Render URL.
# It's used to build the sitemap and canonical link tags, both of which
# Google uses to understand your site.
SITE_BASE_URL = "https://your-site-url-goes-here.example.com"

AGENT_KEYWORDS = ["realtors", "properties", "agency", "homes", "ltd", "estate", "management"]


def looks_like_agent(seller_name: str | None) -> bool:
    """Rough heuristic: does the seller name look like a business, not a person?"""
    if not seller_name:
        return False
    lowered = seller_name.lower()
    return any(keyword in lowered for keyword in AGENT_KEYWORDS)


def esc(text) -> str:
    """Minimal HTML-escaping so listing text can't break the page markup."""
    if text is None:
        return ""
    text = str(text)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def adsense_slot_html() -> str:
    """Returns the AdSense snippet if configured, otherwise an empty string."""
    if not ADSENSE_CLIENT_ID:
        return "<!-- AdSense not configured yet: set ADSENSE_CLIENT_ID in generate_site.py -->"
    return f"""
    <script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={ADSENSE_CLIENT_ID}" crossorigin="anonymous"></script>
    <ins class="adsbygoogle" style="display:block" data-ad-client="{ADSENSE_CLIENT_ID}" data-ad-slot="0000000000" data-ad-format="auto" data-full-width-responsive="true"></ins>
    <script>(adsbygoogle = window.adsbygoogle || []).push({{}});</script>
    """


PAGE_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<meta name="description" content="{description}">
<link rel="canonical" href="{canonical_url}">
<link rel="stylesheet" href="{css_path}">
</head>
<body>
<header class="site-header">
  <a href="{home_path}" class="logo">Juja &amp; Ruiru Rentals</a>
</header>
"""

PAGE_FOOT = """
<footer class="site-footer">
  <p>Listings are gathered from public sources and linked back to their original post.
     We are not the landlord or agent for any listing shown here.</p>
</footer>
</body>
</html>
"""


def render_listing_page(row: sqlite3.Row, depth: str = "../") -> str:
    price_display = f"KSh {int(row['price_numeric']):,}" if row["price_numeric"] else "Contact for price"
    insight_html = f'<p class="insight">{esc(row["insight"])}</p>' if row["insight"] else ""
    agent_badge = (
        '<span class="badge badge-agent">Agent / Agency</span>'
        if looks_like_agent(row["seller_name"])
        else '<span class="badge badge-direct">Direct Contact</span>'
    )

    body = f"""
<main class="listing-page">
  <h1>{esc(row['clean_title'])}</h1>
  <p class="price">{esc(price_display)} / month</p>
  <p class="location">{esc(row['location'])}</p>
  {insight_html}
  <p>{agent_badge}</p>

  <div class="cta-box">
    <p>Photos, full description, and direct contact are on the original listing:</p>
    <a class="cta-button" href="{esc(row['url'])}" target="_blank" rel="nofollow noopener">
      View Full Listing &amp; Photos &rarr;
    </a>
  </div>

  {adsense_slot_html()}
</main>
"""
    head = PAGE_HEAD.format(
        title=esc(f"{row['clean_title']} | Juja & Ruiru Rentals"),
        description=esc(row["insight"] or f"{row['clean_title']} - {price_display}/month"),
        canonical_url=f"{SITE_BASE_URL}/rent/{row['slug']}.html",
        css_path=f"{depth}style.css",
        home_path=f"{depth}index.html",
    )
    return head + body + PAGE_FOOT


def render_index_page(rows: list[sqlite3.Row]) -> str:
    by_town: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        town = row["search_town"] or "Other"
        by_town.setdefault(town, []).append(row)

    sections = []
    for town, town_rows in sorted(by_town.items()):
        town_rows_sorted = sorted(
            town_rows, key=lambda r: (r["price_numeric"] is None, r["price_numeric"])
        )
        cards = []
        for row in town_rows_sorted:
            price_display = f"KSh {int(row['price_numeric']):,}" if row["price_numeric"] else "Contact for price"
            insight_html = f'<p class="insight">{esc(row["insight"])}</p>' if row["insight"] else ""
            cards.append(f"""
      <a class="listing-card" href="rent/{row['slug']}.html">
        <h3>{esc(row['clean_title'])}</h3>
        <p class="price">{esc(price_display)} / month</p>
        {insight_html}
      </a>""")
        sections.append(f"""
  <section class="town-section">
    <h2>{esc(town)}</h2>
    <div class="listing-grid">
      {''.join(cards)}
    </div>
  </section>""")

    body = f"""
<main class="home-page">
  <h1>Affordable Rentals in Juja &amp; Ruiru</h1>
  <p class="subtitle">Updated automatically. Cheapest listings first in every area.</p>
  {adsense_slot_html()}
  {''.join(sections)}
</main>
"""
    head = PAGE_HEAD.format(
        title="Affordable Rentals in Juja & Ruiru - Updated Daily",
        description="Browse the cheapest bedsitters and apartments in Juja and Ruiru, updated automatically.",
        canonical_url=f"{SITE_BASE_URL}/index.html",
        css_path="style.css",
        home_path="index.html",
    )
    return head + body + PAGE_FOOT


STYLE_CSS = """
:root { --accent: #00B53F; --text: #1a1a1a; --muted: #667; --bg: #fafafa; }
* { box-sizing: border-box; }
body { font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 0; background: var(--bg); color: var(--text); }
.site-header { padding: 16px 24px; background: white; border-bottom: 1px solid #eee; }
.site-header .logo { font-weight: 700; font-size: 1.2rem; color: var(--accent); text-decoration: none; }
main { max-width: 900px; margin: 0 auto; padding: 24px; }
h1 { font-size: 1.6rem; }
.subtitle { color: var(--muted); margin-top: -8px; }
.town-section { margin-top: 32px; }
.listing-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 16px; margin-top: 12px; }
.listing-card { display: block; background: white; border: 1px solid #eee; border-radius: 10px; padding: 16px; text-decoration: none; color: var(--text); transition: box-shadow .15s; }
.listing-card:hover { box-shadow: 0 2px 10px rgba(0,0,0,0.08); }
.listing-card h3 { margin: 0 0 8px 0; font-size: 1rem; }
.price { color: var(--accent); font-weight: 700; }
.insight { font-size: 0.85rem; color: var(--muted); }
.location { color: var(--muted); }
.badge { display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: 0.75rem; font-weight: 600; }
.badge-direct { background: #e6f9ec; color: #00733a; }
.badge-agent { background: #eef2f7; color: #46586b; }
.cta-box { margin-top: 24px; padding: 20px; background: white; border: 1px solid #eee; border-radius: 10px; }
.cta-button { display: inline-block; margin-top: 8px; padding: 12px 20px; background: var(--accent); color: white; border-radius: 8px; text-decoration: none; font-weight: 600; }
.site-footer { max-width: 900px; margin: 32px auto; padding: 0 24px 24px; color: var(--muted); font-size: 0.85rem; }
"""


def build_sitemap(rows: list[sqlite3.Row]) -> str:
    urls = [f"{SITE_BASE_URL}/index.html"]
    urls += [f"{SITE_BASE_URL}/rent/{row['slug']}.html" for row in rows]
    entries = "\n".join(f"  <url><loc>{esc(u)}</loc></url>" for u in urls)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{entries}\n</urlset>\n'


def build_robots_txt() -> str:
    return f"User-agent: *\nAllow: /\nSitemap: {SITE_BASE_URL}/sitemap.xml\n"


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM site_listings").fetchall()
    conn.close()

    if not rows:
        print("No rows in site_listings. Run prepare_site_data.py / site_data.py first.")
        return

    RENT_DIR.mkdir(parents=True, exist_ok=True)

    for row in rows:
        page_html = render_listing_page(row)
        (RENT_DIR / f"{row['slug']}.html").write_text(page_html, encoding="utf-8")

    (SITE_DIR / "index.html").write_text(render_index_page(rows), encoding="utf-8")
    (SITE_DIR / "style.css").write_text(STYLE_CSS, encoding="utf-8")
    (SITE_DIR / "sitemap.xml").write_text(build_sitemap(rows), encoding="utf-8")
    (SITE_DIR / "robots.txt").write_text(build_robots_txt(), encoding="utf-8")

    print(f"Built {len(rows)} listing pages + homepage in ./{SITE_DIR}/")
    print(f"Open {SITE_DIR / 'index.html'} in your browser to preview it.")
    if ADSENSE_CLIENT_ID is None:
        print("Note: ADSENSE_CLIENT_ID is not set yet, so ad slots are placeholders for now.")


if __name__ == "__main__":
    main()