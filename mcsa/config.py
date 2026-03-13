"""MCSA-specific configuration — agencies, cadences, output channels."""
from __future__ import annotations

import os

from core.config import (  # noqa: F401
    ANTHROPIC_API_KEY,
    TAVILY_API_KEY,
    FIRECRAWL_API_KEY,
    MODEL,
    MAX_TOKENS,
    OUTPUT_DIR,
    SLACK_MCSA_WEBHOOK_URL,
    SLACK_MCSA_ENABLED,
    CONFLUENCE_URL,
    CONFLUENCE_USER,
    CONFLUENCE_API_TOKEN,
    CONFLUENCE_SPACE_KEY,
    CONFLUENCE_PARENT_PAGE_ID,
    CONFLUENCE_ENABLED,
    get_research_profile,
    validate_config,
)

# ---------------------------------------------------------------------------
# Tomorrow Group agencies
# ---------------------------------------------------------------------------
AGENCIES: list[dict] = [
    {
        "name": "Found",
        "md": "Natalie",
        "champion": "Ric",
        "headcount": 70,
        "focus": "SEO, PPC, digital performance marketing",
        "competitor_guidance": (
            "UK-based SEO and PPC agencies. Direct service competitors "
            "offering search engine optimisation, pay-per-click management, "
            "and digital performance marketing to UK clients."
        ),
    },
    {
        "name": "SEED",
        "md": None,
        "champion": None,
        "headcount": None,
        "focus": "Content strategy, social media, creative production",
        "competitor_guidance": (
            "UK-based social media and content agencies. Competitors that win "
            "briefs for social media strategy, content creation, creative campaigns, "
            "and community management. NOT PR-only firms or media buying agencies."
        ),
    },
    {
        "name": "Braidr",
        "md": None,
        "champion": None,
        "headcount": None,
        "focus": "Data, analytics, marketing science",
        "competitor_guidance": (
            "UK-based data and analytics agencies or consultancies. Competitors "
            "offering marketing measurement, attribution modelling, marketing mix "
            "modelling (MMM), customer data platforms, and marketing science. "
            "NOT generic digital agencies that happen to have an analytics team."
        ),
    },
    {
        "name": "Disrupt",
        "md": None,
        "champion": None,
        "headcount": None,
        "focus": "Paid media, programmatic advertising",
        "competitor_guidance": (
            "UK-based paid media and programmatic agencies. Competitors that plan "
            "and buy paid media — display, programmatic, paid social, PPC at scale. "
            "Must be AGENCIES, not ad-tech platforms or DSPs like StackAdapt or DV360."
        ),
    },
    {
        "name": "Culture3",
        "md": None,
        "champion": None,
        "headcount": None,
        "focus": "Web3, emerging technology, innovation",
        "competitor_guidance": (
            "Web3 marketing and strategy agencies. Competitors offering Web3 "
            "go-to-market, community building, token marketing, metaverse strategy, "
            "or emerging tech consultancy to brands. NOT blockchain infrastructure "
            "companies, crypto exchanges, or software development shops."
        ),
    },
]

# Tomorrow Group sibling agencies — never list these as competitors
SIBLING_AGENCIES = {"Found", "SEED", "Braidr", "Disrupt", "Culture3", "Tomorrow Group"}

# ---------------------------------------------------------------------------
# Slack webhook routing — per-agency channels (Option A from roadmap)
# ---------------------------------------------------------------------------
# Each agency can have its own webhook URL (for a dedicated channel like #mcsa-found).
# Falls back to SLACK_MCSA_WEBHOOK_URL if no per-agency URL is set.
# Env var pattern: SLACK_MCSA_WEBHOOK_URL_<AGENCY_UPPER> e.g. SLACK_MCSA_WEBHOOK_URL_FOUND
SLACK_AGENCY_WEBHOOKS: dict[str, str | None] = {
    agency["name"]: os.getenv(f"SLACK_MCSA_WEBHOOK_URL_{agency['name'].upper()}")
    for agency in AGENCIES
}

# Alerts channel — separate webhook for high-priority alerts (Phase 2)
SLACK_MCSA_WEBHOOK_URL_ALERTS = os.getenv("SLACK_MCSA_WEBHOOK_URL_ALERTS")


def get_slack_webhook(agency_name: str) -> str | None:
    """Return the Slack webhook URL for an agency, falling back to the default."""
    return SLACK_AGENCY_WEBHOOKS.get(agency_name) or SLACK_MCSA_WEBHOOK_URL

# ---------------------------------------------------------------------------
# Reporting cadences
# ---------------------------------------------------------------------------
CADENCE_DAILY = "daily"
CADENCE_WEEKLY = "weekly"
CADENCE_MONTHLY = "monthly"

# Module → report definitions (from the brief's Section 3 consolidated table)
REPORTS: list[dict] = [
    # Module 1 — Registry
    {"module": "registry", "name": "Competitor Registry Update", "cadence": CADENCE_MONTHLY, "channel": "confluence", "audience": "Agency MDs"},
    # Module 2 — LinkedIn
    {"module": "linkedin", "name": "LinkedIn Activity Digest", "cadence": CADENCE_DAILY, "channel": "slack", "audience": "Marketing Leads"},
    {"module": "linkedin", "name": "LinkedIn Theme Report", "cadence": CADENCE_WEEKLY, "channel": "slack+confluence", "audience": "Agency MDs"},
    # Module 3 — Industry
    {"module": "industry", "name": "Industry Pulse Alert", "cadence": CADENCE_DAILY, "channel": "slack", "audience": "Marketing Leads"},
    {"module": "industry", "name": "Key Person Tracker", "cadence": CADENCE_WEEKLY, "channel": "slack", "audience": "Agency MDs"},
    # Module 4 — DIFF
    {"module": "diff", "name": "Narrative Drift Alert", "cadence": CADENCE_WEEKLY, "channel": "slack", "audience": "Marketing Leads + MDs"},
    {"module": "diff", "name": "Competitive Gap Report", "cadence": CADENCE_MONTHLY, "channel": "confluence+slack", "audience": "MDs + CAIO"},
    # Module 5 — Website
    {"module": "website", "name": "Competitor Content Alert", "cadence": CADENCE_DAILY, "channel": "slack", "audience": "Marketing Leads"},
    {"module": "website", "name": "Website Pattern Report", "cadence": CADENCE_WEEKLY, "channel": "confluence", "audience": "Agency MDs"},
]

# ---------------------------------------------------------------------------
# Competitor registry defaults (populated per-agency during Module 1)
# ---------------------------------------------------------------------------
# Each entry: {"name": str, "website": str, "sector": str, "channels": list[str]}
# Registries are stored per-agency and updated monthly.
DEFAULT_COMPETITORS_PER_AGENCY = 5
