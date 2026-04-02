"""MCSA Orchestrator — coordinates surveillance modules across all agencies.

Runs the 5 capability modules for each agency, respecting cadence schedules,
feeding data downstream (LinkedIn + Industry + Website -> DIFF), persisting
reports and registries, and performing website change detection.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import date, datetime
from pathlib import Path
from rich.console import Console
from rich.panel import Panel

from core.config import OUTPUT_DIR
from core.tools import clear_sources, get_all_sources
from core.cost_tracker import cost_tracker

from .config import AGENCIES, REPORTS, CADENCE_DAILY, CADENCE_WEEKLY, CADENCE_MONTHLY
from .agents import RegistryAgent, LinkedInAgent, IndustryAgent, DIFFAgent, WebsiteAgent, ContentStrategyAgent, TopicIntelligenceAgent, KeyPeopleAgent, ContentCalendarAgent, SocialFollowerAgent
from . import storage
from . import formatter
from .slack import deliver_to_slack
from .confluence_delivery import deliver_to_confluence
from .alerts import run_alert_detection
from .watchlist import check_watchlist_matches, deliver_watchlist_alerts

console = Console()


class MCSAOrchestrator:
    """Coordinates MCSA surveillance across all Tomorrow Group agencies.

    Usage:
        orch = MCSAOrchestrator(cadence="daily")
        results = await orch.run()

    Or for a single agency:
        results = await orch.run_agency(agency_config, cadence="weekly")
    """

    def __init__(
        self,
        cadence: str = CADENCE_DAILY,
        agencies: list[dict] | None = None,
        progress_callback=None,
    ):
        self.cadence = cadence
        self.agencies = agencies or AGENCIES
        self.progress_callback = progress_callback
        self.results: dict[str, dict[str, str]] = {}

        # Load persisted registries
        self.registries = storage.load_all_registries()

        # Check for cross-registry duplicates
        self._check_registry_overlap()

        # Agents (stateless, shared across agencies)
        self.registry_agent = RegistryAgent()
        self.linkedin_agent = LinkedInAgent()
        self.industry_agent = IndustryAgent()
        self.diff_agent = DIFFAgent()
        self.website_agent = WebsiteAgent()
        self.content_strategy_agent = ContentStrategyAgent()
        self.topic_intelligence_agent = TopicIntelligenceAgent()
        self.key_people_agent = KeyPeopleAgent()
        self.content_calendar_agent = ContentCalendarAgent()
        self.social_follower_agent = SocialFollowerAgent()

    def _check_registry_overlap(self) -> None:
        """Log warnings when the same competitor appears in multiple agency registries."""
        # Build mapping: competitor_name_lower -> list of agencies
        comp_to_agencies: dict[str, list[str]] = {}
        for agency_key, competitors in self.registries.items():
            for comp in competitors:
                name = comp.get("name", "").strip().lower()
                if name:
                    comp_to_agencies.setdefault(name, []).append(agency_key)

        # Flag duplicates
        duplicates_found = False
        for comp_name, agencies in comp_to_agencies.items():
            if len(agencies) > 1:
                if not duplicates_found:
                    console.print("[yellow]Cross-registry duplicate competitors detected:[/yellow]")
                    duplicates_found = True
                agency_list = ", ".join(agencies)
                console.print(f"[yellow]  '{comp_name}' appears in: {agency_list}[/yellow]")

        if not duplicates_found and self.registries:
            console.print("[dim]Registry overlap check: no duplicates found[/dim]")

    def _report_progress(self, phase: int, total: int, description: str) -> None:
        if self.progress_callback:
            try:
                self.progress_callback(phase, total, description)
            except Exception:
                pass

    async def run(self) -> dict[str, dict[str, str]]:
        """Run all modules for all agencies at the current cadence."""
        clear_sources()
        cost_tracker.reset()
        start_time = time.time()

        agency_count = len(self.agencies)
        console.print(Panel(
            f"[bold]MCSA -- Market & Competitor Surveillance Agent[/bold]\n"
            f"[dim]Cadence: {self.cadence} | Agencies: {agency_count}[/dim]\n"
            f"[dim]Registries loaded: {len(self.registries)} agencies[/dim]",
            border_style="cyan",
        ))

        for i, agency in enumerate(self.agencies, 1):
            agency_name = agency["name"]
            self._report_progress(i, agency_count, agency_name)
            console.print(f"\n[bold]--- Agency: {agency_name} ---[/bold]")

            agency_results = await self.run_agency(agency, self.cadence)
            self.results[agency_name] = agency_results

            # Phase 2a: Daily digest — single Slack message with only highlights
            if self.cadence == CADENCE_DAILY:
                try:
                    await _generate_daily_digest(agency_name, agency_results)
                except Exception as e:
                    console.print(f"[yellow]  Daily digest failed for {agency_name}: {e}[/yellow]")

            # Phase 2b: Alert detection — compare against history
            try:
                await run_alert_detection(
                    agency_name, self.cadence, agency_results, self.registries
                )
            except Exception as e:
                console.print(f"[yellow]  Alert detection failed for {agency_name}: {e}[/yellow]")

            # Phase 3c: Watchlist — check reports against user-defined watches
            try:
                matches = check_watchlist_matches(agency_name, agency_results)
                if matches:
                    deliver_watchlist_alerts(matches)
            except Exception as e:
                console.print(f"[yellow]  Watchlist check failed for {agency_name}: {e}[/yellow]")

        # Save run log
        duration = time.time() - start_time
        sources = get_all_sources()
        cost = cost_tracker.summary()

        storage.save_run_log(
            cadence=self.cadence,
            agencies=[a["name"] for a in self.agencies],
            cost=cost,
            duration_seconds=duration,
        )

        console.print(Panel(
            f"[bold green]MCSA Run Complete[/bold green]\n"
            f"Cadence: {self.cadence}\n"
            f"Agencies: {len(self.results)}\n"
            f"Sources consulted: {len(sources)}\n"
            f"Duration: {duration:.0f}s\n"
            f"Estimated cost: ${cost['total_cost_usd']:.2f}",
            border_style="green",
        ))

        return self.results

    async def run_agency(self, agency: dict, cadence: str) -> dict[str, str]:
        """Run all applicable modules for a single agency.

        Execution order:
        1. Registry (monthly only) -- provides the competitor list
        2. LinkedIn + Industry + Website (parallel) -- independent data gathering
        3. DIFF (weekly/monthly) -- depends on step 2 outputs
        """
        agency_name = agency["name"]
        reports: dict[str, str] = {}

        # Load competitor registry for this agency
        competitors = self.registries.get(
            storage._safe(agency_name),
            self.registries.get(agency_name, [])
        )

        # Seed from manual_competitors if no registry exists yet
        manual_names = agency.get("manual_competitors", [])
        if not competitors and manual_names:
            competitors = [
                {"name": name, "source": "manual"} for name in manual_names
            ]
            storage.save_registry(agency_name, competitors)
            self.registries[storage._safe(agency_name)] = competitors
            console.print(f"[green]  Registry seeded with {len(competitors)} manual competitors[/green]")
        elif competitors and manual_names:
            # Ensure manual competitors are present in loaded registry for ALL cadences
            competitors = _ensure_manual_competitors(competitors, manual_names)
            self.registries[storage._safe(agency_name)] = competitors

        # ── Phase 1: Registry (monthly only) ─────────────────────────────
        if cadence == CADENCE_MONTHLY:
            console.print(f"[dim]  Module 1: Competitor Registry[/dim]")
            try:
                registry_report = await self.registry_agent.research(
                    agency, {"existing_registry": competitors, "manual_competitors": manual_names}
                )
                reports["registry"] = registry_report

                # Try to parse and persist the updated registry
                new_registry = self.registry_agent.parse_registry_json(registry_report)
                if new_registry:
                    # Ensure all manual competitors are preserved
                    new_registry = _ensure_manual_competitors(new_registry, manual_names)
                    storage.save_registry(agency_name, new_registry)
                    competitors = new_registry
                    self.registries[storage._safe(agency_name)] = new_registry
                    manual_count = sum(1 for c in new_registry if c.get("source") == "manual")
                    discovered_count = len(new_registry) - manual_count
                    console.print(f"[green]  Registry: {len(new_registry)} competitors ({manual_count} manual, {discovered_count} discovered)[/green]")
                else:
                    console.print(f"[yellow]  Registry: report generated but JSON parse failed[/yellow]")

                # Save report
                path = storage.save_report(agency_name, "registry", cadence, registry_report)
                _save_formatted(agency_name, "registry", cadence, registry_report, path)
                console.print(f"[green]  Registry report saved: {path.name}[/green]")

            except Exception as e:
                console.print(f"[red]  Registry failed: {e}[/red]")
                reports["registry"] = f"[Error: {e}]"

        if not competitors:
            console.print(f"[yellow]  No competitor registry for {agency_name}[/yellow]")

        # ── Phase 2: LinkedIn + Industry + Website (parallel) ─────────────
        phase2_tasks = []
        phase2_labels = []

        if cadence in (CADENCE_DAILY, CADENCE_WEEKLY):
            # LinkedIn
            li_prior = storage.load_latest_report(agency_name, "linkedin", cadence)
            li_ctx = {
                "competitors": competitors,
                "cadence": cadence,
                "prior_report": li_prior or "",
            }
            phase2_tasks.append(self.linkedin_agent.research(agency, li_ctx))
            phase2_labels.append("linkedin")

            # Industry
            ind_prior = storage.load_latest_report(agency_name, "industry", cadence)
            ind_ctx = {
                "competitors": competitors,
                "cadence": cadence,
                "prior_report": ind_prior or "",
            }
            phase2_tasks.append(self.industry_agent.research(agency, ind_ctx))
            phase2_labels.append("industry")

            # Website (with change detection)
            web_prior = storage.load_latest_report(agency_name, "website", cadence)
            web_ctx = {
                "competitors": competitors,
                "cadence": cadence,
                "prior_report": web_prior or "",
                "change_summaries": {},
            }
            # Pre-load previous snapshots for change detection
            for comp in competitors:
                comp_name = comp.get("name", "")
                if comp_name:
                    prev = storage.load_previous_snapshot(agency_name, comp_name)
                    if prev:
                        web_ctx["_prev_snapshots"] = web_ctx.get("_prev_snapshots", {})
                        web_ctx["_prev_snapshots"][comp_name] = prev
            phase2_tasks.append(self.website_agent.research(agency, web_ctx))
            phase2_labels.append("website")

            # Key People (parallel with other Phase 2 modules)
            existing_people = storage.load_key_people(agency_name, limit=10)
            people_names = [p.get("name", "") for p in existing_people if p.get("name")]
            existing_str = ""
            if existing_people:
                existing_str = json.dumps(
                    [{"name": p["name"], "title": p.get("title", ""), "company": p.get("company", ""),
                      "topics": p.get("topics", []), "status": p.get("status", "active")}
                     for p in existing_people],
                    indent=2,
                )
            kp_ctx = {
                "competitors": competitors,
                "cadence": cadence,
                "existing_people": existing_str,
                "people_names": people_names,
                "linkedin_report": "",  # not available yet, will use search data
                "industry_report": "",
            }
            phase2_tasks.append(self.key_people_agent.research(agency, kp_ctx))
            phase2_labels.append("key_people")

        if phase2_tasks:
            label_str = ", ".join(phase2_labels)
            console.print(f"[dim]  Modules 2/3/5: {label_str} (parallel)[/dim]")
            phase2_results = await asyncio.gather(*phase2_tasks, return_exceptions=True)

            for label, result in zip(phase2_labels, phase2_results):
                if isinstance(result, Exception):
                    console.print(f"[red]  {label} failed: {result}[/red]")
                    reports[label] = f"[Error: {result}]"
                else:
                    reports[label] = result
                    # Save report
                    path = storage.save_report(agency_name, label, cadence, result)
                    _save_formatted(agency_name, label, cadence, result, path)
                    console.print(f"[green]  {label}: {len(result)} chars -> {path.name}[/green]")

            # Post-process: save website snapshots for future change detection
            # (only weekly/monthly — daily uses lightweight mapping, no crawl data)
            if cadence != CADENCE_DAILY and "website" in reports and not reports["website"].startswith("[Error"):
                _save_website_snapshots(agency_name, web_ctx, competitors)

            # Post-process: parse and save key people
            if "key_people" in reports and not reports["key_people"].startswith("[Error"):
                parsed_people = self.key_people_agent.parse_people_json(reports["key_people"])
                if parsed_people:
                    storage.save_key_people(agency_name, parsed_people)
                    console.print(f"[green]  Key People: {len(parsed_people)} people saved to Supabase[/green]")

        # ── Phase 2b: Social Follower Tracking (weekly/monthly) ──────────
        if cadence in (CADENCE_WEEKLY, CADENCE_MONTHLY) and competitors:
            console.print(f"[dim]  Module 10: Social Follower Tracking[/dim]")
            prev_followers = storage.load_latest_followers(agency_name)
            follower_ctx = {
                "competitors": competitors,
                "cadence": cadence,
                "previous_followers": prev_followers,
            }
            try:
                follower_report = await self.social_follower_agent.research(agency, follower_ctx)
                reports["social_followers"] = follower_report
                path = storage.save_report(agency_name, "social_followers", cadence, follower_report)
                console.print(f"[green]  Social Followers: {len(follower_report)} chars -> {path.name}[/green]")

                parsed = self.social_follower_agent.parse_followers_json(follower_report)
                if parsed:
                    storage.save_follower_snapshot(agency_name, parsed)
                    console.print(f"[green]  Followers: {len(parsed)} snapshots saved to Supabase[/green]")
            except Exception as e:
                console.print(f"[red]  Social Followers failed: {e}[/red]")
                reports["social_followers"] = f"[Error: {e}]"

        # ── Phase 3: DIFF (depends on phase 2) ───────────────────────────
        if cadence in (CADENCE_WEEKLY, CADENCE_MONTHLY):
            console.print(f"[dim]  Module 4: Competitive DIFF[/dim]")
            diff_prior = storage.load_latest_report(agency_name, "diff", cadence)

            # Load prior competitor metrics for trend analysis
            trend_data = ""
            if cadence == CADENCE_WEEKLY:
                all_trends = storage.load_competitor_trend(agency_name, weeks=4)
                if all_trends:
                    trend_data = json.dumps(all_trends, indent=2)

            diff_ctx = {
                "competitors": competitors,
                "cadence": cadence,
                "linkedin_report": reports.get("linkedin", ""),
                "industry_report": reports.get("industry", ""),
                "website_report": reports.get("website", ""),
                "prior_report": diff_prior or "",
                "competitor_trends": trend_data,
            }
            try:
                diff_report = await self.diff_agent.research(agency, diff_ctx)
                reports["diff"] = diff_report
                path = storage.save_report(agency_name, "diff", cadence, diff_report)
                _save_formatted(agency_name, "diff", cadence, diff_report, path)
                console.print(f"[green]  DIFF: {len(diff_report)} chars -> {path.name}[/green]")

                # Extract and save competitor metrics for longitudinal tracking
                if cadence == CADENCE_WEEKLY:
                    parsed_metrics = self.diff_agent.parse_metrics_json(diff_report)
                    if parsed_metrics:
                        for m in parsed_metrics:
                            comp_name = m.pop("competitor_name", None)
                            if comp_name:
                                storage.save_competitor_metrics(agency_name, comp_name, m)
                        console.print(f"[green]  Metrics: {len(parsed_metrics)} competitor metrics saved[/green]")

            except Exception as e:
                console.print(f"[red]  DIFF failed: {e}[/red]")
                reports["diff"] = f"[Error: {e}]"

        # ── Phase 4: Content Strategy (depends on phase 2+3) ─────────────
        if cadence in (CADENCE_WEEKLY, CADENCE_MONTHLY):
            console.print(f"[dim]  Module 6: Content Strategy[/dim]")
            cs_ctx = {
                "competitors": competitors,
                "cadence": cadence,
                "linkedin_report": reports.get("linkedin", ""),
                "industry_report": reports.get("industry", ""),
                "website_report": reports.get("website", ""),
                "diff_report": reports.get("diff", ""),
            }
            try:
                cs_report = await self.content_strategy_agent.research(agency, cs_ctx)
                reports["content_strategy"] = cs_report
                path = storage.save_report(agency_name, "content_strategy", cadence, cs_report)
                _save_formatted(agency_name, "content_strategy", cadence, cs_report, path)
                console.print(f"[green]  Content Strategy: {len(cs_report)} chars -> {path.name}[/green]")
            except Exception as e:
                console.print(f"[red]  Content Strategy failed: {e}[/red]")
                reports["content_strategy"] = f"[Error: {e}]"

        # ── Phase 5: Topic Intelligence (depends on all above) ─────────
        if cadence in (CADENCE_WEEKLY, CADENCE_MONTHLY):
            console.print(f"[dim]  Module 7: Topic Intelligence[/dim]")
            # Load previous topics for momentum comparison
            prev_topics = storage.load_topics(agency_name, limit=30)
            prev_topics_str = ""
            if prev_topics:
                prev_topics_str = json.dumps(
                    [{"topic": t["topic"], "momentum": t["momentum"],
                      "category": t.get("category", ""), "mention_count": t.get("mention_count", 0)}
                     for t in prev_topics],
                    indent=2,
                )

            topic_ctx = {
                "competitors": competitors,
                "cadence": cadence,
                "linkedin_report": reports.get("linkedin", ""),
                "industry_report": reports.get("industry", ""),
                "website_report": reports.get("website", ""),
                "diff_report": reports.get("diff", ""),
                "content_strategy_report": reports.get("content_strategy", ""),
                "previous_topics": prev_topics_str,
            }
            try:
                topic_report = await self.topic_intelligence_agent.research(agency, topic_ctx)
                reports["topics"] = topic_report
                path = storage.save_report(agency_name, "topics", cadence, topic_report)
                _save_formatted(agency_name, "topics", cadence, topic_report, path)
                console.print(f"[green]  Topics: {len(topic_report)} chars -> {path.name}[/green]")

                # Parse and persist structured topics
                parsed = self.topic_intelligence_agent.parse_topics_json(topic_report)
                if parsed:
                    storage.save_topics(agency_name, parsed)
                    console.print(f"[green]  Topics: {len(parsed)} topics saved to Supabase[/green]")
            except Exception as e:
                console.print(f"[red]  Topic Intelligence failed: {e}[/red]")
                reports["topics"] = f"[Error: {e}]"

        # ── Phase 6: Content Calendar (depends on all above) ──────────────
        if cadence in (CADENCE_WEEKLY, CADENCE_MONTHLY):
            console.print(f"[dim]  Module 9: Content Calendar[/dim]")

            # Build topics summary for calendar context
            topics_for_calendar = storage.load_topics(agency_name, limit=15)
            topics_str = ""
            if topics_for_calendar:
                topics_str = json.dumps(
                    [{"topic": t["topic"], "momentum": t["momentum"],
                      "category": t.get("category", ""), "relevance": t.get("relevance", "")}
                     for t in topics_for_calendar],
                    indent=2,
                )

            # Build people summary
            people_for_calendar = storage.load_key_people(agency_name, limit=5)
            people_str = ""
            if people_for_calendar:
                people_str = json.dumps(
                    [{"name": p["name"], "title": p.get("title", ""), "company": p.get("company", ""),
                      "topics": p.get("topics", []), "recent_activity": p.get("recent_activity", "")}
                     for p in people_for_calendar],
                    indent=2,
                )

            cal_ctx = {
                "competitors": competitors,
                "cadence": cadence,
                "content_strategy_report": reports.get("content_strategy", ""),
                "diff_report": reports.get("diff", ""),
                "topics_data": topics_str,
                "people_data": people_str,
            }
            try:
                cal_report = await self.content_calendar_agent.research(agency, cal_ctx)
                reports["content_calendar"] = cal_report
                path = storage.save_report(agency_name, "content_calendar", cadence, cal_report)
                _save_formatted(agency_name, "content_calendar", cadence, cal_report, path)
                console.print(f"[green]  Content Calendar: {len(cal_report)} chars -> {path.name}[/green]")

                # Parse and persist structured calendar
                from datetime import date as _date, timedelta
                today = _date.today()
                days_until_monday = (7 - today.weekday()) % 7
                if days_until_monday == 0:
                    days_until_monday = 7
                next_monday = today + timedelta(days=days_until_monday)
                week_start = next_monday.isoformat()

                parsed_cal = self.content_calendar_agent.parse_calendar_json(cal_report)
                if parsed_cal:
                    # Verification loop — check for fabricated claims and rewrite
                    console.print(f"[dim]  Verifying {len(parsed_cal)} calendar items...[/dim]")
                    parsed_cal = await self.content_calendar_agent.verify_and_rewrite(
                        agency, parsed_cal, cal_ctx
                    )
                    storage.save_content_calendar(agency_name, week_start, parsed_cal, cal_report)
                    console.print(f"[green]  Calendar: {len(parsed_cal)} items saved for week of {week_start}[/green]")
            except Exception as e:
                console.print(f"[red]  Content Calendar failed: {e}[/red]")
                reports["content_calendar"] = f"[Error: {e}]"

        return reports


def _ensure_manual_competitors(registry: list[dict], manual_names: list[str]) -> list[dict]:
    """Ensure all manual competitors are in the registry with source='manual'.

    - Manual competitors already in the registry get source='manual' tag
    - Missing manual competitors are added as stubs
    - AI-discovered competitors get source='discovered'
    """
    manual_lower = {n.lower(): n for n in manual_names}
    registry_lower = {c.get("name", "").lower(): c for c in registry}

    # Tag existing entries
    for comp in registry:
        name_lower = comp.get("name", "").lower()
        if name_lower in manual_lower:
            comp["source"] = "manual"
        elif not comp.get("source"):
            comp["source"] = "discovered"

    # Add any missing manual competitors
    for lower, original in manual_lower.items():
        if lower not in registry_lower:
            registry.append({"name": original, "source": "manual"})

    return registry


    # Modules that are internal data (JSON) — don't push to Slack as reports
_INTERNAL_MODULES = {"topics", "key_people", "content_calendar", "social_followers"}


def _save_formatted(agency_name: str, module: str, cadence: str, report: str, raw_path: Path) -> None:
    """Save formatted versions alongside the raw report, then deliver.

    Daily cadence: saves reports locally but does NOT send individual modules
    to Slack. Instead, a single consolidated digest is generated after all
    modules complete (see _generate_daily_digest).

    Weekly/monthly: delivers each module to Slack individually (higher-signal).
    """
    # Skip delivery for internal data modules (contain raw JSON)
    if module in _INTERNAL_MODULES:
        return

    # Prepend a standardized header with the correct date
    header = (
        f"# {module.upper()} Intelligence — {agency_name}\n"
        f"## {cadence.title()} Report — {date.today().strftime('%A %d %B %Y')}\n\n"
    )
    report = header + report

    # Also update the raw report file with the header
    raw_path.write_text(report, encoding="utf-8")

    # Daily: skip per-module Slack delivery (digest handles it)
    if cadence == CADENCE_DAILY:
        return

    # Weekly/monthly: deliver each module individually to Slack
    slack_content = formatter.format_slack_summary(agency_name, module, report)
    slack_path = raw_path.with_suffix(".slack.md")
    slack_path.write_text(slack_content, encoding="utf-8")
    deliver_to_slack(agency_name, module, cadence, slack_content)

    # Confluence version (for weekly and monthly)
    if cadence in (CADENCE_WEEKLY, CADENCE_MONTHLY):
        conf_content = formatter.format_confluence(agency_name, module, cadence, report)
        conf_path = raw_path.with_suffix(".confluence.md")
        conf_path.write_text(conf_content, encoding="utf-8")

        try:
            deliver_to_confluence(agency_name, module, cadence, conf_content)
        except Exception as e:
            console.print(f"[yellow]  Confluence delivery failed: {e}[/yellow]")


async def _generate_daily_digest(agency_name: str, reports: dict[str, str]) -> None:
    """Generate a single daily highlights digest from all module reports.

    Sends ONE Slack message per agency containing only genuinely noteworthy
    items: new client wins, major hires, product launches, significant content,
    competitive moves. Skips "no activity" filler entirely.
    """
    from core.agent import ResearchAgent
    from core.config import ANTHROPIC_API_KEY, MODEL, MAX_TOKENS
    from core.cost_tracker import cost_tracker

    # Collect daily module reports (skip errors and internal modules)
    module_texts = []
    for module in ("linkedin", "industry", "website"):
        text = reports.get(module, "")
        if text and not text.startswith("[Error"):
            module_texts.append(f"## {module.upper()}\n{text}")

    if not module_texts:
        console.print(f"[yellow]  Daily digest: no reports for {agency_name}[/yellow]")
        return

    combined = "\n\n---\n\n".join(module_texts)

    prompt = f"""You are a competitive intelligence analyst writing a daily Slack digest for the agency "{agency_name}".

