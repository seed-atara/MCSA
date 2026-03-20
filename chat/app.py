"""MCSA Chat — Competitive Intelligence chat interface for Tomorrow Group MDs.

Advanced Claude API features:
- Tool Use: Claude queries Supabase directly for relevant data
- Prompt Caching: Intelligence context cached across conversation turns
- Extended Thinking: Deep analysis mode for complex strategic questions
- Streaming: Real-time response streaming with SSE
- Token Tracking: Per-message cost and usage reporting
"""
from __future__ import annotations

import os
import json
import time
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse, Response
import anthropic
from supabase import create_client

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
MODEL = "claude-sonnet-4-20250514"
THINKING_MODEL = "claude-sonnet-4-20250514"

sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

app = FastAPI(title="MCSA Chat")

# Mount Slack slash command handler
from .slack_handler import router as slack_router
app.include_router(slack_router)

TEMPLATE_DIR = Path(__file__).parent / "templates"

# ---------------------------------------------------------------------------
# Tool definitions for Claude
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "search_reports",
        "description": (
            "Search MCSA intelligence reports. Use this to find specific competitive intelligence. "
            "You can filter by agency, module (linkedin/industry/website/diff/registry), "
            "and cadence (daily/weekly/monthly). Returns report content with metadata."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "agency": {
                    "type": "string",
                    "description": "Agency name to filter by (e.g. 'Found', 'SEED', 'Braidr', 'Disrupt', 'Culture3'). Omit for all agencies.",
                },
                "module": {
                    "type": "string",
                    "enum": ["linkedin", "industry", "website", "diff", "registry", "content_strategy", "synthesis", "topics"],
                    "description": "Report module to filter by. Omit for all modules.",
                },
                "cadence": {
                    "type": "string",
                    "enum": ["daily", "weekly", "monthly"],
                    "description": "Report cadence to filter by. Omit for all cadences.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max reports to return. Default 5, max 20.",
                },
                "search_text": {
                    "type": "string",
                    "description": "Optional text to search for within report content.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_competitor_registry",
        "description": (
            "Get the full competitor registry for an agency. Returns competitor names, "
            "websites, threat levels, focus areas, and other metadata. Use this when asked "
            "about specific competitors or for comparison."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "agency": {
                    "type": "string",
                    "description": "Agency name (e.g. 'Found', 'SEED'). Omit for all agencies.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_run_history",
        "description": (
            "Get MCSA system run history — when surveillance ran, for which agencies, "
            "how long it took, and what it cost. Use this for operational questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of recent runs to return. Default 10.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "compare_agencies",
        "description": (
            "Compare the latest reports across multiple agencies for a specific module. "
            "Use this when asked to compare competitive landscapes across agencies."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "agencies": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of agency names to compare. Omit for all 5.",
                },
                "module": {
                    "type": "string",
                    "enum": ["linkedin", "industry", "website", "diff", "registry", "content_strategy", "topics"],
                    "description": "Module to compare across agencies.",
                },
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
                "agency": {
                    "type": "string",
                    "description": "Agency name to filter by.",
                },
                "severity": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "Filter by alert severity.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max alerts to return. Default 10.",
                },
                "unacknowledged_only": {
                    "type": "boolean",
                    "description": "Only show unacknowledged alerts.",
                },
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
                "momentum": {
                    "type": "string",
                    "enum": ["rising", "falling", "stable", "new"],
                    "description": "Filter by momentum. Omit for all.",
                },
                "limit": {"type": "integer", "description": "Max topics per agency (default 10)."},
            },
            "required": [],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

def _execute_tool(name: str, input_data: dict) -> str:
    """Execute a tool call and return the result as a string."""
    try:
        if name == "search_reports":
            return _tool_search_reports(input_data)
        elif name == "get_competitor_registry":
            return _tool_get_registry(input_data)
        elif name == "get_run_history":
            return _tool_get_run_history(input_data)
        elif name == "compare_agencies":
            return _tool_compare_agencies(input_data)
        elif name == "get_alerts":
            return _tool_get_alerts(input_data)
        elif name == "get_trending_topics":
            return _tool_get_trending_topics(input_data)
        else:
            return f"Unknown tool: {name}"
    except Exception as e:
        return f"Tool error: {e}"


def _tool_search_reports(params: dict) -> str:
    limit = min(params.get("limit", 5), 20)
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
        results.append(
            f"### {r['agency_name']} — {r['cadence']} {r['module']} ({r['created_at'][:10]})\n{content}"
        )

    if not results:
        return "No reports found matching your criteria."
    return f"Found {len(results)} report(s):\n\n" + "\n\n---\n\n".join(results)


def _tool_get_registry(params: dict) -> str:
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


def _tool_get_run_history(params: dict) -> str:
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
    lines.append(f"\n**Total cost across {len(rows.data)} runs: ${total_cost:.2f}**")
    return "\n".join(lines)


def _tool_compare_agencies(params: dict) -> str:
    module = params["module"]
    agencies = params.get("agencies", ["Found", "SEED", "Braidr", "Disrupt", "Culture3"])

    parts = []
    for agency in agencies:
        rows = (
            sb.table("reports")
            .select("agency_name, module, cadence, content, created_at")
            .eq("agency_name", agency)
            .eq("module", module)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if rows.data:
            r = rows.data[0]
            parts.append(
                f"### {r['agency_name']} — latest {module} ({r['cadence']}, {r['created_at'][:10]})\n{r['content']}"
            )
        else:
            parts.append(f"### {agency} — no {module} reports found")

    return "\n\n---\n\n".join(parts)


def _tool_get_alerts(params: dict) -> str:
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


def _tool_get_trending_topics(params: dict) -> str:
    limit = min(params.get("limit", 10), 30)
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
        sources = ", ".join(r.get("sources", [])[:3]) if r.get("sources") else "N/A"
        parts.append(
            f"[{icon}] **{r['topic']}** ({r.get('category', '?')})\n"
            f"  Momentum: {r.get('momentum', '?')} | Mentions: {r.get('mention_count', 0)} | "
            f"Confidence: {r.get('confidence', '?')}\n"
            f"  Relevance: {r.get('relevance', '')[:200]}\n"
            f"  Sources: {sources}"
        )
    return f"{len(rows.data)} topic(s):\n" + "\n\n".join(parts)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are the MCSA Intelligence Analyst for Tomorrow Group — a holding company
with 5 specialist agencies: Found (SEO/PPC), SEED (content/creative), Braidr (data/analytics),
Disrupt (paid media/programmatic), and Culture3 (social/influencer).

You have access to competitive intelligence gathered by the MCSA surveillance system through
your tools. Use them to look up specific data before answering.

IMPORTANT GUIDELINES:
- Always use your tools to fetch relevant data before answering — don't guess or make up data
- Be direct, specific, and actionable
- Reference specific competitors and data points from the reports
- When comparing, use concrete evidence from the reports
- When you don't have data on something, say so clearly
- Cite which report (agency, module, date) your information comes from

Format responses in markdown. Use bullet points, bold for key findings, and headers for structure."""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    return (TEMPLATE_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/api/agencies")
async def agencies():
    rows = sb.table("registries").select("agency_name").execute()
    names = sorted(set(r["agency_name"] for r in rows.data or []))
    return {"agencies": names}


@app.get("/api/stats")
async def stats():
    reports = sb.table("reports").select("id", count="exact").execute()
    registries = sb.table("registries").select("id", count="exact").execute()
    logs = sb.table("run_logs").select("cadence, created_at, cost").order("created_at", desc=True).execute()
    last_run = logs.data[0]["created_at"][:16] if logs.data else "never"
    total_cost = sum(r.get("cost", {}).get("total_cost_usd", 0) for r in logs.data)
    return {
        "reports": len(reports.data),
        "registries": len(registries.data),
        "last_run": last_run,
        "total_runs": len(logs.data),
        "total_cost": round(total_cost, 2),
    }


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    message = body.get("message", "")
    agency = body.get("agency")  # None = all agencies
    history = body.get("history", [])  # Previous messages for context
    deep_think = body.get("deep_think", False)  # Extended thinking mode
    conversation_id = body.get("conversation_id")  # Load history from DB if provided
    user_id = body.get("user_id")
    user_name = body.get("user_name")

    # Agency context hint for the system prompt
    agency_hint = ""
    if agency and agency != "all":
        agency_hint = f"\n\nThe user is currently focused on agency: {agency}. Prioritize data for this agency unless they ask about others."

    system_with_cache = [
        {
            "type": "text",
            "text": SYSTEM_PROMPT + agency_hint,
            "cache_control": {"type": "ephemeral"},
        },
    ]

    # Build messages from history — use DB history if conversation_id provided
    messages = []
    if conversation_id:
        db_msgs = (
            sb.table("messages")
            .select("role, content")
            .eq("conversation_id", conversation_id)
            .order("created_at", desc=False)
            .execute()
        )
        for msg in (db_msgs.data or [])[-10:]:
            messages.append({"role": msg["role"], "content": msg["content"]})
    else:
        for msg in history[-10:]:
            messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": message})

    # Tool use loop: Claude may call tools, we execute and feed back
    def generate():
        current_messages = list(messages)
        total_input = 0
        total_output = 0
        tool_calls_made = []
        thinking_text = ""

        for loop_iter in range(5):  # Max 5 tool use rounds
            create_params = {
                "model": THINKING_MODEL if deep_think else MODEL,
                "max_tokens": 16384,
                "system": system_with_cache,
                "messages": current_messages,
                "tools": TOOLS,
            }

            if deep_think:
                create_params["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": 8000,
                }

            response = claude.messages.create(**create_params)

            # Track tokens
            if hasattr(response, "usage"):
                total_input += response.usage.input_tokens
                total_output += response.usage.output_tokens
                cache_read = getattr(response.usage, "cache_read_input_tokens", 0) or 0
                cache_create = getattr(response.usage, "cache_creation_input_tokens", 0) or 0

            # Check if response has tool use
            has_tool_use = any(b.type == "tool_use" for b in response.content)

            if has_tool_use:
                # Process tool calls
                tool_results = []
                for block in response.content:
                    if block.type == "thinking":
                        thinking_text += block.thinking + "\n"
                    elif block.type == "tool_use":
                        tool_name = block.name
                        tool_input = block.input
                        tool_calls_made.append({"tool": tool_name, "input": tool_input})

                        # Send tool call event to frontend
                        yield f"data: {json.dumps({'type': 'tool_call', 'tool': tool_name, 'input': tool_input})}\n\n"

                        # Execute tool
                        result = _execute_tool(tool_name, tool_input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                # Add assistant response and tool results to messages
                current_messages.append({"role": "assistant", "content": response.content})
                current_messages.append({"role": "user", "content": tool_results})
                continue

            # No tool use — extract final text response
            for block in response.content:
                if block.type == "thinking":
                    thinking_text += block.thinking + "\n"
                    yield f"data: {json.dumps({'type': 'thinking', 'text': block.thinking})}\n\n"
                elif block.type == "text":
                    # Stream the text in chunks for smooth display
                    text = block.text
                    chunk_size = 12
                    for i in range(0, len(text), chunk_size):
                        yield f"data: {json.dumps({'type': 'text', 'text': text[i:i+chunk_size]})}\n\n"

            break  # Done — no more tool calls

        # Send usage stats
        cache_read = getattr(response.usage, "cache_read_input_tokens", 0) or 0
        usage_data = {'type': 'usage', 'input_tokens': total_input, 'output_tokens': total_output, 'cache_read_tokens': cache_read, 'tool_calls': len(tool_calls_made), 'tools_used': [t['tool'] for t in tool_calls_made]}
        if conversation_id:
            usage_data['conversation_id'] = conversation_id
        yield f"data: {json.dumps(usage_data)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/api/alerts")
async def api_alerts():
    """Get recent alerts for dashboard."""
    try:
        rows = sb.table("alerts").select("*").order("created_at", desc=True).limit(20).execute()
        return {"alerts": rows.data or []}
    except Exception:
        return {"alerts": []}


@app.get("/api/dashboard")
async def api_dashboard():
    """Aggregated dashboard data — stats, alerts, activity timeline, agency health."""
    try:
        # Stats
        reports = sb.table("reports").select("id", count="exact").execute()
        registries_data = sb.table("registries").select("agency_name, competitors, updated_at").execute()
        logs = sb.table("run_logs").select("*").order("created_at", desc=True).limit(30).execute()
        alerts = sb.table("alerts").select("*").order("created_at", desc=True).limit(10).execute()

        # Report counts by module
        recent_reports = sb.table("reports").select("agency_name, module, cadence, created_at").order("created_at", desc=True).limit(100).execute()
        module_counts = {}
        agency_report_counts = {}
        daily_activity = {}  # date -> count
        for r in recent_reports.data or []:
            mod = r.get("module", "unknown")
            module_counts[mod] = module_counts.get(mod, 0) + 1
            ag = r.get("agency_name", "unknown")
            agency_report_counts[ag] = agency_report_counts.get(ag, 0) + 1
            day = r.get("created_at", "")[:10]
            if day:
                daily_activity[day] = daily_activity.get(day, 0) + 1

        # Cost timeline from run logs
        cost_timeline = []
        for log in (logs.data or [])[:20]:
            cost_timeline.append({
                "date": log.get("created_at", "")[:10],
                "cost": log.get("cost", {}).get("total_cost_usd", 0),
                "cadence": log.get("cadence", ""),
                "duration": log.get("duration_seconds", 0),
            })

        # Agency health
        agency_health = []
        for reg in registries_data.data or []:
            name = reg.get("agency_name", "")
            competitors = reg.get("competitors", [])
            agency_health.append({
                "name": name,
                "competitors": len(competitors),
                "updated": reg.get("updated_at", "")[:10],
                "reports": agency_report_counts.get(name, 0),
            })

        # Alert severity breakdown
        alert_severity = {"high": 0, "medium": 0, "low": 0}
        unacknowledged = 0
        for a in alerts.data or []:
            sev = a.get("severity", "low")
            alert_severity[sev] = alert_severity.get(sev, 0) + 1
            if not a.get("acknowledged"):
                unacknowledged += 1

        last_run = logs.data[0]["created_at"][:16] if logs.data else "never"
        total_cost = sum(r.get("cost", {}).get("total_cost_usd", 0) for r in logs.data or [])

        return {
            "stats": {
                "reports": len(reports.data),
                "registries": len(registries_data.data),
                "total_runs": len(logs.data),
                "total_cost": round(total_cost, 2),
                "last_run": last_run,
                "unacknowledged_alerts": unacknowledged,
            },
            "alerts": alerts.data or [],
            "alert_severity": alert_severity,
            "module_counts": module_counts,
            "agency_health": agency_health,
            "cost_timeline": cost_timeline,
            "daily_activity": daily_activity,
        }
    except Exception as e:
        return {"error": str(e), "stats": {}, "alerts": [], "alert_severity": {}, "module_counts": {}, "agency_health": [], "cost_timeline": [], "daily_activity": {}}


@app.post("/api/digest/{digest_type}")
async def api_digest(digest_type: str):
    """Trigger a digest generation and delivery. Types: morning, weekly, monthly."""
    valid = ("morning", "weekly", "monthly")
    if digest_type not in valid:
        return {"error": f"Invalid type. Must be one of: {', '.join(valid)}"}
    from mcsa.digests import run_digest
    content = await run_digest(digest_type)
    if content:
        return {"status": "delivered", "type": digest_type, "length": len(content), "preview": content[:500]}
    return {"status": "failed", "type": digest_type, "message": "No reports found or generation failed"}


@app.post("/api/export")
async def export_chat(request: Request):
    """Export conversation as markdown."""
    body = await request.json()
    messages = body.get("messages", [])
    agency = body.get("agency", "all")

    lines = [
        f"# MCSA Intelligence Chat Export",
        f"**Agency:** {agency}",
        f"**Exported:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**Reports in database:** {len(sb.table('reports').select('id', count='exact').execute().data)}",
        "",
        "---",
        "",
    ]

    for msg in messages:
        role = "**You**" if msg["role"] == "user" else "**MCSA**"
        lines.append(f"{role}:\n\n{msg['content']}\n\n---\n")

    return {"markdown": "\n".join(lines)}


# ---------------------------------------------------------------------------
# Phase 4: Conversation Memory
# ---------------------------------------------------------------------------

@app.get("/api/conversations")
async def list_conversations(user_id: str):
    """List recent conversations for a user, ordered by updated_at desc."""
    rows = (
        sb.table("conversations")
        .select("id, title, message_count, agency_filter, updated_at")
        .eq("user_id", user_id)
        .order("updated_at", desc=True)
        .limit(20)
        .execute()
    )
    return {"conversations": rows.data or []}


@app.get("/api/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    """Get full conversation with messages."""
    conv = sb.table("conversations").select("*").eq("id", conv_id).single().execute()
    messages = (
        sb.table("messages")
        .select("*")
        .eq("conversation_id", conv_id)
        .order("created_at", desc=False)
        .execute()
    )
    return {
        "conversation": conv.data,
        "messages": messages.data or [],
    }


@app.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    """Delete a conversation and its messages."""
    sb.table("messages").delete().eq("conversation_id", conv_id).execute()
    sb.table("conversations").delete().eq("id", conv_id).execute()
    return {"status": "deleted"}


@app.post("/api/conversations/save")
async def save_conversation(request: Request):
    """Save a conversation turn (user message + assistant response).

    The frontend calls this after receiving the full streamed response.
    Creates a new conversation if conversation_id is not provided.
    """
    body = await request.json()
    conversation_id = body.get("conversation_id")
    user_id = body.get("user_id")
    user_message = body.get("user_message", "")
    assistant_message = body.get("assistant_message", "")
    agency_filter = body.get("agency_filter")

    now = datetime.utcnow().isoformat()

    # Create conversation if new
    if not conversation_id:
        title = user_message[:60].strip()
        if len(user_message) > 60:
            title += "..."
        conv = (
            sb.table("conversations")
            .insert({
                "user_id": user_id,
                "title": title,
                "agency_filter": agency_filter,
                "message_count": 0,
                "created_at": now,
                "updated_at": now,
            })
            .execute()
        )
        conversation_id = conv.data[0]["id"]

    # Insert the two messages
    sb.table("messages").insert([
        {
            "conversation_id": conversation_id,
            "role": "user",
            "content": user_message,
            "created_at": now,
        },
        {
            "conversation_id": conversation_id,
            "role": "assistant",
            "content": assistant_message,
            "created_at": now,
        },
    ]).execute()

    # Update conversation metadata
    msg_count = (
        sb.table("messages")
        .select("id", count="exact")
        .eq("conversation_id", conversation_id)
        .execute()
    )
    sb.table("conversations").update({
        "message_count": len(msg_count.data),
        "updated_at": now,
    }).eq("id", conversation_id).execute()

    return {"conversation_id": conversation_id}


# ---------------------------------------------------------------------------
# Phase 4: User Management
# ---------------------------------------------------------------------------

@app.post("/api/users/login")
async def user_login(request: Request):
    """Simple login — create user if not exists, return user record."""
    body = await request.json()
    name = body.get("name", "").strip()
    agency = body.get("agency", "").strip()

    if not name:
        return {"error": "name is required"}, 400

    # Check if user exists by name
    existing = (
        sb.table("users")
        .select("*")
        .eq("name", name)
        .limit(1)
        .execute()
    )

    if existing.data:
        user = existing.data[0]
        # Update agency if provided and different
        if agency and agency != user.get("agency"):
            sb.table("users").update({"agency": agency}).eq("id", user["id"]).execute()
            user["agency"] = agency
        return {"user": user}

    # Create new user
    now = datetime.utcnow().isoformat()
    new_user = (
        sb.table("users")
        .insert({
            "name": name,
            "agency": agency or None,
            "role": "user",
            "created_at": now,
            "updated_at": now,
        })
        .execute()
    )
    return {"user": new_user.data[0]}


@app.get("/api/users/{user_id}")
async def get_user(user_id: str):
    """Get user profile."""
    user = sb.table("users").select("*").eq("id", user_id).single().execute()
    return {"user": user.data}


@app.put("/api/users/{user_id}")
async def update_user(user_id: str, request: Request):
    """Update user preferences/agency."""
    body = await request.json()
    updates = {}
    if "agency" in body:
        updates["agency"] = body["agency"]
    if "name" in body:
        updates["name"] = body["name"]
    if "role" in body:
        updates["role"] = body["role"]
    if "preferences" in body:
        updates["preferences"] = body["preferences"]

    if not updates:
        return {"error": "No fields to update"}

    updates["updated_at"] = datetime.utcnow().isoformat()
    sb.table("users").update(updates).eq("id", user_id).execute()
    user = sb.table("users").select("*").eq("id", user_id).single().execute()
    return {"user": user.data}


# ---------------------------------------------------------------------------
# Phase 4: Report Browser
# ---------------------------------------------------------------------------

@app.get("/api/reports")
async def list_reports(
    agency: str | None = None,
    module: str | None = None,
    cadence: str | None = None,
    search: str | None = None,
    page: int = 1,
    per_page: int = 20,
):
    """Paginated report listing with filters."""
    per_page = min(per_page, 50)
    offset = (page - 1) * per_page

    # Count query
    count_q = sb.table("reports").select("id", count="exact")
    if agency:
        count_q = count_q.eq("agency_name", agency)
    if module:
        count_q = count_q.eq("module", module)
    if cadence:
        count_q = count_q.eq("cadence", cadence)
    if search:
        count_q = count_q.ilike("content", f"%{search}%")
    count_result = count_q.execute()
    total = len(count_result.data)

    # Data query
    query = sb.table("reports").select("id, agency_name, module, cadence, content, created_at")
    if agency:
        query = query.eq("agency_name", agency)
    if module:
        query = query.eq("module", module)
    if cadence:
        query = query.eq("cadence", cadence)
    if search:
        query = query.ilike("content", f"%{search}%")
    query = query.order("created_at", desc=True).range(offset, offset + per_page - 1)
    rows = query.execute()

    reports = []
    for r in rows.data or []:
        content = r.get("content", "")
        reports.append({
            "id": r["id"],
            "agency_name": r["agency_name"],
            "module": r["module"],
            "cadence": r["cadence"],
            "created_at": r["created_at"],
            "preview": content[:200] + ("..." if len(content) > 200 else ""),
        })

    return {
        "reports": reports,
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@app.get("/api/reports/search")
async def search_reports_fulltext(q: str, limit: int = 20):
    """Full-text search across report content with highlighted snippets."""
    limit = min(limit, 50)
    rows = (
        sb.table("reports")
        .select("id, agency_name, module, cadence, content, created_at")
        .ilike("content", f"%{q}%")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )

    results = []
    q_lower = q.lower()
    for r in rows.data or []:
        content = r.get("content", "")
        # Find the search term and extract a snippet around it
        idx = content.lower().find(q_lower)
        if idx >= 0:
            start = max(0, idx - 80)
            end = min(len(content), idx + len(q) + 80)
            snippet = ("..." if start > 0 else "") + content[start:end] + ("..." if end < len(content) else "")
        else:
            snippet = content[:200]

        results.append({
            "id": r["id"],
            "agency_name": r["agency_name"],
            "module": r["module"],
            "cadence": r["cadence"],
            "created_at": r["created_at"],
            "snippet": snippet,
        })

    return {"results": results, "query": q, "count": len(results)}


@app.post("/api/synthesis")
async def api_synthesis():
    """Trigger cross-agency trend synthesis generation and delivery."""
    from mcsa.synthesis import run_synthesis
    content = await run_synthesis()
    if content:
        return {"status": "delivered", "length": len(content), "preview": content[:500]}
    return {"status": "failed", "message": "No reports found or generation failed"}


@app.get("/api/reports/{agency}/pdf")
async def get_report_pdf(agency: str, period: str = "weekly"):
    """Generate and return a client-facing PDF report for an agency.

    Query params:
        period: weekly (default), daily, or monthly
    """
    valid_periods = ("daily", "weekly", "monthly")
    if period not in valid_periods:
        return {"error": f"Invalid period. Must be one of: {', '.join(valid_periods)}"}

    # Validate agency name
    valid_agencies = {"Found", "SEED", "Braidr", "Disrupt", "Culture3"}
    if agency not in valid_agencies:
        return {"error": f"Unknown agency '{agency}'. Must be one of: {', '.join(sorted(valid_agencies))}"}

    try:
        from mcsa.pdf_report import generate_pdf
        pdf_bytes = generate_pdf(agency, period)

        if not pdf_bytes:
            return {"error": "No report data available for this agency and period."}

        filename = f"MCSA_{agency}_{period}_{datetime.now().strftime('%Y%m%d')}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        return {"error": f"PDF generation failed: {e}"}


@app.get("/api/reports/{report_id}")
async def get_report(report_id: str):
    """Get full report content."""
    report = sb.table("reports").select("*").eq("id", report_id).single().execute()
    return {"report": report.data}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
