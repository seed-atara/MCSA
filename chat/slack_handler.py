"""MCSA Slack slash command handler — /mcsa conversational interface.

Receives slash commands from Slack, runs the same Claude tool-use loop as the
web chat, and posts the response back via response_url.

Slack requires an immediate response (<3s), so we acknowledge instantly and
process in a background thread, posting results back when done.

Setup:
    1. Create a Slack app with a slash command /mcsa pointing to:
       https://<your-domain>/slack/command
    2. Set SLACK_MCSA_SIGNING_SECRET env var for request verification
    3. Mount this router in your FastAPI app
"""
from __future__ import annotations

import os
import json
import hmac
import hashlib
import time
import threading
import urllib.request
import urllib.error
from datetime import datetime

from fastapi import APIRouter, Request, Response
import anthropic

# ---------------------------------------------------------------------------
# Config — reuse clients from parent app to avoid duplicate init issues
# ---------------------------------------------------------------------------

MODEL = "claude-sonnet-4-20250514"


def _get_signing_secret():
    return os.getenv("SLACK_MCSA_SIGNING_SECRET", "")


def _get_claude():
    """Get or create Anthropic client."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    return anthropic.Anthropic(api_key=api_key)


def _get_sb():
    """Get or create Supabase client."""
    from supabase import create_client
    return create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/slack", tags=["slack"])


# ---------------------------------------------------------------------------
# Tools — same as chat/app.py, reused here
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "search_reports",
        "description": (
            "Search MCSA intelligence reports. Filter by agency, module "
            "(linkedin/industry/website/diff/registry), and cadence (daily/weekly/monthly)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "agency": {"type": "string", "description": "Agency name to filter by."},
                "module": {"type": "string", "enum": ["linkedin", "industry", "website", "diff", "registry"]},
                "cadence": {"type": "string", "enum": ["daily", "weekly", "monthly"]},
                "limit": {"type": "integer", "description": "Max reports (default 5, max 10)."},
                "search_text": {"type": "string", "description": "Text to search within reports."},
            },
            "required": [],
        },
    },
    {
        "name": "get_competitor_registry",
        "description": "Get competitor list for an agency with names, websites, threat levels.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agency": {"type": "string", "description": "Agency name. Omit for all."},
            },
            "required": [],
        },
    },
    {
        "name": "get_run_history",
        "description": "Get MCSA surveillance run history — dates, agencies, duration, cost.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of recent runs (default 10)."},
            },
            "required": [],
        },
    },
    {
        "name": "compare_agencies",
        "description": "Compare latest reports across agencies for a specific module.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agencies": {"type": "array", "items": {"type": "string"}},
                "module": {"type": "string", "enum": ["linkedin", "industry", "website", "diff", "registry"]},
            },
            "required": ["module"],
        },
    },
    {
        "name": "get_alerts",
        "description": (
            "Get recent alerts and trend signals detected by the MCSA alert engine. "
            "Filter by agency, severity (high/medium/low), or alert type."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "agency": {"type": "string", "description": "Agency name to filter by."},
                "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                "limit": {"type": "integer", "description": "Max alerts (default 10)."},
                "unacknowledged_only": {"type": "boolean", "description": "Only show unacknowledged alerts."},
            },
            "required": [],
        },
    },
]

SYSTEM_PROMPT = """You are the MCSA Intelligence Analyst for Tomorrow Group — a holding company
with 5 specialist agencies: Found (SEO/PPC), SEED (content/creative), Braidr (data/analytics),
Disrupt (paid media/programmatic), and Culture3 (social/influencer).

You are responding via Slack. Keep responses concise and well-formatted for Slack:
- Use *bold* for emphasis (not **bold**)
- Use _italic_ for secondary info
- Use bullet points and numbered lists
- Keep responses under 3000 characters when possible
- Be direct and actionable

You have tools to query the intelligence database. Always fetch data before answering.
When you don't have data, say so clearly."""


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

