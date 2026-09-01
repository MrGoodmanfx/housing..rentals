"""
prepare_site_data.py
----------------------
STEP 1 of the aggregator pipeline.

What this does, in plain English:
  1. Opens your existing listings.db (the raw scraped data).
  2. Throws out anything broken: missing price, missing title, or a
     price that's obviously wrong (too low or way too high to be real).
  3. Writes a brand new, CLEAN title for every listing (never copies
     Jiji's wording).
  4. Works out the average rent for each location, then calculates
     whether each listing is cheaper or pricier than that average -
     this becomes your "15% cheaper than average" insight sentence.
  5. Builds a clean, SEO-friendly web address (a "slug") for each
     listing, e.g. affordable-bedsitter-to-rent-in-ruiru-abc123
  6. Saves all of this into a NEW table called `site_listings` inside
     the same listings.db file, leaving your original `listings` table
     completely untouched.

This script does NOT publish anything anywhere. It just gets your data
ready. Re-run it any time after main.py finishes a fresh scrape.

Usage:
    python prepare_site_data.py
"""

from __future__ import annotations

import re
import sqlite3

import pandas as pd

DB_PATH = "listings.db"

# --------------------------------------------------------------------------
# Tunables - adjust these numbers any time without touching the logic below.
# --------------------------------------------------------------------------
MIN_REALISTIC_PRICE = 1000       # KSh/month. Below this = broken data, not a real listing.
MAX_REALISTIC_PRICE = 300000     # KSh/month. Above this for a bedsitter/1BR = probably mis-tagged.
MIN_TITLE_LENGTH = 8             # Titles shorter than this are usually broken/empty.
MIN_GROUP_SIZE_FOR_INSIGHT = 3   # Need at least this many same-type listings in an area
                                  # before an "X% vs average" claim means anything.

_BEDROOM_RE = re.compile(r"(\d+)\s*bdrm", re.IGNORECASE)
_PRICE_RE = re.compile(r"K[Ss][Hh]\.?\s?([\d,]+)")


def parse_price(price_text: str | None) -> float | None:
    """Turn 'KSh 6,500 per month' into the plain number 6500.0."""
    if not price_text:
        return None
    match = _PRICE_RE.search(price_text)
    if not match:
        return None
    digits = match.group(1).replace(",", "")
    try:
        return float(digits)
    except ValueError:
        return None


def parse_bedrooms(title: str | None) -> int | None:
    """
    Guess bedroom count from the original scraped title text.

    Checks for "bedsitter" FIRST, before the bdrm-count pattern. Jiji
    often tags a bedsitter listing with a generic "1bdrm" size marker
    even though "bedsitter" (a studio - no separate bedroom) is the
    real, more specific unit type. Checking bdrm-count first would
    wrongly relabel bedsitters as "1 Bedroom Apartment", which changes
    what the listing actually is, not just how it's worded.
    """
    if not title:
        return None
    if "bedsitter" in title.lower():
        return 0
    match = _BEDROOM_RE.search(title)
    if match:
        return int(match.group(1))
    return None


def area_from_location(location: str | None) -> str:
    """'Kiambu, Ruiru' -> 'Ruiru' (use the most specific part)."""
    if not location:
        return "Kenya"
    parts = [p.strip() for p in location.split(",") if p.strip()]
    return parts[-1] if parts else location


def slugify(text: str) -> str:
    """Turn 'Affordable Bedsitter in Ruiru' into 'affordable-bedsitter-in-ruiru'."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def clean_title(bedrooms, location: str | None) -> str:
    """Write a brand new title. Never reuses Jiji's original wording."""
    area = area_from_location(location)
    if pd.isna(bedrooms):
        unit = "Home"
    elif int(bedrooms) == 0:
        unit = "Bedsitter"
    else:
        unit = f"{int(bedrooms)} Bedroom Apartment"
    return f"Affordable {unit} to Rent in {area}"


