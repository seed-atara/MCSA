"""MCSA Module Agents — one agent per capability module.

Each agent inherits core.agent.ResearchAgent for Claude API access,
and uses core.tools for web search/scrape/crawl.

Module 1: RegistryAgent       — build and maintain competitor lists
Module 2: LinkedInAgent       — monitor competitor LinkedIn activity
Module 3: IndustryAgent       — monitor publications and key people
Module 4: DIFFAgent           — compare competitor vs Tomorrow output
Module 5: WebsiteAgent        — monitor competitor website changes

TOKEN OPTIMISATION STRATEGY:
- Daily: search_web only (no deep scraping), smaller context, lower max_tokens
- Weekly: search_and_extract with moderate depth, full context
- Monthly: full deep research with batch_search_and_extract

All reports include confidence labeling as required by governance (Section 5).
"""
from __future__ import annotations

import asyncio
import json
from rich.console import Console

from core.agent import ResearchAgent
from core.tools import (
    search_web,
    search_and_extract,
    batch_search_and_extract,
    tavily_crawl,
    tavily_map,
)

console = Console()

# Invalid website placeholders that should not be crawled/mapped
_INVALID_WEBSITES = {"", "not specified", "unknown", "n/a", "none", "tbc", "tbd"}


def _valid_website(url: str) -> bool:
    """Return True if the URL looks like a real website, not a placeholder."""
    return bool(url) and url.strip().lower() not in _INVALID_WEBSITES


# Shared governance footer appended to every system prompt
_GOVERNANCE = (
    "\n\n--- GOVERNANCE ---\n"
    "All data is from publicly accessible sources only. No authenticated access.\n"
    "Output is classified Internal — not for client distribution without MD review.\n"
    "Include a CONFIDENCE label (HIGH / MEDIUM / LOW) for each major claim.\n"
    "If data is insufficient to make a claim, say so explicitly rather than speculating.\n"
    "--- END GOVERNANCE ---"
)

# ---------------------------------------------------------------------------
# Token budget constants by cadence
# ---------------------------------------------------------------------------
_DAILY_MAX_TOKENS = 2000       # daily alerts are short Slack messages
_WEEKLY_MAX_TOKENS = 6000      # weekly reports are medium-length
_MONTHLY_MAX_TOKENS = 12000    # monthly deep analysis gets full budget

_DAILY_CONTEXT_LIMIT = 12000   # chars of research data for daily
_WEEKLY_CONTEXT_LIMIT = 30000  # chars for weekly
_MONTHLY_CONTEXT_LIMIT = 50000 # chars for monthly

_DAILY_PRIOR_LIMIT = 1500      # chars of prior report for daily
_WEEKLY_PRIOR_LIMIT = 5000     # chars for weekly
_MONTHLY_PRIOR_LIMIT = 8000    # chars for monthly


def _context_limit(cadence: str) -> int:
    if cadence == "daily":
        return _DAILY_CONTEXT_LIMIT
    if cadence == "weekly":
        return _WEEKLY_CONTEXT_LIMIT
    return _MONTHLY_CONTEXT_LIMIT


def _prior_limit(cadence: str) -> int:
    if cadence == "daily":
        return _DAILY_PRIOR_LIMIT
    if cadence == "weekly":
        return _WEEKLY_PRIOR_LIMIT
    return _MONTHLY_PRIOR_LIMIT


def _max_tokens(cadence: str) -> int:
    if cadence == "daily":
        return _DAILY_MAX_TOKENS
    if cadence == "weekly":
        return _WEEKLY_MAX_TOKENS
    return _MONTHLY_MAX_TOKENS


async def _lightweight_search(queries: list[str], max_results: int = 3) -> str:
    """Search-only (no deep scraping). Cheapest data gathering for daily alerts."""
    tasks = [search_web(q, max_results=max_results) for q in queries]
    all_results = await asyncio.gather(*tasks, return_exceptions=True)

    parts = []
    seen_urls = set()
    for results in all_results:
        if isinstance(results, Exception):
            continue
        for r in results:
            url = r.get("url", "")
            if url in seen_urls:
                continue
            seen_urls.add(url)
            parts.append(f"**{r.get('title', '')}** ({url})\n{r.get('content', '')}")

    return "\n\n---\n\n".join(parts)


