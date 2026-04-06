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
# ---------------------------------------------------------------------------
# Tomorrow Group brand voice — shared anti-slop rules for all agencies
# ---------------------------------------------------------------------------
TG_VOICE = {
    "group_identity": (
        "Tomorrow Group is a collective of specialist agencies. Each agency has its own "
        "expertise but shares the group's commitment to evidence-based, commercially-focused "
        "work. The group's edge is specialist depth — not generalist breadth."
    ),
    "anti_slop_rules": [
        "NEVER use: 'In today's rapidly evolving landscape', 'leverage', 'synergy', "
        "'cutting-edge', 'game-changer', 'unlock', 'dive deep', 'holistic'",
        "NEVER start posts with a question unless it's genuinely provocative",
        "NEVER use more than 3 hashtags per LinkedIn post",
        "NEVER include external links in LinkedIn post body (kills reach)",
        "Every claim MUST cite a specific source, date, or data point from the research",
        "Every post MUST take a position worth defending — no fence-sitting",
        "Acknowledge counterarguments rather than ignoring them",
        "Write like a sharp industry insider, not a marketing brochure",
        "Prefer concrete numbers over vague qualifiers ('37% increase' not 'significant growth')",
        "First sentence must earn the 'see more' click — no throat-clearing",
    ],
    "format_rules": {
        "linkedin_post": {
            "length": "150-250 words",
            "structure": "Hook (1 line) → Insight (2-3 paras) → Position (1 para) → CTA (1 line)",
            "hashtags": "2-3 max, industry-specific, no #marketing #digital",
            "tone": "Authoritative insider, not corporate",
        },
        "linkedin_carousel": {
            "slides": "6-10 slides",
            "structure": "Provocative title slide → Problem → Evidence → Framework → Takeaway → CTA",
            "design_notes": "One key point per slide, minimal text, strong visual hierarchy",
        },
        "blog_article": {
            "length": "800-1200 words",
            "structure": "Contrarian hook → Evidence → Framework → Application → Next steps",
            "seo_notes": "Include target keyword in title and first 100 words",
        },
    },
}


