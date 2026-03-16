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
            "  python mcsa_run.py weekly --agency Found --agency SEED\n"
            "  python mcsa_run.py digest-morning\n"
            "  python mcsa_run.py digest-weekly\n"
            "  python mcsa_run.py digest-monthly\n\n"
            "Arguments:\n"
            "  cadence          daily, weekly, or monthly (default: daily)\n"
            "  digest-morning   Generate and deliver Morning Brief\n"
            "  digest-weekly    Generate and deliver Weekly Executive Summary\n"
            "  digest-monthly   Generate and deliver Monthly Board Report\n"
            "  synthesis        Generate and deliver Cross-Agency Trend Synthesis\n\n"
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

    # Handle synthesis command
    positional = [a for a in args if not a.startswith("--")]
    if positional and positional[0].lower() == "synthesis":
        from mcsa.synthesis import run_synthesis
        from core.config import validate_config
        try:
            validate_config()
        except ValueError as e:
            console.print(f"[red]Configuration Error:[/red]\n{e}")
            sys.exit(1)

        content = asyncio.run(run_synthesis())
        if content:
            console.print(f"\n[bold green]Synthesis generated and delivered ({len(content)} chars)[/bold green]")
        else:
            console.print(f"\n[yellow]Synthesis generation returned empty[/yellow]")
        sys.exit(0)

    # Handle digest commands
    if positional and positional[0].startswith("digest-"):
        digest_map = {
            "digest-morning": "morning",
            "digest-weekly": "weekly",
            "digest-monthly": "monthly",
        }
        digest_cmd = positional[0].lower()
        if digest_cmd not in digest_map:
            console.print(f"[red]Unknown digest command '{digest_cmd}'. Use: {', '.join(digest_map.keys())}[/red]")
            sys.exit(1)

        from mcsa.digests import run_digest
        from core.config import validate_config
        try:
            validate_config()
        except ValueError as e:
            console.print(f"[red]Configuration Error:[/red]\n{e}")
            sys.exit(1)

        digest = asyncio.run(run_digest(digest_map[digest_cmd]))
        if digest:
            console.print(f"\n[bold green]Digest generated and delivered ({len(digest)} chars)[/bold green]")
        else:
            console.print(f"\n[yellow]Digest generation returned empty[/yellow]")
        sys.exit(0)

    # Parse cadence
    valid_cadences = {CADENCE_DAILY, CADENCE_WEEKLY, CADENCE_MONTHLY}
    cadence = CADENCE_DAILY
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