async def _moderate_search(queries: list[str], max_results: int = 3) -> str:
    """Search + extract top result per query. Mid-tier for weekly."""
    data = await batch_search_and_extract(
        queries, max_results=max_results, deep_scrape_count=1
    )
    return "\n\n---\n\n".join(r for r in data if isinstance(r, str))


async def _deep_search(queries: list[str], max_results: int = 5) -> str:
    """Full search + extract top 2 per query. Heavy-tier for monthly."""
    data = await batch_search_and_extract(
        queries, max_results=max_results, deep_scrape_count=2
    )
    return "\n\n---\n\n".join(r for r in data if isinstance(r, str))


def _gather_for_cadence(cadence: str):
    """Return the appropriate search function for the cadence."""
    if cadence == "daily":
        return _lightweight_search
    if cadence == "weekly":
        return _moderate_search
    return _deep_search


# ---------------------------------------------------------------------------
# Module 1 — Competitor Registry (monthly only)
# ---------------------------------------------------------------------------

class RegistryAgent(ResearchAgent):
    """Builds and maintains a live competitor list for a single agency."""

    def __init__(self):
        super().__init__(
            "Competitor Registry Agent",
            "Identifies and enriches competitor lists per agency",
        )

    async def research(self, agency: dict, context: dict) -> str:
        agency_name = agency["name"]
        agency_focus = agency.get("focus", "")
        competitor_guidance = agency.get("competitor_guidance", "")
        existing = context.get("existing_registry", [])
        manual_names = context.get("manual_competitors", agency.get("manual_competitors", []))

        # Import sibling list to exclude from results
        from .config import SIBLING_AGENCIES

        # Registry is always monthly — use deep search
        queries = [
            f"top {agency_focus} agencies UK 2025 2026 ranking",
            f"{agency_focus} agency landscape market leaders UK",
            f"{agency_focus} agency awards shortlists winners UK 2025 2026",
            f"best {agency_focus} agencies UK Campaign Drum",
            f"{agency_focus} agency new business wins UK 2026",
        ]

        # Check for updates on existing competitors (batch names to reduce queries)
        existing_names = [c.get("name", "") for c in existing[:5] if c.get("name")]
        if existing_names:
            names_str = " OR ".join(f'"{n}"' for n in existing_names[:3])
            queries.append(f"({names_str}) agency news 2025 2026")

        # Also research manual competitors specifically
        if manual_names:
            manual_str = " OR ".join(f'"{n}"' for n in manual_names[:5])
            queries.append(f"({manual_str}) agency UK services website")
            if len(manual_names) > 5:
                manual_str2 = " OR ".join(f'"{n}"' for n in manual_names[5:10])
                queries.append(f"({manual_str2}) agency UK services website")

        combined = await _deep_search(queries, max_results=5)

        existing_json = ""
        if existing:
            existing_json = (
                "\n\nEXISTING REGISTRY — review, update, and flag any that should be removed:\n"
                f"```json\n{json.dumps(existing, indent=2)}\n```"
            )

        manual_section = ""
        if manual_names:
            manual_list = ", ".join(manual_names)
            manual_section = (
                f"\n\nMANUAL COMPETITORS (human-selected, MUST be included):\n"
                f"{manual_list}\n"
                f"These were hand-picked by the team. Always include them in the registry "
                f"with source: \"manual\". Enrich them with website, sector, size, etc. "
                f"from your research data. You may also discover additional competitors "
                f"beyond this list — mark those with source: \"discovered\".\n"
            )

        sibling_list = ", ".join(sorted(SIBLING_AGENCIES))

        system = (
            f"You are a competitive intelligence analyst for the Tomorrow Group.\n\n"
            f"TASK: Build or update the competitor registry for '{agency_name}' "
            f"which specialises in: {agency_focus}.\n\n"
            f"WHAT COUNTS AS A COMPETITOR:\n{competitor_guidance}\n\n"
            f"CRITICAL EXCLUSIONS — do NOT include any of these:\n"
            f"- Tomorrow Group sibling agencies: {sibling_list}\n"
            f"- Ad-tech platforms, DSPs, or SaaS tools (e.g. StackAdapt, Semrush, HubSpot)\n"
            f"- Blockchain infrastructure, crypto exchanges, or dev shops (unless they are an agency)\n"
            f"- Companies not operating in the UK market\n"
            f"- Companies that share the agency's name but are in unrelated industries\n\n"
            f"COMPETITOR SOURCES:\n"
            f"- Manual (source: \"manual\"): Human-selected competitors. ALWAYS include these.\n"
            f"- Discovered (source: \"discovered\"): AI-identified from research. Include when relevant.\n\n"
            f"For each competitor, provide:\n"
            f"- Name, Website, Sector, Size (approx employees), Key services (top 3-5)\n"
            f"- LinkedIn URL (if findable), Active channels, Threat level (HIGH/MED/LOW)\n"
            f"- Confidence (HIGH/MED/LOW), Source (\"manual\" or \"discovered\")\n\n"
            f"Target: All manual competitors + up to 5 additional discovered ones.\n\n"
            f"OUTPUT: First a ```json``` array, then markdown summary with changes, "
            f"review checklist, and data gaps."
            f"{manual_section}"
            f"{_GOVERNANCE}"
        )

        user = f"RESEARCH DATA:\n{combined[:_MONTHLY_CONTEXT_LIMIT]}{existing_json}"
        return await self._call_claude(system, user, max_tokens=_MONTHLY_MAX_TOKENS, context=context)

    def parse_registry_json(self, report: str) -> list[dict]:
        """Extract the JSON competitor array from a registry report."""
        try:
            start = report.index("```json") + 7
            end = report.index("```", start)
            return json.loads(report[start:end])
        except (ValueError, json.JSONDecodeError):
            return []


