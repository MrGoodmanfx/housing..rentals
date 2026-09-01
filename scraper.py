"""
scraper.py
----------
Playwright-based scraper for Jiji.co.ke real estate listings.

Design notes
============
* Runs headless Chromium with a fresh, randomized browser "identity"
  (User-Agent, viewport, locale) per search target to reduce fingerprint
  consistency across requests.
* Uses randomized delays (not fixed `sleep(n)`) between navigations to
  avoid a robotic, perfectly-periodic request cadence.
* Card and detail-page selectors are wrapped in small helper functions that
  try several plausible CSS strategies in order, since marketplace sites
  frequently tweak their markup/class names. Each helper degrades to
  `None`/empty rather than raising, so a layout change on one field never
  kills the whole scrape.
* Every listing is written to SQLite as it is scraped (not batched at the
  end), so a crash mid-run doesn't lose already-collected data.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

import config
from database import Listing, ListingsDatabase

logger = logging.getLogger("jiji_scraper.scraper")

# --------------------------------------------------------------------------
# Confirmed against a live debug capture on 2026-08-31: the actual listing
# "card" on jiji.co.ke search-results pages IS the anchor tag itself
# (<a class="b-list-advert-base ... qa-advert-list-item" href="...">),
# not a wrapping container. The old `[class*='b-list-advert-base']` guess
# over-matched every nested div sharing that class prefix (BEM naming),
# which is why 300-800 "candidate cards" were found per page but zero
# usable listings were extracted. `qa-` prefixed classes look like Jiji's
# own QA/test hooks, so they're the more stable thing to key off going
# forward. The href-suffix fallback covers the case where that class is
# ever renamed.
CARD_SELECTOR = "a.qa-advert-list-item, a[target='_blank'][href$='.html']"

# The free-text `query=` search on jiji.co.ke returns both for-rent and
# for-sale listings regardless of the /houses-apartments-for-rent path, so
# we filter client-side by URL slug.
REQUIRE_RENT_ONLY = True

_PRICE_RE = re.compile(r"K[Ss][Hh]\.?\s?[\d,]+(?:\s?/\s?\w+)?", re.IGNORECASE)


@dataclass
class ScrapeStats:
    targets_processed: int = 0
    pages_visited: int = 0
    listings_found: int = 0
    listings_new: int = 0
    listings_updated: int = 0
    errors: int = 0


async def _random_delay(bounds: tuple[float, float], reason: str = "") -> None:
    """Sleep for a random duration within `bounds`, logging why."""
    delay = random.uniform(*bounds)
    logger.debug("Sleeping %.2fs (%s)", delay, reason or "throttle")
    await asyncio.sleep(delay)


async def _new_stealthy_context(browser: Browser) -> BrowserContext:
    """
    Create a browser context with a randomized fingerprint and a few basic
    stealth tweaks (navigator.webdriver hidden, plausible headers).
    """
    ua = config.random_user_agent()
    viewport = config.random_viewport()
    locale = config.random_locale()

    context = await browser.new_context(
        user_agent=ua,
        viewport=viewport,
        locale=locale,
        timezone_id="Africa/Nairobi",
        extra_http_headers={
            "Accept-Language": f"{locale},en;q=0.9",
        },
    )

    # Hide the most obvious automation fingerprint. This is a best-effort
    # measure, not a guarantee of bypassing bot detection.
    await context.add_init_script(
        """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        window.chrome = window.chrome || { runtime: {} };
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        """
    )

    logger.debug("New context created (UA=%s, viewport=%s, locale=%s)", ua, viewport, locale)
    return context


async def _goto_with_retries(page: Page, url: str, max_retries: int = config.MAX_RETRIES_PER_PAGE) -> bool:
    """Navigate to `url`, retrying with exponential backoff on failure."""
    for attempt in range(1, max_retries + 1):
        try:
            await page.goto(url, timeout=config.PAGE_LOAD_TIMEOUT_MS, wait_until="domcontentloaded")
            return True
        except PlaywrightTimeoutError:
            logger.warning("Timeout loading %s (attempt %d/%d)", url, attempt, max_retries)
        except Exception as exc:  # noqa: BLE001 - log and retry any nav failure
            logger.warning("Error loading %s (attempt %d/%d): %s", url, attempt, max_retries, exc)

        if attempt < max_retries:
            backoff = config.RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
            jitter = random.uniform(0, backoff * 0.3)
            wait_for = backoff + jitter
            logger.info("Retrying %s in %.1fs", url, wait_for)
            await asyncio.sleep(wait_for)

    logger.error("Giving up on %s after %d attempts", url, max_retries)
    return False


async def _safe_text(locator) -> Optional[str]:
    """Return stripped inner_text() of the first match, or None."""
    try:
        if await locator.count() == 0:
            return None
        text = await locator.first.inner_text()
        text = text.strip()
        return text or None
    except Exception:  # noqa: BLE001
        return None


async def _safe_attr(locator, attr: str) -> Optional[str]:
    """Return an attribute of the first match, or None."""
    try:
        if await locator.count() == 0:
            return None
        value = await locator.first.get_attribute(attr)
        return value.strip() if value else None
    except Exception:  # noqa: BLE001
        return None


def normalize_listing_url(url: str) -> str:
    """
    Strip search-session tracking query params (page, pos, cur_pos,
    ads_per_page, ads_count, lid, indexPosition, ...) from a Jiji listing
    URL, keeping only scheme+host+path. These params change on every
    search/page visit even for the exact same listing, so leaving them in
    would break URL-based deduplication (every re-scrape would look "new").
    """
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _is_rent_listing(url: str) -> bool:
    """
    Jiji's free-text `query=` search returns both for-rent and for-sale
    results regardless of category path, so filter on the URL slug.
    """
    if not REQUIRE_RENT_ONLY:
        return True
    lower = url.lower()
    if "for-sale" in lower:
        return False
    return True


def _derive_location_from_url(url: str) -> Optional[str]:
    """
    Fallback location extractor: Jiji listing URLs start with an
    area/town slug, e.g. /murera/houses-apartments-for-rent/...
    or /juja/houses-apartments-for-sale/... -> "Murera" / "Juja".
    """
    path = urlsplit(url).path
    segments = [s for s in path.split("/") if s]
    if not segments:
        return None
    slug = segments[0]
    if not slug or slug in {"houses-apartments-for-rent", "houses-apartments-for-sale"}:
        return None
    return slug.replace("-", " ").title()


async def _extract_card_fields(card, base_url: str) -> Optional[dict]:
    """
    Extract Title, Price, Location, Image, and listing URL from a single
    search-results card.

    IMPORTANT: on jiji.co.ke, the "card" matched by CARD_SELECTOR is the
    anchor (<a>) element itself, not a wrapping container -- so the href
    is read directly off `card`, not searched for among its descendants.
    """
    # --- Listing URL --------------------------------------------------
    try:
        href = await card.get_attribute("href")
    except Exception:  # noqa: BLE001
        href = None
    if not href:
        return None
    url = normalize_listing_url(urljoin(base_url, href))

    if not _is_rent_listing(url):
        return None

    # --- Image (also doubles as a reliable title source via alt text) -----
    img_locator = card.locator("img").first
    image_url = (
        await _safe_attr(img_locator, "src")
        or await _safe_attr(img_locator, "data-src")
        or await _safe_attr(img_locator, "data-original")
    )
    if image_url:
        image_url = urljoin(base_url, image_url)
    img_alt = await _safe_attr(img_locator, "alt")

    # --- Title --------------------------------------------------------
    title = (
        img_alt
        or await _safe_text(card.locator("[class*='qa-advert-title']"))
        or await _safe_text(card.locator("[class*='title']"))
    )
    if not title:
        return None

    # --- Price ----------------------------------------------------------
    price = (
        await _safe_text(card.locator("[class*='qa-advert-price']"))
        or await _safe_text(card.locator("[class*='price']"))
    )
    if not price:
        # Last resort: scan the card's full text for a "KSh 12,345"-style
        # pattern, since price styling/classes are the most likely thing
        # to change between Jiji front-end releases.
        full_text = await _safe_text(card)
        if full_text:
            match = _PRICE_RE.search(full_text)
            if match:
                price = match.group(0)

    # --- Location ---------------------------------------------------------
    location = (
        await _safe_text(card.locator("[class*='region']"))
        or await _safe_text(card.locator("[class*='location']"))
        or _derive_location_from_url(url)
    )

    return {
        "url": url,
        "title": title,
        "price": price,
        "location": location,
        "image_url": image_url,
    }


async def _extract_seller_info(page: Page) -> tuple[Optional[str], Optional[str]]:
    """
    From an open listing detail page, extract the seller's display name and
    profile link. Confirmed against live jiji.co.ke markup (2026-08-31):
    the seller block is a fixed structure -
        <a class="b-seller-block__avatar__wrapper" href="/sellerpage-...">
            <div class="b-seller-block__name">Seller Name</div>
        </a>
    The phone number itself is masked (e.g. "072XXXXXXX") behind a
    "Show contact" button that requires a click/login to reveal, so we
    deliberately do not attempt to trigger that - the profile link is the
    stable, always-visible "contact" surface we extract instead.
    """
    seller_name = await _safe_text(page.locator(".b-seller-block__name"))

    seller_link = await _safe_attr(
        page.locator("a.b-seller-block__avatar__wrapper").first, "href"
    )
    if seller_link:
        seller_link = urljoin(config.BASE_URL, seller_link)

    return seller_name, seller_link


async def _scrape_listing_detail(
    context: BrowserContext, listing_url: str
) -> tuple[Optional[str], Optional[str]]:
    """
    Open a listing's own page and grab seller info.

    The seller block is rendered client-side by Jiji's Vue app, so a fixed
    short sleep after navigation is unreliable - on a slower render (or
    under headless load) the block simply isn't in the DOM yet when we
    read it, and we'd silently come back with (None, None) with no sign
    anything went wrong. Instead we explicitly wait for the seller name
    element to appear, and log a clear warning (with the URL) if it never
    does, so a real extraction failure is visible instead of indistin-
    guishable from "this listing just has no seller info".
    """
    page = await context.new_page()
    try:
        ok = await _goto_with_retries(page, listing_url)
        if not ok:
            return None, None

        try:
            await page.wait_for_selector(".b-seller-block__name", timeout=8_000)
        except PlaywrightTimeoutError:
            logger.warning(
                "Seller block never appeared on %s (page loaded but seller "
                "info didn't render in time - listing may be delisted, or "
                "the page layout may differ for this listing type)",
                listing_url,
            )
            return None, None

        seller_name, seller_link = await _extract_seller_info(page)
        if not seller_name and not seller_link:
            logger.warning("Seller block present but fields empty on %s", listing_url)
        return seller_name, seller_link
    finally:
        await page.close()


async def _scrape_search_page(
    context: BrowserContext,
    search_url: str,
    stats: ScrapeStats,
) -> list[dict]:
    """Scrape one search-results page and return a list of raw card dicts."""
    page = await context.new_page()
    results: list[dict] = []
    try:
        ok = await _goto_with_retries(page, search_url)
        if not ok:
            stats.errors += 1
            return results

        # Let the results grid render (Jiji's listings load client-side).
        try:
            await page.wait_for_selector(CARD_SELECTOR, timeout=10_000)
        except PlaywrightTimeoutError:
            logger.warning("No listing cards appeared for %s", search_url)

        # Light human-like scrolling to trigger lazy loading, before reading
        # the fully-settled DOM.
        for _ in range(3):
            await page.mouse.wheel(0, random.randint(600, 1200))
            await page.wait_for_timeout(random.randint(300, 800))

        cards = page.locator(CARD_SELECTOR)
        card_count = await cards.count()
        logger.info("Found %d candidate cards on %s", card_count, search_url)

        for i in range(card_count):
            card = cards.nth(i)
            data = await _extract_card_fields(card, config.BASE_URL)
            if data:
                results.append(data)

        stats.pages_visited += 1
        return results
    finally:
        await page.close()


async def scrape_target(
    browser: Browser,
    target: "config.SearchTarget",
    db: ListingsDatabase,
    stats: ScrapeStats,
) -> None:
    """Scrape all configured pages for a single (town, unit_type) target."""
    logger.info("=== Starting target: %s ===", target.label)
    context = await _new_stealthy_context(browser)

    try:
        for page_num in range(1, config.MAX_PAGES_PER_TARGET + 1):
            search_url = target.url(page=page_num)
            logger.info("Scraping page %d for %s -> %s", page_num, target.label, search_url)

            cards = await _scrape_search_page(context, search_url, stats)
            if not cards:
                logger.info("No cards found on page %d for %s; stopping pagination.", page_num, target.label)
                break

            for card in cards:
                stats.listings_found += 1

                seller_name, seller_link = None, None
                if config.VISIT_DETAIL_PAGE_FOR_SELLER_LINK:
                    seller_name, seller_link = await _scrape_listing_detail(context, card["url"])
                    await _random_delay(
                        config.DELAY_BETWEEN_DETAIL_PAGES,
                        reason=f"between detail pages ({target.label})",
                    )

                listing = Listing(
                    url=card["url"],
                    title=card.get("title"),
                    price=card.get("price"),
                    location=card.get("location"),
                    image_url=card.get("image_url"),
                    seller_name=seller_name,
                    seller_link=seller_link,
                    search_town=target.town,
                    search_unit_type=target.unit_type,
                )
                is_new = db.upsert_listing(listing)
                if is_new:
                    stats.listings_new += 1
                else:
                    stats.listings_updated += 1

            await _random_delay(
                config.DELAY_BETWEEN_PAGES,
                reason=f"between search pages ({target.label})",
            )

        stats.targets_processed += 1
    except Exception:  # noqa: BLE001
        logger.exception("Unhandled error while scraping target %s", target.label)
        stats.errors += 1
    finally:
        await context.close()


async def run_scrape(db: ListingsDatabase, targets: list["config.SearchTarget"]) -> ScrapeStats:
    """Entry point: launch Playwright, iterate all targets, return stats."""
    stats = ScrapeStats()

    async with async_playwright() as pw:  # type: Playwright
        browser = await pw.chromium.launch(
            headless=config.HEADLESS,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        try:
            for idx, target in enumerate(targets):
                await scrape_target(browser, target, db, stats)
                if idx < len(targets) - 1:
                    await _random_delay(
                        config.DELAY_BETWEEN_TARGETS,
                        reason="between search targets",
                    )
        finally:
            await browser.close()

    return stats