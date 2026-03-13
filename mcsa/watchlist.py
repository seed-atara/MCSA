"""MCSA Phase 3c — Watch Lists.

Manages user-defined watch lists and checks for matches after each
surveillance run. Matched alerts are delivered to the dedicated Slack
alerts channel via Block Kit.
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime

from rich.console import Console

from mcsa.storage import _get_supabase, _sb_insert, _sb_select
from mcsa.config import SLACK_MCSA_ENABLED, SLACK_MCSA_WEBHOOK_URL_ALERTS

console = Console()


# ---------------------------------------------------------------------------
# Watchlist CRUD
# ---------------------------------------------------------------------------

def add_watch(
    user_name: str,
    watch_type: str,
    watch_value: str,
    agency_name: str | None = None,
    notify_slack: bool = True,
    notify_email: bool = False,
) -> dict | None:
    """Insert a new watchlist entry. Returns the inserted row or None on failure."""
    if watch_type not in ("competitor", "keyword", "agency"):
        console.print(f"[red]Invalid watch_type '{watch_type}' — must be 'competitor', 'keyword', or 'agency'[/red]")
        return None

    data = {
        "user_name": user_name,
        "watch_type": watch_type,
        "watch_value": watch_value,
        "agency_name": agency_name,
        "notify_slack": notify_slack,
        "notify_email": notify_email,
    }

    sb = _get_supabase()
    if not sb:
        console.print("[yellow]Supabase not configured — cannot add watch[/yellow]")
        return None

    try:
        result = sb.table("watchlist").insert(data).execute()
        row = result.data[0] if result.data else data
        console.print(f"[green]Watch added:[/green] {watch_type} = '{watch_value}' for {user_name}")
        return row
    except Exception as e:
        console.print(f"[yellow]Supabase watchlist insert failed: {e}[/yellow]")
        return None


def remove_watch(watch_id: int) -> bool:
    """Delete a watchlist entry by ID. Returns True on success."""
    sb = _get_supabase()
    if not sb:
        console.print("[yellow]Supabase not configured — cannot remove watch[/yellow]")
        return False

    try:
        sb.table("watchlist").delete().eq("id", watch_id).execute()
        console.print(f"[green]Watch {watch_id} removed[/green]")
        return True
    except Exception as e:
        console.print(f"[yellow]Supabase watchlist delete failed: {e}[/yellow]")
        return False


def list_watches(user_name: str | None = None) -> list[dict]:
    """List all watchlist entries, optionally filtered by user_name."""
    filters = {}
    if user_name:
        filters["user_name"] = user_name
    return _sb_select("watchlist", filters, order_by="created_at")


# ---------------------------------------------------------------------------
# Match checking
# ---------------------------------------------------------------------------

def check_watchlist_matches(
    agency_name: str,
    reports: dict[str, str],
) -> list[dict]:
    """Check all watchlist entries against the current run's reports.

    Args:
        agency_name: The agency whose surveillance just ran.
        reports: Mapping of module name -> report content (markdown string).

    Returns:
        List of match dicts, each containing:
            - watch: the watchlist row that matched
            - agency_name: agency that produced the match
            - module: which report module matched
            - snippet: a short excerpt around the match (up to 200 chars)
    """
    all_watches = _sb_select("watchlist", {})
    if not all_watches:
        return []

    matches: list[dict] = []

    for watch in all_watches:
        # If the watch is scoped to a specific agency, skip non-matching agencies
        if watch.get("agency_name") and watch["agency_name"] != agency_name:
            continue

        value_lower = watch["watch_value"].lower()

        for module, content in reports.items():
            content_lower = content.lower()
            idx = content_lower.find(value_lower)
            if idx == -1:
                continue

            # Extract a snippet around the match
            start = max(0, idx - 80)
            end = min(len(content), idx + len(value_lower) + 80)
            snippet = content[start:end].strip()
            if start > 0:
                snippet = "..." + snippet
            if end < len(content):
                snippet = snippet + "..."

            matches.append({
                "watch": watch,
                "agency_name": agency_name,
                "module": module,
                "snippet": snippet,
            })

    if matches:
        console.print(f"[bold cyan]Watchlist:[/bold cyan] {len(matches)} match(es) found for {agency_name}")
    else:
        console.print(f"[dim]Watchlist: no matches for {agency_name}[/dim]")

    return matches


# ---------------------------------------------------------------------------
# Alert delivery
# ---------------------------------------------------------------------------

def deliver_watchlist_alerts(matches: list[dict]) -> bool:
    """Post watchlist matches to the Slack alerts channel using Block Kit.

    Returns True if delivery succeeded (or was skipped because nothing to send).
    """
    if not matches:
        return True

    if not SLACK_MCSA_ENABLED or not SLACK_MCSA_WEBHOOK_URL_ALERTS:
        console.print("[yellow]Slack alerts webhook not configured — skipping watchlist delivery[/yellow]")
        return False

    # Build Block Kit payload
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f":rotating_light: MCSA Watchlist — {len(matches)} Alert(s)",
                "emoji": True,
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Triggered at {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}",
                },
            ],
        },
        {"type": "divider"},
    ]

    for match in matches:
        watch = match["watch"]
        blocks.append({
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Type:* {watch['watch_type']}"},
                {"type": "mrkdwn", "text": f"*Value:* {watch['watch_value']}"},
                {"type": "mrkdwn", "text": f"*Agency:* {match['agency_name']}"},
                {"type": "mrkdwn", "text": f"*Module:* {match['module']}"},
            ],
        })
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"```{match['snippet']}```",
            },
        })
        blocks.append({"type": "divider"})

    # Truncate blocks if we exceed Slack's 50-block limit
    if len(blocks) > 50:
        blocks = blocks[:49]
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"_...and more matches (truncated to fit Slack limits)_",
            },
        })

    payload = json.dumps({"blocks": blocks}).encode("utf-8")

    try:
        req = urllib.request.Request(
            SLACK_MCSA_WEBHOOK_URL_ALERTS,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                console.print(f"[green]Watchlist alerts delivered to Slack ({len(matches)} match(es))[/green]")
                return True
            else:
                console.print(f"[yellow]Slack alerts returned status {resp.status}[/yellow]")
                return False
    except Exception as e:
        console.print(f"[yellow]Slack watchlist alert delivery failed: {e}[/yellow]")
        return False
