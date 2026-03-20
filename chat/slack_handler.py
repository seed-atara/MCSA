"""MCSA Slack handler — /mcsa slash command + Events API for @mentions and DMs.

Supports two interaction modes:
1. Slash command: /mcsa <question> → responds via response_url
2. Events API: @mention or DM → responds via chat.postMessage (bot token)

Both use the same Claude tool-use loop with conversation memory.

Setup:
    1. Create a Slack app with a slash command /mcsa pointing to:
       https://<your-domain>/slack/command
    2. Enable Events API with URL: https://<your-domain>/slack/events
       Subscribe to: app_mention, message.im
    3. Set env vars: SLACK_MCSA_SIGNING_SECRET, SLACK_MCSA_BOT_TOKEN
    4. Mount this router in your FastAPI app
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
from datetime import datetime, timezone

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
# Per-user conversation memory
# ---------------------------------------------------------------------------

# Max messages to include as context (user + assistant pairs)
MAX_HISTORY_MESSAGES = 10


def _load_conversation(user_id: str, channel_id: str) -> dict | None:
    """Load most recent conversation for a user+channel pair from Supabase.

    Returns the conversation row dict (with 'id', 'messages', etc.) or None.
    """
    sb = _get_sb()
    try:
        result = (
            sb.table("conversations")
            .select("*")
            .eq("user_id", user_id)
            .eq("channel_id", channel_id)
            .eq("platform", "slack")
            .order("updated_at", desc=True)
            .limit(1)
            .execute()
        )
        if result.data:
            return result.data[0]
    except Exception as e:
        print(f"[MCSA Slack] Error loading conversation: {e}")
    return None


def _save_conversation(
    user_id: str,
    user_name: str,
    channel_id: str,
    messages: list[dict],
    conversation_id: str | None = None,
    agency_filter: str | None = None,
) -> None:
    """Save or update conversation in Supabase.

    If conversation_id is provided, updates the existing row.
    Otherwise, inserts a new row with a title from the first user message.
    """
    sb = _get_sb()
    now = datetime.now(timezone.utc).isoformat()

    # Only store user/assistant text messages (strip tool_use/tool_result blocks)
    storable = _extract_storable_messages(messages)

    try:
        if conversation_id:
            sb.table("conversations").update({
                "messages": storable,
                "message_count": len(storable),
                "updated_at": now,
            }).eq("id", conversation_id).execute()
        else:
            # Auto-generate title from first user message
            title = "Slack conversation"
            for m in storable:
                if m.get("role") == "user" and isinstance(m.get("content"), str):
                    title = m["content"][:50]
                    break

            sb.table("conversations").insert({
                "user_id": user_id,
                "user_name": user_name,
                "channel_id": channel_id,
                "platform": "slack",
                "title": title,
                "messages": storable,
                "message_count": len(storable),
                "agency_filter": agency_filter,
                "created_at": now,
                "updated_at": now,
            }).execute()
    except Exception as e:
        print(f"[MCSA Slack] Error saving conversation: {e}")


def _extract_storable_messages(messages: list[dict]) -> list[dict]:
    """Extract only plain user/assistant text messages for storage.

    Filters out tool_use/tool_result content blocks — those are ephemeral
    and shouldn't be persisted.
    """
    storable = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")

        if role == "user" and isinstance(content, str):
            storable.append({"role": "user", "content": content})
        elif role == "assistant":
            # Content may be a string or list of blocks
            if isinstance(content, str):
                storable.append({"role": "assistant", "content": content})
            elif isinstance(content, list):
                # Extract only text blocks
                text_parts = []
                for block in content:
                    if hasattr(block, "type") and block.type == "text":
                        text_parts.append(block.text)
                    elif isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                if text_parts:
                    storable.append({"role": "assistant", "content": "\n".join(text_parts)})
    return storable


def _clear_conversation(user_id: str, channel_id: str) -> bool:
    """Delete the active conversation for a user+channel. Returns True if deleted."""
    sb = _get_sb()
    try:
        result = (
            sb.table("conversations")
            .delete()
            .eq("user_id", user_id)
            .eq("channel_id", channel_id)
            .eq("platform", "slack")
            .execute()
        )
        return bool(result.data)
    except Exception as e:
        print(f"[MCSA Slack] Error clearing conversation: {e}")
        return False


def _get_user_profile(user_id: str, user_name: str) -> dict | None:
    """Look up or auto-create a user in the Supabase users table.

    Returns the user row dict, or None on error.
    """
    sb = _get_sb()
    try:
        result = (
            sb.table("users")
            .select("*")
            .eq("slack_user_id", user_id)
            .limit(1)
            .execute()
        )
        if result.data:
            return result.data[0]

        # Auto-create user
        now = datetime.now(timezone.utc).isoformat()
        insert_result = (
            sb.table("users")
            .insert({
                "slack_user_id": user_id,
                "name": user_name,
                "role": "user",
                "created_at": now,
                "updated_at": now,
            })
            .execute()
        )
        if insert_result.data:
            return insert_result.data[0]
    except Exception as e:
        print(f"[MCSA Slack] Error getting/creating user profile: {e}")
    return None


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
                "module": {"type": "string", "enum": ["linkedin", "industry", "website", "diff", "registry", "topics", "content_strategy"]},
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
                "module": {"type": "string", "enum": ["linkedin", "industry", "website", "diff", "registry", "topics", "content_strategy"]},
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
    {
        "name": "get_trending_topics",
        "description": (
            "Get trending topics for an agency with momentum scoring (rising/falling/stable/new). "
            "Shows what topics are gaining traction in each agency's vertical."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "agency": {"type": "string", "description": "Agency name. Omit for all agencies."},
                "momentum": {"type": "string", "enum": ["rising", "falling", "stable", "new"],
                             "description": "Filter by momentum. Omit for all."},
                "limit": {"type": "integer", "description": "Max topics per agency (default 10)."},
            },
            "required": [],
        },
    },
    {
        "name": "suggest_content",
        "description": (
            "Get content suggestions based on competitive intelligence, trending topics, "
            "and key people activity. Returns intelligence data for making specific "
            "post recommendations with topics, formats, platforms, and rationale."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "agency": {"type": "string", "description": "Agency name (required)."},
                "timeframe": {"type": "string", "enum": ["today", "this_week", "this_month"],
                              "description": "Content planning horizon. Default: this_week."},
            },
            "required": ["agency"],
        },
    },
    {
        "name": "get_key_people",
        "description": (
            "Get tracked key people and thought leaders for an agency. "
            "Shows their role, company, topics, recent activity, and relevance."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "agency": {"type": "string", "description": "Agency name. Omit for all agencies."},
                "limit": {"type": "integer", "description": "Max people (default 10)."},
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
When you don't have data, say so clearly.

TOOL TIPS:
- Use get_trending_topics to see what topics are rising/falling per agency
- When the user asks about a specific topic, use search_reports with search_text to find
  detailed intelligence in the actual reports (e.g. search_text="linkedin algorithm")
- Combine multiple tool calls when needed — e.g. get topics first, then search reports
  for details on a specific topic
- Be specific and actionable — tie insights to what the agency should DO about them"""


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
        elif name == "get_trending_topics":
            return _tool_get_trending_topics(sb, input_data)
        elif name == "get_key_people":
            return _tool_get_key_people(sb, input_data)
        elif name == "suggest_content":
            return _tool_suggest_content(sb, input_data)
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
        # When searching, show context around the match; otherwise show more of the report
        if search_text:
            idx = content.lower().index(search_text)
            start = max(0, idx - 300)
            end = min(len(content), idx + len(search_text) + 700)
            preview = ("..." if start > 0 else "") + content[start:end] + ("..." if end < len(content) else "")
        else:
            preview = content[:1500] if len(content) > 1500 else content
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


