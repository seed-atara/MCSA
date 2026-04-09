"""Base research agent — wraps Claude API calls with retry, cost tracking, and notes injection."""
from __future__ import annotations

import asyncio
import anthropic
from rich.console import Console

from .config import ANTHROPIC_API_KEY, MODEL, MAX_TOKENS, get_research_profile
from .cost_tracker import cost_tracker

console = Console()


class ResearchAgent:
    """Base class for all research agents across any project.

    Provides:
    - Claude API wrapper with retry and cost tracking (_call_claude)
    - Research profile integration (controls token limits, search depth)
    - Notes injection into system prompts for steering research direction

    Subclasses must implement ``research(client_name, context) -> str``.
    """

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self.profile = get_research_profile()

    async def _call_claude(self, system: str, user: str, max_tokens: int = None, context: dict = None) -> str:
        """Make a call to Claude API with configurable token limits.

        If context contains 'notes', they are appended to the system prompt so
        all research is steered towards the user's specific direction.
        """
        tokens = max_tokens or self.profile.get("max_tokens", MAX_TOKENS)

        # Inject research direction notes into the system prompt
        notes = (context or {}).get("notes", "")
        if notes:
            system += (
                f"\n\n--- RESEARCH DIRECTION (from the user) ---\n"
                f"The user has provided specific guidance for this research. "
                f"Weight your analysis and focus areas towards this direction:\n\n"
                f"{notes}\n"
                f"--- END RESEARCH DIRECTION ---"
            )

        # Cap max_tokens to model limit (16384 for Sonnet 4)
        tokens = min(tokens, 16384)

        # Retry with backoff — handles rate limits (429) and transient errors
        last_error = None
        for attempt in range(4):
            try:
                response = self.client.messages.create(
                    model=MODEL,
                    max_tokens=tokens,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                )
                if hasattr(response, "usage"):
                    cost_tracker.log_claude(
                        response.usage.input_tokens,
                        response.usage.output_tokens,
                        label=self.name,
                    )
                return response.content[0].text
            except anthropic.RateLimitError as e:
                last_error = e
                wait = 30 * (attempt + 1)  # 30s, 60s, 90s, 120s
                console.print(f"[yellow]Rate limited ({self.name}), waiting {wait}s... (attempt {attempt + 1}/4)[/yellow]")
                await asyncio.sleep(wait)
            except anthropic.BadRequestError as e:
                # Credit balance errors are permanent — fail immediately, don't retry
                if "credit balance is too low" in str(e):
                    console.print(f"[red]Anthropic credit balance exhausted — top up at console.anthropic.com[/red]")
                    raise
                last_error = e
                if attempt < 3:
                    wait = 2 ** attempt
                    console.print(f"[yellow]Claude API error ({self.name}), retrying in {wait}s: {e}[/yellow]")
                    await asyncio.sleep(wait)
            except Exception as e:
                last_error = e
                if attempt < 3:
                    wait = 2 ** attempt
                    console.print(f"[yellow]Claude API error ({self.name}), retrying in {wait}s: {e}[/yellow]")
                    await asyncio.sleep(wait)
        raise last_error

    async def research(self, client_name: str, context: dict) -> str:
        """Override in subclasses to perform specific research."""
        raise NotImplementedError
