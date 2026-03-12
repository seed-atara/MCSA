#!/usr/bin/env python3
"""
MCSA — Market & Competitor Surveillance Agent
==============================================

Runs competitive intelligence surveillance for Tomorrow Group agencies.

Usage:
    python mcsa_run.py daily
    python mcsa_run.py weekly
    python mcsa_run.py monthly
    python mcsa_run.py daily --agency Found
    python mcsa_run.py weekly --agency Found --agency SEED
"""

import asyncio
import sys
from rich.console import Console
from rich.panel import Panel

console = Console()


def main():
    args = list(sys.argv[1:])

    if "--help" in args or "-h" in args:
        console.print(Panel(
            "[bold]MCSA — Market & Competitor Surveillance Agent[/bold]\n\n"
            "Usage:\n"
            "  python mcsa_run.py daily\n"
            "  python mcsa_run.py weekly\n"
            "  python mcsa_run.py monthly\n"
            "  python mcsa_run.py daily --agency Found\n"
            "  python mcsa_run.py weekly --agency Found --agency SEED\n\n"
            "Arguments:\n"
            "  cadence          daily, weekly, or monthly (default: daily)\n\n"
            "Options:\n"
            "  --agency NAME    Run only for specific agency (repeatable)\n"
            "  --list-agencies  Show configured agencies and exit\n"
            "  --help, -h       Show this help message",
            border_style="cyan"
        ))
        sys.exit(0)

    # Light import for --list-agencies (no anthropic dependency)
    from mcsa.config import AGENCIES, CADENCE_DAILY, CADENCE_WEEKLY, CADENCE_MONTHLY

    if "--list-agencies" in args:
        console.print("[bold]Configured agencies:[/bold]")
        for a in AGENCIES:
            md = a.get("md") or "TBD"
            console.print(f"  - {a['name']} (MD: {md}) — {a['focus']}")
        sys.exit(0)

    # Parse cadence
    valid_cadences = {CADENCE_DAILY, CADENCE_WEEKLY, CADENCE_MONTHLY}
    cadence = CADENCE_DAILY
    positional = [a for a in args if not a.startswith("--")]
    if positional:
        cadence = positional[0].lower()
        if cadence not in valid_cadences:
            console.print(f"[red]Invalid cadence '{cadence}'. Must be one of: {', '.join(sorted(valid_cadences))}[/red]")
            sys.exit(1)

    # Parse --agency flags
    agency_filter = []
    i = 0
    while i < len(args):
        if args[i] == "--agency" and i + 1 < len(args):
            agency_filter.append(args[i + 1])
            i += 2
        else:
            i += 1

    # Resolve agency filter
    agencies = None
    if agency_filter:
        agency_names = {a["name"].lower(): a for a in AGENCIES}
        agencies = []
        for name in agency_filter:
            match = agency_names.get(name.lower())
            if not match:
                console.print(f"[red]Unknown agency '{name}'. Known: {', '.join(a['name'] for a in AGENCIES)}[/red]")
                sys.exit(1)
            agencies.append(match)

    # Heavy imports (require anthropic, etc.)
    from mcsa.orchestrator import run_surveillance
    from core.config import validate_config

    # Validate config
    try:
        validate_config()
    except ValueError as e:
        console.print(f"[red]Configuration Error:[/red]\n{e}")
        sys.exit(1)

    # Run
    try:
        results = asyncio.run(run_surveillance(cadence=cadence, agencies=agencies))
        agency_count = len(results)
        module_count = sum(len(v) for v in results.values())
        console.print(f"\n[bold green]MCSA complete — {agency_count} agencies, {module_count} reports generated.[/bold green]")
        sys.exit(0)
    except KeyboardInterrupt:
        console.print("\n[yellow]Surveillance cancelled by user[/yellow]")
        sys.exit(0)
    except Exception as e:
        console.print(f"\n[red]Error during surveillance:[/red] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
