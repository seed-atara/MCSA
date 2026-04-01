"""
Per-run API cost tracking.

Tracks usage across Claude (Anthropic) and Firecrawl APIs and
estimates dollar costs based on published pricing.

Usage:
    from core.cost_tracker import cost_tracker

    cost_tracker.reset()
    cost_tracker.log_claude(input_tokens, output_tokens)
    cost_tracker.log_firecrawl()
    cost_tracker.log_firecrawl_search()
    cost_tracker.log_firecrawl_map(pages)
    cost_tracker.log_firecrawl_crawl(pages)
    summary = cost_tracker.summary()
"""

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Default pricing (USD)
# ---------------------------------------------------------------------------

# Claude Sonnet 4 (claude-sonnet-4-20250514)
CLAUDE_INPUT_COST_PER_TOKEN = 3.00 / 1_000_000      # $3.00 per 1M input tokens
CLAUDE_OUTPUT_COST_PER_TOKEN = 15.00 / 1_000_000     # $15.00 per 1M output tokens

# Firecrawl — Standard plan pricing
FIRECRAWL_COST_PER_SCRAPE = 0.002     # ~$0.002 per scrape/page
FIRECRAWL_COST_PER_SEARCH = 0.004     # ~$0.004 per search (scrapes results)
FIRECRAWL_COST_PER_MAP = 0.001        # ~$0.001 per map call
FIRECRAWL_COST_PER_CRAWL_PAGE = 0.002 # ~$0.002 per crawled page


@dataclass
class _RunCosts:
    """Accumulator for a single research run."""

    # Claude
    claude_calls: int = 0
    claude_input_tokens: int = 0
    claude_output_tokens: int = 0

    # Firecrawl
    firecrawl_calls: int = 0
    firecrawl_search_calls: int = 0
    firecrawl_map_calls: int = 0
    firecrawl_map_pages: int = 0
    firecrawl_crawl_calls: int = 0
    firecrawl_crawl_pages: int = 0

    # Legacy Tavily counters (kept so callers that haven't been updated don't crash)
    tavily_search_calls: int = 0
    tavily_extract_calls: int = 0
    tavily_extract_urls: int = 0
    tavily_research_calls: int = 0
    tavily_research_pro_calls: int = 0
    tavily_research_mini_calls: int = 0
    tavily_crawl_calls: int = 0
    tavily_crawl_pages: int = 0
    tavily_map_calls: int = 0
    tavily_map_pages: int = 0

    # Per-call detail log (optional, for debugging)
    _detail_log: list = field(default_factory=list)

    # ---- Logging methods ----

    def log_claude(self, input_tokens: int, output_tokens: int, label: str = ""):
        self.claude_calls += 1
        self.claude_input_tokens += input_tokens
        self.claude_output_tokens += output_tokens
        self._detail_log.append({
            "api": "claude",
            "label": label,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        })

    def log_firecrawl(self):
        self.firecrawl_calls += 1

    def log_firecrawl_search(self):
        self.firecrawl_search_calls += 1

    def log_firecrawl_map(self, pages: int = 1):
        self.firecrawl_map_calls += 1
        self.firecrawl_map_pages += pages

    def log_firecrawl_crawl(self, pages: int = 1):
        self.firecrawl_crawl_calls += 1
        self.firecrawl_crawl_pages += pages

    # Legacy Tavily stubs — redirect to Firecrawl counters
    def log_tavily_search(self):
        self.firecrawl_search_calls += 1

    def log_tavily_extract(self, url_count: int = 1):
        self.firecrawl_calls += url_count

    def log_tavily_research(self, model: str = "pro"):
        self.firecrawl_search_calls += 1

    def log_tavily_crawl(self, pages: int = 1, extract_depth: str = "basic", has_instructions: bool = False):
        self.firecrawl_crawl_calls += 1
        self.firecrawl_crawl_pages += pages

    def log_tavily_map(self, pages: int = 1, has_instructions: bool = False):
        self.firecrawl_map_calls += 1
        self.firecrawl_map_pages += pages

    # ---- Cost calculation ----

    @property
    def claude_cost(self) -> float:
        return (
            self.claude_input_tokens * CLAUDE_INPUT_COST_PER_TOKEN
            + self.claude_output_tokens * CLAUDE_OUTPUT_COST_PER_TOKEN
        )

    @property
    def firecrawl_cost(self) -> float:
        scrape_cost = self.firecrawl_calls * FIRECRAWL_COST_PER_SCRAPE
        search_cost = self.firecrawl_search_calls * FIRECRAWL_COST_PER_SEARCH
        map_cost = self.firecrawl_map_calls * FIRECRAWL_COST_PER_MAP
        crawl_cost = self.firecrawl_crawl_pages * FIRECRAWL_COST_PER_CRAWL_PAGE
        return scrape_cost + search_cost + map_cost + crawl_cost

    @property
    def tavily_cost(self) -> float:
        """Legacy — always returns 0, costs now tracked under firecrawl."""
        return 0.0

    @property
    def total_cost(self) -> float:
        return self.claude_cost + self.firecrawl_cost

    # ---- Summary ----

    def summary(self) -> dict:
        """Return a JSON-serialisable cost breakdown."""
        return {
            "total_cost_usd": round(self.total_cost, 4),
            "claude": {
                "calls": self.claude_calls,
                "input_tokens": self.claude_input_tokens,
                "output_tokens": self.claude_output_tokens,
                "total_tokens": self.claude_input_tokens + self.claude_output_tokens,
                "cost_usd": round(self.claude_cost, 4),
            },
            "firecrawl": {
                "scrape_calls": self.firecrawl_calls,
                "search_calls": self.firecrawl_search_calls,
                "map_calls": self.firecrawl_map_calls,
                "map_pages": self.firecrawl_map_pages,
                "crawl_calls": self.firecrawl_crawl_calls,
                "crawl_pages": self.firecrawl_crawl_pages,
                "cost_usd": round(self.firecrawl_cost, 4),
            },
        }

    def reset(self):
        """Clear all counters for a new run."""
        self.claude_calls = 0
        self.claude_input_tokens = 0
        self.claude_output_tokens = 0
        self.firecrawl_calls = 0
        self.firecrawl_search_calls = 0
        self.firecrawl_map_calls = 0
        self.firecrawl_map_pages = 0
        self.firecrawl_crawl_calls = 0
        self.firecrawl_crawl_pages = 0
        self.tavily_search_calls = 0
        self.tavily_extract_calls = 0
        self.tavily_extract_urls = 0
        self.tavily_research_calls = 0
        self.tavily_research_pro_calls = 0
        self.tavily_research_mini_calls = 0
        self.tavily_crawl_calls = 0
        self.tavily_crawl_pages = 0
        self.tavily_map_calls = 0
        self.tavily_map_pages = 0
        self._detail_log = []


# Module-level singleton — shared across the whole research run
cost_tracker = _RunCosts()