# ---------------------------------------------------------------------------
# Module 2 — LinkedIn Intelligence
# ---------------------------------------------------------------------------

class LinkedInAgent(ResearchAgent):
    """Monitors competitor LinkedIn activity for a single agency's competitor set."""

    def __init__(self):
        super().__init__(
            "LinkedIn Intelligence Agent",
            "Monitors competitor LinkedIn activity, themes, and engagement",
        )

    async def research(self, agency: dict, context: dict) -> str:
        agency_name = agency["name"]
        agency_focus = agency.get("focus", "")
        competitors = context.get("competitors", [])
        cadence = context.get("cadence", "daily")
        prior_report = context.get("prior_report", "")

        # Batch competitor names into fewer queries instead of 3 per competitor
        comp_names = [c.get("name", "") for c in competitors if c.get("name")]

        if cadence == "daily":
            # Daily: 3-4 lightweight queries total
            names_str = " OR ".join(f'"{n}"' for n in comp_names[:5])
            queries = [
                f'site:linkedin.com ({names_str}) posts 2026',
                f"{agency_focus} LinkedIn trending topics UK 2026",
                f"({names_str}) LinkedIn content thought leadership recent",
            ]
        else:
            # Weekly: moderate depth, a few more queries
            names_str = " OR ".join(f'"{n}"' for n in comp_names[:5])
            queries = [
                f'site:linkedin.com ({names_str}) posts 2026',
                f"({names_str}) LinkedIn content thought leadership engagement",
                f"{agency_focus} LinkedIn trending topics UK 2026",
                f"{agency_focus} LinkedIn thought leadership best content examples",
                f"{agency_focus} agency LinkedIn engagement trends UK",
            ]

        search_fn = _gather_for_cadence(cadence)
        combined = await search_fn(queries)

        comp_list = ", ".join(comp_names) or "none loaded"

        prior_context = ""
        if prior_report:
            prior_context = (
                f"\n\nPRIOR REPORT (flag what has changed):\n"
                f"{prior_report[:_prior_limit(cadence)]}"
            )

        if cadence == "daily":
            system = (
                f"You are a LinkedIn intelligence analyst for {agency_name} (Tomorrow Group).\n\n"
                f"TASK: DAILY LinkedIn Activity Digest.\n"
                f"Competitors: {comp_list}\n\n"
                f"For each competitor with detectable activity:\n"
                f"- Post summary (1 sentence), theme tag, engagement signal (HIGH/MED/LOW)\n\n"
                f"Then: Top 3 trending topics in {agency_focus} on LinkedIn.\n"
                f"Signal vs noise verdict: 1 sentence.\n\n"
                f"FORMAT: Concise bullets for Slack. If no activity detected, say so."
                f"{_GOVERNANCE}"
            )
        else:
            system = (
                f"You are a LinkedIn intelligence analyst for {agency_name} (Tomorrow Group).\n\n"
                f"TASK: WEEKLY LinkedIn Theme Report.\n"
                f"Competitors: {comp_list}\n\n"
                f"Include:\n"
                f"1. Top themes by competitor\n"
                f"2. Tone of voice shifts\n"
                f"3. Format breakdown (thought leadership vs promo vs culture)\n"
                f"4. Posting frequency estimates\n"
                f"5. Whitespace topics {agency_name} could own\n"
                f"6. Engagement winners\n\n"
                f"FORMAT: Structured markdown for Confluence."
                f"{_GOVERNANCE}"
            )

        ctx_limit = _context_limit(cadence)
        user = f"LINKEDIN RESEARCH DATA:\n{combined[:ctx_limit]}{prior_context}"
        return await self._call_claude(system, user, max_tokens=_max_tokens(cadence), context=context)


