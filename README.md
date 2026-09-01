# Jiji.co.ke Real Estate Scraper

A modular Playwright-based scraper that collects rental listings
(Bedsitters and 1-Bedroom apartments in Nairobi, Juja, and Ruiru) from
Jiji.co.ke into a local SQLite database, with duplicate-safe upserts and
basic anti-blocking measures.

## Project layout

```
jiji_scraper/
├── config.py       # All tunables: towns, unit types, UA list, delays, timeouts
├── database.py     # SQLite schema + Listing dataclass + dedup upsert logic
├── scraper.py       # Playwright navigation, extraction, retry/backoff logic
├── main.py          # CLI entry point + logging setup
├── requirements.txt
└── README.md
```

## Setup

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium       # downloads the Chromium binary Playwright drives
```

## Running

```bash
# Default run: Bedsitter + 1 Bedroom, across Nairobi / Juja / Ruiru
python main.py

# Narrow the run
python main.py --towns Juja Ruiru --unit-types Bedsitter

# Limit pagination and skip the extra per-listing detail-page visit
# (faster, but seller_link/seller_name will be empty)
python main.py --max-pages 1 --no-detail-pages

# Watch it run in a visible browser window (debugging selectors)
python main.py --headed --verbose
```

Output:
- `listings.db` — SQLite database, table `listings`
- `scraper.log` — full run log (also echoed to stdout)

## Database schema

```sql
CREATE TABLE listings (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    url               TEXT NOT NULL UNIQUE,   -- dedup key
    title             TEXT,
    price             TEXT,
    location          TEXT,
    image_url         TEXT,
    seller_name       TEXT,
    seller_link       TEXT,
    search_town       TEXT,
    search_unit_type  TEXT,
    first_seen_at     TEXT,
    last_seen_at      TEXT
);
```

Re-running the scraper is safe: existing rows (matched by `url`) are
refreshed in place (`last_seen_at` bumped, fields updated) instead of
duplicated.

Query examples:

```bash
sqlite3 listings.db "SELECT title, price, location FROM listings WHERE search_town='Juja';"
sqlite3 listings.db "SELECT COUNT(*) FROM listings;"
```

## Anti-blocking measures implemented

- Random User-Agent, viewport, and locale per search target (`config.py`,
  `scraper._new_stealthy_context`).
- `navigator.webdriver` and a few related fingerprint tells patched via
  `add_init_script`.
- Randomized (not fixed) delays between search pages, detail pages, and
  targets (`config.DELAY_BETWEEN_*`).
- Exponential backoff with jitter on navigation failure/timeout
  (`scraper._goto_with_retries`).
- Light scroll simulation before reading each results grid, to trigger the
  same lazy-loading a human visit would.
- Headless by default, with a `--headed` escape hatch for debugging.

These reduce the chance of being blocked but do not guarantee it — Jiji
(like any site) may still rate-limit or challenge automated traffic, and
its markup will change over time.

## Selector resilience

Jiji's front-end class names change periodically. Every field extractor in
`scraper.py` (`_extract_card_fields`, `_extract_seller_info`) tries several
plausible CSS strategies in priority order and simply returns `None` for a
field it can't find, rather than raising — one broken selector never takes
down the whole run. If Jiji reworks its markup, re-point the selector
strings in these two functions (search for `[class*=...]` patterns).

## Legal / operational note

Scraping Jiji.co.ke is subject to its Terms of Service and robots.txt.
Keep request rates low (the defaults here are deliberately conservative),
identify a reasonable use case (e.g. personal market research), and avoid
republishing scraped data at scale without checking the site's terms.
