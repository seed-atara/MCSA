"""MCSA Client-Facing PDF Reports — Tomorrow Group branded.

Self-contained PDF generator using fpdf2, replicating the SEED design system
(sage green, charcoal, Helvetica) with Tomorrow Group branding.

PDF is generated in-memory (no file writes) for Railway's ephemeral filesystem.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta

import anthropic
from fpdf import FPDF
from rich.console import Console

from .storage import _get_supabase, _sb_select

console = Console()

# ---------------------------------------------------------------------------
# Text sanitisation (subset of src/pdf_generator.sanitize_text)
# ---------------------------------------------------------------------------

_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U000024C2-\U0001F251"
    "\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF"
    "\U00002600-\U000026FF\U00002700-\U000027BF\U0001F700-\U0001F77F"
    "]+",
    flags=re.UNICODE,
)

_REPLACEMENTS = {
    '\u2014': '-', '\u2013': '-', '\u2018': "'", '\u2019': "'",
    '\u201c': '"', '\u201d': '"', '\u2026': '...', '\u2022': '-',
    '\u2192': '->', '\u2190': '<-', '\u2265': '>=', '\u2264': '<=',
    '\u00b1': '+/-', '\u00d7': 'x', '\u00f7': '/',
    '\u20ac': 'EUR', '\u00a5': 'JPY',
    '\u00a9': '(c)', '\u00ae': '(R)', '\u2122': '(TM)',
    '\u200b': '', '\u00a0': ' ',
    '\u2713': '[OK]', '\u2717': '[X]', '\u2714': '[OK]', '\u2718': '[X]',
    '\u274c': '[X]', '\u274e': '[X]', '\u2705': '[OK]',
}


def _sanitize(text: str) -> str:
    """Make text safe for Helvetica (latin-1) PDF rendering."""
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'__([^_]+)__', r'\1', text)
    text = re.sub(r'_([^_]+)_', r'\1', text)
    text = _EMOJI_RE.sub('', text)
    for uc, ac in _REPLACEMENTS.items():
        text = text.replace(uc, ac)
    text = text.encode('latin-1', 'replace').decode('latin-1')
    return text


# ---------------------------------------------------------------------------
# Tomorrow Group branding
# ---------------------------------------------------------------------------

_TG_BRAND = "Tomorrow Group"
_TG_TAGLINE = "Market & Competitor Surveillance"


class MCSAReportPDF(FPDF):
    """Tomorrow Group branded PDF using the SEED design system palette."""

    # Colour palette (same as IntelligenceBriefPDF)
    PRIMARY = (123, 158, 107)       # Sage Green
    PRIMARY_DARK = (91, 125, 79)
    DARK = (26, 25, 23)             # Charcoal
    GRAY_700 = (64, 62, 59)
    GRAY_500 = (125, 123, 118)
    GRAY_300 = (196, 192, 184)
    GRAY_200 = (221, 217, 208)
    GRAY_50 = (239, 237, 231)
    WHITE = (255, 255, 255)

    def __init__(self, agency_name: str, period: str = "weekly"):
        super().__init__()
        self.agency_name = agency_name
        self.period = period
        self.set_margins(25, 25, 25)
        self.set_auto_page_break(auto=True, margin=30)
        self.section_count = 0

    def _safe(self, text: str) -> str:
        text = _sanitize(text)
        words = text.split()
        safe = []
        for w in words:
            if len(w) > 40:
                for k in range(0, len(w), 38):
                    safe.append(w[k:k+38])
            else:
                safe.append(w)
        return ' '.join(safe)

    # -- Header / Footer ---------------------------------------------------

    def header(self):
        if self.page_no() == 1:
            return
        self.set_font('Helvetica', '', 9)
        self.set_text_color(*self.GRAY_500)
        self.set_y(15)
        self.cell(0, 5, f'{_TG_BRAND} — {_TG_TAGLINE}', align='L')
        self.cell(0, 5, self.agency_name, align='R')
        self.ln(15)

    def footer(self):
        if self.page_no() == 1:
            return
        self.set_y(-20)
        self.set_font('Helvetica', '', 9)
        self.set_text_color(*self.GRAY_500)
        self.set_draw_color(*self.GRAY_200)
        self.line(25, self.get_y(), self.w - 25, self.get_y())
        self.ln(5)
        self.cell(0, 5, f'{self.page_no()}  |  {_TG_BRAND} MCSA — Confidential', align='C')

    # -- Cover page --------------------------------------------------------

    def add_cover(self, title: str, subtitle: str = ""):
        self.add_page()

        # Top accent bars
        self.set_fill_color(*self.DARK)
        self.rect(0, 0, self.w, 4, 'F')
        self.set_fill_color(*self.PRIMARY)
        self.rect(0, 4, self.w, 1.5, 'F')

        # Brand
        self.set_y(25); self.set_x(25)
        self.set_font('Helvetica', 'B', 18)
        self.set_text_color(*self.PRIMARY)
        self.cell(0, 8, _TG_BRAND, align='L')

        self.ln(10); self.set_x(25)
        self.set_font('Helvetica', '', 10)
        self.set_text_color(*self.GRAY_500)
        self.cell(0, 5, _TG_TAGLINE, align='L')

        # Agency
        self.ln(20); self.set_x(25)
        self.set_font('Helvetica', '', 11)
        self.set_text_color(*self.GRAY_500)
        self.cell(25, 6, 'Agency:', align='L')
        self.set_font('Helvetica', 'B', 11)
        self.set_text_color(*self.DARK)
        self.cell(0, 6, self._safe(self.agency_name), align='L')

        # Title
        self.set_y(100)
        self.set_font('Helvetica', 'B', 32)
        self.set_text_color(*self.DARK)
        self.multi_cell(0, 12, self._safe(title), align='C')

        if subtitle:
            self.ln(6)
            self.set_font('Helvetica', '', 14)
            self.set_text_color(*self.GRAY_700)
            self.multi_cell(0, 7, self._safe(subtitle), align='C')

        # Bottom
        self.set_y(-70)
        self.set_draw_color(*self.GRAY_200)
        self.line(25, self.get_y(), self.w - 25, self.get_y())
        self.ln(15)
        self.set_font('Helvetica', '', 10)
        self.set_text_color(*self.GRAY_500)
        self.cell(0, 6, datetime.now().strftime("%B %d, %Y"), align='C')
        self.ln(8)
        self.set_font('Helvetica', 'B', 10)
        self.set_text_color(*self.PRIMARY)
        self.cell(0, 6, f'{_TG_BRAND} — MCSA', align='C')

        self.set_fill_color(*self.PRIMARY)
        self.rect(0, self.h - 5.5, self.w, 1.5, 'F')
        self.set_fill_color(*self.DARK)
        self.rect(0, self.h - 4, self.w, 4, 'F')

    # -- Section divider ---------------------------------------------------

    def add_section_page(self, title: str):
        self.add_page()
        self.section_count += 1
        self.set_y(80)
        self.set_font('Helvetica', 'B', 72)
        self.set_text_color(*self.GRAY_200)
        self.cell(0, 30, f'{self.section_count:02d}', align='C')
        self.ln(10)
        self.set_font('Helvetica', 'B', 24)
        self.set_text_color(*self.DARK)
        self.multi_cell(0, 10, self._safe(title), align='C')

    # -- Content helpers ---------------------------------------------------

    def chapter_title(self, title: str, level: int = 1):
        title = self._safe(title)
        if level == 1:
            self.ln(8)
            self.set_font('Helvetica', 'B', 20)
            self.set_text_color(*self.DARK)
            self.multi_cell(0, 9, title)
            self.set_draw_color(*self.PRIMARY)
            self.set_line_width(0.8)
            self.line(25, self.get_y() + 2, 70, self.get_y() + 2)
            self.set_line_width(0.2)
            self.ln(8)
        elif level == 2:
            self.ln(10)
            self.set_font('Helvetica', 'B', 14)
            self.set_text_color(*self.DARK)
            self.multi_cell(0, 7, title)
            self.ln(4)
        elif level == 3:
            self.ln(6)
            self.set_font('Helvetica', 'B', 12)
            self.set_text_color(*self.GRAY_700)
            self.multi_cell(0, 6, title)
            self.ln(3)
        else:
            self.ln(4)
            self.set_font('Helvetica', 'B', 11)
            self.set_text_color(*self.GRAY_700)
            self.multi_cell(0, 5, title)
            self.ln(2)

    def body_text(self, text: str):
        text = self._safe(text)
        if not text.strip():
            return
        self.set_x(25)
        self.set_font('Helvetica', '', 10)
        self.set_text_color(*self.GRAY_700)
        self.multi_cell(0, 5.5, text)
        self.ln(3)

    def bullet_point(self, text: str, indent: int = 0):
        text = self._safe(text)
        if not text.strip():
            return
        self.set_font('Helvetica', '', 10)
        self.set_text_color(*self.GRAY_700)
        base_x = 28 + (indent * 8)
        self.set_x(base_x)
        self.set_fill_color(*self.PRIMARY)
        self.ellipse(base_x, self.get_y() + 2, 2, 2, 'F')
        self.set_x(base_x + 6)
        remaining = self.w - base_x - 6 - 25
        if remaining < 80:
            remaining = 120
        self.multi_cell(remaining, 5.5, text)
        self.ln(1)

    def numbered_item(self, number: int, text: str):
        text = self._safe(text)
        if not text.strip():
            return
        self.set_x(28)
        self.set_font('Helvetica', 'B', 10)
        self.set_text_color(*self.PRIMARY)
        self.cell(8, 5.5, f'{number}.')
        self.set_font('Helvetica', '', 10)
        self.set_text_color(*self.GRAY_700)
        self.multi_cell(self.w - 36 - 25, 5.5, text)
        self.ln(2)

    # -- Markdown renderer -------------------------------------------------

    def render_markdown(self, markdown: str):
        """Parse markdown and render into the PDF."""
        if not markdown or markdown.startswith("[Error"):
            self.body_text("No data available for this section.")
            return

        for line in markdown.split("\n"):
            line = line.rstrip()

            h = re.match(r'^(#{1,4})\s+(.+)$', line)
            if h:
                self.chapter_title(h.group(2), level=len(h.group(1)))
                continue

            b = re.match(r'^(\s*)[-*]\s+(.+)$', line)
            if b:
                self.bullet_point(b.group(2), indent=len(b.group(1)) // 2)
                continue

            n = re.match(r'^(\d+)\.\s+(.+)$', line)
            if n:
                self.numbered_item(int(n.group(1)), n.group(2))
                continue

            if re.match(r'^\s*[-*_]{3,}\s*$', line):
                self.ln(4)
                continue

            if not line.strip():
                self.ln(2)
                continue

            self.body_text(line)


# ---------------------------------------------------------------------------
# Report data loading
# ---------------------------------------------------------------------------

def load_reports_for_period(agency_name: str, period: str = "weekly") -> dict[str, str]:
    """Fetch all module reports for an agency for the given period.

    Returns {module_name: report_content}.
    """
    hours = {"daily": 24, "weekly": 7 * 24, "monthly": 30 * 24}.get(period, 7 * 24)
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()

    sb = _get_supabase()
    if not sb:
        return {}

    try:
        result = (
            sb.table("reports")
            .select("module, content, cadence, created_at")
            .eq("agency_name", agency_name)
            .gte("created_at", cutoff)
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as e:
        console.print(f"[yellow]PDF: Supabase query failed — {e}[/yellow]")
        return {}

    reports: dict[str, str] = {}
    for row in result.data or []:
        module = row.get("module", "")
        if module and module not in reports:
            reports[module] = row.get("content", "")

    return reports


def _load_registry(agency_name: str) -> list[dict]:
    """Load competitor registry for an agency."""
    rows = _sb_select("registries", {"agency_name": agency_name}, limit=1)
    if rows:
        return rows[0].get("competitors", [])
    return []


# ---------------------------------------------------------------------------
# Executive summary generation
# ---------------------------------------------------------------------------

def _generate_executive_summary(agency_name: str, reports: dict[str, str]) -> str:
    """Use Claude to generate a brief executive summary from the reports."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return "Executive summary unavailable — API key not configured."

    context_parts = []
    for module, content in reports.items():
        context_parts.append(f"## {module}\n{content[:2000]}")
    context = "\n\n".join(context_parts)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            system=(
                f"You are writing an executive summary for {agency_name}'s competitive "
                f"intelligence report. Be concise, direct, and focus on the 3-5 most "
                f"important findings. Write in 2-3 short paragraphs."
            ),
            messages=[{"role": "user", "content": f"Summarise these reports:\n\n{context}"}],
        )
        return response.content[0].text
    except Exception as e:
        console.print(f"[yellow]PDF: Executive summary generation failed — {e}[/yellow]")
        return "Executive summary could not be generated."


