"""
Per-run API cost tracking.

Tracks usage across Claude (Anthropic), Tavily, and Firecrawl APIs and
estimates dollar costs based on published pricing.

Usage:
    from core.cost_tracker import cost_tracker

    cost_tracker.reset()
    cost_tracker.log_claude(input_tokens, output_tokens)
    cost_tracker.log_tavily_search()
    cost_tracker.log_tavily_extract(url_count)
    cost_tracker.log_tavily_research(model)
    cost_tracker.log_tavily_crawl(pages, extract_depth, has_instructions)
    cost_tracker.log_tavily_map(pages, has_instructions)
    cost_tracker.log_firecrawl()
    summary = cost_tracker.summary()
"""

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Default pricing (USD)
# ---------------------------------------------------------------------------

# Claude Sonnet 4 (claude-sonnet-4-20250514)
CLAUDE_INPUT_COST_PER_TOKEN = 3.00 / 1_000_000      # $3.00 per 1M input tokens
CLAUDE_OUTPUT_COST_PER_TOKEN = 15.00 / 1_000_000     # $15.00 per 1M output tokens

# Tavily — pay-as-you-go rate: $0.008 per API credit
TAVILY_CREDIT_COST = 0.008
TAVILY_SEARCH_CREDITS = 1            # basic search = 1 credit
TAVILY_EXTRACT_CREDITS_PER_5 = 2     # advanced extract = 2 credits per 5 URLs
TAVILY_RESEARCH_PRO_CREDITS = 50     # estimated average (range 15-250)
TAVILY_RESEARCH_MINI_CREDITS = 15    # estimated average (range 4-110)

# Firecrawl — Standard plan ~$0.00083/credit, 1 credit per page
FIRECRAWL_COST_PER_SCRAPE = 0.002    # conservative estimate per scrape


@dataclass
class _RunCosts:
    """Accumulator for a single research run."""

    # Claude
    claude_calls: int = 0
    claude_input_tokens: int = 0
    claude_output_tokens: int = 0

    # Tavily
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

    # Firecrawl
    firecrawl_calls: int = 0

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

    def log_tavily_search(self):
        self.tavily_search_calls += 1

    def log_tavily_extract(self, url_count: int = 1):
        self.tavily_extract_calls += 1
        self.tavily_extract_urls += url_count

    def log_tavily_research(self, model: str = "pro"):
        self.tavily_research_calls += 1
        if model == "pro":
            self.tavily_research_pro_calls += 1
        else:
            self.tavily_research_mini_calls += 1

    def log_tavily_crawl(self, pages: int = 1, extract_depth: str = "basic", has_instructions: bool = False):
        self.tavily_crawl_calls += 1
        self.tavily_crawl_pages += pages

    def log_tavily_map(self, pages: int = 1, has_instructions: bool = False):
        self.tavily_map_calls += 1
        self.tavily_map_pages += pages

    def log_firecrawl(self):
        self.firecrawl_calls += 1

    # ---- Cost calculation ----

    @property
    def claude_cost(self) -> float:
        return (
            self.claude_input_tokens * CLAUDE_INPUT_COST_PER_TOKEN
            + self.claude_output_tokens * CLAUDE_OUTPUT_COST_PER_TOKEN
        )

    @property
    def tavily_cost(self) -> float:
        search_credits = self.tavily_search_calls * TAVILY_SEARCH_CREDITS
        extract_credits = 0
        if self.tavily_extract_urls > 0:
            batches = -(-self.tavily_extract_urls // 5)  # ceiling division
            extract_credits = batches * TAVILY_EXTRACT_CREDITS_PER_5
        research_credits = (
            self.tavily_research_pro_calls * TAVILY_RESEARCH_PRO_CREDITS
            + self.tavily_research_mini_calls * TAVILY_RESEARCH_MINI_CREDITS
        )
        crawl_extract_credits = 0
        if self.tavily_crawl_pages > 0:
            crawl_extract_credits = -(-self.tavily_crawl_pages // 5)
        crawl_map_credits = 0
        if self.tavily_map_pages > 0:
            crawl_map_credits = -(-self.tavily_map_pages // 10)
        return (search_credits + extract_credits + research_credits
                + crawl_extract_credits + crawl_map_credits) * TAVILY_CREDIT_COST

    @property
    def firecrawl_cost(self) -> float:
        return self.firecrawl_calls * FIRECRAWL_COST_PER_SCRAPE

    @property
    def total_cost(self) -> float:
        return self.claude_cost + self.tavily_cost + self.firecrawl_cost

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
            "tavily": {
                "search_calls": self.tavily_search_calls,
                "extract_calls": self.tavily_extract_calls,
                "extract_urls": self.tavily_extract_urls,
                "research_calls": self.tavily_research_calls,
                "research_pro_calls": self.tavily_research_pro_calls,
                "research_mini_calls": self.tavily_research_mini_calls,
                "crawl_calls": self.tavily_crawl_calls,
                "crawl_pages": self.tavily_crawl_pages,
                "map_calls": self.tavily_map_calls,
                "map_pages": self.tavily_map_pages,
                "cost_usd": round(self.tavily_cost, 4),
            },
            "firecrawl": {
                "calls": self.firecrawl_calls,
                "cost_usd": round(self.firecrawl_cost, 4),
            },
        }

    def reset(self):
        """Clear all counters for a new run."""
        self.claude_calls = 0
        self.claude_input_tokens = 0
        self.claude_output_tokens = 0
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
        self.firecrawl_calls = 0
        self._detail_log = []


# Module-level singleton — shared across the whole research run
cost_tracker = _RunCosts()