def _execute_tool(name: str, input_data: dict) -> str:
    sb = _get_sb()
    try:
        if name == "search_reports":
            return _tool_search_reports(sb, input_data)
        elif name == "get_competitor_registry":
            return _tool_get_registry(sb, input_data)
        elif name == "get_run_history":
            return _tool_get_run_history(sb, input_data)
        elif name == "compare_agencies":
            return _tool_compare_agencies(sb, input_data)
        elif name == "get_alerts":
            return _tool_get_alerts(sb, input_data)
        else:
            return f"Unknown tool: {name}"
    except Exception as e:
        return f"Tool error: {e}"


def _tool_search_reports(sb, params: dict) -> str:
    limit = min(params.get("limit", 3), 5)  # Keep small for Slack token budget
    query = sb.table("reports").select("agency_name, module, cadence, content, created_at")
    query = query.order("created_at", desc=True).limit(limit)
    if params.get("agency"):
        query = query.eq("agency_name", params["agency"])
    if params.get("module"):
        query = query.eq("module", params["module"])
    if params.get("cadence"):
        query = query.eq("cadence", params["cadence"])
    rows = query.execute()
    search_text = params.get("search_text", "").lower()
    results = []
    for r in rows.data or []:
        content = r.get("content", "")
        if search_text and search_text not in content.lower():
            continue
        # Truncate content aggressively for Slack token budget
        preview = content[:500] if len(content) > 500 else content
        results.append(
            f"### {r['agency_name']} — {r['cadence']} {r['module']} ({r['created_at'][:10]})\n{preview}"
        )
    if not results:
        return "No reports found matching your criteria."
    return f"Found {len(results)} report(s):\n\n" + "\n\n---\n\n".join(results)


def _tool_get_registry(sb, params: dict) -> str:
    query = sb.table("registries").select("agency_name, competitors, updated_at")
    if params.get("agency"):
        query = query.eq("agency_name", params["agency"])
    rows = query.execute()
    if not rows.data:
        return "No competitor registries found."
    parts = []
    for r in rows.data:
        competitors = r.get("competitors", [])
        comp_text = json.dumps(competitors, indent=2)
        parts.append(
            f"### {r['agency_name']} Registry (updated {r['updated_at'][:10]})\n"
            f"{len(competitors)} competitors:\n{comp_text}"
        )
    return "\n\n".join(parts)


def _tool_get_run_history(sb, params: dict) -> str:
    limit = min(params.get("limit", 10), 50)
    rows = sb.table("run_logs").select("*").order("created_at", desc=True).limit(limit).execute()
    if not rows.data:
        return "No run history found."
    lines = ["| Date | Cadence | Agencies | Duration | Cost |", "|------|---------|----------|----------|------|"]
    total_cost = 0
    for r in rows.data:
        cost = r.get("cost", {}).get("total_cost_usd", 0)
        total_cost += cost
        agencies = ", ".join(r.get("agencies", []))
        lines.append(
            f"| {r['created_at'][:16]} | {r['cadence']} | {agencies} | "
            f"{r.get('duration_seconds', 0):.0f}s | ${cost:.2f} |"
        )
    lines.append(f"\nTotal cost across {len(rows.data)} runs: ${total_cost:.2f}")
    return "\n".join(lines)


def _tool_compare_agencies(sb, params: dict) -> str:
    module = params["module"]
    agencies = params.get("agencies", ["Found", "SEED", "Braidr", "Disrupt", "Culture3"])
    parts = []
    for agency in agencies:
        rows = (
            sb.table("reports").select("agency_name, module, cadence, content, created_at")
            .eq("agency_name", agency).eq("module", module)
            .order("created_at", desc=True).limit(1).execute()
        )
        if rows.data:
            r = rows.data[0]
            preview = r["content"][:600]
            parts.append(f"### {r['agency_name']} — latest {module} ({r['created_at'][:10]})\n{preview}")
        else:
            parts.append(f"### {agency} — no {module} reports found")
    return "\n\n---\n\n".join(parts)