def _tool_get_trending_topics(sb, params: dict) -> str:
    limit = min(params.get("limit", 10), 20)
    agency = params.get("agency")
    momentum = params.get("momentum")

    query = sb.table("topics").select("*").order("last_seen_at", desc=True)
    if agency:
        query = query.eq("agency_name", agency)
    if momentum:
        query = query.eq("momentum", momentum)
    query = query.limit(limit)

    rows = query.execute()
    if not rows.data:
        return "No topics tracked yet. Topics are extracted during weekly surveillance runs."

    momentum_icon = {"rising": "UP", "falling": "DOWN", "stable": "STEADY", "new": "NEW"}
    parts = []
    current_agency = None
    for r in rows.data:
        if r["agency_name"] != current_agency:
            current_agency = r["agency_name"]
            parts.append(f"\n## {current_agency}")
        icon = momentum_icon.get(r.get("momentum", ""), "?")
        parts.append(
            f"[{icon}] {r['topic']} ({r.get('category', '?')})\n"
            f"  Momentum: {r.get('momentum', '?')} | Mentions: {r.get('mention_count', 0)} | "
            f"Confidence: {r.get('confidence', '?')}\n"
            f"  {r.get('relevance', '')[:150]}"
        )
    return f"{len(rows.data)} topic(s):\n" + "\n".join(parts)


