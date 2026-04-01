"""SEED Orchestrator - Coordinates all 6C agents to produce PhD-grade intelligence briefs."""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from rich.console import Console
from rich.panel import Panel

from .agents import (
    CustomerAgent,
    CompanyAgent,
    CompetitorAgent,
    ContextAgent,
    CatalystAgent,
    ConstraintAgent,
    InfluencerAgent,
    SynthesisAgent,
)
from .config import (
    OUTPUT_DIR, BRAND_NAME, BRAND_FULL, BRAND_TAGLINE, BRAND_BY,
    ANTHROPIC_API_KEY, MODEL,
)
from .pdf_generator import markdown_to_pdf, markdown_to_pdf_redacted, markdown_to_pdf_proposal, markdown_to_pdf_brand_guidelines, markdown_to_pdf_media_strategy, markdown_to_pdf_citation_report, markdown_to_pdf_strategic_analysis
from .tools import clear_sources, get_all_sources
from .cost_tracker import cost_tracker
from .research_library import ResearchLibrary

console = Console()


class SixCOrchestrator:
    """Orchestrates the 6C research process with error recovery and comprehensive output."""
    
    def __init__(self, client_name: str, generate_redacted: bool = True, client_location: str = "", notes: str = "", company_website: str = "", product: str = "", social_links: dict | None = None, competitors: list[dict] | None = None, uploaded_documents: list[str] | None = None, progress_callback=None):
        self.client_name = client_name
        self.client_location = client_location
        self.generate_redacted = generate_redacted
        self.notes = notes
        self.company_website = company_website
        self.product = product
        self.progress_callback = progress_callback  # Called with (phase: int, total: int, description: str)
        self.social_links = social_links or {}
        self.competitors = competitors or []  # e.g. [{"name": "Garnier", "website": "https://..."}, ...]
        self.uploaded_documents = uploaded_documents or []  # File paths to uploaded PDFs/DOCX/PPTX
        self.research_results = {}
        self.context = {}
        self.errors = []
        
        # Initialize agents
        self.customer_agent = CustomerAgent()
        self.company_agent = CompanyAgent()
        self.competitor_agent = CompetitorAgent()
        self.context_agent = ContextAgent()
        self.catalyst_agent = CatalystAgent()
        self.constraint_agent = ConstraintAgent()
        self.influencer_agent = InfluencerAgent()
        self.synthesis_agent = SynthesisAgent()
    
    def _report_progress(self, phase: int, total: int, description: str) -> None:
        """Report phase progress via callback if one was provided."""
        if self.progress_callback:
            try:
                self.progress_callback(phase, total, description)
            except Exception:
                pass  # Never let callback errors break the research
    
    async def run(self) -> str:
        """Run the full 6C research process with error recovery."""
        
        # Clear sources registry and cost tracker for fresh tracking
        clear_sources()
        cost_tracker.reset()
        
        # Track research start time for citation log
        research_start_time = datetime.now()
        
        console.print(Panel(
            f"[bold]{BRAND_NAME} - {BRAND_FULL}[/bold]\n"
            f"[dim]{BRAND_TAGLINE}[/dim]\n\n"
            f"Client: [cyan]{self.client_name}[/cyan]",
            title=f"{BRAND_NAME} Research Engine",
            border_style="cyan"
        ))
        
        # Set user-provided location hint (may be empty — will be auto-detected after Phase 1)
        self.context["location"] = self.client_location
        
        # Set company website (direct URL avoids needing to search for it)
        if self.company_website:
            self.context["company_website"] = self.company_website
            console.print(f"[dim]   Company website: {self.company_website}[/dim]")
        
        # Set product focus (optional — narrows the research)
        if self.product:
            self.context["product"] = self.product
            console.print(f"[dim]   Product focus: {self.product}[/dim]")
        
        # Set social links (optional — lets agents scrape channels directly)
        if self.social_links:
            self.context["social_links"] = self.social_links
            links_summary = ", ".join(f"{k}: {v}" for k, v in self.social_links.items())
            console.print(f"[dim]   Social links: {links_summary}[/dim]")
        
        # Set target competitors (optional — focuses competitive analysis on specific brands)
        if self.competitors:
            self.context["competitors"] = self.competitors
            comp_names = ", ".join(c["name"] for c in self.competitors)
            console.print(f"[dim]   Target competitors: {comp_names}[/dim]")
        
        # Set research direction notes (guides all agents to focus on specific aspects)
        if self.notes:
            self.context["notes"] = self.notes
            console.print(f"[dim]   Research direction: {self.notes[:120]}{'...' if len(self.notes) > 120 else ''}[/dim]")
        
        # Process uploaded documents (workshops, decks, pitches, brand guidelines, reports)
        # These are PRIMARY SOURCES — more authoritative than web scraping for company context
        if self.uploaded_documents:
            from .document_ingest import process_uploaded_documents
            console.print(f"[dim]   Processing {len(self.uploaded_documents)} uploaded document(s)...[/dim]")
            doc_content = process_uploaded_documents(self.uploaded_documents)
            if doc_content and len(doc_content) > 100:
                self.context["uploaded_documents"] = doc_content
                console.print(f"[dim]   Extracted {len(doc_content):,} chars from uploaded documents[/dim]")
            else:
                console.print(f"[yellow]   No usable text extracted from uploaded documents[/yellow]")
        
        # Phase 1: Company research first (provides context for all others)
        self._report_progress(1, 7, "Company Foundation")
        console.print("\n[bold]Phase 1: Foundation Research[/bold]")
        if self.client_location:
            console.print(f"[dim]   User-provided location hint: {self.client_location}[/dim]")
        await self._run_agent_safe("company", self.company_agent)
        self.context["company"] = self.research_results.get("company", "")
        
        # Infer industry using Claude for much better results
        self.context["industry"] = await self._infer_industry_smart()
        console.print(f"[dim]   Industry identified: {self.context['industry']}[/dim]")
        
        # Infer geographic scope from company research — this overrides/enriches user input
        geo_scope = await self._infer_geographic_scope()
        self.context["geographic_scope"] = geo_scope
        self.context["location"] = geo_scope.get("search_context", self.client_location)
        console.print(f"[dim]   Geographic scope: {geo_scope.get('scope_level', 'Unknown')} — {geo_scope.get('summary', 'N/A')}[/dim]")
        
        # ── Research Library: Centralized data gathering ─────────────────────
        # Runs ALL web searches, Tavily Research reports, and URL extractions
        # for every agent dimension in one massive parallel batch.  Agents
        # downstream become pure analysts — no individual searching.
        self._report_progress(2, 7, "Research Library")
        console.print("\n[bold]Phase 2: Research Library — Gathering All Data[/bold]")
        
        library = ResearchLibrary(self.client_name, self.context)
        await library.gather()
        self.context["research_library"] = library
        
        console.print(
            f"[dim]   Library stats: {library.unique_queries_run} searches, "
            f"{library.total_research_topics} research reports, "
            f"{library.dedup_savings} dupes removed, "
            f"{library.total_chars:,} chars gathered[/dim]"
        )
        
        # ── Phase 3: Customer + Context (landscape analysis) ─────────────────
        # These two establish the foundational landscape: WHO is the audience and
        # WHAT is happening in the market.  Neither depends on the other, so they
        # run in parallel.  All subsequent agents benefit from their findings.
        self._report_progress(3, 7, "Customer + Context")
        console.print("\n[bold]Phase 3: Landscape Analysis — Customer + Context[/bold]")
        
        await asyncio.gather(
            self._run_agent_safe("customer", self.customer_agent),
            self._run_agent_safe("context", self.context_agent),
        )
        
        # Store Phase 3 results so downstream agents can reference them
        self.context["customer"] = self.research_results.get("customer", "")
        self.context["market_context"] = self.research_results.get("context", "")
        
        # ── Phase 4: Competitor + Influencer (informed by audience & market) ──
        # Competitor analysis is sharper when it knows the target audience and
        # market dynamics.  Influencer discovery is more targeted when it knows
        # which audience personas to map to and which platforms are trending.
        self._report_progress(4, 7, "Competitor + Influencer")
        console.print("\n[bold]Phase 4: Competitive & Influencer Analysis[/bold]")
        
        await asyncio.gather(
            self._run_agent_safe("competitor", self.competitor_agent),
            self._run_agent_safe("influencer", self.influencer_agent),
        )
        
        # Store Phase 4 results
        self.context["competitor"] = self.research_results.get("competitor", "")
        self.context["influencer"] = self.research_results.get("influencer", "")
        
        # ── Phase 5: Catalyst + Constraint (capstone — full picture) ─────────
        # Catalyst ("why now?") produces the strongest arguments when it can
        # reference competitive moves, audience shifts, market trends, and
        # influencer landscape gaps.  Constraint already expects customer and
        # competitor data (its prompt template references them) but previously
        # always received "No context yet."  Now it gets real data.
        self._report_progress(5, 7, "Catalyst + Constraint")
        console.print("\n[bold]Phase 5: Catalyst & Constraint Analysis[/bold]")
        
        await asyncio.gather(
            self._run_agent_safe("catalyst", self.catalyst_agent),
            self._run_agent_safe("constraint", self.constraint_agent),
        )
        
        # Inject geographic scope into research results so synthesis agents can use it
        geo = self.context.get("geographic_scope", {})
        if geo:
            self.research_results["_geographic_scope"] = (
                f"GEOGRAPHIC SCOPE: {geo.get('summary', 'Unknown')}\n"
                f"Scope level: {geo.get('scope_level', 'unknown')}\n"
                f"Headquarters: {geo.get('headquarters', 'Unknown')}\n"
                f"Operating regions: {', '.join(geo.get('operating_regions', []))}\n"
                f"Primary market: {geo.get('primary_market', 'Unknown')}\n\n"
                f"IMPORTANT: All research findings, competitor analysis, market data, and recommendations "
                f"in this document MUST be relevant to this geographic scope. Do not include data, "
                f"competitors, or market statistics from regions where this company does not operate "
                f"unless explicitly comparing against global benchmarks."
            )
        
        # Inject uploaded documents into research results so ALL synthesis agents can use them
        if self.context.get("uploaded_documents"):
            self.research_results["_uploaded_documents"] = self.context["uploaded_documents"]
        
        # Inject Firecrawl branding data (extracted during CompanyAgent phase)
        if self.context.get("_branding_data"):
            self.research_results["_branding_data"] = self.context["_branding_data"]
        
        # Inject employee content data from Research Library
        # This provides raw employee activity data to the Content Plan generator
        library = self.context.get("research_library")
        if library:
            employee_data = library.for_employee_content()
            if employee_data and len(employee_data) > 100:
                self.research_results["_employee_content"] = employee_data
        
        # Inject company website so synthesis agents can reference it
        if self.company_website:
            self.research_results["_company_website"] = (
                f"COMPANY WEBSITE: {self.company_website}\n"
                f"This URL was provided by the user as the official company website."
            )
        
        # Inject social links so synthesis agents can reference them
        if self.social_links:
            links_text = "\n".join(f"- {platform.title()}: {url}" for platform, url in self.social_links.items())
            self.research_results["_social_links"] = (
                f"SOCIAL MEDIA PROFILES (provided by user):\n{links_text}\n\n"
                f"These are the verified social media accounts for the company. "
                f"Use these when referencing their social presence and content strategy."
            )
        
        # Inject product focus so synthesis agents can see it
        if self.product:
            self.research_results["_product_focus"] = (
                f"PRODUCT FOCUS: {self.product}\n\n"
                f"IMPORTANT: The user has specified this product as the primary focus. "
                f"All analysis, content pillars, recommendations, and creative strategy "
                f"should be weighted towards this specific product rather than the company as a whole."
            )
        
        # Inject research direction notes so synthesis agents can see them
        if self.notes:
            self.research_results["_research_direction"] = (
                f"RESEARCH DIRECTION (from the user):\n"
                f"{self.notes}\n\n"
                f"IMPORTANT: The user specifically requested this research focus. "
                f"Ensure the final brief, recommendations, content pillars, and creative "
                f"strategy are all weighted towards this direction."
            )
        
        # Phase 6: Synthesis
        self._report_progress(6, 7, "Synthesis")
        console.print("\n[bold]Phase 6: Final Synthesis[/bold]")
        
        # Check we have enough data to synthesize (exclude internal keys starting with _)
        successful_agents = [k for k, v in self.research_results.items() if not k.startswith("_") and v and len(v) > 100]
        if len(successful_agents) < 3:
            console.print(f"[red]Only {len(successful_agents)} agents produced results. Minimum 3 required.[/red]")
            console.print(f"[red]Successful: {successful_agents}[/red]")
            console.print(f"[red]Errors: {self.errors}[/red]")
            raise RuntimeError("Insufficient research data for synthesis")
        
        final_brief = await self.synthesis_agent.synthesize(
            self.client_name, self.research_results
        )
        
        # Add citations section
        sources = get_all_sources()
        final_brief = self._add_citations(final_brief, sources)
        
        # Save full output
        output_path = self._save_output(final_brief)
        
        # Generate comprehensive citation log
        citation_log_path = None
        console.print("\n[bold]Generating Citation Log[/bold]")
        try:
            citation_log = self._generate_citation_log(sources, research_start_time)
            citation_log_path = self._save_citation_log(citation_log)
        except Exception as e:
            console.print(f"[yellow]Citation log generation failed: {e}[/yellow]")
        
        # Phase 7: Generate ALL output documents in parallel
        # Redacted, Proposal, Brand Guidelines, One-Pager, Content Plan, and HTML Brief all run concurrently
        redacted_path = None
        proposal_path = None
        brand_guidelines_path = None
        one_pager_path = None
        content_plan_path = None
        media_strategy_path = None
        strategic_analysis_path = None
        one_pager = None
        self._report_progress(7, 7, "Document Generation")
        console.print("\n[bold]Phase 7: Generating Output Documents (7 in parallel)[/bold]")
        
        # ── All documents in parallel ──
        output_coroutines = []
        output_labels = []
        
        # 0: Redacted preview
        if self.generate_redacted:
            output_coroutines.append(self.synthesis_agent.generate_redacted_version(
                self.client_name, final_brief
            ))
            output_labels.append("redacted")
        
        # 1: Internal proposal
        output_coroutines.append(self.synthesis_agent.generate_proposal(
            self.client_name, final_brief, self.research_results
        ))
        output_labels.append("proposal")
        
        # 2: Brand guidelines
        output_coroutines.append(self.synthesis_agent.generate_brand_guidelines(
            self.client_name, final_brief, self.research_results
        ))
        output_labels.append("brand_guidelines")
        
        # 3: One-pager
        output_coroutines.append(self.synthesis_agent.generate_one_pager(
            self.client_name, final_brief, self.research_results
        ))
        output_labels.append("one_pager")
        
        # 4: 90-Day Content Plan
        output_coroutines.append(self.synthesis_agent.generate_content_plan(
            self.client_name, final_brief, self.research_results
        ))
        output_labels.append("content_plan")
        
        # 5: Paid Media & SEO Strategy (high-level strategic document)
        output_coroutines.append(self.synthesis_agent.generate_media_strategy(
            self.client_name, final_brief, self.research_results
        ))
        output_labels.append("media_strategy")
        
        # 6: Strategic Analysis (MBA/PhD-grade comprehensive strategy document)
        output_coroutines.append(self.synthesis_agent.generate_strategic_analysis(
            self.client_name, final_brief, self.research_results
        ))
        output_labels.append("strategic_analysis")
        
        # Run batch 1 (7 document generations in parallel)
        output_results = await asyncio.gather(*output_coroutines, return_exceptions=True)
        
        # Process results
        output_map = dict(zip(output_labels, output_results))
        
        # Save redacted
        if "redacted" in output_map and not isinstance(output_map["redacted"], Exception):
            try:
                redacted_path = self._save_redacted_output(output_map["redacted"])
            except Exception as e:
                console.print(f"[yellow]Redacted save failed: {e}[/yellow]")
        elif "redacted" in output_map and isinstance(output_map["redacted"], Exception):
            console.print(f"[yellow]Redacted generation failed: {output_map['redacted']}[/yellow]")
        
        # Save proposal
        if not isinstance(output_map.get("proposal"), Exception):
            try:
                proposal_path = self._save_proposal_output(output_map["proposal"])
            except Exception as e:
                console.print(f"[yellow]Proposal save failed: {e}[/yellow]")
        else:
            console.print(f"[yellow]Proposal generation failed: {output_map.get('proposal')}[/yellow]")
        
        # Save brand guidelines
        if not isinstance(output_map.get("brand_guidelines"), Exception):
            try:
                brand_guidelines_path = self._save_brand_guidelines_output(output_map["brand_guidelines"])
            except Exception as e:
                console.print(f"[yellow]Brand guidelines save failed: {e}[/yellow]")
        else:
            console.print(f"[yellow]Brand guidelines generation failed: {output_map.get('brand_guidelines')}[/yellow]")
        
        # Save one-pager
        if not isinstance(output_map.get("one_pager"), Exception):
            one_pager = output_map["one_pager"]
            try:
                one_pager_path = self._save_one_pager_output(one_pager)
            except Exception as e:
                console.print(f"[yellow]One-pager save failed: {e}[/yellow]")
        else:
            console.print(f"[yellow]One-pager generation failed: {output_map.get('one_pager')}[/yellow]")
        
        # Save 90-Day Content Plan
        if not isinstance(output_map.get("content_plan"), Exception):
            try:
                content_plan_path = self._save_content_plan_output(output_map["content_plan"] or "")
            except Exception as e:
                console.print(f"[yellow]Content plan save failed: {e}[/yellow]")
        else:
            console.print(f"[yellow]Content plan generation failed: {output_map.get('content_plan')}[/yellow]")
        
        # Save Paid Media & SEO Strategy
        if not isinstance(output_map.get("media_strategy"), Exception):
            try:
                media_strategy_path = self._save_media_strategy_output(output_map["media_strategy"])
            except Exception as e:
                console.print(f"[yellow]Media strategy save failed: {e}[/yellow]")
        else:
            console.print(f"[yellow]Media strategy generation failed: {output_map.get('media_strategy')}[/yellow]")
        
        # Save Strategic Analysis
        if not isinstance(output_map.get("strategic_analysis"), Exception):
            try:
                strategic_analysis_path = self._save_strategic_analysis_output(output_map["strategic_analysis"])
            except Exception as e:
                console.print(f"[yellow]Strategic analysis save failed: {e}[/yellow]")
        else:
            console.print(f"[yellow]Strategic analysis generation failed: {output_map.get('strategic_analysis')}[/yellow]")
        
        # Summary panel
        run_dir = self._make_output_dir()
        summary_parts = [
            f"[bold green]Research Complete![/bold green]\n",
            f"Output folder: [cyan]{run_dir}[/cyan]\n",
            f"Full brief: [cyan]{output_path}[/cyan]",
        ]
        if one_pager_path:
            summary_parts.append(f"One-pager: [cyan]{one_pager_path}[/cyan]")
        if redacted_path:
            summary_parts.append(f"Redacted preview: [cyan]{redacted_path}[/cyan]")
        if proposal_path:
            summary_parts.append(f"Internal proposal: [cyan]{proposal_path}[/cyan]")
        if brand_guidelines_path:
            summary_parts.append(f"Brand guidelines: [cyan]{brand_guidelines_path}[/cyan]")
        if content_plan_path:
            summary_parts.append(f"90-Day Content Plan: [cyan]{content_plan_path}[/cyan]")
        if media_strategy_path:
            summary_parts.append(f"Media & SEO Strategy: [cyan]{media_strategy_path}[/cyan]")
        if strategic_analysis_path:
            summary_parts.append(f"Strategic Analysis: [cyan]{strategic_analysis_path}[/cyan]")
        if citation_log_path:
            summary_parts.append(f"Citation log: [cyan]{citation_log_path}[/cyan]")
        if self.errors:
            summary_parts.append(f"\n[yellow]Warnings: {len(self.errors)} agent(s) had issues[/yellow]")
        summary_parts.append(f"\nSources consulted: {len(sources)}")
        summary_parts.append(f"Agents completed: {len(successful_agents)}/7")
        
        console.print(Panel(
            "\n".join(summary_parts),
            border_style="green"
        ))
        
        return final_brief
    
    async def _run_agent_safe(self, name: str, agent) -> None:
        """Run an agent with error recovery - graceful degradation."""
        try:
            result = await agent.research(self.client_name, self.context)
            self.research_results[name] = result
            console.print(f"[green]   {agent.name}: Complete ({len(result)} chars)[/green]")
        except Exception as e:
            error_msg = f"{agent.name} failed: {type(e).__name__}: {str(e)[:200]}"
            self.errors.append(error_msg)
            console.print(f"[red]   {error_msg}[/red]")
            self.research_results[name] = f"[Research partially available - agent encountered an error: {type(e).__name__}]"
    
    def _add_citations(self, brief: str, sources: list[dict]) -> str:
        """Add a comprehensive citations section to the brief."""
        if not sources:
            return brief
        
        citations = "\n\n---\n\n## Sources & Citations\n\n"
        citations += "The following sources were consulted during the research process:\n\n"
        
        # Group sources by domain for cleaner presentation
        domains = {}
        for i, source in enumerate(sources, 1):
            url = source.get('url', '')
            title = source.get('title', 'Untitled')
            accessed = source.get('accessed', '')
            
            # Extract domain
            try:
                from urllib.parse import urlparse
                domain = urlparse(url).netloc
            except:
                domain = 'Other'
            
            if domain not in domains:
                domains[domain] = []
            domains[domain].append({
                'num': i,
                'title': title,
                'url': url,
                'accessed': accessed
            })
        
        # Output by domain
        citation_num = 1
        for domain, domain_sources in sorted(domains.items()):
            citations += f"### {domain}\n\n"
            for src in domain_sources:
                title = src['title'][:80] + '...' if len(src['title']) > 80 else src['title']
                citations += f"{citation_num}. **{title}**\n"
                citations += f"   - URL: {src['url']}\n"
                citations += f"   - Accessed: {src['accessed']}\n\n"
                citation_num += 1
        
        citations += f"\n*Total sources consulted: {len(sources)}*\n"
        
        return brief + citations
    
    def _generate_citation_log(self, sources: list[dict], start_time: datetime) -> str:
        """Generate a comprehensive citation log markdown document.
        
        This document is designed to demonstrate the robustness and breadth of
        the research process by cataloguing every single data point consulted.
        """
        from urllib.parse import urlparse
        from collections import Counter
        
        end_time = datetime.now()
        duration_minutes = (end_time - start_time).total_seconds() / 60
        
        # ─── Compute statistics ───
        total_sources = len(sources)
        total_content_chars = sum(s.get('content_length', 0) for s in sources)
        
        # By extraction method
        method_counts = Counter(s.get('extraction_method', 'unknown') for s in sources)
        
        # By domain
        domain_map: dict[str, list[dict]] = {}
        for s in sources:
            try:
                domain = urlparse(s.get('url', '')).netloc or 'N/A'
            except Exception:
                domain = 'N/A'
            domain_map.setdefault(domain, []).append(s)
        
        # Unique search queries
        queries = list(dict.fromkeys(
            s.get('query', '') for s in sources if s.get('query') and s['query'] != 'direct_scrape'
        ))
        
        # Direct scrapes (company websites etc.)
        direct_scrapes = [s for s in sources if s.get('query') == 'direct_scrape']
        
        # Successful agents
        successful_agents = [
            k for k, v in self.research_results.items() 
            if not k.startswith("_") and v and len(v) > 100
        ]
        
        # ─── Build the document ───
        lines = []
        
        # Header
        lines.append(f"# {BRAND_NAME} — Comprehensive Citation Log")
        lines.append("")
        lines.append(f"**Client:** {self.client_name}  ")
        lines.append(f"**Generated:** {end_time.strftime('%Y-%m-%d %H:%M')}  ")
        lines.append(f"**Research Duration:** {duration_minutes:.1f} minutes  ")
        lines.append(f"**{BRAND_BY}**  ")
        lines.append("")
        lines.append("---")
        lines.append("")
        
        # Purpose statement
        lines.append("## Purpose")
        lines.append("")
        lines.append(
            "This citation log provides a comprehensive record of every data source consulted "
            "during the research process. It is designed to demonstrate the rigour, breadth, and "
            "robustness of the analysis by documenting all information inputs — including web searches, "
            "deep content extractions, company website scrapes, and AI-powered research reports. "
            "Every claim and insight in the Intelligence Brief can be traced back to one or more "
            "of the sources listed below."
        )
        lines.append("")
        lines.append("---")
        lines.append("")
        
        # ─── Research Overview ───
        lines.append("## Research Overview")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Total Sources Consulted | **{total_sources}** |")
        lines.append(f"| Unique Domains Accessed | **{len(domain_map)}** |")
        lines.append(f"| Search Queries Executed | **{len(queries)}** |")
        lines.append(f"| Direct Website Scrapes | **{len(direct_scrapes)}** |")
        lines.append(f"| Total Content Extracted | **{total_content_chars:,} characters** (~{total_content_chars // 250:,} words) |")
        lines.append(f"| Research Agents Completed | **{len(successful_agents)}/7** ({', '.join(successful_agents)}) |")
        lines.append(f"| Research Duration | **{duration_minutes:.1f} minutes** |")
        lines.append(f"| Research Date | {end_time.strftime('%A %d %B %Y')} |")
        lines.append("")
        
        # ─── Extraction Methods Breakdown ───
        lines.append("### Data Collection Methods")
        lines.append("")
        method_labels = {
            'firecrawl_search': 'Firecrawl Search — real-time web search with full content extraction',
            'firecrawl_extract': 'Firecrawl Extract — advanced content extraction from URLs',
            'firecrawl_research': 'Firecrawl Research — search + scrape + AI synthesis',
            'firecrawl_crawl': 'Firecrawl Crawl — deep website crawling with markdown extraction',
            'firecrawl_branding': 'Firecrawl Branding — brand identity extraction',
            'firecrawl': 'Firecrawl — intelligent web scraping with structured markdown extraction',
            'basic_scrape': 'Direct HTTP Scrape — BeautifulSoup-based content extraction',
            'unknown': 'Other / Unclassified',
        }
        lines.append("| Method | Sources | Description |")
        lines.append("|--------|---------|-------------|")
        for method, count in sorted(method_counts.items(), key=lambda x: x[1], reverse=True):
            label = method_labels.get(method, method)
            lines.append(f"| {method} | {count} | {label} |")
        lines.append("")
        lines.append("---")
        lines.append("")
        
        # ─── Search Queries ───
        lines.append("## Search Queries Executed")
        lines.append("")
        lines.append(
            f"The following **{len(queries)} search queries** were executed across all research agents "
            "to gather intelligence. Each query was designed to explore a specific dimension of the "
            "6C research framework."
        )
        lines.append("")
        for i, q in enumerate(queries, 1):
            lines.append(f"{i}. {q}")
        lines.append("")
        if direct_scrapes:
            lines.append(f"Additionally, **{len(direct_scrapes)} websites** were scraped directly "
                        "(company sites, product pages, etc.).")
            lines.append("")
        lines.append("---")
        lines.append("")
        
        # ─── Full Citation List (numbered) ───
        lines.append("## Complete Citation List")
        lines.append("")
        lines.append(
            f"All **{total_sources} sources** are listed below in the order they were accessed, "
            "with full metadata including URL, title, extraction method, content volume, "
            "and the search query that led to discovery."
        )
        lines.append("")
        
        for i, source in enumerate(sources, 1):
            url = source.get('url', 'N/A')
            title = source.get('title', 'Untitled')
            query = source.get('query', 'N/A')
            accessed = source.get('accessed', 'N/A')
            method = source.get('extraction_method', 'unknown')
            content_len = source.get('content_length', 0)
            snippet = source.get('snippet', '')
            
            lines.append(f"### [{i}] {title}")
            lines.append("")
            lines.append(f"- **URL:** {url}")
            lines.append(f"- **Accessed:** {accessed}")
            lines.append(f"- **Method:** `{method}`")
            lines.append(f"- **Content Extracted:** {content_len:,} characters")
            if query and query != 'direct_scrape':
                lines.append(f"- **Query:** *\"{query}\"*")
            elif query == 'direct_scrape':
                lines.append(f"- **Query:** Direct website scrape")
            if snippet:
                # Clean snippet for markdown
                clean_snippet = snippet.replace('\n', ' ').replace('|', '\\|').strip()
                if len(clean_snippet) > 150:
                    clean_snippet = clean_snippet[:150] + "..."
                lines.append(f"- **Preview:** {clean_snippet}")
            lines.append("")
        
        lines.append("---")
        lines.append("")
        
        # ─── Domain Breakdown ───
        lines.append("## Sources by Domain")
        lines.append("")
        lines.append(
            f"Sources grouped by the **{len(domain_map)} unique domains** accessed during research. "
            "This breakdown demonstrates the diversity of information sources consulted."
        )
        lines.append("")
        
        # Sort domains by count (descending)
        for domain, domain_sources in sorted(domain_map.items(), key=lambda x: len(x[1]), reverse=True):
            total_chars = sum(s.get('content_length', 0) for s in domain_sources)
            lines.append(f"### {domain} ({len(domain_sources)} source{'s' if len(domain_sources) != 1 else ''})")
            lines.append("")
            lines.append(f"*Total content: {total_chars:,} characters*")
            lines.append("")
            lines.append("| # | Title | Method | Content | Accessed |")
            lines.append("|---|-------|--------|---------|----------|")
            for s in domain_sources:
                # Find the global citation number
                global_idx = next((idx for idx, gs in enumerate(sources, 1) if gs['url'] == s['url']), '?')
                title_short = s.get('title', 'Untitled')
                if len(title_short) > 60:
                    title_short = title_short[:57] + "..."
                method = s.get('extraction_method', 'unknown')
                c_len = s.get('content_length', 0)
                accessed = s.get('accessed', 'N/A')
                lines.append(f"| [{global_idx}] | {title_short} | `{method}` | {c_len:,} chars | {accessed} |")
            lines.append("")
        
        lines.append("---")
        lines.append("")
        
        # ─── Data Quality & Methodology ───
        lines.append("## Research Methodology & Data Quality")
        lines.append("")
        lines.append("### Methodology")
        lines.append("")
        lines.append(
            "This research was conducted using the SEED 6C Intelligence Framework, which deploys "
            "seven specialised AI research agents in a structured pipeline:"
        )
        lines.append("")
        lines.append("1. **Company Agent** — Analyses the client's positioning, offerings, digital presence, and brand identity")
        lines.append("2. **Customer Agent** — Identifies target audiences, personas, pain points, and content preferences")
        lines.append("3. **Competitor Agent** — Maps the competitive landscape, content strategies, and white-space opportunities")
        lines.append("4. **Context Agent** — Examines market trends, industry dynamics, and cultural/platform shifts")
        lines.append("5. **Catalyst Agent** — Identifies urgency drivers, timing windows, and momentum factors")
        lines.append("6. **Constraint Agent** — Reviews brand guidelines, compliance requirements, and operational limits")
        lines.append("7. **Influencer Agent** — Maps the influencer ecosystem with channel-weighted search across LinkedIn, Instagram, TikTok, YouTube, Twitter/X, and Podcasts")
        lines.append("")
        lines.append("Each agent conducts independent research using multiple data collection methods, "
                     "then a **Synthesis Agent** combines all findings into a unified intelligence brief.")
        lines.append("")
        
        lines.append("### Data Collection Techniques")
        lines.append("")
        lines.append("| Technique | Purpose | Quality Control |")
        lines.append("|-----------|---------|-----------------|")
        lines.append("| **Web Search (Tavily)** | Broad discovery of relevant sources via relevance-ranked search | Multiple queries per topic; cross-referenced results |")
        lines.append("| **Deep Extraction (Tavily Extract)** | Full-page content extraction for in-depth analysis | Advanced extraction mode with query-focused chunking |")
        lines.append("| **Research Reports (Tavily Research)** | AI-powered multi-source synthesis reports | Pro-model with numbered citations |")
        lines.append("| **Web Scraping (Firecrawl / httpx)** | Direct company website and product page analysis | Structured markdown extraction; fallback methods |")
        lines.append("| **Dual Extraction** | Parallel Tavily + Firecrawl extraction for corroboration | Longer extraction kept as primary; secondary used for validation |")
        lines.append("")
        
        lines.append("### Corroboration & Cross-Referencing")
        lines.append("")
        lines.append(
            "Where possible, sources were accessed using multiple extraction methods simultaneously. "
            "This dual-extraction approach provides natural corroboration — facts that appear "
            "consistently across different extraction methods are more reliable. The research pipeline "
            "also cross-references findings across all six agent dimensions, so that claims made in one "
            "area (e.g., market trends) are validated against evidence from another (e.g., competitor activity)."
        )
        lines.append("")
        
        # Coverage assessment
        lines.append("### Coverage Assessment")
        lines.append("")
        lines.append("| Dimension | Status | Sources | Confidence |")
        lines.append("|-----------|--------|---------|------------|")
        for agent_name in ['company', 'customer', 'competitor', 'context', 'catalyst', 'constraint', 'influencer']:
            agent_result = self.research_results.get(agent_name, "")
            if agent_result and len(agent_result) > 100:
                status = "Complete"
                confidence = "High" if len(agent_result) > 2000 else "Medium"
            else:
                status = "Partial / Failed"
                confidence = "Low"
            # Count sources that likely relate to this agent (heuristic via query content)
            agent_source_count = "—"
            lines.append(f"| {agent_name.title()} | {status} | {agent_source_count} | {confidence} |")
        lines.append("")
        
        lines.append("---")
        lines.append("")
        
        # ─── Footer ───
        lines.append("## Disclaimer")
        lines.append("")
        lines.append(
            "This citation log was generated automatically by the SEED research system. "
            "All sources were accessed programmatically at the times indicated. Content from "
            "third-party sources is used for research and analysis purposes only. URLs may change "
            "or become unavailable after the access date. The extraction previews shown are "
            "abbreviated excerpts; full extracted content was used during the analysis process."
        )
        lines.append("")
        lines.append(f"*{BRAND_NAME} — {BRAND_FULL}*  ")
        lines.append(f"*Generated: {end_time.strftime('%Y-%m-%d %H:%M')} | {total_sources} sources | {len(queries)} queries | {duration_minutes:.1f} min*")
        lines.append("")
        
        return "\n".join(lines)
    
    def _save_citation_log(self, citation_log: str) -> Path:
        """Save the comprehensive citation log. MD in raw-research/, PDF in run folder root."""
        run_dir = self._make_output_dir()
        raw_dir = self._get_raw_research_dir()
        
        md_path = raw_dir / "Citation-Log.md"
        md_path.write_text(citation_log, encoding="utf-8")
        console.print(f"[dim]Saved citation log markdown: {md_path} ({len(citation_log):,} chars)[/dim]")
        
        # Generate Citation Report PDF in run folder root
        try:
            pdf_path = markdown_to_pdf_citation_report(
                citation_log, md_path,
                pdf_output_path=run_dir / "Citation-Report.pdf"
            )
            console.print(f"[dim]Saved Citation Report PDF: {pdf_path}[/dim]")
        except Exception as e:
            console.print(f"[yellow]Citation Report PDF generation failed: {e}[/yellow]")
        
        return md_path
    
    async def _infer_industry_smart(self) -> str:
        """Use Claude to intelligently infer the industry from company research."""
        company_text = self.research_results.get("company", "")
        if not company_text or len(company_text) < 50:
            return self._infer_industry_fallback()
        
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            response = client.messages.create(
                model=MODEL,
                max_tokens=100,
                system="You are an industry classification expert. Given information about a company, identify their primary industry/sector in 3-5 descriptive words. Just return the industry classification, nothing else.",
                messages=[{
                    "role": "user",
                    "content": f"What industry is this company in? Company: {self.client_name}\n\nCompany info: {company_text[:2000]}"
                }]
            )
            if hasattr(response, "usage"):
                cost_tracker.log_claude(response.usage.input_tokens, response.usage.output_tokens, label="infer_industry")
            return response.content[0].text.strip()
        except Exception:
            return self._infer_industry_fallback()
    
    async def _infer_geographic_scope(self) -> dict:
        """Use Claude to infer the geographic scope and operating regions from company research.
        
        Returns a dict with:
        - scope_level: 'local' | 'regional' | 'national' | 'multinational' | 'global'
        - headquarters: where the company is based (e.g. 'Sydney, Australia')
        - operating_regions: list of specific regions/countries they operate in
        - primary_market: the single most important market for search context
        - search_context: a concise string to append to search queries (e.g. 'Australia East Coast')
        - summary: human-readable one-line summary
        """
        company_text = self.research_results.get("company", "")
        if not company_text or len(company_text) < 50:
            # Fall back to user-provided location or empty
            return {
                "scope_level": "unknown",
                "headquarters": self.client_location or "Unknown",
                "operating_regions": [self.client_location] if self.client_location else [],
                "primary_market": self.client_location or "",
                "search_context": self.client_location or "",
                "summary": self.client_location or "Geographic scope could not be determined",
            }
        
        try:
            import anthropic
            import json as _json
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            
            user_hint = f"\nUser-provided location hint: {self.client_location}" if self.client_location else ""
            
            geo_response = client.messages.create(
                model=MODEL,
                max_tokens=500,
                system="""You are a business geography analyst. Given research about a company, determine their geographic operating scope.

Return ONLY valid JSON with these fields:
- "scope_level": one of "local", "regional", "national", "multinational", "global"
  - local: single city only (e.g. one restaurant in Manchester, a bakery with 2 shops in the same city)
  - regional: multiple cities within one part of a country (e.g. stores in Sydney + Melbourne + Brisbane = East Coast Australia; shops in London + Brighton + Oxford = South East England). This is the right level for a business in 2-3 cities within the same region.
  - national: operates across most of one country (e.g. Greggs across the UK, In-N-Out across multiple US states)
  - multinational: operates in multiple countries but not truly global (e.g. Aldi in Europe + Australia)
  - global: worldwide presence across most continents (e.g. L'Oreal, Nike, Apple)
- "headquarters": city and country of HQ (e.g. "Sydney, Australia")
- "operating_regions": array listing EVERY specific city, region, or country they operate in. Be exhaustive — list all locations found in the research (e.g. ["Sydney", "Melbourne", "Brisbane"] or ["UK", "France", "Germany", "USA"])
- "primary_market": the single most important market (e.g. "East Coast Australia" or "United Kingdom" or "Global")
- "search_context": a SHORT string (2-5 words max) to append to web search queries to keep results geographically relevant. This must be BROAD ENOUGH to cover all operating regions. For global companies use "". For local use the city (e.g. "Manchester UK"). For regional use the region (e.g. "East Coast Australia", "South East England"). For national use the country (e.g. "United Kingdom", "Australia").
- "summary": one-line human-readable summary that lists the specific cities/regions (e.g. "Regional cookie chain — East Coast Australia (Sydney, Melbourne, Brisbane)")

Be precise. A single-location cookie shop in Sydney is "local", not "regional". A cookie shop with stores in Sydney AND Melbourne is "regional", not "local". L'Oreal is "global", not "national".""",
                messages=[{
                    "role": "user",
                    "content": f"Determine the geographic scope for: {self.client_name}{user_hint}\n\nCompany research:\n{company_text[:3000]}"
                }]
            )
            
            if hasattr(geo_response, "usage"):
                cost_tracker.log_claude(geo_response.usage.input_tokens, geo_response.usage.output_tokens, label="infer_geographic_scope")
            raw = geo_response.content[0].text.strip()
            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                if raw.endswith("```"):
                    raw = raw[:-3]
                raw = raw.strip()
            
            geo = _json.loads(raw)
            
            # For global companies, don't restrict search queries geographically
            if geo.get("scope_level") == "global":
                geo["search_context"] = ""
            
            return geo
            
        except Exception as e:
            console.print(f"[yellow]   Geographic scope inference failed ({type(e).__name__}), using user-provided location[/yellow]")
            return {
                "scope_level": "unknown",
                "headquarters": self.client_location or "Unknown",
                "operating_regions": [self.client_location] if self.client_location else [],
                "primary_market": self.client_location or "",
                "search_context": self.client_location or "",
                "summary": self.client_location or "Geographic scope could not be determined",
            }
    
    def _infer_industry_fallback(self) -> str:
        """Fallback keyword-based industry inference."""
        company_text = self.research_results.get("company", "").lower()
        
        industry_keywords = {
            "hospitality hotel travel accommodation": ["hotel", "hospitality", "accommodation", "resort", "boutique hotel"],
            "restaurant food service dining": ["restaurant", "food", "dining", "cafe", "catering", "noodle", "kitchen"],
            "technology software SaaS": ["tech", "software", "saas", "platform", "app", "digital", "AI", "artificial intelligence"],
            "beauty cosmetics skincare": ["beauty", "cosmetic", "skincare", "makeup", "hair", "shampoo", "fragrance"],
            "fashion apparel luxury": ["fashion", "clothing", "apparel", "luxury", "designer", "wear"],
            "retail ecommerce": ["retail", "ecommerce", "shop", "store", "marketplace"],
            "financial services fintech": ["finance", "banking", "fintech", "investment", "insurance"],
            "health wellness fitness": ["health", "wellness", "fitness", "gym", "nutrition", "supplement"],
            "media entertainment content": ["media", "entertainment", "content", "streaming", "music", "podcast"],
            "marketing advertising agency": ["marketing", "advertising", "agency", "creative", "digital marketing", "production"],
            "real estate property": ["real estate", "property", "spaces", "coworking", "office"],
            "education edtech": ["education", "learning", "edtech", "course", "training"],
        }
        
        for industry, keywords in industry_keywords.items():
            if any(kw in company_text for kw in keywords):
                return industry
        
        return "business services"
    
    def _make_safe_name(self) -> str:
        """Generate a filesystem-safe client name."""
        return self.client_name.replace(" ", "-").replace("/", "-").replace("'", "")
    
    def _make_output_dir(self) -> Path:
        """Create and return the output folder for this research run.
        
        Convention: CLIENT_DATE_TIME (e.g. LOreal-Elvive_20260207_021852)
        All outputs for a single run are packaged together in one folder.
        """
        if not hasattr(self, '_run_output_dir'):
            safe_name = self._make_safe_name()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            folder_name = f"{safe_name}_{timestamp}"
            self._run_output_dir = OUTPUT_DIR / folder_name
            self._run_output_dir.mkdir(parents=True, exist_ok=True)
        return self._run_output_dir
    
    def _make_header(self, title: str = "Content Intelligence Brief", subtitle: str = "") -> str:
        """Generate a standard markdown header for output documents."""
        # Use explicit product if provided; otherwise split client_name heuristically
        if self.product:
            client = self.client_name.replace(self.product, "").strip()
            product = self.product
        else:
            parts = self.client_name.split()
            if len(parts) > 1:
                client = parts[0]
                product = " ".join(parts[1:])
            else:
                client = self.client_name
                product = ""
        
        header = f"""# {title}

**Client:** {client}  
**Product:** {product if product else self.client_name}  
"""
        if self.company_website:
            header += f"**Website:** {self.company_website}  \n"
        header += f"""**Generated:** {datetime.now().strftime("%Y-%m-%d %H:%M")}  
**{BRAND_BY}**  
"""
        if subtitle:
            header += f"\n{subtitle}\n"
        
        header += "\n---\n\n"
        return header
    
    def _get_raw_research_dir(self) -> Path:
        """Get the raw-research subdirectory (all markdown files go here)."""
        raw_dir = self._make_output_dir() / "raw-research"
        raw_dir.mkdir(exist_ok=True)
        return raw_dir

    def _save_output(self, brief: str) -> Path:
        """Save the full intelligence brief. MD in raw-research/, PDF in run folder root."""
        run_dir = self._make_output_dir()
        raw_dir = self._get_raw_research_dir()
        
        full_content = self._make_header() + brief
        
        # Save markdown to raw-research/
        md_path = raw_dir / "Intelligence-Brief.md"
        md_path.write_text(full_content)
        console.print(f"[dim]Saved markdown: {md_path}[/dim]")
        
        # Generate PDF in run folder root
        try:
            pdf_path = markdown_to_pdf(full_content, md_path, pdf_output_path=run_dir / "Intelligence-Brief.pdf")
            console.print(f"[dim]Saved PDF: {pdf_path}[/dim]")
        except Exception as e:
            console.print(f"[yellow]PDF generation failed: {e}[/yellow]")
        
        # Save individual agent research sections for reference
        for section, content in self.research_results.items():
            section_path = raw_dir / f"{section}.md"
            section_path.write_text(f"# {section.upper()} INTELLIGENCE\n\n{content}")
        
        return md_path
    
    def _save_redacted_output(self, redacted_brief: str) -> Path:
        """Save the redacted/preview version. MD in raw-research/, PDF in run folder root."""
        run_dir = self._make_output_dir()
        raw_dir = self._get_raw_research_dir()
        
        subtitle = (
            f"> **PREVIEW VERSION** - This is a redacted preview of the full {BRAND_NAME} Intelligence Brief.\n"
            f"> Contact the {BRAND_NAME} team to unlock the complete document with full strategic analysis, \n"
            f"> creative playbook, content calendar, and measurement framework."
        )
        full_redacted = self._make_header("Content Intelligence Brief - PREVIEW", subtitle) + redacted_brief
        
        # Save markdown to raw-research/
        md_path = raw_dir / "Preview-Brief.md"
        md_path.write_text(full_redacted)
        console.print(f"[dim]Saved redacted markdown: {md_path}[/dim]")
        
        # Save PDF in run folder root
        try:
            pdf_path = markdown_to_pdf_redacted(full_redacted, md_path, pdf_output_path=run_dir / "Preview-Brief.pdf")
            console.print(f"[dim]Saved redacted PDF: {pdf_path}[/dim]")
        except Exception as e:
            console.print(f"[yellow]Redacted PDF generation failed: {e}[/yellow]")
        
        return md_path

    def _save_proposal_output(self, proposal_brief: str) -> Path:
        """Save the internal proposal. MD in raw-research/, PDF in run folder root."""
        run_dir = self._make_output_dir()
        raw_dir = self._get_raw_research_dir()
        
        subtitle = (
            "> **INTERNAL USE ONLY** — This document contains pricing strategy and commercial \n"
            "> assessments. Do not share with the client. Use this to prepare your proposal \n"
            "> and inform the sales conversation."
        )
        full_proposal = self._make_header("Internal Proposal & Pricing", subtitle) + proposal_brief
        
        # Save markdown to raw-research/
        md_path = raw_dir / "Internal-Proposal.md"
        md_path.write_text(full_proposal)
        console.print(f"[dim]Saved proposal markdown: {md_path}[/dim]")
        
        # Save PDF in run folder root
        try:
            pdf_path = markdown_to_pdf_proposal(full_proposal, md_path, pdf_output_path=run_dir / "Internal-Proposal.pdf")
            console.print(f"[dim]Saved proposal PDF: {pdf_path}[/dim]")
        except Exception as e:
            console.print(f"[yellow]Proposal PDF generation failed: {e}[/yellow]")
        
        return md_path

    def _save_brand_guidelines_output(self, brand_guidelines: str) -> Path:
        """Save the brand guidelines. MD in raw-research/, PDF in run folder root."""
        run_dir = self._make_output_dir()
        raw_dir = self._get_raw_research_dir()
        
        subtitle = (
            f"> **BRAND GUIDELINES** — Research-informed brand identity system and creative \n"
            f"> production guide. Generated from the {BRAND_NAME} 6C Intelligence Framework."
        )
        full_brand_guide = self._make_header("Brand Guidelines", subtitle) + brand_guidelines
        
        # Save markdown to raw-research/
        md_path = raw_dir / "Brand-Guidelines.md"
        md_path.write_text(full_brand_guide)
        console.print(f"[dim]Saved brand guidelines markdown: {md_path}[/dim]")
        
        # Save PDF in run folder root
        try:
            pdf_path = markdown_to_pdf_brand_guidelines(full_brand_guide, md_path, pdf_output_path=run_dir / "Brand-Guidelines.pdf")
            console.print(f"[dim]Saved brand guidelines PDF: {pdf_path}[/dim]")
        except Exception as e:
            console.print(f"[yellow]Brand guidelines PDF generation failed: {e}[/yellow]")
        
        return md_path

    def _save_html_output(self, html_content: str, filename: str) -> Path:
        """Save a Claude-generated HTML file into the html/ subdirectory."""
        run_dir = self._make_output_dir()
        html_dir = run_dir / "html"
        html_dir.mkdir(exist_ok=True)
        
        # Strip any markdown code fences Claude may have wrapped around the HTML
        content = html_content.strip()
        if content.startswith("```"):
            # Remove opening fence (possibly ```html)
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
        if content.endswith("```"):
            content = content[:-3].strip()
        
        # Validate HTML completeness — detect truncated output from token limits
        if "</html>" not in content.lower():
            console.print(f"[bold red]WARNING: HTML output for {filename} appears truncated (missing </html> tag).[/bold red]")
            console.print(f"[yellow]Attempting to repair by closing open tags...[/yellow]")
            # Attempt basic repair: close any open script/body/html tags
            if "<script>" in content and "</script>" not in content:
                content += "\n    </script>"
            if "<body" in content and "</body>" not in content:
                content += "\n</body>"
            content += "\n</html>"
        
        html_path = html_dir / filename
        html_path.write_text(content, encoding="utf-8")
        console.print(f"[dim]Saved HTML: {html_path}[/dim]")
        return html_path

    def _save_one_pager_output(self, one_pager: str) -> Path:
        """Save the one-page executive summary. MD in raw-research/, PDF in run folder root."""
        run_dir = self._make_output_dir()
        raw_dir = self._get_raw_research_dir()
        
        subtitle = (
            f"> **EXECUTIVE SUMMARY** — One-page overview of the {BRAND_NAME} 6C Intelligence Brief.\n"
            f"> For full analysis, see the complete Intelligence Brief."
        )
        full_one_pager = self._make_header("Executive Summary — One Pager", subtitle) + one_pager
        
        # Save markdown to raw-research/
        md_path = raw_dir / "One-Pager.md"
        md_path.write_text(full_one_pager)
        console.print(f"[dim]Saved one-pager markdown: {md_path}[/dim]")
        
        # Save PDF in run folder root
        try:
            pdf_path = markdown_to_pdf(full_one_pager, md_path, pdf_output_path=run_dir / "One-Pager.pdf")
            console.print(f"[dim]Saved one-pager PDF: {pdf_path}[/dim]")
        except Exception as e:
            console.print(f"[yellow]One-pager PDF generation failed: {e}[/yellow]")
        
        return md_path

    def _save_content_plan_output(self, content_plan: str) -> Path:
        """Save the 90-Day Content Plan. MD in raw-research/, PDF in run folder root."""
        run_dir = self._make_output_dir()
        raw_dir = self._get_raw_research_dir()
        
        subtitle = (
            f"> **90-DAY CONTENT PLAN** — Tactical content calendar, creative playbook, and measurement\n"
            f"> framework. Companion document to the {BRAND_NAME} 6C Intelligence Brief."
        )
        full_content_plan = self._make_header("90-Day Content Plan", subtitle) + content_plan
        
        # Save markdown to raw-research/
        md_path = raw_dir / "Content-Plan.md"
        md_path.write_text(full_content_plan, encoding="utf-8")
        console.print(f"[dim]Saved content plan markdown: {md_path}[/dim]")
        
        # Save PDF in run folder root
        try:
            pdf_path = markdown_to_pdf(full_content_plan, md_path, pdf_output_path=run_dir / "Content-Plan.pdf")
            console.print(f"[dim]Saved content plan PDF: {pdf_path}[/dim]")
        except Exception as e:
            console.print(f"[yellow]Content plan PDF generation failed: {e}[/yellow]")
        
        return md_path

    def _save_media_strategy_output(self, media_strategy: str) -> Path:
        """Save the Paid Media & SEO Strategy. MD in raw-research/, PDF in run folder root."""
        run_dir = self._make_output_dir()
        raw_dir = self._get_raw_research_dir()
        
        subtitle = (
            f"> **PAID MEDIA & SEO STRATEGY** — High-level channel mix, search optimisation roadmap,\n"
            f"> and integrated media flywheel. Companion document to the {BRAND_NAME} 6C Intelligence Brief."
        )
        full_media_strategy = self._make_header("Paid Media & SEO Strategy", subtitle) + media_strategy
        
        # Save markdown to raw-research/
        md_path = raw_dir / "Media-Strategy.md"
        md_path.write_text(full_media_strategy, encoding="utf-8")
        console.print(f"[dim]Saved media strategy markdown: {md_path}[/dim]")
        
        # Save PDF in run folder root
        try:
            pdf_path = markdown_to_pdf_media_strategy(full_media_strategy, md_path, pdf_output_path=run_dir / "Media-Strategy.pdf")
            console.print(f"[dim]Saved media strategy PDF: {pdf_path}[/dim]")
        except Exception as e:
            console.print(f"[yellow]Media strategy PDF generation failed: {e}[/yellow]")
        
        return md_path

    def _save_strategic_analysis_output(self, strategic_analysis: str) -> Path:
        """Save the Strategic Analysis. MD in raw-research/, PDF in run folder root."""
        run_dir = self._make_output_dir()
        raw_dir = self._get_raw_research_dir()
        
        subtitle = (
            f"> **STRATEGIC ANALYSIS** — Comprehensive business and content strategy analysis using\n"
            f"> Porter's Five Forces, PESTEL, SWOT, Ansoff, and Blue Ocean frameworks. Companion document to the {BRAND_NAME} 6C Intelligence Brief."
        )
        full_strategic_analysis = self._make_header("Strategic Analysis", subtitle) + strategic_analysis
        
        # Save markdown to raw-research/
        md_path = raw_dir / "Strategic-Analysis.md"
        md_path.write_text(full_strategic_analysis, encoding="utf-8")
        console.print(f"[dim]Saved strategic analysis markdown: {md_path}[/dim]")
        
        # Save PDF in run folder root
        try:
            pdf_path = markdown_to_pdf_strategic_analysis(full_strategic_analysis, md_path, pdf_output_path=run_dir / "Strategic-Analysis.pdf")
            console.print(f"[dim]Saved strategic analysis PDF: {pdf_path}[/dim]")
        except Exception as e:
            console.print(f"[yellow]Strategic analysis PDF generation failed: {e}[/yellow]")
        
        return md_path


async def run_research(client_name: str, generate_redacted: bool = True, client_location: str = "", notes: str = "", company_website: str = "", product: str = "", social_links: dict | None = None, competitors: list[dict] | None = None) -> str:
    """Main entry point for running research."""
    orchestrator = SixCOrchestrator(client_name, generate_redacted=generate_redacted, client_location=client_location, notes=notes, company_website=company_website, product=product, social_links=social_links, competitors=competitors)
    return await orchestrator.run()
