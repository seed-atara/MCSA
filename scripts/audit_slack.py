"""Audit MCSA Slack channels for user feedback, issues, and actionable dev tasks.

Only reads NEW messages since the last audit (tracked via cursor file).
Sends new messages to Claude for analysis and outputs actionable items.

Usage:
    python scripts/audit_slack.py              # audit all MCSA channels
    python scripts/audit_slack.py --reset      # reset cursors and audit everything
    python scripts/audit_slack.py --channel mcsa-found   # audit one channel
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import requests
import anthropic

CURSOR_FILE = Path(__file__).parent.parent / "output" / "mcsa" / "slack_audit_cursors.json"
MCSA_CHANNELS_PREFIX = "mcsa-"

BOT_TOKEN = os.getenv("SLACK_MCSA_BOT_TOKEN", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")


def _get_channels() -> list[dict]:
    """Get all MCSA channels the bot is a member of."""
    all_channels = []
    cursor = None
    while True:
        params = {"types": "public_channel,private_channel", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(
            "https://slack.com/api/conversations.list",
            headers={"Authorization": f"Bearer {BOT_TOKEN}"},
            params=params,
        )
        data = resp.json()
        if not data.get("ok"):
            print(f"Error listing channels: {data.get('error')}")
            return []
        all_channels.extend(data.get("channels", []))
        cursor = data.get("response_metadata", {}).get("next_cursor", "")
        if not cursor:
            break

    return [
        ch for ch in all_channels
        if ch["name"].startswith(MCSA_CHANNELS_PREFIX) and ch.get("is_member")
    ]


def _get_user_name(user_id: str, cache: dict) -> str:
    """Resolve Slack user ID to display name."""
    if user_id in cache:
        return cache[user_id]
    resp = requests.get(
        "https://slack.com/api/users.info",
        headers={"Authorization": f"Bearer {BOT_TOKEN}"},
        params={"user": user_id},
    )
    data = resp.json()
    if data.get("ok"):
        name = data["user"].get("real_name", data["user"].get("name", user_id))
        cache[user_id] = name
        return name
    cache[user_id] = user_id
    return user_id


def _load_cursors() -> dict:
    """Load last-read timestamps per channel."""
    if CURSOR_FILE.exists():
        return json.loads(CURSOR_FILE.read_text())
    return {}


def _save_cursors(cursors: dict) -> None:
    """Save last-read timestamps."""
    CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    CURSOR_FILE.write_text(json.dumps(cursors, indent=2))


def _fetch_new_messages(channel_id: str, oldest_ts: str = "0") -> list[dict]:
    """Fetch messages newer than oldest_ts from a channel."""
    messages = []
    cursor = None
    while True:
        params = {"channel": channel_id, "limit": 100, "oldest": oldest_ts}
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(
            "https://slack.com/api/conversations.history",
            headers={"Authorization": f"Bearer {BOT_TOKEN}"},
            params=params,
        )
        data = resp.json()
        if not data.get("ok"):
            print(f"  Error reading channel: {data.get('error')}")
            return messages
        messages.extend(data.get("messages", []))
        if not data.get("has_more"):
            break
        cursor = data.get("response_metadata", {}).get("next_cursor", "")
        if not cursor:
            break
    return messages


def _analyse_messages(channel_name: str, messages: list[dict], user_cache: dict) -> str:
    """Send messages to Claude for analysis and extract actionable items."""
    # Format messages for Claude
    formatted = []
    for msg in sorted(messages, key=lambda m: float(m.get("ts", "0"))):
        # Skip bot messages and system messages
        if msg.get("bot_id") or msg.get("subtype") in (
            "channel_join", "channel_leave", "bot_add", "bot_remove",
            "channel_purpose", "channel_topic",
        ):
            continue

        user = msg.get("user", "")
        if user:
            name = _get_user_name(user, user_cache)
        else:
            continue

        text = msg.get("text", "").strip()
        if not text:
            continue

        ts = datetime.fromtimestamp(float(msg["ts"])).strftime("%Y-%m-%d %H:%M")
        formatted.append(f"[{ts}] {name}: {text}")

    if not formatted:
        return ""

    conversation_text = "\n".join(formatted)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        system=(
            "You are a dev team assistant auditing Slack channel messages from the MCSA "
            "(Market & Competitor Surveillance Agent) system. Extract actionable items.\n\n"
            "Categorise each finding as:\n"
            "- [BUG] — something is broken or wrong\n"
            "- [FEEDBACK] — user opinion on quality, accuracy, or usefulness\n"
            "- [REQUEST] — user wants a feature or change\n"
            "- [DATA ISSUE] — wrong competitors, bad data, inaccurate reports\n"
            "- [PRAISE] — positive feedback (track what works)\n"
            "- [QUESTION] — user asked something the bot couldn't answer well\n\n"
            "For each item include: category, who said it, what they said, and a suggested dev action.\n"
            "If there's nothing actionable, say so briefly.\n"
            "Be concise — bullet points, no fluff."
        ),
        messages=[{
            "role": "user",
            "content": f"Channel: #{channel_name}\n\nMessages:\n{conversation_text}",
        }],
    )

    return response.content[0].text


def main():
    parser = argparse.ArgumentParser(description="Audit MCSA Slack channels")
    parser.add_argument("--reset", action="store_true", help="Reset cursors and audit everything")
    parser.add_argument("--channel", type=str, help="Audit a specific channel (e.g. mcsa-found)")
    args = parser.parse_args()

    if not BOT_TOKEN:
        print("Error: SLACK_MCSA_BOT_TOKEN not set")
        sys.exit(1)
    if not ANTHROPIC_API_KEY:
        print("Error: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    # Load cursors
    cursors = {} if args.reset else _load_cursors()

    # Get channels
    channels = _get_channels()
    if args.channel:
        channels = [ch for ch in channels if ch["name"] == args.channel]
        if not channels:
            print(f"Channel {args.channel} not found or bot not a member")
            sys.exit(1)

    print(f"Auditing {len(channels)} MCSA channels...")

    user_cache: dict[str, str] = {}
    all_findings: list[str] = []
    total_new = 0

    for ch in sorted(channels, key=lambda c: c["name"]):
        channel_name = ch["name"]
        channel_id = ch["id"]
        oldest = cursors.get(channel_id, "0")

        messages = _fetch_new_messages(channel_id, oldest)

        # Filter to only human messages (not bot)
        human_msgs = [
            m for m in messages
            if not m.get("bot_id")
            and m.get("subtype") not in ("channel_join", "channel_leave", "bot_add", "channel_purpose")
            and m.get("user")
            and m.get("text", "").strip()
        ]

        if not human_msgs:
            print(f"  #{channel_name}: no new human messages")
        else:
            total_new += len(human_msgs)
            print(f"  #{channel_name}: {len(human_msgs)} new human messages — analysing...")

            analysis = _analyse_messages(channel_name, messages, user_cache)
            if analysis:
                all_findings.append(f"\n## #{channel_name}\n{analysis}")

        # Update cursor to latest message timestamp
        if messages:
            latest_ts = max(float(m.get("ts", "0")) for m in messages)
            cursors[channel_id] = str(latest_ts)

    # Save cursors for next run
    _save_cursors(cursors)

    # Output results
    if not all_findings:
        report = f"# MCSA Slack Audit — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\nNo new actionable messages found."
    else:
        report = (
            f"# MCSA Slack Audit — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"**{total_new} new human messages** across {len(channels)} channels\n"
            + "\n".join(all_findings)
        )

    # Write to file
    output_path = Path(__file__).parent.parent / "tmp.txt"
    output_path.write_text(report, encoding="utf-8")
    print(f"\nAudit complete — results in tmp.txt")


if __name__ == "__main__":
    main()