def _tool_get_key_people(sb, params: dict) -> str:
    limit = min(params.get("limit", 10), 20)
    agency = params.get("agency")

    query = sb.table("key_people").select("*").eq("status", "active").order("updated_at", desc=True)
    if agency:
        query = query.eq("agency_name", agency)
    query = query.limit(limit)

    rows = query.execute()
    if not rows.data:
        return "No key people tracked yet. People are discovered during surveillance runs."

    parts = []
    current_agency = None
    for r in rows.data:
        if r["agency_name"] != current_agency:
            current_agency = r["agency_name"]
            parts.append(f"\n## {current_agency}")
        topics = ", ".join(r.get("topics", [])[:4]) if r.get("topics") else "N/A"
        parts.append(
            f"**{r['name']}** — {r.get('title', '?')} at {r.get('company', '?')}\n"
            f"  Topics: {topics}\n"
            f"  Relevance: {r.get('relevance', '')[:200]}\n"
            f"  Recent: {r.get('recent_activity', 'No recent activity')[:200]}"
        )
    return f"{len(rows.data)} key people:\n" + "\n\n".join(parts)


def _tool_suggest_content(sb, params: dict) -> str:
    agency = params.get("agency", "")
    if not agency:
        return "Please specify an agency name."

    parts = [f"# Content Intelligence for {agency}\n"]

    # 1. Get rising topics
    topics = sb.table("topics").select("topic, momentum, category, relevance").eq(
        "agency_name", agency
    ).order("last_seen_at", desc=True).limit(10).execute()
    if topics.data:
        rising = [t for t in topics.data if t.get("momentum") == "rising"]
        new = [t for t in topics.data if t.get("momentum") == "new"]
        parts.append("## Rising Topics")
        for t in rising[:5]:
            parts.append(f"- {t['topic']} ({t.get('category','?')}): {t.get('relevance','')[:150]}")
        if new:
            parts.append("\n## New/Emerging Topics")
            for t in new[:3]:
                parts.append(f"- {t['topic']} ({t.get('category','?')}): {t.get('relevance','')[:150]}")

    # 2. Get key people activity
    people = sb.table("key_people").select("name, company, topics, recent_activity").eq(
        "agency_name", agency
    ).eq("status", "active").limit(5).execute()
    if people.data:
        parts.append("\n## Key People Activity")
        for p in people.data:
            parts.append(f"- {p['name']} ({p.get('company','?')}): {p.get('recent_activity','No recent activity')[:150]}")

    # 3. Get latest content strategy report
    cs = sb.table("reports").select("content, created_at").eq(
        "agency_name", agency
    ).eq("module", "content_strategy").order("created_at", desc=True).limit(1).execute()
    if cs.data:
        parts.append(f"\n## Latest Content Strategy ({cs.data[0]['created_at'][:10]})")
        parts.append(cs.data[0]["content"][:1500])

    # 4. Get latest DIFF report
    diff = sb.table("reports").select("content, created_at").eq(
        "agency_name", agency
    ).eq("module", "diff").order("created_at", desc=True).limit(1).execute()
    if diff.data:
        parts.append(f"\n## Latest Competitive DIFF ({diff.data[0]['created_at'][:10]})")
        parts.append(diff.data[0]["content"][:1000])

    return "\n".join(parts)


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

def _process_command(
    text: str,
    user_id: str,
    user_name: str,
    channel_id: str,
    response_url: str,
) -> None:
    """Run the Claude tool-use loop and post result back to Slack.

    Loads prior conversation history for this user+channel, includes
    the last MAX_HISTORY_MESSAGES messages as context, and saves the
    updated conversation after receiving Claude's response.
    """
    try:
        claude_client = _get_claude()

        # Ensure user profile exists
        _get_user_profile(user_id, user_name)

        # Load conversation history for this user+channel
        conv = _load_conversation(user_id, channel_id)
        conv_id = conv["id"] if conv else None
        history = conv.get("messages", []) if conv else []

        # Build messages: include last N history messages + new user message
        messages = []
        if history:
            # Take the tail of the conversation to stay within token limits
            recent = history[-MAX_HISTORY_MESSAGES:]
            for m in recent:
                messages.append({"role": m["role"], "content": m["content"]})
        messages.append({"role": "user", "content": text})

        # Tool-use loop (max 5 rounds, same as web chat)
        final_text = ""
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
            for block in response.content:
                if block.type == "text":
                    final_text += block.text

            # Append the final assistant message for storage
            messages.append({"role": "assistant", "content": response.content})
            break

        # Save conversation (history + new user message + assistant reply)
        # Merge old history with the new messages for persistence
        full_history = list(history)  # prior messages
        full_history.append({"role": "user", "content": text})
        if final_text:
            full_history.append({"role": "assistant", "content": final_text})
        _save_conversation(user_id, user_name, channel_id, full_history, conv_id)

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


