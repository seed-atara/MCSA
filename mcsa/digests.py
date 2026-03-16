"""MCSA Scheduled Digests — curated intelligence summaries from existing reports.

This module generates digest-level intelligence products by querying recent
reports from Supabase and sending them to Claude for synthesis. It does NOT
run new surveillance — it synthesises what already exists.

Three digest types:
    Morning Brief     (daily, 8am)  — overnight changes, top 3 things to know
    Weekly Executive  (Monday)      — cross-agency trends, strategic implications
    Monthly Board     (end of month) — formatted for leadership presentation

Phase 3b of the MCSA roadmap.
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timedelta

import anthropic
from rich.console import Console

from .config import SLACK_MCSA_WEBHOOK_URL, SLACK_MCSA_ENABLED
from .formatter import _md_to_mrkdwn
from .storage import _get_supabase, _sb_select

console = Console()

# ---------------------------------------------------------------------------
# Claude client
# ---------------------------------------------------------------------------

_MODEL = "claude-sonnet-4-20250514"
_MAX_TOKENS = 4096


def _call_claude(system: str, user: str) -> str:
    """Send a synthesis prompt to Claude and return the text response."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        console.print("[red]Digest: ANTHROPIC_API_KEY not set[/red]")
        return ""

    client = anthropic.Anthropic(api_key=api_key)
    try:
        response = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return response.content[0].text
    except Exception as e:
        console.print(f"[red]Digest: Claude API error — {e}[/red]")
        return ""


# ---------------------------------------------------------------------------
# Report fetching
# ---------------------------------------------------------------------------

def _fetch_recent_reports(hours: int) -> list[dict]:
    """Query Supabase for reports created within the last `hours` hours.

    Returns a list of report rows (dicts) or [] on failure.
    Falls back to _sb_select with no date filter if the Supabase client
    doesn't support .gte() (shouldn't happen, but defensive).
    """
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()

    sb = _get_supabase()
    if not sb:
        console.print("[yellow]Digest: Supabase not configured — no reports to digest[/yellow]")
        return []

    try:
        result = (
            sb.table("reports")
            .select("*")
            .gte("created_at", cutoff)
            .order("created_at", desc=True)
            .execute()
        )
        return result.data or []
    except Exception as e:
        console.print(f"[yellow]Digest: Supabase query failed — {e}[/yellow]")
        return []


def _format_reports_for_prompt(reports: list[dict]) -> str:
    """Concatenate report rows into a single context string for Claude."""
    if not reports:
        return "(No reports found in the requested time window.)"

    sections = []
    for r in reports:
        agency = r.get("agency_name", "Unknown")
        module = r.get("module", "unknown")
        cadence = r.get("cadence", "")
        created = r.get("created_at", "")
        content = r.get("content", "")

        sections.append(
            f"--- {agency} | {module} | {cadence} | {created} ---\n{content}"
        )

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Digest generators
# ---------------------------------------------------------------------------

def generate_morning_brief() -> str:
    """Morning Brief — overnight changes across all agencies, top 3 things to know."""
    console.print("[bold cyan]Digest: Generating Morning Brief...[/bold cyan]")

    reports = _fetch_recent_reports(hours=24)
    context = _format_reports_for_prompt(reports)

    system = (
        "You are the MCSA Intelligence Digest system for the Tomorrow Group, "
        "a UK marketing and advertising group with five agencies: Found, SEED, "
        "Braidr, Disrupt, and Culture3. You produce concise, actionable "
        "intelligence digests for senior stakeholders."
    )

    user = (
        "Below are all MCSA surveillance reports generated in the last 24 hours "
        "across every Tomorrow Group agency.\n\n"
        f"{context}\n\n"
        "Write a Morning Brief digest with the following structure:\n"
        "1. **Top 3 Things to Know** — the three most important signals from "
        "overnight, each with 1-2 sentences of context and why it matters.\n"
        "2. **Agency Highlights** — one bullet per agency that had activity, "
        "noting the key development.\n"
        "3. **Action Items** — any items that require a response today.\n"
        "4. **Content Actions** — specific content to post today and why. "
        "Format: 'Post about X today because competitor Y just did Z'. "
        "Include who should post, on which platform, and what angle to take.\n\n"
        "Keep it under 600 words. Be direct and specific — this is read at 8am "
        "by busy executives. Use markdown formatting."
    )

    digest = _call_claude(system, user)
    console.print(f"[green]Digest: Morning Brief generated ({len(digest)} chars)[/green]")
    return digest


