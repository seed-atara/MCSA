"""MCSA Trend Detection & Alerts — Phase 2.

Post-processing step that runs after each surveillance cycle. Compares current
reports against historical baselines to detect significant changes and push
real-time alerts.

Detection signals:
- New competitor appears in registry
- Competitor website major redesign (page changes)
- LinkedIn engagement/theme shifts
- Industry award mentions
- Competitor hiring signals
- Positioning/narrative drift
- New service/product launch

Alerts are persisted to Supabase and delivered to Slack (#mcsa-alerts).
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error
from datetime import datetime
from rich.console import Console

from core.agent import ResearchAgent
from core.config import ANTHROPIC_API_KEY, MODEL

from . import storage
from .config import SLACK_MCSA_ENABLED, SLACK_MCSA_WEBHOOK_URL_ALERTS
from .formatter import _md_to_mrkdwn

console = Console()

# ---------------------------------------------------------------------------
# Alert severity levels
# ---------------------------------------------------------------------------
SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"

# Severity → Slack emoji
_SEVERITY_ICON = {
    SEVERITY_HIGH: ":red_circle:",
    SEVERITY_MEDIUM: ":large_orange_circle:",
    SEVERITY_LOW: ":white_circle:",
}


# ---------------------------------------------------------------------------
# Alert detection engine
# ---------------------------------------------------------------------------

class AlertDetector:
    """Detects anomalies by comparing current surveillance output against history."""

    def __init__(self):
        self.alerts: list[dict] = []

    async def detect(
        self,
        agency_name: str,
        cadence: str,
        current_reports: dict[str, str],
        registries: dict[str, list[dict]],
    ) -> list[dict]:
        """Run all detection checks for one agency's surveillance output.

        Args:
            agency_name: The agency that was just surveilled.
            cadence: "daily", "weekly", or "monthly".
            current_reports: {module_name: report_markdown} from this run.
            registries: All loaded registries (for registry diff).

        Returns:
            List of alert dicts ready for persistence and delivery.
        """
        self.alerts = []

        # Registry diff (monthly only — that's when registries are refreshed)
        if cadence == "monthly" and "registry" in current_reports:
            self._detect_registry_changes(agency_name, registries)

        # Website change detection
        if "website" in current_reports and not current_reports["website"].startswith("[Error"):
            self._detect_website_changes(agency_name)

        # Claude-powered analysis of report content for significant signals
        analysis_modules = {
            k: v for k, v in current_reports.items()
            if not v.startswith("[Error") and k != "registry"
        }
        if analysis_modules:
            await self._detect_content_signals(agency_name, cadence, analysis_modules)

        return self.alerts

    def _detect_registry_changes(
        self, agency_name: str, registries: dict[str, list[dict]]
    ) -> None:
        """Compare current registry against the previous one to find new competitors."""
        safe_name = storage._safe(agency_name)

        # Load previous registry from before this run
        # The orchestrator already updated self.registries, so we need the stored version
        prev_rows = storage._sb_select(
            "registries",
            {"agency_name": safe_name},
            order_by="created_at",
            limit=2,
        )

        if len(prev_rows) < 2:
            # No previous registry to compare against
            return

        current_competitors = prev_rows[0].get("competitors", [])
        previous_competitors = prev_rows[1].get("competitors", [])

        current_names = {c.get("name", "").lower() for c in current_competitors if c.get("name")}
        previous_names = {c.get("name", "").lower() for c in previous_competitors if c.get("name")}

        new_names = current_names - previous_names
        removed_names = previous_names - current_names

        for name in new_names:
            # Find full entry for detail
            entry = next(
                (c for c in current_competitors if c.get("name", "").lower() == name), {}
            )
            self.alerts.append({
                "agency_name": agency_name,
                "alert_type": "new_competitor",
                "severity": SEVERITY_HIGH,
                "title": f"New competitor detected: {entry.get('name', name)}",
                "detail": (
                    f"A new competitor has appeared in the {agency_name} registry.\n"
                    f"Name: {entry.get('name', name)}\n"
                    f"Website: {entry.get('website', 'Unknown')}\n"
                    f"Sector: {entry.get('sector', 'Unknown')}"
                ),
            })

        if removed_names:
            self.alerts.append({
                "agency_name": agency_name,
                "alert_type": "competitor_removed",
                "severity": SEVERITY_LOW,
                "title": f"{len(removed_names)} competitor(s) dropped from registry",
                "detail": f"Removed: {', '.join(sorted(removed_names))}",
            })

    def _detect_website_changes(self, agency_name: str) -> None:
        """Check website snapshots for significant changes."""
        safe_name = storage._safe(agency_name)
        snap_dir = storage.MCSA_DIR / "snapshots" / safe_name

        if not snap_dir.exists():
            return

        today = datetime.now().strftime("%Y%m%d")

        # Find today's snapshots and compare against previous
        for snap_file in snap_dir.glob(f"*_website_{today}.json"):
            comp_name = snap_file.name.rsplit("_website_", 1)[0]

            try:
                current = json.loads(snap_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            prev = storage.load_previous_snapshot(agency_name, comp_name)
            if not prev:
                continue

            changes = storage.diff_snapshots(prev, current)
            new_count = len(changes["new_pages"])
            changed_count = len(changes["changed_pages"])
            removed_count = len(changes["removed_pages"])
            total_pages = len(current)

            # Significant change threshold: >80% pages changed (raised from 50%
            # to reduce false positives when crawling only ~10 pages)
            change_ratio = (new_count + changed_count) / max(total_pages, 1)

            # Structural changes (pages added/removed) are HIGH severity
            if new_count >= 5 or removed_count >= 5:
                self.alerts.append({
                    "agency_name": agency_name,
                    "alert_type": "website_redesign",
                    "severity": SEVERITY_HIGH,
                    "title": f"Major structural website change: {comp_name}",
                    "detail": (
                        f"{comp_name} website structure changed significantly.\n"
                        f"New pages: {new_count} | Changed: {changed_count} | "
                        f"Removed: {removed_count} | Total: {total_pages}\n"
                        f"Change ratio: {change_ratio:.0%}"
                    ),
                })
            elif change_ratio > 0.8:
                # Content-only changes at high ratio are LOW severity
                self.alerts.append({
                    "agency_name": agency_name,
                    "alert_type": "website_content_change",
                    "severity": SEVERITY_LOW,
                    "title": f"Website content refresh: {comp_name}",
                    "detail": (
                        f"{comp_name} website content has been updated.\n"
                        f"New pages: {new_count} | Changed: {changed_count} | "
                        f"Removed: {removed_count} | Total: {total_pages}\n"
                        f"Change ratio: {change_ratio:.0%}"
                    ),
                })
            elif new_count >= 3:
                # Multiple new pages suggest a product/service launch
                new_titles = [p.get("title", p.get("url", "?")) for p in changes["new_pages"][:5]]
                self.alerts.append({
                    "agency_name": agency_name,
                    "alert_type": "new_pages",
                    "severity": SEVERITY_MEDIUM,
                    "title": f"New content detected: {comp_name} ({new_count} pages)",
                    "detail": (
                        f"{comp_name} added {new_count} new pages:\n"
                        + "\n".join(f"  - {t}" for t in new_titles)
                    ),
                })

    async def _detect_content_signals(
        self, agency_name: str, cadence: str, reports: dict[str, str]
    ) -> None:
        """Use Claude to detect significant signals across reports.

        A single cheap Claude call analyses the combined reports for:
        - Hiring surges
        - Award shortlists
        - Positioning shifts
        - New product/service launches
        - Engagement spikes
        """
        # Load the previous reports for comparison
        prior_summaries = {}
        for module in reports:
            prev = storage.load_latest_report(agency_name, module, cadence)
            if prev:
                prior_summaries[module] = prev[:1500]  # Truncate for context window

        # Build the analysis prompt
        report_block = ""
        for module, content in reports.items():
            report_block += f"\n\n### Current {module} report:\n{content[:2000]}"

        prior_block = ""
        for module, content in prior_summaries.items():
            prior_block += f"\n\n### Previous {module} report:\n{content}"

        system_prompt = """You are an intelligence analyst reviewing surveillance reports for significant changes.

