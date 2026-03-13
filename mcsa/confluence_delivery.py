"""MCSA Confluence delivery — publish formatted reports to Confluence via REST API.

Uses urllib.request with Basic Auth (no extra dependencies). Creates or updates
pages under a configurable parent page so all MCSA reports live in one Confluence
space hierarchy.

Env vars (all optional — delivery is silently skipped if not configured):
    CONFLUENCE_URL           — e.g. https://yoursite.atlassian.net
    CONFLUENCE_USER          — e.g. user@company.com
    CONFLUENCE_API_TOKEN     — Atlassian API token
    CONFLUENCE_SPACE_KEY     — e.g. MCSA
    CONFLUENCE_PARENT_PAGE_ID — numeric page ID for the parent container page
"""
from __future__ import annotations

import base64
import json
import re
import urllib.request
import urllib.error
from datetime import datetime

from rich.console import Console

from .config import (
    CONFLUENCE_URL,
    CONFLUENCE_USER,
    CONFLUENCE_API_TOKEN,
    CONFLUENCE_SPACE_KEY,
    CONFLUENCE_PARENT_PAGE_ID,
    CONFLUENCE_ENABLED,
)

console = Console()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def deliver_to_confluence(
    agency_name: str,
    module: str,
    cadence: str,
    content: str,
) -> bool:
    """Publish a report to Confluence, creating or updating the page.

    Args:
        agency_name: Agency this report belongs to (e.g. "Found").
        module: Module name (e.g. "linkedin", "industry").
        cadence: "daily", "weekly", or "monthly".
        content: Confluence-formatted markdown from formatter.format_confluence().

    Returns:
        True if published successfully, False otherwise.
    """
    if not CONFLUENCE_ENABLED:
        return False

    date_str = datetime.now().strftime("%Y-%m-%d")
    title = f"MCSA | {agency_name} | {module} | {cadence} | {date_str}"

    storage_body = _wiki_to_storage(content)

    try:
        existing = _find_page_by_title(title)
        if existing:
            page_id = existing["id"]
            version = existing["version"]["number"] + 1
            _update_page(page_id, title, storage_body, version)
            console.print(f"[green]  Confluence: updated '{title}' (v{version})[/green]")
        else:
            _create_page(title, storage_body)
            console.print(f"[green]  Confluence: created '{title}'[/green]")
        return True

    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        console.print(f"[red]  Confluence: HTTP {e.code} — {body}[/red]")
        return False
    except Exception as e:
        console.print(f"[red]  Confluence: delivery failed — {e}[/red]")
        return False


# ---------------------------------------------------------------------------
# Wiki markdown → Confluence storage format (XHTML)
# ---------------------------------------------------------------------------

def _wiki_to_storage(content: str) -> str:
    """Convert Confluence wiki markdown to Confluence storage format (XHTML).

    Handles: headers, bold, italic, lists, links, tables, horizontal rules,
    blockquotes, and paragraphs.
    """
    lines = content.split("\n")
    result: list[str] = []
    in_list = False
    in_table = False
    in_blockquote = False
    i = 0

    while i < len(lines):
        line = lines[i]

        # Horizontal rule
        if re.match(r"^\s*[-*_]{3,}\s*$", line):
            if in_list:
                result.append("</ul>")
                in_list = False
            if in_table:
                result.append("</tbody></table>")
                in_table = False
            if in_blockquote:
                result.append("</blockquote>")
                in_blockquote = False
            result.append("<hr />")
            i += 1
            continue

        # Headers: # Title -> <h1>Title</h1>
        header_match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if header_match:
            _close_blocks(result, in_list, in_table, in_blockquote)
            in_list = in_table = in_blockquote = False
            level = len(header_match.group(1))
            text = _inline_format(header_match.group(2).strip())
            result.append(f"<h{level}>{text}</h{level}>")
            i += 1
            continue

        # Table rows: | a | b |
        if line.strip().startswith("|") and line.strip().endswith("|"):
            # Skip separator rows like |---|---|
            if re.match(r"^\s*\|[\s\-:|]+\|\s*$", line):
                i += 1
                continue

            if in_list:
                result.append("</ul>")
                in_list = False
            if in_blockquote:
                result.append("</blockquote>")
                in_blockquote = False

            cells = [c.strip() for c in line.strip().strip("|").split("|")]

            if not in_table:
                # Check if next line is a separator (header row)
                is_header = (
                    i + 1 < len(lines)
                    and re.match(r"^\s*\|[\s\-:|]+\|\s*$", lines[i + 1])
                )
                result.append("<table><tbody>")
                in_table = True

                if is_header:
                    cell_tag = "th"
                else:
                    cell_tag = "td"
            else:
                cell_tag = "td"

            row_cells = "".join(
                f"<{cell_tag}>{_inline_format(c)}</{cell_tag}>" for c in cells
            )
            result.append(f"<tr>{row_cells}</tr>")
            i += 1
            continue

        # Close table if we were in one and this line isn't a table row
        if in_table:
            result.append("</tbody></table>")
            in_table = False

        # Unordered list: - item or * item
        list_match = re.match(r"^\s*[-*]\s+(.+)$", line)
        if list_match:
            if in_blockquote:
                result.append("</blockquote>")
                in_blockquote = False
            if not in_list:
                result.append("<ul>")
                in_list = True
            item_text = _inline_format(list_match.group(1))
            result.append(f"<li>{item_text}</li>")
            i += 1
            continue

        # Close list if we were in one
        if in_list:
            result.append("</ul>")
            in_list = False

        # Blockquote: > text
        bq_match = re.match(r"^>\s*(.*)", line)
        if bq_match:
            if not in_blockquote:
                result.append("<blockquote>")
                in_blockquote = True
            bq_text = _inline_format(bq_match.group(1))
            result.append(f"<p>{bq_text}</p>")
            i += 1
            continue

        if in_blockquote:
            result.append("</blockquote>")
            in_blockquote = False

        # Empty lines
        if not line.strip():
            i += 1
            continue

        # Regular paragraph
        result.append(f"<p>{_inline_format(line)}</p>")
        i += 1

    # Close any open blocks
    _close_blocks(result, in_list, in_table, in_blockquote)

    return "\n".join(result)


