# MCSA Intelligence Terminal — Claude API Features Deep Dive

## Overview

The MCSA Chat UI is a competitive intelligence interface that leverages **5 advanced Claude API features** to deliver fast, accurate, and cost-efficient analysis for Tomorrow Group MDs. This document details each feature, why we chose it, and how it works in practice.

---

## 1. Tool Use (Function Calling)

**What it is:** Instead of dumping all intelligence data into the prompt, Claude has access to 4 custom tools that query Supabase in real-time. Claude decides what data it needs based on the question and calls the appropriate tool(s).

**Why it matters:** Traditional RAG stuffs everything into context and hopes for the best. Tool use lets Claude be surgical — it fetches only the data relevant to the question, keeping responses focused and costs low.

### Tools Available

| Tool | Purpose | Example Query |
|------|---------|---------------|
| `search_reports` | Search reports by agency, module, cadence, date, or keyword | "What did Brainlabs post on LinkedIn last week?" |
| `get_competitor_registry` | Pull full competitor registry with threat levels and metadata | "Who are Found's main competitors?" |
| `get_run_history` | System operational data — run times, costs, agencies covered | "How much has surveillance cost this month?" |
| `compare_agencies` | Side-by-side latest reports across agencies for one module | "Compare LinkedIn activity across all 5 agencies" |

### How It Works in Practice

1. User asks: *"What content gaps exist for SEED vs competitors?"*
2. Claude calls `get_competitor_registry(agency="SEED")` to get competitor list
3. Claude calls `search_reports(agency="SEED", module="diff", limit=3)` to get competitive diff reports
4. Claude synthesizes the data into actionable recommendations
5. The UI shows each tool call in real-time with a pulsing indicator

### API Implementation

```python
# Tools defined as JSON schema — Claude understands the structure
tools = [{
    "name": "search_reports",
    "description": "Search MCSA intelligence reports...",
    "input_schema": {
        "type": "object",
        "properties": {
            "agency": {"type": "string"},
            "module": {"type": "string", "enum": ["linkedin", "industry", ...]},
        }
    }
}]

# Claude receives tools and decides whether to call them
response = claude.messages.create(
    model="claude-sonnet-4-20250514",
    tools=tools,
    messages=messages,
)

# If response contains tool_use blocks, execute and loop back
for block in response.content:
    if block.type == "tool_use":
        result = execute_tool(block.name, block.input)
        # Feed result back to Claude for final analysis
```

### Multi-Turn Tool Loop

Claude can make **up to 5 sequential tool calls** per question. For complex queries like "Compare Found's competitive landscape to Disrupt's", it will:
1. Fetch Found's registry
2. Fetch Disrupt's registry
3. Search recent reports for both
4. Synthesize a comparison

---

## 2. Prompt Caching

**What it is:** The system prompt (including intelligence context and tool definitions) is marked with `cache_control: ephemeral`, telling Claude to cache it across conversation turns.

**Why it matters:** The system prompt with tool definitions is ~2,000 tokens. Without caching, you pay for it on every message. With caching, you pay full price on the first message and ~90% less on every follow-up.

### Cost Impact

| Scenario | Input Cost per Message | With Caching |
|----------|----------------------|--------------|
| First message | Full price (~$0.009) | Full price + tiny cache write fee |
| Messages 2-10 | Full price (~$0.009) | **~$0.001** (90% savings) |
| 10-message conversation | ~$0.09 total | ~$0.018 total |

### Implementation

```python
system_with_cache = [
    {
        "type": "text",
        "text": SYSTEM_PROMPT + agency_hint,
        "cache_control": {"type": "ephemeral"},  # This one line saves 90%
    },
]

response = claude.messages.create(
    system=system_with_cache,  # Cached across turns
    messages=messages,
    tools=TOOLS,
)
```

### Visible in the UI

After each message, the usage bar shows cache hit percentage:
```
IN: 2,450  OUT: 890  CACHED: 87%  TOOLS: 2 (search_reports, get_competitor_registry)
```

---

## 3. Extended Thinking

**What it is:** When toggled on via the "DEEP THINK" switch, Claude gets an 8,000-token thinking budget to reason through complex strategic questions before responding.

**Why it matters:** For simple factual queries ("What did Brainlabs post?"), thinking is unnecessary overhead. But for strategic analysis ("What's the biggest competitive vulnerability across all 5 agencies?"), extended thinking produces dramatically better insights — Claude can cross-reference data, identify patterns, and build structured arguments.

### When to Use It

| Question Type | Deep Think? | Why |
|--------------|------------|-----|
| "What are Found's competitors?" | No | Simple data lookup |
| "Summarise this week's LinkedIn activity" | No | Straightforward synthesis |
| "What's our biggest competitive blind spot?" | **Yes** | Requires cross-agency analysis and inference |
| "Design a content strategy based on competitor gaps" | **Yes** | Strategic planning needs deeper reasoning |
| "Compare threat trajectories across all agencies" | **Yes** | Multi-dimensional analysis |

### Implementation

