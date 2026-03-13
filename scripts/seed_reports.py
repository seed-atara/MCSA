"""Seed existing local MCSA reports into the Supabase `reports` table.

Scans output/mcsa/reports/ for *.md files (excluding *.slack.md and
*.confluence.md), parses the agency/cadence/module from the path and
filename, and inserts them into Supabase — skipping duplicates based on
agency_name + module + cadence + content hash.

Usage:
    python scripts/seed_reports.py
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure the project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from supabase import create_client


def md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def parse_report_filename(filename: str) -> dict | None:
    """Parse a filename like 'daily_industry_20260310_141448.md'.

    Returns dict with cadence, module, and timestamp, or None on failure.
    """
    # Pattern: {cadence}_{module}_{YYYYMMDD}_{HHMMSS}.md
    m = re.match(r"^([a-z]+)_([a-z]+)_(\d{8})_(\d{6})\.md$", filename)
    if not m:
        return None
    cadence, module, date_str, time_str = m.groups()
    try:
        ts = datetime.strptime(f"{date_str}_{time_str}", "%Y%m%d_%H%M%S")
        ts = ts.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return {"cadence": cadence, "module": module, "timestamp": ts}


def main():
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY")

    if not supabase_url or not supabase_key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env")
        sys.exit(1)

    sb = create_client(supabase_url, supabase_key)

    # Determine output dir (same logic as core/config.py)
    if os.getenv("RAILWAY_ENVIRONMENT"):
        output_dir = Path("/data/output")
    else:
        output_dir = PROJECT_ROOT / "output"

    reports_dir = output_dir / "mcsa" / "reports"
    if not reports_dir.exists():
        print(f"No reports directory found at {reports_dir}")
        sys.exit(1)

    # Collect all candidate report files
    all_md_files = sorted(reports_dir.rglob("*.md"))
    report_files = [
        f for f in all_md_files
        if not f.name.endswith(".slack.md")
        and not f.name.endswith(".confluence.md")
    ]

    print(f"Found {len(report_files)} report files to process")

    # Fetch existing content hashes from Supabase so we can skip duplicates.
    # We'll pull all existing reports and build a set of
    # (agency_name, module, cadence, content_hash) tuples.
    print("Fetching existing reports from Supabase for dedup...")
    existing_hashes: set[tuple[str, str, str, str]] = set()
    try:
        # Paginate through all existing reports
        offset = 0
        page_size = 1000
        while True:
            result = (
                sb.table("reports")
                .select("agency_name, module, cadence, content")
                .range(offset, offset + page_size - 1)
                .execute()
            )
            rows = result.data or []
            for row in rows:
                h = md5(row.get("content", ""))
                existing_hashes.add((
                    row["agency_name"],
                    row["module"],
                    row["cadence"],
                    h,
                ))
            if len(rows) < page_size:
                break
            offset += page_size
    except Exception as e:
        print(f"WARNING: Could not fetch existing reports: {e}")
        print("Proceeding without dedup — duplicates may be created.")

    print(f"  {len(existing_hashes)} existing report(s) found in Supabase")

    inserted = 0
    skipped = 0
    errors = 0

    for filepath in report_files:
        # Agency name is the parent directory
        agency_name = filepath.parent.name

        parsed = parse_report_filename(filepath.name)
        if not parsed:
            print(f"  SKIP (bad filename): {filepath.name}")
            skipped += 1
            continue

        cadence = parsed["cadence"]
        module = parsed["module"]
        created_at = parsed["timestamp"]

        try:
            content = filepath.read_text(encoding="utf-8")
        except OSError as e:
            print(f"  ERROR reading {filepath}: {e}")
            errors += 1
            continue

        content_hash = md5(content)
        dedup_key = (agency_name, module, cadence, content_hash)

        if dedup_key in existing_hashes:
            print(f"  SKIP (exists): {agency_name}/{filepath.name}")
            skipped += 1
            continue

        # Insert into Supabase
        try:
            sb.table("reports").insert({
                "agency_name": agency_name,
                "module": module,
                "cadence": cadence,
                "content": content,
                "created_at": created_at.isoformat(),
            }).execute()
            existing_hashes.add(dedup_key)
            inserted += 1
            print(f"  INSERT: {agency_name}/{filepath.name}")
        except Exception as e:
            print(f"  ERROR inserting {agency_name}/{filepath.name}: {e}")
            errors += 1

    print()
    print(f"Done. Inserted: {inserted}, Skipped: {skipped}, Errors: {errors}")


if __name__ == "__main__":
    main()