def _tool_get_alerts(sb, params: dict) -> str:
    limit = min(params.get("limit", 10), 30)
    query = sb.table("alerts").select("*").order("created_at", desc=True).limit(limit)
    if params.get("agency"):
        query = query.eq("agency_name", params["agency"])
    if params.get("severity"):
        query = query.eq("severity", params["severity"])
    if params.get("unacknowledged_only"):
        query = query.eq("acknowledged", False)
    rows = query.execute()
    if not rows.data:
        return "No alerts found."
    severity_icon = {"high": "RED", "medium": "ORANGE", "low": "WHITE"}
    parts = []
    for r in rows.data:
        icon = severity_icon.get(r["severity"], "?")
        parts.append(
            f"[{icon}] {r['title']}\n"
            f"  Agency: {r['agency_name']} | Type: {r['alert_type']} | "
            f"Severity: {r['severity']} | Date: {r['created_at'][:16]}\n"
            f"  {r['detail']}"
        )
    return f"{len(parts)} alert(s):\n\n" + "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Slack request verification
# ---------------------------------------------------------------------------

def _verify_slack_request(body: bytes, timestamp: str, signature: str) -> bool:
    """Verify the request came from Slack using signing secret."""
    secret = _get_signing_secret()
    if not secret:
        # If no signing secret configured, skip verification (dev mode)
        return True

    # Reject requests older than 5 minutes (replay protection)
    if abs(time.time() - int(timestamp)) > 300:
        return False

    sig_basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
    computed = "v0=" + hmac.new(
        secret.encode(), sig_basestring.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(computed, signature)


# ---------------------------------------------------------------------------
# Markdown → Slack mrkdwn (reuse from formatter)
# ---------------------------------------------------------------------------

def _md_to_slack(text: str) -> str:
    """Convert Claude's markdown response to Slack mrkdwn."""
    import re
    lines = text.split("\n")
    result = []
    in_code = False

    for line in lines:
        if line.strip().startswith("```"):
            in_code = not in_code
            result.append(line)
            continue
        if in_code:
            result.append(line)
            continue

        # Headers → bold
        header = re.match(r"^(#{1,6})\s+(.+)$", line)
        if header:
            heading = header.group(2).replace("**", "")
            result.append(f"\n*{heading}*")
            continue

        # Horizontal rules → empty
        if re.match(r"^\s*[-*_]{3,}\s*$", line):
            result.append("")
            continue

        # Bold: **text** → *text*
        line = re.sub(r"\*{3}([^*]+)\*{3}", r"*_\1_*", line)
        line = re.sub(r"\*{2}([^*]+)\*{2}", r"*\1*", line)

        # Links: [text](url) → <url|text>
        line = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", line)

        # Table separators → skip
        if re.match(r"^\s*\|[\s\-:|]+\|\s*$", line):
            continue

        # Table rows → plain text
        if line.strip().startswith("|") and line.strip().endswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if any(c.strip("-: ") for c in cells):
                result.append("  ".join(c for c in cells if c.strip()))
            continue

        result.append(line)

    return "\n".join(result)


# ---------------------------------------------------------------------------
# Background processing
# ---------------------------------------------------------------------------

def _process_command(text: str, user_id: str, response_url: str) -> None:
    """Run the Claude tool-use loop and post result back to Slack."""
    try:
        claude_client = _get_claude()

        messages = [{"role": "user", "content": text}]

        # Tool-use loop (max 5 rounds, same as web chat)
        for _ in range(5):
            # Retry with backoff for rate limits (429)
            response = None
            for attempt in range(3):
                try:
                    response = claude_client.messages.create(
                        model=MODEL,
                        max_tokens=2048,  # Concise for Slack
                        system=SYSTEM_PROMPT,
                        messages=messages,
                        tools=TOOLS,
                    )
                    break
                except anthropic.RateLimitError:
                    if attempt < 2:
                        import time as _time
                        _time.sleep(5 * (attempt + 1))  # 5s, 10s backoff
                    else:
                        raise

            if response is None:
                break

            has_tool_use = any(b.type == "tool_use" for b in response.content)

            if has_tool_use:
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result = _execute_tool(block.name, block.input)
                        # Truncate tool results to reduce token usage
                        if len(result) > 2000:
                            result = result[:2000] + "\n\n[...truncated for brevity]"
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})
                continue

            # Extract final text
            final_text = ""
            for block in response.content:
                if block.type == "text":
                    final_text += block.text
            break

        # Convert to Slack formatting
        slack_text = _md_to_slack(final_text)

        # Truncate if needed (Slack limit ~4000 per block)
        if len(slack_text) > 3800:
            slack_text = slack_text[:3800] + "\n\n_...response truncated_"

        # Post back to Slack via response_url
        payload = {
            "response_type": "in_channel",
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*<@{user_id}> asked:* {text}"},
                },
                {"type": "divider"},
            ],
        }

        # Chunk the response into 3000-char blocks
        remaining = slack_text
        while remaining:
            chunk = remaining[:3000]
            if len(remaining) > 3000:
                cut = chunk.rfind("\n")
                if cut > 0:
                    chunk = remaining[:cut]
                    remaining = remaining[cut:].lstrip("\n")
                else:
                    remaining = remaining[3000:]
            else:
                remaining = ""

            payload["blocks"].append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": chunk},
            })

        payload["blocks"].append({"type": "divider"})
        payload["blocks"].append({
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": f":robot_face: MCSA Intelligence Analyst | {datetime.now().strftime('%H:%M')}",
            }],
        })

        _post_to_slack(response_url, payload)

    except anthropic.RateLimitError:
        _post_to_slack(response_url, {
            "response_type": "ephemeral",
            "text": ":hourglass: MCSA is temporarily rate-limited. Please try again in 30 seconds.",
        })
    except Exception as e:
        # Log full error server-side, show clean message to user
        print(f"[MCSA Slack] Error: {e}")
        _post_to_slack(response_url, {
            "response_type": "ephemeral",
            "text": ":warning: MCSA encountered an issue processing your request. Please try again.",
        })