# ---------------------------------------------------------------------------
# PDF generation
# ---------------------------------------------------------------------------

def generate_pdf(agency_name: str, period: str = "weekly") -> bytes:
    """Generate a client-facing PDF report for an agency.

    Returns PDF content as bytes (in-memory, no file writes).
    """
    console.print(f"[cyan]PDF: Generating {period} report for {agency_name}...[/cyan]")

    reports = load_reports_for_period(agency_name, period)
    registry = _load_registry(agency_name)

    pdf = MCSAReportPDF(agency_name, period)

    # 1. Cover page
    pdf.add_cover(
        "Competitive Intelligence Report",
        f"{period.title()} Report — {agency_name}",
    )

    # 2. Executive Summary
    pdf.add_section_page("Executive Summary")
    pdf.add_page()
    exec_summary = _generate_executive_summary(agency_name, reports)
    pdf.chapter_title("Executive Summary", level=1)
    pdf.body_text(exec_summary)

    # 3. Competitor Landscape
    if registry:
        pdf.add_section_page("Competitor Landscape")
        pdf.add_page()
        pdf.chapter_title("Competitor Landscape", level=1)
        pdf.body_text(f"{len(registry)} competitors tracked for {agency_name}.")
        for comp in registry:
            name = comp.get("name", "Unknown")
            website = comp.get("website", "")
            threat = comp.get("threat_level", comp.get("threat", ""))
            services = comp.get("key_services", comp.get("services", []))
            source = comp.get("source", "discovered")

            line = name
            if website:
                line += f" ({website})"
            if threat:
                line += f" — Threat: {threat}"
            if source == "manual":
                line += " [manual]"
            pdf.bullet_point(line)

            if services:
                svc = ", ".join(services[:5]) if isinstance(services, list) else str(services)
                pdf.body_text(f"    Services: {svc}")

    # 4. LinkedIn Intelligence
    if reports.get("linkedin"):
        pdf.add_section_page("LinkedIn Intelligence")
        pdf.add_page()
        pdf.render_markdown(reports["linkedin"])

    # 5. Industry Trends
    if reports.get("industry"):
        pdf.add_section_page("Industry Trends")
        pdf.add_page()
        pdf.render_markdown(reports["industry"])

    # 6. Website Changes
    if reports.get("website"):
        pdf.add_section_page("Website Intelligence")
        pdf.add_page()
        pdf.render_markdown(reports["website"])

    # 7. Strategic Recommendations (DIFF + content strategy)
    has_diff = reports.get("diff")
    has_cs = reports.get("content_strategy")
    if has_diff or has_cs:
        pdf.add_section_page("Strategic Recommendations")
        pdf.add_page()
        if has_diff:
            pdf.chapter_title("Competitive DIFF Analysis", level=1)
            pdf.render_markdown(reports["diff"])
        if has_cs:
            if has_diff:
                pdf.add_page()
            pdf.chapter_title("Content Strategy", level=1)
            pdf.render_markdown(reports["content_strategy"])

    # Output as bytes
    pdf_bytes = pdf.output()
    console.print(f"[green]PDF: Generated ({len(pdf_bytes)} bytes, {pdf.page_no()} pages)[/green]")
    return bytes(pdf_bytes)
