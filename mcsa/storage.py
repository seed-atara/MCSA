"""MCSA persistent storage — JSON-based persistence for registries, reports, and snapshots.

Directory structure under OUTPUT_DIR/mcsa/:
    registries/
        {agency_name}.json          — current competitor registry
    reports/
        {agency_name}/
            {cadence}_{module}_{YYYYMMDD_HHMMSS}.md
    snapshots/
        {agency_name}/
            {competitor_name}_website_{YYYYMMDD}.json   — website page hashes for change detection
"""
from __future__ import annotations

import json
import hashlib
from datetime import datetime
from pathlib import Path

from core.config import OUTPUT_DIR


MCSA_DIR = OUTPUT_DIR / "mcsa"


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Competitor Registries
# ---------------------------------------------------------------------------

def load_registry(agency_name: str) -> list[dict]:
    """Load the competitor registry for an agency. Returns [] if none exists."""
    path = MCSA_DIR / "registries" / f"{_safe(agency_name)}.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def save_registry(agency_name: str, competitors: list[dict]) -> Path:
    """Save the competitor registry for an agency. Returns the file path."""
    _ensure_dir(MCSA_DIR / "registries")
    path = MCSA_DIR / "registries" / f"{_safe(agency_name)}.json"
    path.write_text(json.dumps(competitors, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_all_registries() -> dict[str, list[dict]]:
    """Load all registries. Returns {agency_name: [competitors]}."""
    reg_dir = MCSA_DIR / "registries"
    if not reg_dir.exists():
        return {}
    result = {}
    for f in reg_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            result[f.stem] = data
        except (json.JSONDecodeError, OSError):
            pass
    return result


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

def save_report(agency_name: str, module: str, cadence: str, content: str) -> Path:
    """Save a report and return its path."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = _ensure_dir(MCSA_DIR / "reports" / _safe(agency_name))
    filename = f"{cadence}_{module}_{ts}.md"
    path = report_dir / filename
    path.write_text(content, encoding="utf-8")
    return path


def load_latest_report(agency_name: str, module: str, cadence: str) -> str | None:
    """Load the most recent report for an agency/module/cadence. Returns None if none."""
    report_dir = MCSA_DIR / "reports" / _safe(agency_name)
    if not report_dir.exists():
        return None
    pattern = f"{cadence}_{module}_*.md"
    files = sorted(report_dir.glob(pattern), reverse=True)
    if not files:
        return None
    try:
        return files[0].read_text(encoding="utf-8")
    except OSError:
        return None


def load_report_history(agency_name: str, module: str, cadence: str, limit: int = 5) -> list[dict]:
    """Load recent reports with timestamps. Returns [{path, timestamp, content_preview}]."""
    report_dir = MCSA_DIR / "reports" / _safe(agency_name)
    if not report_dir.exists():
        return []
    pattern = f"{cadence}_{module}_*.md"
    files = sorted(report_dir.glob(pattern), reverse=True)[:limit]
    result = []
    for f in files:
        try:
            content = f.read_text(encoding="utf-8")
            result.append({
                "path": str(f),
                "timestamp": f.stem.split("_", 2)[-1] if "_" in f.stem else "",
                "content_preview": content[:500],
                "content": content,
            })
        except OSError:
            pass
    return result


# ---------------------------------------------------------------------------
# Website Snapshots (for change detection)
# ---------------------------------------------------------------------------

def save_website_snapshot(agency_name: str, competitor_name: str, pages: list[dict]) -> Path:
    """Save a website snapshot — list of {url, content_hash, title, word_count}.

    Used by the WebsiteAgent to detect changes between runs.
    """
    snap_dir = _ensure_dir(MCSA_DIR / "snapshots" / _safe(agency_name))
    today = datetime.now().strftime("%Y%m%d")
    path = snap_dir / f"{_safe(competitor_name)}_website_{today}.json"

    snapshot = []
    for page in pages:
        url = page.get("url", "")
        content = page.get("raw_content", page.get("content", ""))
        snapshot.append({
            "url": url,
            "content_hash": hashlib.md5(content.encode()).hexdigest() if content else "",
            "title": page.get("title", ""),
            "word_count": len(content.split()) if content else 0,
        })

    path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_previous_snapshot(agency_name: str, competitor_name: str) -> list[dict] | None:
    """Load the most recent previous snapshot for change detection."""
    snap_dir = MCSA_DIR / "snapshots" / _safe(agency_name)
    if not snap_dir.exists():
        return None
    pattern = f"{_safe(competitor_name)}_website_*.json"
    files = sorted(snap_dir.glob(pattern), reverse=True)
    # Skip today's snapshot if it exists — we want the previous one
    today = datetime.now().strftime("%Y%m%d")
    for f in files:
        if today not in f.name:
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
    return None


def diff_snapshots(previous: list[dict], current: list[dict]) -> dict:
    """Compare two website snapshots. Returns {new_pages, changed_pages, removed_pages}."""
    prev_urls = {p["url"]: p for p in previous}
    curr_urls = {p["url"]: p for p in current}

    new_pages = [curr_urls[u] for u in curr_urls if u not in prev_urls]
    removed_pages = [prev_urls[u] for u in prev_urls if u not in curr_urls]
    changed_pages = [
        curr_urls[u] for u in curr_urls
        if u in prev_urls and curr_urls[u]["content_hash"] != prev_urls[u]["content_hash"]
    ]

    return {
        "new_pages": new_pages,
        "changed_pages": changed_pages,
        "removed_pages": removed_pages,
        "unchanged_count": len(set(curr_urls) & set(prev_urls)) - len(changed_pages),
    }


# ---------------------------------------------------------------------------
# Run log
# ---------------------------------------------------------------------------

def save_run_log(cadence: str, agencies: list[str], cost: dict, duration_seconds: float) -> Path:
    """Append a run entry to the run log."""
    _ensure_dir(MCSA_DIR)
    log_path = MCSA_DIR / "run_log.jsonl"
    entry = {
        "timestamp": datetime.now().isoformat(),
        "cadence": cadence,
        "agencies": agencies,
        "cost": cost,
        "duration_seconds": round(duration_seconds, 1),
    }
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return log_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe(name: str) -> str:
    """Make a name filesystem-safe."""
    return name.strip().replace(" ", "-").replace("/", "-").replace("\\", "-")
