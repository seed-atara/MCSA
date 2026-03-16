"""MCSA output formatting — Slack alerts and Confluence documents.

Each formatter takes a report markdown string and returns a formatted version
for the target channel.
"""
from __future__ import annotations

import re
from datetime import datetime


# ---------------------------------------------------------------------------
# Markdown → Slack mrkdwn conversion
# ---------------------------------------------------------------------------

def _md_to_mrkdwn(text: str) -> str:
    """Convert standard markdown to Slack mrkdwn.

    Slack mrkdwn differences from standard markdown:
    - Bold: *text* (not **text**)
    - Italic: _text_ (same)
    - Headers: not supported — convert to *bold* lines
    - Links: <url|text> (not [text](url))
    - Blockquotes: > (same)
    - Code: ``` (same)
    - Horizontal rules: not rendered — remove or leave blank
    - Tables: not supported — convert to plain text
    """
    lines = text.split("\n")
    result = []
    in_code_block = False

    for line in lines:
        # Don't touch code blocks
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            result.append(line)
            continue
        if in_code_block:
            result.append(line)
            continue

        # Horizontal rules → empty line (Slack doesn't render ---)
        if re.match(r"^\s*[-*_]{3,}\s*$", line):
            result.append("")
            continue

        # Headers → *bold* lines
        header_match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if header_match:
            heading_text = header_match.group(2).strip()
            # Remove any existing bold markers from heading text
            heading_text = heading_text.replace("**", "")
            result.append(f"\n*{heading_text}*")
            continue

        # Table separator rows → skip
        if re.match(r"^\s*\|[\s\-:|]+\|\s*$", line):
            continue

        # Table rows → plain text with bullet
        if line.strip().startswith("|") and line.strip().endswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            # Skip if all cells are empty/separator
            if any(c.strip("-: ") for c in cells):
                row_text = "  ".join(c for c in cells if c.strip())
                result.append(f"• {row_text}")
            continue

        # Inline conversions (only on non-header, non-code lines)
        converted = _convert_inline(line)
        result.append(converted)

    return "\n".join(result)


def _convert_inline(line: str) -> str:
    """Convert inline markdown elements to Slack mrkdwn."""
    # Links: [text](url) → <url|text>
    line = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", line)

    # Bold+italic: ***text*** → *_text_*
    line = re.sub(r"\*{3}([^*]+)\*{3}", r"*_\1_*", line)

    # Bold: **text** → *text*
    line = re.sub(r"\*{2}([^*]+)\*{2}", r"*\1*", line)

    return line


# ---------------------------------------------------------------------------
# Slack formatting
# ---------------------------------------------------------------------------

def format_slack_daily(agency_name: str, module: str, report: str) -> str:
    """Format a daily alert for Slack delivery."""
    today = datetime.now().strftime("%A %d %B %Y")
    icon = _module_icon(module)

    header = f"{icon} *{_module_title(module)} — {agency_name}*\n_{today}_"

    body = _md_to_mrkdwn(report)

    # Truncate for Slack (4000 char limit per message)
    if len(body) > 3500:
        body = body[:3500] + "\n\n_...report truncated. Full version on Confluence._"

    return f"{header}\n\n{body}"


def format_slack_summary(agency_name: str, module: str, report: str) -> str:
    """Format a weekly/monthly summary for Slack — shorter than Confluence version."""
    today = datetime.now().strftime("%A %d %B %Y")
    icon = _module_icon(module)

    header = f"{icon} *{_module_title(module)} — {agency_name}*\n_{today}_"

    # Truncate first, then convert — avoids cutting mid-conversion
    summary = report[:2000]
    if len(report) > 2000:
        summary += "\n\n_Full report available on Confluence._"

    body = _md_to_mrkdwn(summary)

    return f"{header}\n\n{body}"


# ---------------------------------------------------------------------------
# Confluence formatting
# ---------------------------------------------------------------------------

def format_confluence(agency_name: str, module: str, cadence: str, report: str) -> str:
    """Format a report for Confluence — standard markdown with metadata header."""
    now = datetime.now()
    date_str = now.strftime("%d %B %Y")
    time_str = now.strftime("%H:%M")

    metadata = (
        f"| Field | Value |\n"
        f"|-------|-------|\n"
        f"| **Agency** | {agency_name} |\n"
        f"| **Module** | {_module_title(module)} |\n"
        f"| **Cadence** | {cadence.title()} |\n"
        f"| **Generated** | {date_str} at {time_str} |\n"
        f"| **Classification** | Internal — not for client distribution |\n"
        f"| **System** | MCSA v1.0 — Tomorrow AI Operating System |\n"
    )

    title = f"# {_module_title(module)} — {agency_name}\n\n"

    confidence_note = (
        "\n\n---\n\n"
        "> **Confidence note:** This report was generated by the Market & Competitor "
        "Surveillance Agent using publicly available data. All competitive claims "
        "require human editorial review before use in external communications.\n"
    )

    return f"{title}{metadata}\n\n---\n\n{report}{confidence_note}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _module_icon(module: str) -> str:
    return {
        "registry": "[REG]",
        "linkedin": "[LI]",
        "industry": "[IND]",
        "diff": "[DIFF]",
        "website": "[WEB]",
        "content_strategy": "[CS]",
        "synthesis": "[SYN]",
    }.get(module, "[?]")


def _module_title(module: str) -> str:
    return {
        "registry": "Competitor Registry Update",
        "linkedin": "LinkedIn Intelligence",
        "industry": "Industry Publications & Key People",
        "diff": "Competitive DIFF Analysis",
        "website": "Website Pattern Analysis",
        "content_strategy": "Content Strategy",
        "synthesis": "Cross-Agency Trend Synthesis",
    }.get(module, module.title())
