"""MCSA persistent storage — local filesystem + Supabase dual-write.

Local files are written for dev/debugging. Supabase is the persistent store
that survives Railway's ephemeral filesystem and serves the chat UI.

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

from rich.console import Console

from core.config import OUTPUT_DIR, SUPABASE_URL, SUPABASE_SERVICE_KEY

console = Console()

MCSA_DIR = OUTPUT_DIR / "mcsa"

# ---------------------------------------------------------------------------
# Supabase client (lazy init)
# ---------------------------------------------------------------------------

_supabase_client = None


def _get_supabase():
    """Lazy-init the Supabase client. Returns None if not configured."""
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        from supabase import create_client
        _supabase_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        return _supabase_client
    except Exception as e:
        console.print(f"[yellow]Supabase init failed: {e}[/yellow]")
        return None


def _sb_insert(table: str, data: dict) -> None:
    """Insert a row into Supabase. Fails silently with a warning."""
    sb = _get_supabase()
    if not sb:
        return
    try:
        sb.table(table).insert(data).execute()
    except Exception as e:
        console.print(f"[yellow]Supabase {table} insert failed: {e}[/yellow]")


def _sb_upsert(table: str, data: dict, on_conflict: str = "") -> None:
    """Upsert a row into Supabase. Fails silently with a warning."""
    sb = _get_supabase()
    if not sb:
        return
    try:
        q = sb.table(table).upsert(data, on_conflict=on_conflict) if on_conflict else sb.table(table).upsert(data)
        q.execute()
    except Exception as e:
        console.print(f"[yellow]Supabase {table} upsert failed: {e}[/yellow]")


def _sb_select(table: str, filters: dict, order_by: str = None, limit: int = None):
    """Select rows from Supabase. Returns [] on failure."""
    sb = _get_supabase()
    if not sb:
        return []
    try:
        query = sb.table(table).select("*")
        for col, val in filters.items():
            query = query.eq(col, val)
        if order_by:
            query = query.order(order_by, desc=True)
        if limit:
            query = query.limit(limit)
        result = query.execute()
        return result.data or []
    except Exception as e:
        console.print(f"[yellow]Supabase {table} select failed: {e}[/yellow]")
        return []


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Competitor Registries
# ---------------------------------------------------------------------------

def load_registry(agency_name: str) -> list[dict]:
    """Load the competitor registry for an agency. Returns [] if none exists."""
    # Try local first
    path = MCSA_DIR / "registries" / f"{_safe(agency_name)}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    # Fall back to Supabase
    rows = _sb_select("registries", {"agency_name": _safe(agency_name)}, limit=1)
    if rows:
        competitors = rows[0].get("competitors", [])
        # Cache locally
        try:
            _ensure_dir(MCSA_DIR / "registries")
            path.write_text(json.dumps(competitors, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        return competitors

    return []


def save_registry(agency_name: str, competitors: list[dict]) -> Path:
    """Save the competitor registry for an agency. Returns the file path."""
    _ensure_dir(MCSA_DIR / "registries")
    path = MCSA_DIR / "registries" / f"{_safe(agency_name)}.json"
    path.write_text(json.dumps(competitors, indent=2, ensure_ascii=False), encoding="utf-8")

    # Sync to Supabase
    _sb_upsert("registries", {
        "agency_name": _safe(agency_name),
        "competitors": competitors,
        "updated_at": datetime.now().isoformat(),
    }, on_conflict="agency_name")

    return path


def load_all_registries() -> dict[str, list[dict]]:
    """Load all registries. Returns {agency_name: [competitors]}."""
    result = {}

    # Try local first
    reg_dir = MCSA_DIR / "registries"
    if reg_dir.exists():
        for f in reg_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                result[f.stem] = data
            except (json.JSONDecodeError, OSError):
                pass

    # If no local registries, try Supabase
    if not result:
        rows = _sb_select("registries", {})
        for row in rows:
            name = row.get("agency_name", "")
            competitors = row.get("competitors", [])
            if name and competitors:
                result[name] = competitors
                # Cache locally
                try:
                    _ensure_dir(MCSA_DIR / "registries")
                    p = MCSA_DIR / "registries" / f"{name}.json"
                    p.write_text(json.dumps(competitors, indent=2, ensure_ascii=False), encoding="utf-8")
                except OSError:
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

    # Sync to Supabase
    _sb_insert("reports", {
        "agency_name": _safe(agency_name),
        "module": module,
        "cadence": cadence,
        "content": content,
    })

    return path


def load_latest_report(agency_name: str, module: str, cadence: str) -> str | None:
    """Load the most recent report for an agency/module/cadence. Returns None if none."""
    # Try local first
    report_dir = MCSA_DIR / "reports" / _safe(agency_name)
    if report_dir.exists():
        pattern = f"{cadence}_{module}_*.md"
        files = sorted(report_dir.glob(pattern), reverse=True)
        if files:
            try:
                return files[0].read_text(encoding="utf-8")
            except OSError:
                pass

    # Fall back to Supabase
    rows = _sb_select(
        "reports",
        {"agency_name": _safe(agency_name), "module": module, "cadence": cadence},
        order_by="created_at",
        limit=1,
    )
    if rows:
        return rows[0].get("content")

    return None


def load_report_history(agency_name: str, module: str, cadence: str, limit: int = 5) -> list[dict]:
    """Load recent reports with timestamps. Returns [{path, timestamp, content_preview, content}]."""
    # Try local first
    report_dir = MCSA_DIR / "reports" / _safe(agency_name)
    if report_dir.exists():
        pattern = f"{cadence}_{module}_*.md"
        files = sorted(report_dir.glob(pattern), reverse=True)[:limit]
        if files:
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

    # Fall back to Supabase
    rows = _sb_select(
        "reports",
        {"agency_name": _safe(agency_name), "module": module, "cadence": cadence},
        order_by="created_at",
        limit=limit,
    )
    return [
        {
            "path": "",
            "timestamp": row.get("created_at", ""),
            "content_preview": row.get("content", "")[:500],
            "content": row.get("content", ""),
        }
        for row in rows
    ]


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

    # Sync to Supabase
    _sb_insert("snapshots", {
        "agency_name": _safe(agency_name),
        "competitor_name": _safe(competitor_name),
        "pages": snapshot,
    })

    return path


def load_previous_snapshot(agency_name: str, competitor_name: str) -> list[dict] | None:
    """Load the most recent previous snapshot for change detection."""
    # Try local first
    snap_dir = MCSA_DIR / "snapshots" / _safe(agency_name)
    today = datetime.now().strftime("%Y%m%d")

    if snap_dir.exists():
        pattern = f"{_safe(competitor_name)}_website_*.json"
        files = sorted(snap_dir.glob(pattern), reverse=True)
        for f in files:
            if today not in f.name:
                try:
                    return json.loads(f.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    pass

    # Fall back to Supabase — get 2nd most recent (skip today's)
    rows = _sb_select(
        "snapshots",
        {"agency_name": _safe(agency_name), "competitor_name": _safe(competitor_name)},
        order_by="created_at",
        limit=2,
    )
    for row in rows:
        ts = row.get("created_at", "")
        if today not in ts:
            return row.get("pages", [])

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

    # Sync to Supabase
    _sb_insert("run_logs", {
        "cadence": cadence,
        "agencies": agencies,
        "cost": cost,
        "duration_seconds": round(duration_seconds, 1),
    })

    return log_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe(name: str) -> str:
    """Make a name filesystem-safe."""
    return name.strip().replace(" ", "-").replace("/", "-").replace("\\", "-")


# ---------------------------------------------------------------------------
# Topics (Topic Intelligence)
# ---------------------------------------------------------------------------

def save_topics(agency_name: str, topics: list[dict]) -> None:
    """Upsert topics for an agency. Each topic dict should have at minimum:
    topic, category, momentum, mention_count, confidence, relevance, sources.
    """
    now = datetime.now().isoformat()

    for t in topics:
        topic_name = t.get("topic", "").strip()
        if not topic_name:
            continue

        row = {
            "agency_name": agency_name,
            "topic": topic_name,
            "category": t.get("category", ""),
            "momentum": t.get("momentum", "stable"),
            "mention_count": t.get("mention_count", 1),
            "confidence": t.get("confidence", "MEDIUM"),
            "relevance": t.get("relevance", ""),
            "sources": t.get("sources", []),
            "last_seen_at": now,
            "updated_at": now,
        }

        _sb_upsert("topics", row, on_conflict="agency_name,topic")


def load_topics(agency_name: str, momentum: str | None = None, limit: int = 20) -> list[dict]:
    """Load topics for an agency, optionally filtered by momentum."""
    sb = _get_supabase()
    if not sb:
        return []
    try:
        query = sb.table("topics").select("*").eq("agency_name", agency_name)
        if momentum:
            query = query.eq("momentum", momentum)
        query = query.order("last_seen_at", desc=True).limit(limit)
        result = query.execute()
        return result.data or []
    except Exception as e:
        console.print(f"[yellow]Topics load failed: {e}[/yellow]")
        return []


def load_all_topics(limit_per_agency: int = 10) -> dict[str, list[dict]]:
    """Load topics grouped by agency."""
    sb = _get_supabase()
    if not sb:
        return {}
    try:
        query = sb.table("topics").select("*").order("last_seen_at", desc=True).limit(200)
        result = query.execute()
        grouped: dict[str, list[dict]] = {}
        for row in (result.data or []):
            agency = row.get("agency_name", "?")
            if agency not in grouped:
                grouped[agency] = []
            if len(grouped[agency]) < limit_per_agency:
                grouped[agency].append(row)
        return grouped
    except Exception as e:
        console.print(f"[yellow]All topics load failed: {e}[/yellow]")
        return {}


# ---------------------------------------------------------------------------
# Key People
# ---------------------------------------------------------------------------

def save_key_people(agency_name: str, people: list[dict]) -> None:
    """Upsert key people for an agency."""
    now = datetime.now().isoformat()

    for p in people:
        name = p.get("name", "").strip()
        if not name:
            continue

        row = {
            "agency_name": agency_name,
            "name": name,
            "title": p.get("title", ""),
            "company": p.get("company", ""),
            "linkedin_url": p.get("linkedin_url", ""),
            "topics": p.get("topics", []),
            "relevance": p.get("relevance", ""),
            "recent_activity": p.get("recent_activity", ""),
            "status": p.get("status", "active"),
            "updated_at": now,
        }

        _sb_upsert("key_people", row, on_conflict="agency_name,name")


def load_key_people(agency_name: str, limit: int = 10) -> list[dict]:
    """Load key people for an agency."""
    sb = _get_supabase()
    if not sb:
        return []
    try:
        query = (
            sb.table("key_people")
            .select("*")
            .eq("agency_name", agency_name)
            .eq("status", "active")
            .order("updated_at", desc=True)
            .limit(limit)
        )
        result = query.execute()
        return result.data or []
    except Exception as e:
        console.print(f"[yellow]Key people load failed: {e}[/yellow]")
        return []


def load_all_key_people() -> dict[str, list[dict]]:
    """Load key people grouped by agency."""
    sb = _get_supabase()
    if not sb:
        return {}
    try:
        query = sb.table("key_people").select("*").eq("status", "active").order("updated_at", desc=True).limit(100)
        result = query.execute()
        grouped: dict[str, list[dict]] = {}
        for row in (result.data or []):
            agency = row.get("agency_name", "?")
            if agency not in grouped:
                grouped[agency] = []
            grouped[agency].append(row)
        return grouped
    except Exception as e:
        console.print(f"[yellow]All key people load failed: {e}[/yellow]")
        return {}
