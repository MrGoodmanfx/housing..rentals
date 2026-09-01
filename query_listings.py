"""
query_listings.py
-------------------
Quick reusable script to peek at your scraped listings without fighting
PowerShell's quote-escaping. Just run:

    python query_listings.py

Edit the SQL query below any time you want to look at the data
differently (e.g. sort by price, filter by town, etc).
"""

import sqlite3

conn = sqlite3.connect("listings.db")
conn.row_factory = sqlite3.Row  # lets us access columns by name

QUERY = """
SELECT title, price, location, seller_name, seller_link, first_seen_at
FROM listings
ORDER BY first_seen_at DESC
LIMIT 20;
"""

rows = conn.execute(QUERY).fetchall()

if not rows:
    print("No rows found. Did you run main.py yet?")
else:
    for row in rows:
        print("-" * 70)
        print(f"Title:    {row['title']}")
        print(f"Price:    {row['price']}")
        print(f"Location: {row['location']}")
        print(f"Seller:   {row['seller_name']}")
        print(f"Profile:  {row['seller_link']}")
        print(f"Seen:     {row['first_seen_at']}")

conn.close()