def generate_weekly_summary() -> str:
    """Weekly Executive Summary — cross-agency trends, strategic implications."""
    console.print("[bold cyan]Digest: Generating Weekly Executive Summary...[/bold cyan]")

    reports = _fetch_recent_reports(hours=7 * 24)
    context = _format_reports_for_prompt(reports)

    system = (
        "You are the MCSA Intelligence Digest system for the Tomorrow Group, "
        "a UK marketing and advertising group with five agencies: Found, SEED, "
        "Braidr, Disrupt, and Culture3. You produce strategic intelligence "
        "summaries for executive leadership."
    )

    user = (
        "Below are all MCSA surveillance reports generated in the last 7 days "
        "across every Tomorrow Group agency.\n\n"
        f"{context}\n\n"
        "Write a Weekly Executive Summary with the following structure:\n"
        "1. **Executive Overview** — 2-3 sentence summary of the competitive "
        "landscape this week.\n"
        "2. **Cross-Agency Trends** — patterns that span multiple agencies or "
        "competitors (e.g. everyone is investing in AI, a sector-wide pricing "
        "shift, talent movement patterns).\n"
        "3. **Agency-by-Agency** — a short paragraph per agency covering key "
        "competitive movements, threats, and opportunities.\n"
        "4. **Strategic Implications** — what these trends mean for Tomorrow "
        "Group's positioning, and any recommended strategic responses.\n"
        "5. **Watch List** — 2-3 things to monitor closely next week.\n"
        "6. **Content Strategy Priorities** — this week's priority topics, "
        "angles, and formats. For each: who should post (MD, team, company page), "
        "what platform, and what format (video, carousel, article, etc.).\n\n"
        "Keep it under 1200 words. Use markdown formatting. Be analytical, "
        "not just descriptive — interpret the signals."
    )

    digest = _call_claude(system, user)
    console.print(f"[green]Digest: Weekly Summary generated ({len(digest)} chars)[/green]")
    return digest


def generate_monthly_board_report() -> str:
    """Monthly Board Report — formatted for leadership presentation."""
    console.print("[bold cyan]Digest: Generating Monthly Board Report...[/bold cyan]")

    reports = _fetch_recent_reports(hours=30 * 24)
    context = _format_reports_for_prompt(reports)

    system = (
        "You are the MCSA Intelligence Digest system for the Tomorrow Group, "
        "a UK marketing and advertising group with five agencies: Found, SEED, "
        "Braidr, Disrupt, and Culture3. You produce board-level intelligence "
        "reports suitable for leadership presentations."
    )

    user = (
        "Below are all MCSA surveillance reports generated in the last 30 days "
        "across every Tomorrow Group agency.\n\n"
        f"{context}\n\n"
        "Write a Monthly Board Report with the following structure:\n"
        "1. **Competitive Landscape Summary** — high-level view of the market "
        "this month. What is the overall competitive temperature?\n"
        "2. **Key Movements by Agency** — for each Tomorrow agency, summarise "
        "the most significant competitor actions, wins, hires, and positioning "
        "changes observed.\n"
        "3. **Threat Assessment** — rank the top 3 competitive threats across "
        "the group and explain the risk level (Low/Medium/High) with rationale.\n"
        "4. **Opportunity Identification** — gaps or weaknesses in competitor "
        "positioning that Tomorrow agencies could exploit.\n"
        "5. **Strategic Recommendations** — 3-5 actionable recommendations for "
        "the board, each with a clear owner (agency or group level).\n"
        "6. **MCSA System Performance** — how many reports were generated, "
        f"coverage across agencies ({len(reports)} reports ingested this month).\n\n"
        "Keep it under 1500 words. Use markdown formatting. The tone should be "
        "authoritative and suitable for a board presentation — lead with insight, "
        "not data."
    )

    digest = _call_claude(system, user)
    console.print(f"[green]Digest: Board Report generated ({len(digest)} chars)[/green]")
    return digest


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------

