"""Core research infrastructure — shared across all agent systems.

Provides the reusable primitives: web search/scrape/crawl tools,
Claude API wrapper (ResearchAgent base), cost tracking, and config.

Heavy imports (anthropic, httpx) are deferred to submodule access
to avoid import errors when only config is needed.
"""