# ---------------------------------------------------------------------------
# Module 3 — Industry Publications & Key People
# ---------------------------------------------------------------------------

class IndustryAgent(ResearchAgent):
    """Monitors industry publications and key people per agency vertical."""

    def __init__(self):
        super().__init__(
            "Industry Publications Agent",
            "Monitors publications, key people, and editorial themes",
        )

    async def research(self, agency: dict, context: dict) -> str:
        agency_name = agency["name"]
        agency_focus = agency.get("focus", "")
        competitors = context.get("competitors", [])
        cadence = context.get("cadence", "daily")
        prior_report = context.get("prior_report", "")

        comp_names = [c.get("name", "") for c in competitors if c.get("name")]

        if cadence == "daily":
            # Daily: 4 focused queries
            queries = [
                f"{agency_focus} industry news UK 2026",
                f"Campaign OR \"The Drum\" OR \"Marketing Week\" {agency_focus} news",
                f"{agency_focus} agency awards shortlist 2026 UK",
            ]
            if comp_names:
                names_str = " OR ".join(f'"{n}"' for n in comp_names[:4])
                queries.append(f"({names_str}) press coverage news 2026")
        else:
            # Weekly: broader search
            names_str = " OR ".join(f'"{n}"' for n in comp_names[:4]) if comp_names else ""
            queries = [
                f"{agency_focus} industry news analysis UK 2026",
                f"Campaign OR \"The Drum\" {agency_focus} agency news UK",
                f"Marketing Week {agency_focus} trends opinion",
                f"{agency_focus} conference events speakers UK 2026",
            ]
            if names_str:
                queries.extend([
                    f"({names_str}) press coverage spokesperson quoted",
                    f'"{agency_name}" agency press coverage 2026',
                ])

        search_fn = _gather_for_cadence(cadence)
        combined = await search_fn(queries)

        comp_list = ", ".join(comp_names) or "none loaded"

        prior_context = ""
        if prior_report:
            prior_context = (
                f"\n\nPRIOR REPORT (flag what's new):\n"
                f"{prior_report[:_prior_limit(cadence)]}"
            )

        if cadence == "daily":
            system = (
                f"You are an industry intelligence analyst for {agency_name} (Tomorrow Group).\n\n"
                f"TASK: DAILY Industry Pulse Alert.\n"
                f"Focus: {agency_focus} | Competitors: {comp_list}\n\n"
                f"Include:\n"
                f"- New articles/research — tag by topic\n"
                f"- Competitor mentions in tier-1 pubs — flag with [COMPETITOR MENTION]\n"
                f"- Award shortlists/wins\n"
                f"- Relevant events\n\n"
                f"FORMAT: Concise Slack alert. Most important first.\n"
                f"If nothing significant: 'No major signals today'."
                f"{_GOVERNANCE}"
            )
        else:
            system = (
                f"You are an industry intelligence analyst for {agency_name} (Tomorrow Group).\n\n"
                f"TASK: WEEKLY Key Person Tracker.\n"
                f"Focus: {agency_focus} | Competitors: {comp_list}\n\n"
                f"Include:\n"
                f"1. Competitor spokespeople gaining visibility\n"
                f"2. Emerging voices\n"
                f"3. Publication share-of-voice\n"
                f"4. Editorial themes gaining frequency\n"
                f"5. Upcoming events\n"
                f"6. {agency_name} visibility check\n\n"
                f"FORMAT: Structured markdown for Confluence."
                f"{_GOVERNANCE}"
            )

        ctx_limit = _context_limit(cadence)
        user = f"INDUSTRY RESEARCH DATA:\n{combined[:ctx_limit]}{prior_context}"
        return await self._call_claude(system, user, max_tokens=_max_tokens(cadence), context=context)


