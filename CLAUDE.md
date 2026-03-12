# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This repo contains two agent systems built on a shared research infrastructure:

1. **SEED 6C Research Agent** (`src/`) — produces Content Intelligence Briefs using the 6C Framework (Customer, Company, Competitor, Context, Catalyst, Constraint) plus Influencer Intelligence
2. **MCSA** (`mcsa/`) — Market & Competitor Surveillance Agent for the Tomorrow Group. Always-on competitive intelligence across 5 modules (Registry, LinkedIn, Industry, DIFF, Website) with daily/weekly/monthly cadences

Both share the `core/` package for search, scrape, Claude API calls, and cost tracking.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# 6C Research via CLI
python run.py "Client Name"
python run.py --location "London, UK" --notes "Focus on TikTok" "Client Name"

# 6C Web server
uvicorn web.app:app --host 0.0.0.0 --port 8080

# MCSA surveillance
python mcsa_run.py daily
python mcsa_run.py weekly --agency Found
python mcsa_run.py monthly
python mcsa_run.py --list-agencies
```

No test suite exists. No linter config.

## Architecture

### Package Layout

```
core/              ← Shared research infrastructure
  config.py        ← API keys, model settings, research profiles, output dir
  cost_tracker.py  ← Per-run cost estimation (Claude/Tavily/Firecrawl)
  tools.py         ← search_web, scrape_url, tavily_extract/research/crawl/map, batch ops, source registry
  agent.py         ← ResearchAgent base class (_call_claude with retry + cost tracking + notes injection)

src/               ← 6C Research Agent (re-exports core, adds 6C-specific logic)
  config.py        ← Re-exports core.config + 6C branding (SEED) and locale (GBP/UK)
  agents.py        ← 8 agent classes (6C + Influencer + Synthesis), inherits core.agent.ResearchAgent
  research_library.py ← 6C query generation, cross-dimension dedup, batch execution
  orchestrator.py  ← SixCOrchestrator — 7-phase pipeline
  pdf_generator.py ← fpdf2 PDF generation
  document_ingest.py ← PDF/DOCX/PPTX text extraction
  cost_tracker.py  ← Re-export from core
  tools.py         ← Re-export from core

mcsa/              ← Market & Competitor Surveillance Agent
  config.py        ← 5 Tomorrow agencies, 9 report definitions, cadence constants
  agents.py        ← 5 module agents (Registry, LinkedIn, Industry, DIFF, Website)
  orchestrator.py  ← MCSAOrchestrator — per-agency multi-module pipeline
  storage.py       ← JSON persistence (registries, reports, website snapshots, run log)
  formatter.py     ← Slack mrkdwn + Confluence markdown output formatters

web/               ← 6C web interface (FastAPI)
```

### Core → Project Import Pattern

`core/` contains the actual implementations. `src/` modules re-export from `core/` for backward compatibility — existing code like `from .tools import search_web` still works. New code (e.g. `mcsa/`) imports directly from `core/`.

### 6C Execution Phases

1. **Company Agent** — foundation; extracts industry, geographic scope
2. **Research Library** — centralized data gathering; ALL search queries in one parallel batch with dedup (~25-35% savings)
3. **Customer + Context** — parallel landscape analysis using library data
4. **Competitor + Influencer** — parallel, informed by phase 3
5. **Catalyst + Constraint** — parallel capstone with full prior context
6. **Synthesis** — combines all 7 agent outputs into final brief
7. **Document Generation** — all output documents in parallel

### MCSA Execution Flow (per agency)

1. **Registry** (monthly only) — build/refresh competitor list
2. **LinkedIn + Industry + Website** (parallel) — independent data gathering
3. **DIFF** (depends on step 2) — compares competitor output vs Tomorrow's own

### Data Flow

6C agents don't search independently — the `ResearchLibrary` gathers all data in phase 2, agents receive pre-gathered data and make a single Claude API call for pure analysis.

MCSA agents each gather their own data via `core.tools`, then the DIFF agent receives outputs from LinkedIn/Industry/Website agents as context.

### Environment Variables

Required: `ANTHROPIC_API_KEY`, `TAVILY_API_KEY`. Optional: `FIRECRAWL_API_KEY`, `RESEND_API_KEY`, `FROM_EMAIL`, `CC_EMAILS`, `ACCESS_CODE`, `AUTH_USERNAME`, `AUTH_PASSWORD`, `BASE_URL`. Railway uses `RAILWAY_ENVIRONMENT` to switch output dir.

## Known Technical Debt

- `src/agents.py` is monolithic (~3,400+ lines) — see TODO.md for split plan
- Duplicated markdown parsing in pdf_generator.py across 3 converter functions
- `web/requirements.txt` is a redundant subset of root requirements.txt
- Research depth profiles exist in config but no `--depth` CLI flag
- Default currency/locale hardcoded to GBP/UK in src/config.py
- MCSA has no Slack/Confluence webhook delivery yet (formatters exist, delivery via Make.com/Zapier TBD)
