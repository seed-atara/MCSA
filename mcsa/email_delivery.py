"""MCSA Email Delivery — send digests and alerts via Resend REST API.

Uses urllib.request (no extra dependencies) to POST to Resend's API.
Gracefully skips if RESEND_API_KEY is not configured.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
import urllib.error
from datetime import datetime

from rich.console import Console

console = Console()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
FROM_EMAIL = os.getenv("FROM_EMAIL", "MCSA Intelligence <mcsa@yourdomain.com>")
CC_EMAILS = os.getenv("CC_EMAILS", "")

_RESEND_ENDPOINT = "https://api.resend.com/emails"

# ---------------------------------------------------------------------------
# Severity colour coding for alert emails
# ---------------------------------------------------------------------------

_SEVERITY_COLORS = {
    "high": "#ff4444",
    "medium": "#ff9900",
    "low": "#888888",
}

# ---------------------------------------------------------------------------
# Dark-themed HTML template
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  body {{
    margin: 0;
    padding: 0;
    background-color: #0d1117;
    color: #e6edf3;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    font-size: 15px;
    line-height: 1.6;
  }}
  .container {{
    max-width: 680px;
    margin: 0 auto;
    padding: 32px 24px;
  }}
  .header {{
    border-bottom: 2px solid #00d4ff;
    padding-bottom: 16px;
    margin-bottom: 24px;
  }}
  .header h1 {{
    margin: 0;
    font-size: 22px;
    color: #00d4ff;
    font-weight: 600;
  }}
  .header .date {{
    margin-top: 4px;
    font-size: 13px;
    color: #8b949e;
  }}
  .content {{
    background-color: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 24px;
    margin-bottom: 24px;
  }}
  .content h1 {{ font-size: 20px; color: #00d4ff; margin-top: 24px; margin-bottom: 8px; }}
  .content h2 {{ font-size: 17px; color: #58a6ff; margin-top: 20px; margin-bottom: 8px; }}
  .content h3 {{ font-size: 15px; color: #79c0ff; margin-top: 16px; margin-bottom: 6px; }}
  .content p {{ margin: 8px 0; }}
  .content ul, .content ol {{ padding-left: 24px; margin: 8px 0; }}
  .content li {{ margin: 4px 0; }}
  .content a {{ color: #58a6ff; text-decoration: none; }}
  .content a:hover {{ text-decoration: underline; }}
  .content code {{
    background-color: #0d1117;
    border: 1px solid #30363d;
    border-radius: 4px;
    padding: 2px 6px;
    font-size: 13px;
    font-family: 'SFMono-Regular', Consolas, monospace;
  }}
  .content pre {{
    background-color: #0d1117;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 16px;
    overflow-x: auto;
    font-size: 13px;
    font-family: 'SFMono-Regular', Consolas, monospace;
  }}
  .content pre code {{
    background: none;
    border: none;
    padding: 0;
  }}
  .footer {{
    text-align: center;
    font-size: 12px;
    color: #484f58;
    padding-top: 16px;
    border-top: 1px solid #21262d;
  }}
  .alert-card {{
    background-color: #0d1117;
    border-left: 4px solid;
    border-radius: 4px;
    padding: 12px 16px;
    margin: 12px 0;
  }}
  .alert-card .alert-title {{
    font-weight: 600;
    font-size: 15px;
    margin-bottom: 4px;
  }}
  .alert-card .alert-meta {{
    font-size: 12px;
    color: #8b949e;
    margin-bottom: 8px;
  }}
  .alert-card .alert-detail {{
    font-size: 14px;
    color: #c9d1d9;
  }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>{title}</h1>
    <div class="date">{date}</div>
  </div>
  <div class="content">
    {body}
  </div>
  <div class="footer">
    MCSA v1.0 &mdash; Market &amp; Competitor Surveillance Agent &mdash; Tomorrow Group
  </div>
</div>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Markdown to HTML converter
# ---------------------------------------------------------------------------


def _md_to_html(text: str) -> str:
    """Convert simple markdown to HTML.

    Handles: headers (h1-h3), bold, italic, links, unordered/ordered lists,
    code blocks (fenced), inline code, and paragraphs.
    """
    if not text:
        return ""

    lines = text.split("\n")
    html_lines: list[str] = []
    in_code_block = False
    in_ul = False
    in_ol = False

    def _close_list() -> None:
        nonlocal in_ul, in_ol
        if in_ul:
            html_lines.append("</ul>")
            in_ul = False
        if in_ol:
            html_lines.append("</ol>")
            in_ol = False

    for line in lines:
        # Fenced code blocks
        if line.strip().startswith("```"):
            if in_code_block:
                html_lines.append("</code></pre>")
                in_code_block = False
            else:
                _close_list()
                html_lines.append("<pre><code>")
                in_code_block = True
            continue

        if in_code_block:
            html_lines.append(_escape_html(line))
            continue

        stripped = line.strip()

        # Empty line
        if not stripped:
            _close_list()
            continue

        # Headers
        if stripped.startswith("### "):
            _close_list()
            html_lines.append(f"<h3>{_inline_format(stripped[4:])}</h3>")
            continue
        if stripped.startswith("## "):
            _close_list()
            html_lines.append(f"<h2>{_inline_format(stripped[3:])}</h2>")
            continue
        if stripped.startswith("# "):
            _close_list()
            html_lines.append(f"<h1>{_inline_format(stripped[2:])}</h1>")
            continue

        # Unordered list
        ul_match = re.match(r"^[\-\*]\s+(.+)$", stripped)
        if ul_match:
            if in_ol:
                html_lines.append("</ol>")
                in_ol = False
            if not in_ul:
                html_lines.append("<ul>")
                in_ul = True
            html_lines.append(f"<li>{_inline_format(ul_match.group(1))}</li>")
            continue

        # Ordered list
        ol_match = re.match(r"^\d+[\.\)]\s+(.+)$", stripped)
        if ol_match:
            if in_ul:
                html_lines.append("</ul>")
                in_ul = False
            if not in_ol:
                html_lines.append("<ol>")
                in_ol = True
            html_lines.append(f"<li>{_inline_format(ol_match.group(1))}</li>")
            continue

        # Regular paragraph
        _close_list()
        html_lines.append(f"<p>{_inline_format(stripped)}</p>")

    # Close any unclosed blocks
    if in_code_block:
        html_lines.append("</code></pre>")
    _close_list()

    return "\n".join(html_lines)


def _inline_format(text: str) -> str:
    """Apply inline markdown formatting: bold, italic, links, inline code."""
    # Inline code (before bold/italic to avoid conflicts)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    # Links: [text](url)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    # Bold: **text** or __text__
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"__(.+?)__", r"<strong>\1</strong>", text)
    # Italic: *text* or _text_ (careful not to match inside words for _)
    text = re.sub(r"(?<!\w)\*(.+?)\*(?!\w)", r"<em>\1</em>", text)
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"<em>\1</em>", text)
    return text


def _escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ---------------------------------------------------------------------------
# Resend API helper
# ---------------------------------------------------------------------------


def _send_via_resend(
    to: list[str],
    subject: str,
    html_body: str,
) -> bool:
    """POST an email via the Resend REST API.

    Returns True on success, False otherwise.
    """
    if not RESEND_API_KEY:
        return False

    payload = {
        "from": FROM_EMAIL,
        "to": to,
        "subject": subject,
        "html": html_body,
    }

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            _RESEND_ENDPOINT,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {RESEND_API_KEY}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status in (200, 201):
                return True
            else:
                console.print(f"[red]Email: Resend HTTP {resp.status}[/red]")
                return False
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        console.print(f"[red]Email: Resend HTTP {e.code} — {body}[/red]")
        return False
    except Exception as e:
        console.print(f"[red]Email: Resend delivery failed — {e}[/red]")
        return False


def _resolve_recipients(recipients: list[str] | None) -> list[str]:
    """Return explicit recipients or fall back to CC_EMAILS env var."""
    if recipients:
        return recipients
    if CC_EMAILS:
        return [e.strip() for e in CC_EMAILS.split(",") if e.strip()]
    return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_DIGEST_SUBJECTS = {
    "morning": "MCSA Morning Brief",
    "weekly": "MCSA Weekly Executive Summary",
    "monthly": "MCSA Monthly Board Report",
}


def send_digest_email(
    digest_type: str,
    digest_content: str,
    recipients: list[str] | None = None,
) -> bool:
    """Send a digest as a styled HTML email via Resend.

    Args:
        digest_type: One of "morning", "weekly", "monthly".
        digest_content: Raw markdown digest text.
        recipients: Email addresses. Defaults to CC_EMAILS env var.

    Returns:
        True if sent successfully, False otherwise.
    """
    if not RESEND_API_KEY:
        console.print("[yellow]Email: RESEND_API_KEY not set — skipping email delivery[/yellow]")
        return False

    to = _resolve_recipients(recipients)
    if not to:
        console.print("[yellow]Email: no recipients configured — skipping email delivery[/yellow]")
        return False

    date_str = datetime.now().strftime("%A %d %B %Y")
    label = _DIGEST_SUBJECTS.get(digest_type, f"MCSA {digest_type.title()} Digest")
    subject = f"{label} — {date_str}"

    body_html = _md_to_html(digest_content)
    full_html = _HTML_TEMPLATE.format(
        title=label,
        date=date_str,
        body=body_html,
    )

    ok = _send_via_resend(to, subject, full_html)
    if ok:
        console.print(f"[green]Email: {label} sent to {len(to)} recipient(s)[/green]")
    return ok


def send_alert_email(
    alerts: list[dict],
    recipients: list[str] | None = None,
) -> bool:
    """Send alert notifications as a styled HTML email via Resend.

    Args:
        alerts: List of alert dicts with keys: severity, title,
                agency_name, alert_type, detail.
        recipients: Email addresses. Defaults to CC_EMAILS env var.

    Returns:
        True if sent successfully, False otherwise.
    """
    if not RESEND_API_KEY:
        console.print("[yellow]Email: RESEND_API_KEY not set — skipping alert email[/yellow]")
        return False

    if not alerts:
        return False

    to = _resolve_recipients(recipients)
    if not to:
        console.print("[yellow]Email: no recipients configured — skipping alert email[/yellow]")
        return False

    date_str = datetime.now().strftime("%A %d %B %Y")
    count = len(alerts)
    subject = f"MCSA Alert: {count} new signal{'s' if count != 1 else ''} — {date_str}"

    # Build alert cards HTML
    cards: list[str] = []
    for alert in alerts:
        severity = alert.get("severity", "medium")
        color = _SEVERITY_COLORS.get(severity, "#888888")
        severity_label = severity.upper()
        title = _escape_html(alert.get("title", "Untitled signal"))
        agency = _escape_html(alert.get("agency_name", "Unknown"))
        alert_type = _escape_html(alert.get("alert_type", "unknown"))
        detail = _escape_html(alert.get("detail", ""))
        # Preserve newlines in detail
        detail = detail.replace("\n", "<br>")

        cards.append(
            f'<div class="alert-card" style="border-left-color: {color};">'
            f'<div class="alert-title" style="color: {color};">'
            f"[{severity_label}] {title}</div>"
            f'<div class="alert-meta">{agency} &mdash; {alert_type}</div>'
            f'<div class="alert-detail">{detail}</div>'
            f"</div>"
        )

    body_html = "\n".join(cards)
    full_html = _HTML_TEMPLATE.format(
        title=f"MCSA Alert — {count} signal{'s' if count != 1 else ''} detected",
        date=date_str,
        body=body_html,
    )

    ok = _send_via_resend(to, subject, full_html)
    if ok:
        console.print(f"[green]Email: {count} alert(s) sent to {len(to)} recipient(s)[/green]")
    return ok