```python
if deep_think:
    create_params["thinking"] = {
        "type": "enabled",
        "budget_tokens": 8000,  # Claude can use up to 8K tokens to think
    }
```

### Visible in the UI

When extended thinking is active:
- Header shows an orange "THINKING" badge
- A collapsible thinking block appears showing Claude's reasoning process
- Click to expand/collapse the thinking trace

---

## 4. Streaming (Server-Sent Events)

**What it is:** Responses are streamed token-by-token to the browser via SSE, so the user sees text appear in real-time instead of waiting for the full response.

**Why it matters:** Claude responses for complex intelligence queries can take 10-15 seconds. Without streaming, the user stares at a blank screen. With streaming, they see the answer forming immediately.

### Event Types Streamed

| Event | Purpose |
|-------|---------|
| `tool_call` | Shows which tool Claude is calling and with what parameters |
| `thinking` | Extended thinking content (when deep think is on) |
| `text` | The actual response text, chunked for smooth display |
| `usage` | Token counts, cache stats, and tools used — sent at the end |
| `[DONE]` | End-of-stream signal |

### Implementation

```python
def generate():
    # Tool use loop
    for loop_iter in range(5):
        response = claude.messages.create(**create_params)

        if has_tool_use:
            yield f"data: {json.dumps({'type': 'tool_call', 'tool': name, 'input': input})}\n\n"
            # Execute tool, add to messages, continue loop
        else:
            for block in response.content:
                if block.type == "thinking":
                    yield f"data: {json.dumps({'type': 'thinking', 'text': block.thinking})}\n\n"
                elif block.type == "text":
                    # Chunk for smooth streaming
                    for i in range(0, len(text), 12):
                        yield f"data: {json.dumps({'type': 'text', 'text': text[i:i+12]})}\n\n"

    yield f"data: {json.dumps({'type': 'usage', ...})}\n\n"
    yield "data: [DONE]\n\n"

return StreamingResponse(generate(), media_type="text/event-stream")
```

---

## 5. Token Usage Tracking

**What it is:** Every API call tracks input tokens, output tokens, cache read tokens, and tool calls. This data is displayed after each message and accumulated for the session.

**Why it matters:** MDs and leadership want to know what this costs. Transparent token tracking builds trust and enables cost optimization.

### What's Tracked

| Metric | Displayed |
|--------|-----------|
| Input tokens | Total tokens sent to Claude (includes tool results) |
| Output tokens | Tokens generated by Claude |
| Cache read tokens | Tokens served from cache (free/cheap) |
| Cache percentage | % of input that hit cache |
| Tool calls | Number of Supabase queries Claude made |
| Tools used | Names of tools called |

### Session Totals

The bottom-right of the input area shows running session totals:
```
12,450↓  3,200↑  8,900⚡  ·  $0.0234
```
- ↓ = total input tokens
- ↑ = total output tokens
- ⚡ = total cached tokens
- $ = estimated cost

---

## Architecture Summary

```
User Question
     │
     ▼
┌─────────────────────────┐
│  FastAPI Backend         │
│  /api/chat               │
│                          │
│  System Prompt ──────────┼──► cache_control: ephemeral
│  + Tool Definitions      │
│                          │
│  Claude API Call ────────┼──► model: claude-sonnet-4
│  (with tools)            │    thinking: optional 8K budget
│                          │
│  ┌─ Tool Use Loop ─────┐ │
│  │ Claude calls tool    │ │
│  │ Backend executes     │◄┼──► Supabase (reports, registries, run_logs)
│  │ Result fed back      │ │
│  │ Claude continues     │ │
│  └──────────────────────┘ │
│                          │
│  SSE Stream ─────────────┼──► tool_call | thinking | text | usage events
└─────────────────────────┘
     │
     ▼
Browser (Cyberpunk UI)
  - Real-time streaming display
  - Tool call indicators
  - Thinking blocks (collapsible)
  - Token usage bar
  - Markdown rendering (marked.js)
  - Session cost accumulator
```

---

## What's NOT Used (and Why)

| Feature | Status | Reason |
|---------|--------|--------|
| Files API (beta) | Not used | Tool use is better for this case — Claude queries exactly what it needs rather than processing pre-uploaded files |
| Claude Projects | Not used | No API — can't automate report uploads. Our chat UI is the automated alternative |
| Vision / Image input | Not used | MCSA deals in text reports, no visual data |
| Batch API | Not used | Chat is interactive, not batch. MCSA surveillance (the data collection) could use batch for cost savings in future |
| Citations (beta) | Future | When the API supports structured citations, we can link responses to specific report sources |

---

## Running the Chat UI

```bash
# Local development
python -m uvicorn chat.app:app --host 0.0.0.0 --port 8080

# Required environment variables
ANTHROPIC_API_KEY=...
SUPABASE_URL=https://gvhgfjdlpelaopnilqce.supabase.co
SUPABASE_SERVICE_KEY=...
```

Open http://localhost:8080 — select an agency, ask a question, and watch Claude work.