def _post_to_slack(url: str, payload: dict) -> None:
    """POST JSON to a Slack response_url."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15):
            pass
    except Exception as e:
        print(f"[MCSA Slack] Failed to post response: {e}")


# ---------------------------------------------------------------------------
# Slash command endpoint
# ---------------------------------------------------------------------------

@router.post("/command")
async def slack_command(request: Request):
    """Handle /mcsa slash command from Slack.

    Slack sends a form-encoded POST with:
    - text: the user's query after /mcsa
    - user_id: Slack user ID
    - response_url: URL to post delayed responses to
    - token / team_id / channel_id etc.
    """
    body = await request.body()
    form = await request.form()

    # Verify request signature
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "0")
    signature = request.headers.get("X-Slack-Signature", "")
    if not _verify_slack_request(body, timestamp, signature):
        return Response(status_code=401, content="Invalid signature")

    text = form.get("text", "").strip()
    user_id = form.get("user_id", "")
    response_url = form.get("response_url", "")

    if not text:
        return {
            "response_type": "ephemeral",
            "text": (
                "*MCSA Intelligence Analyst*\n\n"
                "Usage: `/mcsa <your question>`\n\n"
                "Examples:\n"
                "• `/mcsa what are the latest threats to Found?`\n"
                "• `/mcsa compare linkedin activity across all agencies`\n"
                "• `/mcsa any new alerts this week?`\n"
                "• `/mcsa show competitor registry for SEED`\n"
                "• `/mcsa what changed on competitor websites recently?`"
            ),
        }

    # Acknowledge immediately (Slack requires <3s response)
    # Process in background thread, post result via response_url
    thread = threading.Thread(
        target=_process_command,
        args=(text, user_id, response_url),
        daemon=True,
    )
    thread.start()

    return {
        "response_type": "in_channel",
        "text": f":hourglass_flowing_sand: _Analysing: {text}_",
    }
