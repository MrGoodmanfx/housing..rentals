"""
database.py
-----------
SQLite persistence layer for scraped listings.

The listing URL is used as the natural unique key (UNIQUE constraint) so
re-running the scraper is idempotent: existing listings are skipped/updated
rather than duplicated.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger("jiji_scraper.database")

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    url             TEXT NOT NULL UNIQUE,
    title           TEXT,
    price           TEXT,
    location        TEXT,
    image_url       TEXT,
    seller_name     TEXT,
    seller_link     TEXT,
    search_town     TEXT,
    search_unit_type TEXT,
    first_seen_at   TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_listings_town ON listings(search_town);
CREATE INDEX IF NOT EXISTS idx_listings_unit_type ON listings(search_unit_type);
"""

UPSERT_SQL = """
INSERT INTO listings (
    url, title, price, location, image_url,
    seller_name, seller_link, search_town, search_unit_type
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(url) DO UPDATE SET
    title            = excluded.title,
    price            = excluded.price,
    location         = excluded.location,
    image_url        = excluded.image_url,
    seller_name      = excluded.seller_name,
    seller_link      = excluded.seller_link,
    last_seen_at     = datetime('now')
;
"""

EXISTS_SQL = "SELECT 1 FROM listings WHERE url = ? LIMIT 1;"


@dataclass
class Listing:
    """A single scraped real-estate listing."""

    url: str
    title: Optional[str] = None
    price: Optional[str] = None
    location: Optional[str] = None
    image_url: Optional[str] = None
    seller_name: Optional[str] = None
    seller_link: Optional[str] = None
    search_town: Optional[str] = None
    search_unit_type: Optional[str] = None

    def as_row(self) -> tuple:
        return (
            self.url,
            self.title,
            self.price,
            self.location,
            self.image_url,
            self.seller_name,
            self.seller_link,
            self.search_town,
            self.search_unit_type,
        )


class ListingsDatabase:
    """Thin wrapper around a SQLite connection dedicated to listings."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> None:
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.execute("PRAGMA journal_mode = WAL;")
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        logger.info("Connected to SQLite database at %s", self.db_path)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.commit()
            self._conn.close()
            self._conn = None
            logger.info("Database connection closed.")

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected. Call connect() first.")
        return self._conn

    def exists(self, url: str) -> bool:
        """Check whether a listing with this URL is already stored."""
        cur = self.conn.execute(EXISTS_SQL, (url,))
        return cur.fetchone() is not None

    def upsert_listing(self, listing: Listing) -> bool:
        """
        Insert a new listing, or refresh an existing one (same URL) with the
        latest scraped values. Returns True if the listing was newly
        inserted, False if it already existed and was refreshed.
        """
        was_new = not self.exists(listing.url)
        self.conn.execute(UPSERT_SQL, listing.as_row())
        self.conn.commit()
        if was_new:
            logger.debug("Inserted new listing: %s", listing.url)
        else:
            logger.debug("Listing already existed, refreshed: %s", listing.url)
        return was_new

    def count(self) -> int:
        cur = self.conn.execute("SELECT COUNT(*) FROM listings;")
        return cur.fetchone()[0]


@contextmanager
def open_database(db_path: Path) -> Iterator[ListingsDatabase]:
    """Context manager for safe open/close of the listings database."""
    db = ListingsDatabase(db_path)
    db.connect()
    try:
        yield db
    finally:
        db.close()
