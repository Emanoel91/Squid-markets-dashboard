#!/usr/bin/env python3
"""
Record a daily snapshot of Squid (QUID) 24h volumes per market.

Run it once at the end of each UTC day (e.g. 23:55 UTC). It appends one row per
market to a CSV file; running it again on the same date replaces that date's rows.

Usage:
    python collect_daily_snapshot.py                      # fetch API, write data/squid_daily_history.csv
    python collect_daily_snapshot.py --out my_history.csv
    python collect_daily_snapshot.py --input saved.json   # use a JSON file you saved manually
    python collect_daily_snapshot.py --date 2026-11-14    # override the recorded date (UTC)

CSV columns:
    date, recorded_at, exchange, pair, venue, volume, volume_prev, vol_normalized
"""

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd
import requests

API_URL = "https://defillama.com/api/public/markets/squid-3"
COLUMNS = ["date", "recorded_at", "exchange", "pair", "venue",
           "volume", "volume_prev", "vol_normalized"]


def fetch_api(url: str) -> dict:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://defillama.com/",
    }
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


def build_rows(data: dict, date_str: str, recorded_at: str) -> pd.DataFrame:
    rows = []
    for venue in ("cex", "dex"):
        block = data.get(venue) or {}
        if not isinstance(block, dict):
            continue
        for _segment, items in block.items():
            for m in items or []:
                nvol = m.get("nvol") if isinstance(m.get("nvol"), dict) else {}
                rows.append({
                    "date": date_str,
                    "recorded_at": recorded_at,
                    "exchange": m.get("display_name") or m.get("slug") or "Unknown",
                    "pair": (m.get("symbol") or m.get("pair_id") or "N/A").upper(),
                    "venue": (m.get("type") or venue).upper(),
                    "volume": m.get("volume_24h") or 0,
                    "volume_prev": m.get("volume_prev_24h"),
                    "vol_normalized": nvol.get("normalized"),
                })
    return pd.DataFrame(rows, columns=COLUMNS)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--url", default=API_URL)
    ap.add_argument("--input", help="Read this JSON file instead of calling the API")
    ap.add_argument("--out", default="data/squid_daily_history.csv")
    ap.add_argument("--date", help="Date to record (YYYY-MM-DD, UTC). Default: today (UTC)")
    args = ap.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    date_str = args.date or now.strftime("%Y-%m-%d")

    if args.input:
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    else:
        data = fetch_api(args.url)

    new = build_rows(data, date_str, now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    if new.empty:
        print("No markets found in the API response; nothing recorded.", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        old = pd.read_csv(out)
        old = old[old["date"].astype(str) != date_str]  # replace rows of the same date
        combined = pd.concat([old, new], ignore_index=True)
    else:
        combined = new

    combined = combined.sort_values(["date", "exchange", "pair"]).reset_index(drop=True)
    combined.to_csv(out, index=False)
    print(f"Recorded {len(new)} markets for {date_str} -> {out} "
          f"(total volume ${new['volume'].sum():,.0f}, {combined['date'].nunique()} day(s) in file)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