# ---------------------------------------------------------------------------
# Module 4 — Competitive DIFF vs Tomorrow Marketing
# ---------------------------------------------------------------------------

class DIFFAgent(ResearchAgent):
    """Compares competitor output against Tomorrow's own marketing."""

    def __init__(self):
        super().__init__(
            "Competitive DIFF Agent",
            "DIFFs competitor output against Tomorrow's own marketing",
        )

    async def research(self, agency: dict, context: dict) -> str:
        agency_name = agency["name"]
        agency_focus = agency.get("focus", "")
        competitors = context.get("competitors", [])
        cadence = context.get("cadence", "weekly")
        linkedin_data = context.get("linkedin_report", "")
        industry_data = context.get("industry_report", "")
        website_data = context.get("website_report", "")
        prior_diff = context.get("prior_report", "")

        # Search for Tomorrow's own output to compare against
        queries = [
            f'"{agency_name}" blog OR content OR "case study" 2026',
            f'site:linkedin.com/company "{agency_name}" posts',
            f'"{agency_name}" website services about',
        ]

        search_fn = _gather_for_cadence(cadence)
        combined = await search_fn(queries)

        comp_list = ", ".join(c.get("name", "?") for c in competitors)

        # Build context from upstream modules — cap each to avoid bloat
        upstream_limit = 8000 if cadence == "weekly" else 12000
        module_context = ""
        if linkedin_data:
            module_context += f"\n\n## LINKEDIN INTELLIGENCE\n{linkedin_data[:upstream_limit]}"
        if industry_data:
            module_context += f"\n\n## INDUSTRY INTELLIGENCE\n{industry_data[:upstream_limit]}"
        if website_data:
            module_context += f"\n\n## WEBSITE INTELLIGENCE\n{website_data[:upstream_limit]}"

        prior_context = ""
        if prior_diff:
            prior_context = (
                f"\n\nPRIOR DIFF (compare trends):\n"
                f"{prior_diff[:_prior_limit(cadence)]}"
            )

        if cadence == "weekly":
            system = (
                f"You are a competitive positioning analyst for {agency_name} (Tomorrow Group).\n\n"
                f"TASK: WEEKLY Narrative Drift Alert.\n"
                f"Competitors: {comp_list}\n\n"
                f"Flag:\n"
                f"- Positioning language shifts\n"
                f"- New narratives being tested\n"
                f"- Sudden output increases\n"
                f"- Format shifts\n\n"
                f"For each signal: What changed, which competitor, significance (HIGH/MED/LOW), "
                f"recommended response.\n\n"
                f"FORMAT: Concise Slack alert. Lead with biggest signal."
                f"{_GOVERNANCE}"
            )
        else:
            system = (
                f"You are a competitive positioning analyst for {agency_name} (Tomorrow Group).\n\n"
                f"TASK: MONTHLY Competitive Gap Report.\n"
                f"Focus: {agency_focus} | Competitors: {comp_list}\n\n"
                f"Produce:\n"
                f"1. Theme DIFF — topics competitors cover vs {agency_name}\n"
                f"2. Whitespace map — conversations with no dominant voice\n"
                f"3. Format DIFF — content formats winning engagement\n"
                f"4. Share-of-voice ranking\n"
                f"5. Positioning differentiation\n"
                f"6. Top 3 content opportunities\n"
                f"7. Trend vs prior month\n\n"
                f"FORMAT: Structured markdown for Confluence + Slack summary."
                f"{_GOVERNANCE}"
            )

        ctx_limit = _context_limit(cadence)
        user = f"RESEARCH DATA:\n{combined[:ctx_limit]}{module_context}{prior_context}"
        return await self._call_claude(system, user, max_tokens=_max_tokens(cadence), context=context)


