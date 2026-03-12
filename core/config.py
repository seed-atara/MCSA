"""Shared configuration — API keys, model settings, output directory.

Project-specific config (branding, locale, etc.) lives in the respective
project package (e.g. src/config.py for the 6C agent, mcsa/config.py for MCSA).
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from the project root .env
# Walk up from this file: core/config.py -> project_root/.env
_project_root = Path(__file__).parent.parent
_env_path = _project_root / ".env"
load_dotenv(_env_path)

# ---------------------------------------------------------------------------
# API Keys
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY")

# ---------------------------------------------------------------------------
# Model settings
# ---------------------------------------------------------------------------
MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 16384

# ---------------------------------------------------------------------------
# Research depth profiles
# ---------------------------------------------------------------------------
RESEARCH_PROFILES = {
    "standard": {
        "max_tokens": 8192,
        "search_results_per_query": 3,
        "deep_scrape_count": 2,
        "tavily_research_model": "mini",
        "kol_count": 25,
        "description": "Standard research - good for initial assessment",
    },
    "deep": {
        "max_tokens": 16384,
        "search_results_per_query": 5,
        "deep_scrape_count": 3,
        "tavily_research_model": "pro",
        "kol_count": 50,
        "description": "Deep research - comprehensive PhD-grade analysis",
    },
    "exhaustive": {
        "max_tokens": 16384,
        "search_results_per_query": 8,
        "deep_scrape_count": 4,
        "tavily_research_model": "pro",
        "kol_count": 75,
        "description": "Exhaustive research - maximum depth for high-value clients",
    },
}

DEFAULT_RESEARCH_PROFILE = "deep"


def get_research_profile(profile_name: str = None) -> dict:
    """Get research profile settings."""
    name = profile_name or DEFAULT_RESEARCH_PROFILE
    return RESEARCH_PROFILES.get(name, RESEARCH_PROFILES["deep"])


# ---------------------------------------------------------------------------
# Output directory
# ---------------------------------------------------------------------------
# On Railway, use the persistent volume at /data/output so files survive redeploys.
# Locally, fall back to the project's output/ directory.
if os.getenv("RAILWAY_ENVIRONMENT"):
    OUTPUT_DIR = Path("/data/output")
else:
    OUTPUT_DIR = _project_root / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def validate_config():
    """Check that required API keys are set."""
    missing = []
    if not ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if not TAVILY_API_KEY:
        missing.append("TAVILY_API_KEY")

    if missing:
        raise ValueError(
            f"Missing required API keys: {', '.join(missing)}\n"
            f"Copy .env.example to .env and fill in your keys."
        )
