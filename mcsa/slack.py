"""MCSA Slack delivery — POST formatted reports to Slack via incoming webhooks.

Separate from the DJ agent's Slack integration by design. MCSA uses its own
Slack app + webhook URLs so the two systems can be developed independently
and merged later if desired.
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error
from rich.console import Console

from .config import SLACK_MCSA_ENABLED, get_slack_webhook

console = Console()


def deliver_to_slack(
    agency_name: str,
    module: str,
    cadence: str,
    slack_content: str,
) -> bool:
    """Post a pre-formatted Slack message to the appropriate agency channel.

    Args:
        agency_name: Agency this report belongs to (e.g. "Found").
        module: Module name (e.g. "linkedin", "industry").
        cadence: "daily", "weekly", or "monthly".
        slack_content: Already-formatted mrkdwn content from formatter.py.

    Returns:
        True if delivered successfully, False otherwise.
    """
    if not SLACK_MCSA_ENABLED:
        return False

    webhook_url = get_slack_webhook(agency_name)
    if not webhook_url:
        console.print(f"[yellow]  Slack: no webhook for {agency_name}, skipping[/yellow]")
        return False

    # Slack incoming webhooks accept a JSON payload with a "text" field.
    # For richer formatting, use blocks. We send both for compatibility:
    # - "text" is the fallback (notifications, mobile previews)
    # - "blocks" carries the formatted mrkdwn content
    payload = _build_payload(agency_name, module, cadence, slack_content)

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
                console.print(f"[green]  Slack: {module} delivered for {agency_name}[/green]")
                return True
            else:
                console.print(f"[red]  Slack: HTTP {resp.status} for {agency_name}/{module}[/red]")
                return False
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        console.print(f"[red]  Slack: HTTP {e.code} — {body}[/red]")
        return False
    except Exception as e:
        console.print(f"[red]  Slack: delivery failed — {e}[/red]")
        return False


def _build_payload(
    agency_name: str, module: str, cadence: str, slack_content: str
) -> dict:
    """Build the Slack webhook JSON payload using Block Kit.

    Uses a section block for the main content and a context block for metadata.
    Slack's mrkdwn block limit is 3000 chars, so we chunk if needed.
    """
    # Split content into chunks that fit Slack's 3000-char section limit
    chunks = _chunk_text(slack_content, max_len=3000)

    blocks: list[dict] = []
    for chunk in chunks:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": chunk},
        })

    # Context footer
    blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": f":robot_face: MCSA v1.0 | {agency_name} | {cadence} | {module}",
            }
        ],
    })

    # Plain-text fallback for notifications
    fallback = slack_content[:300].replace("*", "").replace("_", "")

    return {
        "text": fallback,
        "blocks": blocks,
    }


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
        # Find last newline before the limit
        cut = remaining.rfind("\n", 0, max_len)
        if cut <= 0:
            cut = max_len
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    return chunks