Compare the CURRENT reports against the PREVIOUS reports and identify any signals worth alerting on.

ONLY flag genuinely significant changes. Do NOT flag routine updates or minor variations.

Signal types to look for:
- hiring_surge: Competitor posting multiple job openings or announcing team growth
- award_shortlist: Competitor nominated for or winning industry awards
- positioning_shift: Competitor changing their messaging, tagline, or market positioning
- new_service: Competitor launching a new service, product, or capability
- engagement_spike: Unusual increase in social media activity or engagement
- key_person_move: Senior hire, departure, or appointment at a competitor
- partnership: New strategic partnership or acquisition

Respond ONLY with a JSON array. Each item:
{"alert_type": "...", "severity": "high|medium|low", "title": "...", "detail": "..."}

If there are NO significant signals, respond with: []

Be conservative. 2-3 genuine alerts are better than 10 noise alerts."""

        user_prompt = f"""Agency: {agency_name}
Cadence: {cadence}

{report_block}

--- PREVIOUS REPORTS FOR COMPARISON ---
{prior_block if prior_block else "(No previous reports available — this may be the first run)"}"""

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            response = client.messages.create(
                model=MODEL,
                max_tokens=2000,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            result_text = response.content[0].text.strip()

            # Parse the JSON response
            # Handle markdown code blocks if Claude wraps the response
            if result_text.startswith("```"):
                result_text = result_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

            signals = json.loads(result_text)
            if not isinstance(signals, list):
                return

            for signal in signals:
                if not isinstance(signal, dict):
                    continue
                alert_type = signal.get("alert_type", "unknown")
                severity = signal.get("severity", SEVERITY_MEDIUM)
                if severity not in (SEVERITY_HIGH, SEVERITY_MEDIUM, SEVERITY_LOW):
                    severity = SEVERITY_MEDIUM

                self.alerts.append({
                    "agency_name": agency_name,
                    "alert_type": alert_type,
                    "severity": severity,
                    "title": signal.get("title", "Untitled signal"),
                    "detail": signal.get("detail", ""),
                })

        except json.JSONDecodeError:
            console.print(f"[yellow]  Alerts: Claude response was not valid JSON[/yellow]")
        except Exception as e:
            console.print(f"[yellow]  Alerts: content analysis failed — {e}[/yellow]")


# ---------------------------------------------------------------------------
# Alert persistence (Supabase + local)
# ---------------------------------------------------------------------------

def save_alerts(alerts: list[dict]) -> None:
    """Persist alerts to Supabase and local JSONL."""
    if not alerts:
        return

    # Local JSONL log
    storage._ensure_dir(storage.MCSA_DIR / "alerts")
    log_path = storage.MCSA_DIR / "alerts" / "alerts.jsonl"
    with open(log_path, "a", encoding="utf-8") as f:
        for alert in alerts:
            entry = {**alert, "created_at": datetime.now().isoformat(), "acknowledged": False}
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # Supabase
    for alert in alerts:
        storage._sb_insert("alerts", {
            "agency_name": alert["agency_name"],
            "alert_type": alert["alert_type"],
            "severity": alert["severity"],
            "title": alert["title"],
            "detail": alert["detail"],
            "acknowledged": False,
        })


# ---------------------------------------------------------------------------
# Alert delivery to Slack (#mcsa-alerts channel)
# ---------------------------------------------------------------------------

def deliver_alerts_to_slack(alerts: list[dict]) -> int:
    """Post alerts to the #mcsa-alerts Slack channel.

    Returns the number of alerts successfully delivered.
    """
    if not alerts or not SLACK_MCSA_ENABLED:
        return 0

    webhook_url = SLACK_MCSA_WEBHOOK_URL_ALERTS
    if not webhook_url:
        console.print("[yellow]  Alerts: SLACK_MCSA_WEBHOOK_URL_ALERTS not set, skipping[/yellow]")
        return 0

    delivered = 0

    # Group alerts by severity for a single consolidated message
    high = [a for a in alerts if a["severity"] == SEVERITY_HIGH]
    medium = [a for a in alerts if a["severity"] == SEVERITY_MEDIUM]
    low = [a for a in alerts if a["severity"] == SEVERITY_LOW]

    blocks: list[dict] = []

    # Header
    blocks.append({
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": f"MCSA Alert — {len(alerts)} signal(s) detected",
        },
    })

    blocks.append({"type": "divider"})

    # Build alert sections grouped by severity
    for severity_group, label in [
        (high, "High Priority"),
        (medium, "Medium Priority"),
        (low, "Low Priority"),
    ]:
        if not severity_group:
            continue

        icon = _SEVERITY_ICON.get(severity_group[0]["severity"], ":white_circle:")

        for alert in severity_group:
            alert_text = (
                f"{icon} *{alert['title']}*\n"
                f"_{alert['agency_name']} — {alert['alert_type']}_\n\n"
                f"{alert['detail']}"
            )
            # Truncate to Slack block limit
            if len(alert_text) > 2900:
                alert_text = alert_text[:2900] + "\n_...truncated_"

            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": alert_text},
            })

    # Footer
    blocks.append({"type": "divider"})
    blocks.append({
        "type": "context",
        "elements": [{
            "type": "mrkdwn",
            "text": (
                f":robot_face: MCSA v1.0 Alert Engine | "
                f"{datetime.now().strftime('%A %d %B %Y %H:%M')} | "
                f"{len(high)} high, {len(medium)} medium, {len(low)} low"
            ),
        }],
    })

    # Fallback text
    fallback = f"MCSA Alert: {len(alerts)} signal(s) — {len(high)} high priority"

    payload = {"text": fallback, "blocks": blocks}

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                delivered = len(alerts)
                console.print(
                    f"[green]  Alerts: {delivered} alert(s) delivered to #mcsa-alerts[/green]"
                )
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        console.print(f"[red]  Alerts: Slack HTTP {e.code} — {body}[/red]")
    except Exception as e:
        console.print(f"[red]  Alerts: Slack delivery failed — {e}[/red]")

    return delivered


# ---------------------------------------------------------------------------
# Main entry point — called from orchestrator
# ---------------------------------------------------------------------------

async def run_alert_detection(
    agency_name: str,
    cadence: str,
    current_reports: dict[str, str],
    registries: dict[str, list[dict]],
) -> list[dict]:
    """Detect, persist, and deliver alerts for one agency's surveillance run.

    Called by MCSAOrchestrator after all modules complete for an agency.

    Returns the list of alerts (empty if none detected).
    """
    console.print(f"[dim]  Alert detection: analysing {agency_name} reports...[/dim]")

    detector = AlertDetector()
    alerts = await detector.detect(agency_name, cadence, current_reports, registries)

    if not alerts:
        console.print(f"[dim]  Alert detection: no significant signals for {agency_name}[/dim]")
        return []

    console.print(
        f"[bold yellow]  Alert detection: {len(alerts)} signal(s) for {agency_name}[/bold yellow]"
    )

    # Persist
    save_alerts(alerts)

    # Deliver to Slack
    deliver_alerts_to_slack(alerts)

    # Email delivery (gracefully skips if RESEND_API_KEY not set)
    from .email_delivery import send_alert_email
    send_alert_email(alerts)

    return alerts