def _close_blocks(
    result: list[str], in_list: bool, in_table: bool, in_blockquote: bool
) -> None:
    """Close any open block-level elements."""
    if in_list:
        result.append("</ul>")
    if in_table:
        result.append("</tbody></table>")
    if in_blockquote:
        result.append("</blockquote>")


def _inline_format(text: str) -> str:
    """Convert inline markdown to HTML: bold, italic, links."""
    # Links: [text](url) -> <a href="url">text</a>
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)

    # Bold+italic: ***text*** -> <strong><em>text</em></strong>
    text = re.sub(r"\*{3}([^*]+)\*{3}", r"<strong><em>\1</em></strong>", text)

    # Bold: **text** -> <strong>text</strong>
    text = re.sub(r"\*{2}([^*]+)\*{2}", r"<strong>\1</strong>", text)

    # Italic: *text* -> <em>text</em>  (but not inside tags)
    text = re.sub(r"(?<![<\w/])\*([^*]+)\*(?![>])", r"<em>\1</em>", text)

    # Inline code: `text` -> <code>text</code>
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)

    return text


# ---------------------------------------------------------------------------
# Confluence REST API helpers
# ---------------------------------------------------------------------------

def _auth_header() -> str:
    """Build the Basic auth header value."""
    creds = f"{CONFLUENCE_USER}:{CONFLUENCE_API_TOKEN}"
    encoded = base64.b64encode(creds.encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


def _api_request(
    method: str,
    path: str,
    body: dict | None = None,
) -> dict:
    """Make an authenticated request to the Confluence REST API.

    Args:
        method: HTTP method (GET, POST, PUT).
        path: API path, e.g. /wiki/rest/api/content.
        body: JSON body for POST/PUT requests.

    Returns:
        Parsed JSON response.
    """
    url = f"{CONFLUENCE_URL.rstrip('/')}{path}"

    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": _auth_header(),
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method=method,
    )

    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _find_page_by_title(title: str) -> dict | None:
    """Search for an existing page by exact title in the configured space.

    Returns the page dict (with id and version) if found, None otherwise.
    """
    encoded_title = urllib.request.quote(title, safe="")
    path = (
        f"/wiki/rest/api/content"
        f"?title={encoded_title}"
        f"&spaceKey={CONFLUENCE_SPACE_KEY}"
        f"&expand=version"
    )

    response = _api_request("GET", path)
    results = response.get("results", [])
    if results:
        return results[0]
    return None


def _create_page(title: str, storage_body: str) -> dict:
    """Create a new Confluence page under the parent page."""
    payload: dict = {
        "type": "page",
        "title": title,
        "space": {"key": CONFLUENCE_SPACE_KEY},
        "body": {
            "storage": {
                "value": storage_body,
                "representation": "storage",
            }
        },
    }

    if CONFLUENCE_PARENT_PAGE_ID:
        payload["ancestors"] = [{"id": CONFLUENCE_PARENT_PAGE_ID}]

    return _api_request("POST", "/wiki/rest/api/content", payload)


def _update_page(page_id: str, title: str, storage_body: str, version: int) -> dict:
    """Update an existing Confluence page."""
    payload = {
        "type": "page",
        "title": title,
        "version": {"number": version},
        "body": {
            "storage": {
                "value": storage_body,
                "representation": "storage",
            }
        },
    }

    return _api_request("PUT", f"/wiki/rest/api/content/{page_id}", payload)
