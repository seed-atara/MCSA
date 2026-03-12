# MCSA — Market & Competitor Surveillance Agent

Always-on competitive intelligence for the Tomorrow Group. Monitors the competitive landscape across 5 agencies, synthesises findings into actionable insight, and benchmarks against Tomorrow's own marketing output.

## Quick Start

```bash
pip install -r requirements.txt
cp .env.example .env  # Add your API keys

python mcsa_run.py daily                # All 5 agencies
python mcsa_run.py daily --agency Found # Single agency
python mcsa_run.py weekly               # Deeper analysis + DIFF
python mcsa_run.py monthly              # Registry refresh + Gap Report
python mcsa_run.py --list-agencies      # Show configured agencies
```

## What It Does

**Daily** — 3 Slack alerts per agency: LinkedIn activity, industry news, website changes.

**Weekly** — 4 deeper reports: LinkedIn themes, key person tracker, website patterns, competitive narrative drift.

**Monthly** — 2 strategic reports: competitor registry refresh, competitive gap analysis with content opportunities.

## 5 Capability Modules

| Module | What it monitors | Cadence |
|--------|-----------------|---------|
| 1. Registry | Competitor lists per agency — auto-discovered, enriched, threat-ranked | Monthly |
| 2. LinkedIn | Competitor posts, themes, engagement, trending topics | Daily + Weekly |
| 3. Industry | Publications, press mentions, awards, conferences, key people | Daily + Weekly |
| 4. DIFF | Competitor output vs Tomorrow's own — gaps, whitespace, opportunities | Weekly + Monthly |
| 5. Website | New content, positioning changes, publishing cadence, SEO signals | Daily + Weekly |

## Architecture

```
Tavily Search API ──→ Web data (search, crawl, extract)
                           |
                    Claude (analysis + synthesis)
                           |
                    Report Engine
                     /     |     \
              .slack.md   .md   .confluence.md
```

## Environment Variables

Required: `ANTHROPIC_API_KEY`, `TAVILY_API_KEY`
Optional: `FIRECRAWL_API_KEY` (better scraping)

## Output

Reports saved to `output/mcsa/`:
- `registries/{agency}.json` — competitor lists
- `reports/{agency}/{cadence}_{module}_{timestamp}.md` — raw reports
- `reports/{agency}/{cadence}_{module}_{timestamp}.slack.md` — Slack-formatted
- `reports/{agency}/{cadence}_{module}_{timestamp}.confluence.md` — Confluence-formatted
- `run_log.jsonl` — cost and duration tracking

## Cost

~$0.20/agency daily, ~$0.40/agency weekly, ~$0.30/agency monthly. Approximately $30/month for all 5 agencies across all cadences.