_DIGEST_TITLES = {
    "morning": "MCSA Morning Brief",
    "weekly": "MCSA Weekly Executive Summary",
    "monthly": "MCSA Monthly Board Report",
}


def deliver_digest(digest_type: str, digest_content: str) -> bool:
    """Format a digest for Slack using Block Kit and deliver via webhook.

    Args:
        digest_type: One of "morning", "weekly", "monthly".
        digest_content: The raw markdown digest text.

    Returns:
        True if delivered successfully, False otherwise.
    """
    if not SLACK_MCSA_ENABLED:
        console.print("[yellow]Digest: Slack delivery disabled (SLACK_MCSA_ENABLED=false)[/yellow]")
        return False

    webhook_url = SLACK_MCSA_WEBHOOK_URL
    if not webhook_url:
        console.print("[yellow]Digest: no SLACK_MCSA_WEBHOOK_URL configured[/yellow]")
        return False

    title = _DIGEST_TITLES.get(digest_type, f"MCSA {digest_type.title()} Digest")
    mrkdwn_body = _md_to_mrkdwn(digest_content)

    # Build Block Kit payload with header
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": title, "emoji": True},
        },
        {"type": "divider"},
    ]

    # Chunk body into 3000-char sections (Slack block limit)
    chunks = _chunk_text(mrkdwn_body, max_len=3000)
    for chunk in chunks:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": chunk},
        })

    # Context footer
    now = datetime.now().strftime("%A %d %B %Y, %H:%M")
    blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": f":robot_face: MCSA v1.0 | {title} | {now}",
            }
        ],
    })

    fallback = digest_content[:300].replace("*", "").replace("_", "")
    payload = {"text": fallback, "blocks": blocks}

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                console.print(f"[green]Digest: {title} delivered to Slack[/green]")
                return True
            else:
                console.print(f"[red]Digest: Slack HTTP {resp.status}[/red]")
                return False
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        console.print(f"[red]Digest: Slack HTTP {e.code} — {body}[/red]")
        return False
    except Exception as e:
        console.print(f"[red]Digest: Slack delivery failed — {e}[/red]")
        return False


def _chunk_text(text: str, max_len: int = 3000) -> list[str]:
    """Split text into chunks, breaking at newlines when possible."""
    if len(text) <= max_len:
        return [text]

    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, max_len)
        if cut <= 0:
            cut = max_len
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    return chunks


# ---------------------------------------------------------------------------
# Convenience runner
# ---------------------------------------------------------------------------

_GENERATORS = {
    "morning": generate_morning_brief,
    "weekly": generate_weekly_summary,
    "monthly": generate_monthly_board_report,
}


async def run_digest(digest_type: str) -> str:
    """Generate and deliver a digest. Returns the digest content.

    Args:
        digest_type: One of "morning", "weekly", "monthly".

    Returns:
        The generated digest markdown string (empty string on failure).
    """
    if digest_type not in _GENERATORS:
        console.print(
            f"[red]Digest: unknown type '{digest_type}'. "
            f"Must be one of: {', '.join(_GENERATORS.keys())}[/red]"
        )
        return ""

    console.print(f"\n[bold]{'=' * 60}[/bold]")
    console.print(f"[bold cyan]MCSA Digest: {digest_type.title()}[/bold cyan]")
    console.print(f"[bold]{'=' * 60}[/bold]\n")

    generator = _GENERATORS[digest_type]
    digest = generator()

    if not digest:
        console.print("[red]Digest: generation returned empty — skipping delivery[/red]")
        return ""

    deliver_digest(digest_type, digest)

    # Email delivery (gracefully skips if RESEND_API_KEY not set)
    from .email_delivery import send_digest_email
    send_digest_email(digest_type, digest)

    return digest
