"""
config.py
---------
Central configuration for the Jiji.co.ke real estate scraper.

Keeping all tunables in one place makes the scraper easy to adapt when
Jiji changes its URL structure, or when you want to add new towns/categories
without touching scraping logic.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "listings.db"
LOG_PATH = BASE_DIR / "scraper.log"

# --------------------------------------------------------------------------
# Target categories / search URLs
# --------------------------------------------------------------------------
# Jiji.co.ke organizes real-estate-to-rent listings under
# /houses-apartments-for-rent with query params for text search.
# We target Bedsitters and 1-Bedroom units in Nairobi, Juja and Ruiru by
# combining the category URL with a free-text query per town, which is the
# most reliable way to scope results without relying on Jiji's internal
# (and frequently-changing) sub-category IDs.
BASE_URL = "https://jiji.co.ke"
CATEGORY_PATH = "/houses-apartments-for-rent"

TOWNS = ["Nairobi", "Juja", "Ruiru"]
UNIT_TYPES = ["Bedsitter", "1 Bedroom"]


@dataclass(frozen=True)
class SearchTarget:
    """A single (town, unit_type) search job."""

    town: str
    unit_type: str

    @property
    def query(self) -> str:
        return f"{self.unit_type} {self.town}"

    def url(self, page: int = 1) -> str:
        from urllib.parse import urlencode

        params = {"query": self.query}
        if page > 1:
            params["page"] = page
        return f"{BASE_URL}{CATEGORY_PATH}?{urlencode(params)}"

    @property
    def label(self) -> str:
        return f"{self.unit_type} - {self.town}"


def build_search_targets() -> list[SearchTarget]:
    return [SearchTarget(town=t, unit_type=u) for t in TOWNS for u in UNIT_TYPES]


# --------------------------------------------------------------------------
# Anti-blocking configuration
# --------------------------------------------------------------------------
USER_AGENTS = [
    # Chrome - Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Chrome - macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Firefox - Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
    "Gecko/20100101 Firefox/125.0",
    # Safari - macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_6) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    # Edge - Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    # Chrome - Android (mobile realism)
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
]

VIEWPORTS = [
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1920, "height": 1080},
    {"width": 1280, "height": 720},
]

LOCALES = ["en-KE", "en-US", "en-GB"]


def random_user_agent() -> str:
    return random.choice(USER_AGENTS)


def random_viewport() -> dict:
    return random.choice(VIEWPORTS)


def random_locale() -> str:
    return random.choice(LOCALES)


# --------------------------------------------------------------------------
# Timing / throttling (seconds). Randomized ranges rather than fixed sleeps
# to avoid a fingerprint-able, perfectly regular request cadence.
# --------------------------------------------------------------------------
DELAY_BETWEEN_PAGES = (3.0, 7.0)          # between listing search pages
DELAY_BETWEEN_DETAIL_PAGES = (2.0, 5.0)   # between individual listing pages
DELAY_BETWEEN_TARGETS = (5.0, 10.0)       # between different search targets
PAGE_LOAD_TIMEOUT_MS = 30_000
MAX_RETRIES_PER_PAGE = 3
RETRY_BACKOFF_BASE = 4.0  # seconds, exponential backoff multiplier

# How many search-result pages to walk per target before moving on.
MAX_PAGES_PER_TARGET = 3

# Whether to also open each listing's detail page to fetch the seller
# profile link (slower, one extra navigation per listing) or to try to
# extract everything from the search results grid only (faster).
VISIT_DETAIL_PAGE_FOR_SELLER_LINK = True

HEADLESS = True