# ---------------------------------------------------------------------------
# Module 5 — Competitor Website Pattern Analysis
# ---------------------------------------------------------------------------

class WebsiteAgent(ResearchAgent):
    """Monitors competitor websites for content and positioning changes.

    Daily: uses tavily_map (URL-only, no content extraction) + search for new content.
    Weekly: uses tavily_crawl with reduced page limit for full analysis.
    """

    def __init__(self):
        super().__init__(
            "Website Pattern Agent",
            "Monitors competitor website content and positioning changes",
        )

    async def research(self, agency: dict, context: dict) -> str:
        agency_name = agency["name"]
        competitors = context.get("competitors", [])
        cadence = context.get("cadence", "daily")
        change_summaries = context.get("change_summaries", {})
        prior_report = context.get("prior_report", "")

        if cadence == "daily":
            return await self._daily_scan(agency_name, competitors, change_summaries, prior_report, context)
        else:
            return await self._weekly_crawl(agency_name, competitors, change_summaries, prior_report, context)

    async def _daily_scan(self, agency_name, competitors, change_summaries, prior_report, context):
        """Daily: lightweight URL mapping + search. No full crawls."""
        # Use search to find recently published competitor content
        comp_names = [c.get("name", "") for c in competitors if c.get("name")]
        names_str = " OR ".join(f'"{n}"' for n in comp_names[:5]) if comp_names else ""

        queries = []
        if names_str:
            queries.append(f"({names_str}) blog OR \"case study\" OR news published 2026")
            queries.append(f"({names_str}) new website content landing page 2026")

        # Also do lightweight URL mapping (no content extraction) for change detection
        map_tasks = []
        map_comps = []
        for comp in competitors:
            website = comp.get("website", "")
            if _valid_website(website):
                map_tasks.append(tavily_map(website, max_depth=1, limit=20))
                map_comps.append(comp)

        # Run searches and maps in parallel
        search_task = _lightweight_search(queries) if queries else asyncio.coroutine(lambda: "")()
        all_tasks = [search_task] + map_tasks
        results = await asyncio.gather(*all_tasks, return_exceptions=True)

        search_data = results[0] if not isinstance(results[0], Exception) else ""
        map_results = results[1:]

        # Format map data
        map_parts = []
        for comp, result in zip(map_comps, map_results):
            if isinstance(result, Exception):
                continue
            urls = result.get("urls", [])
            if urls:
                comp_name = comp.get("name", "?")
                url_list = "\n".join(f"- {u}" for u in urls[:15])
                map_parts.append(f"## {comp_name} — {len(urls)} pages mapped\n{url_list}")

        combined = search_data
        if map_parts:
            combined += "\n\n===\n\n" + "\n\n".join(map_parts)

        # Add change detection summaries
        change_text = ""
        for comp_name, changes in change_summaries.items():
            n = len(changes.get("new_pages", []))
            c = len(changes.get("changed_pages", []))
            r = len(changes.get("removed_pages", []))
            if n or c or r:
                change_text += f"\n{comp_name}: {n} new, {c} changed, {r} removed pages"

        prior_context = ""
        if prior_report:
            prior_context = f"\n\nPRIOR REPORT:\n{prior_report[:_DAILY_PRIOR_LIMIT]}"

        system = (
            f"You are a website intelligence analyst for {agency_name} (Tomorrow Group).\n\n"
            f"TASK: DAILY Competitor Content Alert.\n"
            f"Competitors: {', '.join(c.get('name', '?') for c in competitors)}\n\n"
            f"Identify new blog posts, case studies, or landing pages.\n"
            f"For each: title, category, 1-sentence summary.\n\n"
            f"FORMAT: Concise Slack alert. Group by competitor.\n"
            f"If nothing new: 'No new competitor content detected today'."
            f"{_GOVERNANCE}"
        )

        user = f"WEBSITE DATA:\n{combined[:_DAILY_CONTEXT_LIMIT]}"
        if change_text:
            user += f"\n\nCHANGE DETECTION:{change_text}"
        user += prior_context

        return await self._call_claude(system, user, max_tokens=_DAILY_MAX_TOKENS, context=context)

    async def _weekly_crawl(self, agency_name, competitors, change_summaries, prior_report, context):
        """Weekly: actual crawls but with reduced page limits."""
        crawl_tasks = []
        crawl_comps = []
        for comp in competitors:
            website = comp.get("website", "")
            if _valid_website(website):
                crawl_tasks.append(
                    tavily_crawl(
                        website,
                        instructions="Find blog posts, case studies, service pages, about pages. Focus on recent content.",
                        max_depth=2,
                        limit=10,  # was 25 — 10 is enough for weekly pattern analysis
                        extract_depth="basic",
                    )
                )
                crawl_comps.append(comp)

        crawl_results = []
        if crawl_tasks:
            crawl_results = await asyncio.gather(*crawl_tasks, return_exceptions=True)

        crawl_text_parts = []
        for comp, result in zip(crawl_comps, crawl_results):
            if isinstance(result, Exception):
                continue
            pages = result.get("results", [])
            if not pages:
                continue

            comp_name = comp.get("name", "?")
            # Reduce per-page content to 800 chars (was 1500)
            page_summaries = []
            for p in pages[:10]:
                url = p.get("url", "")
                content = p.get("raw_content", "")[:800]
                page_summaries.append(f"**{url}**\n{content}")

            section = f"## {comp_name} ({len(pages)} pages)\n\n" + "\n\n---\n\n".join(page_summaries)

            changes = change_summaries.get(comp_name)
            if changes:
                n = len(changes.get("new_pages", []))
                c = len(changes.get("changed_pages", []))
                r = len(changes.get("removed_pages", []))
                if n or c or r:
                    section += f"\n\nChanges: {n} new, {c} changed, {r} removed"

            crawl_text_parts.append(section)

        combined = "\n\n===\n\n".join(crawl_text_parts)
        comp_list = ", ".join(c.get("name", "?") for c in competitors)

        prior_context = ""
        if prior_report:
            prior_context = f"\n\nPRIOR REPORT:\n{prior_report[:_WEEKLY_PRIOR_LIMIT]}"

        system = (
            f"You are a website intelligence analyst for {agency_name} (Tomorrow Group).\n\n"
            f"TASK: WEEKLY Website Pattern Report.\n"
            f"Competitors: {comp_list}\n\n"
            f"Analyse:\n"
            f"1. Publishing cadence estimates\n"
            f"2. Positioning language on service/about pages\n"
            f"3. New content themes\n"
            f"4. Service page changes\n"
            f"5. SEO signals\n"
            f"6. {agency_name} comparison\n\n"
            f"FORMAT: Structured markdown for Confluence."
            f"{_GOVERNANCE}"
        )

        user = f"WEBSITE CRAWL DATA:\n{combined[:_WEEKLY_CONTEXT_LIMIT]}{prior_context}"

        report = await self._call_claude(system, user, max_tokens=_WEEKLY_MAX_TOKENS, context=context)

        # Attach raw crawl data for snapshot storage
        context["_crawl_results"] = list(zip(crawl_comps, crawl_results))

        return report
