"""MCSA Cross-Agency Trend Synthesis — weekly group-level intelligence.

Fetches last 7 days of reports from Supabase across all agencies,
synthesises cross-agency themes and strategic recommendations,
delivers to Slack (#mcsa-general) and saves to Supabase.

Follows the same pattern as digests.py.
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timedelta

import anthropic
from rich.console import Console

from .config import SLACK_MCSA_WEBHOOK_URL, SLACK_MCSA_ENABLED, AGENCIES
from .formatter import _md_to_mrkdwn
from .slack import deliver_to_slack
from .storage import _get_supabase, _sb_insert

console = Console()

_MODEL = "claude-sonnet-4-20250514"
_MAX_TOKENS = 6000


def _call_claude(system: str, user: str) -> str:
    """Send a synthesis prompt to Claude and return the text response."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        console.print("[red]Synthesis: ANTHROPIC_API_KEY not set[/red]")
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
        console.print(f"[red]Synthesis: Claude API error — {e}[/red]")
        return ""


def _fetch_week_reports() -> list[dict]:
    """Fetch all reports from the last 7 days across all agencies."""
    cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()

    sb = _get_supabase()
    if not sb:
        console.print("[yellow]Synthesis: Supabase not configured[/yellow]")
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
        console.print(f"[yellow]Synthesis: Supabase query failed — {e}[/yellow]")
        return []


def _group_reports(reports: list[dict]) -> str:
    """Group reports by agency and module for the synthesis prompt."""
    if not reports:
        return "(No reports found in the last 7 days.)"

    # Group by agency
    by_agency: dict[str, list[dict]] = {}
    for r in reports:
        agency = r.get("agency_name", "Unknown")
        by_agency.setdefault(agency, []).append(r)

    sections = []
    for agency, agency_reports in sorted(by_agency.items()):
        agency_section = f"# {agency}\n"
        for r in agency_reports:
            module = r.get("module", "unknown")
            cadence = r.get("cadence", "")
            created = r.get("created_at", "")[:10]
            content = r.get("content", "")
            # Cap each report to avoid token bloat
            if len(content) > 3000:
                content = content[:3000] + "\n...(truncated)"
            agency_section += f"\n## {module} ({cadence}, {created})\n{content}\n"
        sections.append(agency_section)

    return "\n\n---\n\n".join(sections)


def generate_synthesis() -> str:
    """Generate a cross-agency trend synthesis from the last 7 days of reports."""
    console.print("[bold cyan]Synthesis: Generating Cross-Agency Trend Report...[/bold cyan]")

    reports = _fetch_week_reports()
    if not reports:
        console.print("[yellow]Synthesis: No reports found — skipping[/yellow]")
        return ""

    context = _group_reports(reports)

    system = (
        "You are the MCSA Strategic Intelligence Analyst for the Tomorrow Group, "
        "a UK marketing and advertising group with five agencies: Found (SEO/PPC), "
        "SEED (content/creative), Braidr (data/analytics), Disrupt (paid media), "
        "and Culture3 (social/influencer). You produce cross-agency strategic "
        "intelligence that identifies patterns only visible at group level."
    )

    user = (
        "Below are all MCSA surveillance reports from the last 7 days, "
        f"grouped by agency and module ({len(reports)} reports total).\n\n"
        f"{context}\n\n"
        "Write a Cross-Agency Trend Synthesis with the following structure:\n\n"
        "## 1. Cross-Agency Themes\n"
        "Patterns that span multiple agencies or their competitors — e.g. "
        "industry-wide shifts, shared narrative trends, common competitor tactics. "
        "Only include themes visible across 2+ agencies.\n\n"
        "## 2. Shared Competitive Threats\n"
        "Competitors or competitive moves that threaten multiple Tomorrow agencies. "
        "Rank by urgency and include specific evidence.\n\n"
        "## 3. Knowledge Transfer Opportunities\n"
        "Where one agency's intelligence could benefit another — e.g. a competitor "
        "trend spotted in Found's data that Braidr should know about, or a content "
        "strategy working for Disrupt's competitors that SEED could adapt.\n\n"
        "## 4. Group-Level Positioning Recommendations\n"
        "Strategic recommendations that require group-level coordination — e.g. "
        "unified messaging, cross-agency campaigns, shared thought leadership.\n\n"
        "## 5. Emerging Industry Patterns\n"
        "New industry developments or weak signals that could become significant. "
        "Flag early indicators even if confidence is low.\n\n"
        "Be specific, reference actual data from the reports, and prioritise "
        "actionable insights over observations. Keep it under 1500 words."
    )

    synthesis = _call_claude(system, user)
    console.print(f"[green]Synthesis: Generated ({len(synthesis)} chars)[/green]")
    return synthesis


