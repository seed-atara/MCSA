"""MCSA Orchestrator — coordinates surveillance modules across all agencies.

Runs the 5 capability modules for each agency, respecting cadence schedules,
feeding data downstream (LinkedIn + Industry + Website -> DIFF), persisting
reports and registries, and performing website change detection.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from pathlib import Path
from rich.console import Console
from rich.panel import Panel

from core.config import OUTPUT_DIR
from core.tools import clear_sources, get_all_sources
from core.cost_tracker import cost_tracker

from .config import AGENCIES, REPORTS, CADENCE_DAILY, CADENCE_WEEKLY, CADENCE_MONTHLY
from .agents import RegistryAgent, LinkedInAgent, IndustryAgent, DIFFAgent, WebsiteAgent
from . import storage
from . import formatter

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

        # Agents (stateless, shared across agencies)
        self.registry_agent = RegistryAgent()
        self.linkedin_agent = LinkedInAgent()
        self.industry_agent = IndustryAgent()
        self.diff_agent = DIFFAgent()
        self.website_agent = WebsiteAgent()

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

        # ── Phase 1: Registry (monthly only) ─────────────────────────────
        if cadence == CADENCE_MONTHLY:
            console.print(f"[dim]  Module 1: Competitor Registry[/dim]")
            try:
                registry_report = await self.registry_agent.research(
                    agency, {"existing_registry": competitors}
                )
                reports["registry"] = registry_report

                # Try to parse and persist the updated registry
                new_registry = self.registry_agent.parse_registry_json(registry_report)
                if new_registry:
                    storage.save_registry(agency_name, new_registry)
                    competitors = new_registry
                    self.registries[storage._safe(agency_name)] = new_registry
                    console.print(f"[green]  Registry: {len(new_registry)} competitors saved[/green]")
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

        # ── Phase 3: DIFF (depends on phase 2) ───────────────────────────
        if cadence in (CADENCE_WEEKLY, CADENCE_MONTHLY):
            console.print(f"[dim]  Module 4: Competitive DIFF[/dim]")
            diff_prior = storage.load_latest_report(agency_name, "diff", cadence)
            diff_ctx = {
                "competitors": competitors,
                "cadence": cadence,
                "linkedin_report": reports.get("linkedin", ""),
                "industry_report": reports.get("industry", ""),
                "website_report": reports.get("website", ""),
                "prior_report": diff_prior or "",
            }
            try:
                diff_report = await self.diff_agent.research(agency, diff_ctx)
                reports["diff"] = diff_report
                path = storage.save_report(agency_name, "diff", cadence, diff_report)
                _save_formatted(agency_name, "diff", cadence, diff_report, path)
                console.print(f"[green]  DIFF: {len(diff_report)} chars -> {path.name}[/green]")
            except Exception as e:
                console.print(f"[red]  DIFF failed: {e}[/red]")
                reports["diff"] = f"[Error: {e}]"

        return reports


def _save_formatted(agency_name: str, module: str, cadence: str, report: str, raw_path: Path) -> None:
    """Save Slack and Confluence formatted versions alongside the raw report."""
    report_dir = raw_path.parent

    # Slack version
    if cadence == CADENCE_DAILY:
        slack_content = formatter.format_slack_daily(agency_name, module, report)
    else:
        slack_content = formatter.format_slack_summary(agency_name, module, report)
    slack_path = raw_path.with_suffix(".slack.md")
    slack_path.write_text(slack_content, encoding="utf-8")

    # Confluence version (for weekly and monthly)
    if cadence in (CADENCE_WEEKLY, CADENCE_MONTHLY):
        conf_content = formatter.format_confluence(agency_name, module, cadence, report)
        conf_path = raw_path.with_suffix(".confluence.md")
        conf_path.write_text(conf_content, encoding="utf-8")


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
