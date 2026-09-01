#!/usr/bin/env python3
"""
main.py
-------
CLI entry point for the Jiji.co.ke real estate scraper.

Usage
=====
    python main.py
    python main.py --towns Nairobi Juja --unit-types Bedsitter "1 Bedroom"
    python main.py --max-pages 2 --no-detail-pages
    python main.py --headed          # watch the browser for debugging

Before first run:
    pip install playwright
    playwright install chromium
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import config
from database import open_database
from scraper import run_scrape


def setup_logging(verbose: bool) -> None:
    """Configure root logger with both console and rotating-friendly file output."""
    level = logging.DEBUG if verbose else logging.INFO
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    file_handler = logging.FileHandler(config.LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # Playwright's own logger is noisy at DEBUG; keep it at WARNING unless
    # the user explicitly wants everything.
    if not verbose:
        logging.getLogger("playwright").setLevel(logging.WARNING)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape rental real-estate listings from Jiji.co.ke into a local SQLite database."
    )
    parser.add_argument(
        "--towns",
        nargs="+",
        default=config.TOWNS,
        help=f"Towns to search (default: {config.TOWNS})",
    )
    parser.add_argument(
        "--unit-types",
        nargs="+",
        default=config.UNIT_TYPES,
        help=f"Unit types to search (default: {config.UNIT_TYPES})",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=config.MAX_PAGES_PER_TARGET,
        help="Maximum search-result pages to walk per (town, unit type) target.",
    )
    parser.add_argument(
        "--no-detail-pages",
        action="store_true",
        help="Skip visiting each listing's own page for seller info (faster, less data).",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run with a visible browser window instead of headless (useful for debugging).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )
    return parser.parse_args(argv)


def apply_cli_overrides(args: argparse.Namespace) -> None:
    """Push CLI flags into the config module before the scraper reads them."""
    config.TOWNS = args.towns
    config.UNIT_TYPES = args.unit_types
    config.MAX_PAGES_PER_TARGET = args.max_pages
    config.VISIT_DETAIL_PAGE_FOR_SELLER_LINK = not args.no_detail_pages
    config.HEADLESS = not args.headed


async def async_main(args: argparse.Namespace) -> int:
    targets = config.build_search_targets()
    logger = logging.getLogger("jiji_scraper.main")
    logger.info(
        "Starting scrape run: %d target(s) [%s], max_pages=%d, headless=%s, detail_pages=%s",
        len(targets),
        ", ".join(t.label for t in targets),
        config.MAX_PAGES_PER_TARGET,
        config.HEADLESS,
        config.VISIT_DETAIL_PAGE_FOR_SELLER_LINK,
    )

    with open_database(config.DB_PATH) as db:
        starting_count = db.count()
        stats = await run_scrape(db, targets)
        ending_count = db.count()

    logger.info("=" * 60)
    logger.info("SCRAPE RUN COMPLETE")
    logger.info("Targets processed : %d / %d", stats.targets_processed, len(targets))
    logger.info("Search pages hit  : %d", stats.pages_visited)
    logger.info("Listings found    : %d", stats.listings_found)
    logger.info("New listings      : %d", stats.listings_new)
    logger.info("Updated listings  : %d", stats.listings_updated)
    logger.info("Errors            : %d", stats.errors)
    logger.info("DB rows before/after: %d -> %d", starting_count, ending_count)
    logger.info("Database file     : %s", config.DB_PATH)
    logger.info("=" * 60)

    return 0 if stats.errors == 0 else 1


def main() -> None:
    args = parse_args()
    setup_logging(args.verbose)
    apply_cli_overrides(args)
    exit_code = asyncio.run(async_main(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