Below are today's raw intelligence reports across LinkedIn, Industry, and Website monitoring.

Your job: extract ONLY the genuinely important items and write a single tight digest. Apply strict editorial judgement:

INCLUDE (these matter):
- New client wins or account losses
- Senior hires, departures, or leadership changes
- Acquisitions, mergers, funding, or restructuring
- Award wins or major shortlist announcements
- Significant new service launches or pivots
- Notable content that signals strategic direction
- Regulatory or industry shifts that affect the competitive landscape

EXCLUDE (this is noise):
- Generic blog posts or routine content updates
- "No new content detected" for any competitor
- Low-confidence mentions or vague signals
- Routine job postings (unless C-suite)
- Minor website copy changes

FORMAT (Slack mrkdwn):
- Start with a one-line verdict: either "Nothing significant today." or a count like "3 items worth noting:"
- If nothing significant, just send the one-line verdict — nothing more
- For each item: *Bold competitor name* — what happened (1-2 sentences max)
- No headers, no sections, no emojis, no filler — just the signal
- Maximum 5 items. If more, pick the top 5 by impact.

RAW REPORTS:

{combined}"""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=MODEL,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        digest = response.content[0].text

        cost_tracker.log_claude(
            response.usage.input_tokens,
            response.usage.output_tokens,
            label=f"daily_digest:{agency_name}",
        )

        # Wrap with agency header
        today_str = date.today().strftime("%A %d %B %Y")
        slack_msg = f"*Daily Intel — {agency_name}*\n_{today_str}_\n\n{digest}"

        deliver_to_slack(agency_name, "daily_digest", CADENCE_DAILY, slack_msg)
        console.print(f"[green]  Daily digest delivered for {agency_name} ({len(digest)} chars)[/green]")

    except Exception as e:
        console.print(f"[red]  Daily digest failed for {agency_name}: {e}[/red]")


def _save_website_snapshots(agency_name: str, web_ctx: dict, competitors: list[dict]) -> None:
    """Save website crawl snapshots for future change detection."""
    crawl_results = web_ctx.get("_crawl_results", [])
    for comp, result in crawl_results:
        if isinstance(result, Exception):
            continue
        pages = result.get("results", [])
        if pages:
            comp_name = comp.get("name", "?")
            storage.save_website_snapshot(agency_name, comp_name, pages)

            # Compute and log change detection
            prev_snapshots = web_ctx.get("_prev_snapshots", {})
            prev = prev_snapshots.get(comp_name)
            if prev:
                current_snap = []
                for p in pages:
                    import hashlib
                    content = p.get("raw_content", p.get("content", ""))
                    current_snap.append({
                        "url": p.get("url", ""),
                        "content_hash": hashlib.md5(content.encode()).hexdigest() if content else "",
                        "title": p.get("title", ""),
                        "word_count": len(content.split()) if content else 0,
                    })
                changes = storage.diff_snapshots(prev, current_snap)
                n, c, r = len(changes["new_pages"]), len(changes["changed_pages"]), len(changes["removed_pages"])
                if n or c or r:
                    console.print(
                        f"[dim]    {comp_name}: {n} new, {c} changed, {r} removed pages[/dim]"
                    )


# ---------------------------------------------------------------------------
# Convenience runner
# ---------------------------------------------------------------------------

async def run_surveillance(
    cadence: str = CADENCE_DAILY,
    agencies: list[dict] | None = None,
) -> dict[str, dict[str, str]]:
    """Run MCSA surveillance.

    Args:
        cadence: "daily", "weekly", or "monthly".
        agencies: Override agency list (defaults to all 5 Tomorrow agencies).

    Returns:
        dict: agency_name -> {module_name -> report_markdown}
    """
    orchestrator = MCSAOrchestrator(cadence=cadence, agencies=agencies)
    return await orchestrator.run()