def build_slug(clean_title_text: str, original_url: str) -> str:
    """
    Build a unique, SEO-friendly slug. We tack on a short chunk of the
    original listing's unique ID (from its Jiji URL) so two listings
    that clean to the same title never collide.
    """
    listing_id = original_url.rstrip("/").split("/")[-1].replace(".html", "")
    unique_suffix = listing_id.split("-")[-1][:10]
    return f"{slugify(clean_title_text)}-{unique_suffix.lower()}"


def insight_sentence(pct_vs_avg: float | None, location: str | None, bedrooms) -> str | None:
    """Turn a percentage into the human-readable insight line."""
    if pct_vs_avg is None or pd.isna(pct_vs_avg):
        return None
    area = area_from_location(location)
    if pd.isna(bedrooms):
        unit_label = "home"
    elif int(bedrooms) == 0:
        unit_label = "bedsitter"
    else:
        unit_label = f"{int(bedrooms)}-bedroom unit"
    if pct_vs_avg <= -5:
        return f"This {unit_label} is {abs(pct_vs_avg):.0f}% cheaper than the average {unit_label} rent in {area}."
    if pct_vs_avg >= 5:
        return f"This {unit_label} is {pct_vs_avg:.0f}% more expensive than the average {unit_label} rent in {area}."
    return f"This {unit_label} is priced close to the average {unit_label} rent in {area}."


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM listings", conn)
    total_before = len(df)
    print(f"Loaded {total_before} raw scraped listings")

    if total_before == 0:
        print("No listings found. Run main.py first to scrape some data.")
        conn.close()
        return

    # --- Parse structured fields out of the messy scraped text -----------
    df["price_numeric"] = df["price"].apply(parse_price)
    df["bedrooms"] = df["title"].apply(parse_bedrooms).astype("Int64")

    # --- Drop broken / unrealistic rows ("filter out scams") -------------
    df = df[df["price_numeric"].notna()]
    df = df[df["price_numeric"].between(MIN_REALISTIC_PRICE, MAX_REALISTIC_PRICE)]
    df = df[df["title"].notna() & (df["title"].str.len() >= MIN_TITLE_LENGTH)]
    df = df.drop_duplicates(subset=["url"])
    dropped = total_before - len(df)
    print(f"Dropped {dropped} listings with missing/broken/unrealistic data")

    # --- Rewrite titles + build SEO slugs ---------------------------------
    df["clean_title"] = df.apply(
        lambda row: clean_title(row["bedrooms"], row["location"]), axis=1
    )
    df["slug"] = df.apply(lambda row: build_slug(row["clean_title"], row["url"]), axis=1)

    # --- Compute the "cheaper/pricier than average" insight ---------------
    # IMPORTANT: group by (location AND bedroom count), not location alone.
    # Comparing a bedsitter's price against a blended average that includes
    # 4-5 bedroom houses in the same town made every small unit look
    # artificially "cheap" regardless of its real price - a misleading
    # insight, not a useful one. Comparing like-for-like (bedsitter vs
    # other bedsitters, 1BR vs other 1BRs) gives a genuinely meaningful
    # number.
    group_cols = ["location", "bedrooms"]
    df["group_size"] = df.groupby(group_cols)["price_numeric"].transform("count")
    avg_by_group = df.groupby(group_cols)["price_numeric"].transform("mean")
    df["pct_vs_avg"] = ((df["price_numeric"] - avg_by_group) / avg_by_group * 100).round(1)

    def safe_insight(row):
        # Don't show a "% vs average" claim backed by only 1-2 listings -
        # that's not a real average, it's noise dressed up as a stat.
        if row["group_size"] < MIN_GROUP_SIZE_FOR_INSIGHT:
            return None
        return insight_sentence(row["pct_vs_avg"], row["location"], row["bedrooms"])

    df["insight"] = df.apply(safe_insight, axis=1)

    # --- Save the cleaned, ready-to-publish table --------------------------
    df.to_sql("site_listings", conn, if_exists="replace", index=False)
    conn.commit()

    print(f"Saved {len(df)} cleaned listings to the 'site_listings' table")
    print("\nSample of what got created:")
    preview_cols = ["clean_title", "price_numeric", "insight", "slug"]
    print(df[preview_cols].head(5).to_string(index=False))

    conn.close()


if __name__ == "__main__":
    main()