def _post_chat_message(channel: str, text: str, thread_ts: str | None = None) -> None:
    """Post a message to Slack via chat.postMessage (requires bot token)."""
    bot_token = os.getenv("SLACK_MCSA_BOT_TOKEN")
    if not bot_token:
        print("[MCSA Slack] No SLACK_MCSA_BOT_TOKEN — cannot post via Events API")
        return

    payload: dict = {
        "channel": channel,
        "text": text,  # fallback for notifications
        "blocks": [],
    }
    if thread_ts:
        payload["thread_ts"] = thread_ts

    # Build blocks — chunk the response into 3000-char sections
    slack_text = _md_to_slack(text) if text else ""
    if len(slack_text) > 3800:
        slack_text = slack_text[:3800] + "\n\n_...response truncated_"

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

    payload["blocks"].append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": f":robot_face: MCSA Intelligence Analyst | {datetime.now().strftime('%H:%M')}"}],
    })

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=data,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {bot_token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            if not result.get("ok"):
                print(f"[MCSA Slack] chat.postMessage error: {result.get('error')}")
    except Exception as e:
        print(f"[MCSA Slack] Failed to post chat message: {e}")


def _process_event(text: str, user_id: str, channel_id: str, thread_ts: str | None = None) -> None:
    """Process an Events API message (mention or DM) — same as slash command but posts via bot token."""
    try:
        claude_client = _get_claude()

        # Get user name from Slack API
        bot_token = os.getenv("SLACK_MCSA_BOT_TOKEN", "")
        user_name = user_id
        if bot_token:
            try:
                req = urllib.request.Request(
                    f"https://slack.com/api/users.info?user={user_id}",
                    headers={"Authorization": f"Bearer {bot_token}"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read())
                    if data.get("ok"):
                        user_name = data["user"].get("real_name", data["user"].get("name", user_id))
            except Exception:
                pass

        _get_user_profile(user_id, user_name)

        # Load conversation history
        conv = _load_conversation(user_id, channel_id)
        conv_id = conv["id"] if conv else None
        history = conv.get("messages", []) if conv else []

        messages = []
        if history:
            recent = history[-MAX_HISTORY_MESSAGES:]
            for m in recent:
                messages.append({"role": m["role"], "content": m["content"]})
        messages.append({"role": "user", "content": text})

        # Tool-use loop (same as _process_command)
        final_text = ""
        for _ in range(5):
            response = None
            for attempt in range(3):
                try:
                    response = claude_client.messages.create(
                        model=MODEL,
                        max_tokens=2048,
                        system=SYSTEM_PROMPT,
                        messages=messages,
                        tools=TOOLS,
                    )
                    break
                except anthropic.RateLimitError:
                    if attempt < 2:
                        import time as _time
                        _time.sleep(5 * (attempt + 1))
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

            for block in response.content:
                if block.type == "text":
                    final_text += block.text
            messages.append({"role": "assistant", "content": response.content})
            break

        # Save conversation
        full_history = list(history)
        full_history.append({"role": "user", "content": text})
        if final_text:
            full_history.append({"role": "assistant", "content": final_text})
        _save_conversation(user_id, user_name, channel_id, full_history, conv_id)

        # Post response via bot token
        _post_chat_message(channel_id, final_text, thread_ts=thread_ts)

    except anthropic.RateLimitError:
        _post_chat_message(channel_id, ":hourglass: MCSA is temporarily rate-limited. Please try again in 30 seconds.", thread_ts=thread_ts)
    except Exception as e:
        print(f"[MCSA Slack Events] Error: {e}")
        _post_chat_message(channel_id, ":warning: MCSA encountered an issue processing your request.", thread_ts=thread_ts)


# Event dedup — prevent processing the same event twice
_processed_events: set[str] = set()
_MAX_EVENT_CACHE = 500


# ---------------------------------------------------------------------------
# Slash command endpoint
# ---------------------------------------------------------------------------

@router.post("/command")
async def slack_command(request: Request):
    """Handle /mcsa slash command from Slack.

    Slack sends a form-encoded POST with:
    - text: the user's query after /mcsa
    - user_id: Slack user ID
    - user_name: Slack username
    - channel_id: Slack channel ID
    - response_url: URL to post delayed responses to
    - token / team_id etc.
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
    user_name = form.get("user_name", "")
    channel_id = form.get("channel_id", "")
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
                "• `/mcsa what changed on competitor websites recently?`\n\n"
                "Conversation commands:\n"
                "• `/mcsa new` — start a fresh conversation\n"
                "• `/mcsa reset` — clear conversation history\n"
                "• `/mcsa history` — show conversation stats"
            ),
        }

    # Handle special conversation commands
    cmd_lower = text.lower()

    if cmd_lower in ("new", "reset"):
        cleared = _clear_conversation(user_id, channel_id)
        msg = (
            ":white_check_mark: Conversation cleared. Ask me anything!"
            if cleared
            else ":white_check_mark: No active conversation — starting fresh."
        )
        return {"response_type": "ephemeral", "text": msg}

    if cmd_lower == "history":
        conv = _load_conversation(user_id, channel_id)
        if conv:
            count = conv.get("message_count", 0)
            title = conv.get("title", "Untitled")
            created = conv.get("created_at", "")[:16]
            updated = conv.get("updated_at", "")[:16]
            return {
                "response_type": "ephemeral",
                "text": (
                    f"*Active conversation:* {title}\n"
                    f"Messages: {count} | Started: {created} | Last active: {updated}\n\n"
                    f"Use `/mcsa new` to start a fresh conversation."
                ),
            }
        return {
            "response_type": "ephemeral",
            "text": "No active conversation in this channel. Just ask a question to start one!",
        }

    # Acknowledge immediately (Slack requires <3s response)
    # Process in background thread, post result via response_url
    thread = threading.Thread(
        target=_process_command,
        args=(text, user_id, user_name, channel_id, response_url),
        daemon=True,
    )
    thread.start()

    return {
        "response_type": "in_channel",
        "text": f":hourglass_flowing_sand: _Analysing: {text}_",
    }


# ---------------------------------------------------------------------------
# Events API endpoint — @mentions and DMs
# ---------------------------------------------------------------------------

@router.post("/events")
async def slack_events(request: Request):
    """Handle Slack Events API — app_mention and message.im events.

    Slack sends JSON POST with event data. Must respond with 200 within 3s.
    """
    body = await request.body()

    # Verify request signature
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "0")
    signature = request.headers.get("X-Slack-Signature", "")
    if not _verify_slack_request(body, timestamp, signature):
        return Response(status_code=401, content="Invalid signature")

    payload = json.loads(body)

    # URL verification challenge (Slack sends this once during setup)
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}

    # Handle events
    if payload.get("type") == "event_callback":
        event = payload.get("event", {})
        event_type = event.get("type", "")
        event_id = payload.get("event_id", "")

        # Deduplicate (Slack retries events)
        if event_id in _processed_events:
            return {"ok": True}
        _processed_events.add(event_id)
        # Keep cache bounded
        if len(_processed_events) > _MAX_EVENT_CACHE:
            _processed_events.clear()

        # Ignore bot messages (prevent loops)
        if event.get("bot_id") or event.get("subtype") == "bot_message":
            return {"ok": True}

        user_id = event.get("user", "")
        channel_id = event.get("channel", "")
        thread_ts = event.get("thread_ts") or event.get("ts")
        text = event.get("text", "").strip()

        if event_type == "app_mention":
            # Strip the bot mention from the text: "<@U12345> what's trending" → "what's trending"
            import re
            text = re.sub(r"<@[A-Z0-9]+>\s*", "", text).strip()

        elif event_type == "message" and event.get("channel_type") == "im":
            # Direct message — use text as-is
            pass
        else:
            # Unhandled event type
            return {"ok": True}

        if not text or not user_id:
            return {"ok": True}

        # Send a typing indicator
        _post_chat_message(channel_id, f":hourglass_flowing_sand: _Analysing: {text}_", thread_ts=thread_ts)

        # Process in background
        thread = threading.Thread(
            target=_process_event,
            args=(text, user_id, channel_id, thread_ts),
            daemon=True,
        )
        thread.start()

    return {"ok": True}