def deliver_synthesis(content: str) -> bool:
    """Deliver synthesis to Slack and save to Supabase."""
    if not content:
        return False

    # Save to Supabase as a report
    _sb_insert("reports", {
        "agency_name": "Tomorrow-Group",
        "module": "synthesis",
        "cadence": "weekly",
        "content": content,
    })
    console.print("[green]Synthesis: Saved to Supabase[/green]")

    # Deliver to Slack
    if not SLACK_MCSA_ENABLED:
        console.print("[yellow]Synthesis: Slack delivery disabled[/yellow]")
        return False

    webhook_url = SLACK_MCSA_WEBHOOK_URL
    if not webhook_url:
        console.print("[yellow]Synthesis: no SLACK_MCSA_WEBHOOK_URL configured[/yellow]")
        return False

    title = "MCSA Cross-Agency Trend Synthesis"
    mrkdwn_body = _md_to_mrkdwn(content)

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": title, "emoji": True},
        },
        {"type": "divider"},
    ]

    # Chunk body into 3000-char sections
    chunks = _chunk_text(mrkdwn_body, max_len=3000)
    for chunk in chunks:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": chunk},
        })

    now = datetime.now().strftime("%A %d %B %Y, %H:%M")
    blocks.append({
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": f":robot_face: MCSA v1.0 | {title} | {now}"},
        ],
    })

    fallback = content[:300].replace("*", "").replace("_", "")
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
                console.print(f"[green]Synthesis: Delivered to Slack[/green]")
                return True
            else:
                console.print(f"[red]Synthesis: Slack HTTP {resp.status}[/red]")
                return False
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        console.print(f"[red]Synthesis: Slack HTTP {e.code} — {body}[/red]")
        return False
    except Exception as e:
        console.print(f"[red]Synthesis: Slack delivery failed — {e}[/red]")
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


def _generate_agency_takeaway(agency_name: str, synthesis_text: str) -> str:
    """Generate a short per-agency takeaway from the cross-agency synthesis."""
    system = (
        f"You are a strategic intelligence analyst for {agency_name} (part of Tomorrow Group). "
        f"Be direct and actionable. No preamble."
    )
    user = (
        f"Given this cross-agency synthesis, write 3 bullet points about what this means "
        f"specifically for {agency_name}. Be direct and actionable.\n\n"
        f"SYNTHESIS:\n{synthesis_text}"
    )
    return _call_claude(system, user)


async def run_synthesis() -> str:
    """Generate and deliver a cross-agency synthesis. Returns the content."""
    console.print(f"\n[bold]{'=' * 60}[/bold]")
    console.print(f"[bold cyan]MCSA Cross-Agency Trend Synthesis[/bold cyan]")
    console.print(f"[bold]{'=' * 60}[/bold]\n")

    content = generate_synthesis()

    if not content:
        console.print("[red]Synthesis: generation returned empty — skipping delivery[/red]")
        return ""

    deliver_synthesis(content)

    # Generate per-agency takeaways
    console.print("[dim]Generating per-agency takeaways...[/dim]")
    for agency in AGENCIES:
        agency_name = agency["name"]
        try:
            agency_takeaway = _generate_agency_takeaway(agency_name, content)
            if agency_takeaway:
                header = f"*What This Means for {agency_name}*\n\n"
                deliver_to_slack(agency_name, "synthesis", "weekly", header + agency_takeaway)
                console.print(f"[green]  {agency_name}: takeaway delivered to Slack[/green]")
            else:
                console.print(f"[yellow]  {agency_name}: takeaway generation returned empty[/yellow]")
        except Exception as e:
            console.print(f"[yellow]  {agency_name}: takeaway failed — {e}[/yellow]")

    return content