AGENCIES: list[dict] = [
    {
        "name": "Found",
        "md": "Natalie",
        "champion": "Ric",
        "headcount": 70,
        "website": "www.found.co.uk",
        "focus": "SEO, PPC, digital performance marketing",
        "pr_topics": [
            "Google algorithm updates", "AI search", "zero-click search", "SGE",
            "attribution modelling", "performance marketing budgets", "paid search CPCs",
            "agency new business wins", "marketing effectiveness research",
        ],
        "facts": {
            "founded": "2009",
            "locations": ["London"],
            "known_clients": [],  # only add verified clients
            "awards": [],
            "do_not_claim": "Do not invent client names, case study results, or team sizes beyond what's on found.co.uk",
        },
        "voice": {
            "personality": "Data-obsessed performance experts who cut through SEO hype with evidence",
            "tone": "Direct, metric-driven, slightly contrarian — we prove things, we don't guess",
            "positioning": "The agency that shows receipts. Real performance data, not vanity metrics.",
            "on_voice": [
                "Here's what actually happened when we tested this",
                "The data says X, which contradicts the popular take",
                "3 things we measured that changed our approach",
            ],
            "off_voice": [
                "We're passionate about helping brands grow",
                "In today's competitive digital landscape",
                "Our award-winning team delivers results",
            ],
        },
        "competitor_guidance": (
            "UK-based SEO and PPC agencies. Direct service competitors "
            "offering search engine optimisation, pay-per-click management, "
            "and digital performance marketing to UK clients."
        ),
        "manual_competitors": [
            "Impression",
            "Croud",
            "Brave Bison",
            "Journey Further",
            "Publicis",
            "Jellyfish",
            "Accenture Song",
            "Rise at Seven",
        ],
    },
    {
        "name": "SEED",
        "md": None,
        "champion": None,
        "headcount": None,
        "website": "www.seedstudios.ai",
        "focus": "Content strategy, social media, creative production",
        "pr_topics": [
            "social media algorithm changes", "creator economy trends", "content marketing ROI",
            "brand social media strategy", "short-form video", "TikTok brand marketing",
            "content production budgets", "AI content tools", "brand storytelling",
        ],
        "facts": {
            "founded": "2025",
            "age": "Under 1 year old — launched 2025",
            "locations": ["London"],
            "known_clients": [],
            "awards": [],
            "do_not_claim": "SEED is UNDER 1 YEAR OLD. Never claim years of experience, legacy clients, or established track record. No Manchester, Berlin, or any office outside London.",
        },
        "voice": {
            "personality": "Creative strategists who blend culture with commerce",
            "tone": "Culturally sharp, commercially grounded — we get trends before they peak",
            "positioning": "The agency that makes brands culturally relevant, not just visible.",
            "on_voice": [
                "This trend is about to hit mainstream — here's why brands should care now",
                "We tested 3 content formats and here's what actually drove engagement",
                "The creator economy just shifted — here's what it means for your strategy",
            ],
            "off_voice": [
                "Content is king",
                "We create thumb-stopping content",
                "Our creative team is passionate about storytelling",
            ],
        },
        "competitor_guidance": (
            "UK-based social media and content agencies. Competitors that win "
            "briefs for social media strategy, content creation, creative campaigns, "
            "and community management. NOT PR-only firms or media buying agencies."
        ),
        "manual_competitors": [
            "Wonder Studios",
            "Secret Level",
            "Silverside",
            "Asteria",
            "Dor Brothers",
        ],
    },
    {
        "name": "Braidr",
        "md": None,
        "champion": None,
        "headcount": None,
        "website": "braidr.ai",
        "focus": "Data, analytics, marketing science",
        "pr_topics": [
            "marketing measurement", "marketing mix modelling", "attribution",
            "AI in analytics", "data privacy regulation", "third-party data deprecation",
            "marketing ROI proof", "customer data platforms", "analytics agency market",
        ],
        "facts": {
            "founded": "2024",
            "locations": ["London"],
            "known_clients": [],
            "awards": [],
            "do_not_claim": "Do not invent specific client names, revenue figures, or team sizes beyond what's on braidr.ai",
        },
        "voice": {
            "personality": "Marketing scientists who translate complex data into commercial decisions",
            "tone": "Intellectually rigorous but accessible — we explain, we don't obfuscate",
            "positioning": "The agency that turns data into decisions, not dashboards.",
            "on_voice": [
                "We ran the attribution model and the results surprised us",
                "Here's why your current measurement approach is costing you money",
                "The gap between data collection and data-driven decisions is where value lives",
            ],
            "off_voice": [
                "Data is the new oil",
                "Our proprietary AI platform delivers insights",
                "We harness the power of big data",
            ],
        },
        "competitor_guidance": (
            "UK-based data and analytics agencies or consultancies. Competitors "
            "offering marketing measurement, attribution modelling, marketing mix "
            "modelling (MMM), customer data platforms, and marketing science. "
            "NOT generic digital agencies that happen to have an analytics team."
        ),
        "manual_competitors": [
            "Fifty-five",
            "Artefact",
            "Data Forest",
            "Unit8",
            "Cynozure",
            "Merkle (dentsu)",
            "Datatonic",
            "JMAN Group",
            "Elastacloud",
            "Advancing Analytics",
            "Baringa",
            "Methods Analytics",
        ],
    },
    {
        "name": "Disrupt",
        "md": None,
        "champion": None,
        "headcount": None,
        "website": "disruptmarketing.co",
        "focus": "Creator economy, influencer marketing, paid social",
        "pr_topics": [
            "influencer marketing trends", "creator economy", "paid social performance",
            "celebrity brand deals", "influencer fraud", "creator monetisation",
            "TikTok advertising", "Instagram reach", "brand creator partnerships",
            "influencer regulation ASA", "cultural moments brand activations",
        ],
        "facts": {
            "founded": "2024",
            "locations": ["London"],
            "known_clients": [],
            "awards": [],
            "do_not_claim": "Do not invent specific client names, campaign results, or team sizes beyond what's on disruptmarketing.co",
        },
        "voice": {
            "personality": "Creator economy strategists who connect brands with culture through influencers",
            "tone": "Culturally plugged-in, commercially sharp — we know what creators actually drive results",
            "positioning": "The agency that turns creator partnerships into measurable business outcomes.",
            "on_voice": [
                "This creator partnership drove X because the audience fit was right, not because the follower count was high",
                "The creator economy just shifted — here's what brands paying attention are doing differently",
                "Most influencer campaigns fail because brands buy reach, not relevance",
            ],
            "off_voice": [
                "We offer a full-service influencer solution",
                "Our creator network delivers results",
                "Influencer marketing is transforming how brands connect",
            ],
        },
        "competitor_guidance": (
            "UK-based influencer marketing and creator economy agencies. Competitors "
            "that plan and execute creator partnerships, influencer campaigns, paid social "
            "with creators, and talent management. Must be AGENCIES, not platforms or tools."
        ),
        "manual_competitors": [
            "Ogilvy",
            "Socially Powerful",
            "The Fifth Agency",
            "Viral Nation",
            "Billion Dollar Boy",
            "Goat Agency",
            "Whalar",
            "Kyra",
            "Seen Connects",
            "VAMP",
            "Connect Management",
        ],
    },
    {
        "name": "Culture3",
        "md": None,
        "champion": None,
        "headcount": None,
        "website": "www.culture3.com",
        "focus": "Web3, emerging technology, innovation",
        "pr_topics": [
            "Web3 brand adoption", "NFT marketing", "AI creative tools",
            "metaverse brand activations", "blockchain transparency",
            "emerging tech regulation", "digital ownership", "tokenised loyalty",
            "decentralised social media", "AI art copyright",
        ],
        "facts": {
            "founded": "2022",
            "locations": ["London"],
            "known_clients": ["TED"],
            "partnerships": ["TED Official Creative Impact Partner"],
            "awards": [],
            "do_not_claim": "Only claim the TED partnership — do not invent other client names or partnerships",
        },
        "voice": {
            "personality": "Emerging tech translators who bridge innovation and commercial reality",
            "tone": "Forward-thinking but grounded — we separate signal from noise in new tech",
            "positioning": "The agency that makes emerging technology commercially useful, not just exciting.",
            "on_voice": [
                "Everyone's talking about X but here's what actually matters for brands",
                "We tested this Web3 approach with a real brand — here's what we learned",
                "The post-hype reality of [technology] is more interesting than the hype",
            ],
            "off_voice": [
                "Web3 will revolutionise everything",
                "The metaverse is the future of marketing",
                "Blockchain technology enables unprecedented possibilities",
            ],
        },
        "competitor_guidance": (
            "Web3 marketing and strategy agencies. Competitors offering Web3 "
            "go-to-market, community building, token marketing, metaverse strategy, "
            "or emerging tech consultancy to brands. NOT blockchain infrastructure "
            "companies, crypto exchanges, or software development shops."
        ),
        "manual_competitors": [],
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
    # Module 6 — Content Strategy
    {"module": "content_strategy", "name": "Content Strategy Brief", "cadence": CADENCE_WEEKLY, "channel": "slack+confluence", "audience": "Agency MDs + Marketing Leads"},
    # Module 11 — Digital PR
    {"module": "digital_pr", "name": "Digital PR Opportunity Alert", "cadence": CADENCE_DAILY, "channel": "slack", "audience": "Organic / BD Teams"},
]

# ---------------------------------------------------------------------------
# Competitor registry defaults (populated per-agency during Module 1)
# ---------------------------------------------------------------------------
# Each entry: {"name": str, "website": str, "sector": str, "channels": list[str]}
# Registries are stored per-agency and updated monthly.
DEFAULT_COMPETITORS_PER_AGENCY = 5
