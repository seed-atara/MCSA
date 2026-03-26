"""MCSA Auto-Improvement Agent — autonomous feedback-to-fix loop.

Runs on a schedule (hourly). Reads new Slack feedback, triages it,
creates improvement plans, verifies them, executes safe fixes,
notifies the user and dev team.

Safety tiers:
  AUTO-FIX:  Config changes, blocklist additions, search query tweaks
  NOTIFY:    Code changes that need human review before merge
  SKIP:      Vague feedback, praise, or non-actionable items

Usage:
    python scripts/auto_improve.py           # run once
    python scripts/auto_improve.py --dry-run # plan only, don't execute
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import subprocess
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

import anthropic
import requests

# Config
BOT_TOKEN = os.getenv("SLACK_MCSA_BOT_TOKEN", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
DEV_USER_ID = "U09G7RVBLCQ"  # Johannes — gets notified of all auto-fixes
CURSOR_FILE = Path(__file__).parent.parent / "output" / "mcsa" / "auto_improve_cursors.json"
LOG_FILE = Path(__file__).parent.parent / "output" / "mcsa" / "auto_improve_log.jsonl"
PROJECT_ROOT = Path(__file__).parent.parent

MCSA_CHANNELS = {
    "Found": "C0AL55MSXD1", "SEED": "C0AL9E1LQES", "Braidr": "C0ALF3NE9QC",
    "Disrupt": "C0AKW3W02NT", "Culture3": "C0AM5QYFE8G",
    "general": "C0AM5RA8GUQ", "alerts": "C0AL54BBJEP",
}

# Files that are SAFE for auto-modification
SAFE_FILES = {
    "mcsa/agents.py": ["governance footer", "outdated topics list", "search queries", "system prompts"],
    "mcsa/config.py": ["anti_slop_rules", "competitor_guidance", "facts"],
    "mcsa/alerts.py": ["thresholds", "severity levels"],
}

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def _load_cursors() -> dict:
    if CURSOR_FILE.exists():
        return json.loads(CURSOR_FILE.read_text())
    return {}


def _save_cursors(cursors: dict):
    CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    CURSOR_FILE.write_text(json.dumps(cursors, indent=2))


def _log_action(action: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({**action, "timestamp": datetime.now().isoformat()}) + "\n")


def _get_username(user_id: str) -> str:
    try:
        resp = requests.get("https://slack.com/api/users.info",
            headers={"Authorization": f"Bearer {BOT_TOKEN}"},
            params={"user": user_id}, timeout=10)
        data = resp.json()
        if data.get("ok"):
            return data["user"].get("real_name", data["user"].get("name", user_id))
    except Exception:
        pass
    return user_id


def _post_slack(channel_id: str, text: str, thread_ts: str = None):
    payload = {"channel": channel_id, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    requests.post("https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {BOT_TOKEN}", "Content-Type": "application/json"},
        json=payload, timeout=15)


def _dm_dev(text: str):
    """DM Johannes about an auto-fix."""
    # Open DM channel
    resp = requests.post("https://slack.com/api/conversations.open",
        headers={"Authorization": f"Bearer {BOT_TOKEN}", "Content-Type": "application/json"},
        json={"users": DEV_USER_ID}, timeout=10)
    data = resp.json()
    if data.get("ok"):
        dm_channel = data["channel"]["id"]
        _post_slack(dm_channel, text)


def _fetch_new_messages() -> list[dict]:
    """Fetch new human messages from all MCSA channels since last check."""
    cursors = _load_cursors()
    all_messages = []

    for agency, ch_id in MCSA_CHANNELS.items():
        oldest = cursors.get(ch_id, "0")
        try:
            resp = requests.get("https://slack.com/api/conversations.history",
                headers={"Authorization": f"Bearer {BOT_TOKEN}"},
                params={"channel": ch_id, "limit": 50, "oldest": oldest}, timeout=15)
            data = resp.json()
            if not data.get("ok"):
                continue

            for msg in data.get("messages", []):
                if msg.get("bot_id") or msg.get("subtype"):
                    continue
                if not msg.get("user") or not msg.get("text", "").strip():
                    continue
                all_messages.append({
                    "channel": agency,
                    "channel_id": ch_id,
                    "user_id": msg["user"],
                    "user_name": _get_username(msg["user"]),
                    "text": msg["text"].strip(),
                    "ts": msg["ts"],
                })

            # Update cursor
            if data.get("messages"):
                latest_ts = max(float(m.get("ts", "0")) for m in data["messages"])
                cursors[ch_id] = str(latest_ts)
        except Exception as e:
            print(f"  Error reading {agency}: {e}")

    _save_cursors(cursors)
    return all_messages


def _triage_messages(messages: list[dict]) -> list[dict]:
    """Use Claude to triage messages into actionable items."""
    if not messages:
        return []

    msgs_text = "\n".join(
        f"[{m['channel']}] {m['user_name']}: {m['text']}" for m in messages
    )

    resp = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        system=(
            "You are triaging Slack messages from an intelligence system's channels. "
            "Classify each message and extract actionable items.\n\n"
            "For each actionable message, output a JSON array:\n"
            "```json\n"
            '[{"message": "original text", "user": "name", "channel": "agency", '
            '"category": "BUG|DATA_ISSUE|REQUEST|FEEDBACK", '
            '"actionable": true/false, '
            '"summary": "one line summary", '
            '"auto_fixable": true/false, '
            '"fix_type": "blocklist|config|query_tweak|prompt_update|code_change|none", '
            '"fix_description": "what to change"}]\n'
            "```\n\n"
            "auto_fixable=true ONLY for:\n"
            "- Adding topics to outdated/irrelevant blocklist\n"
            "- Adjusting search query terms\n"
            "- Updating config values (competitor names, thresholds)\n"
            "- Minor prompt wording improvements\n\n"
            "auto_fixable=false for:\n"
            "- New features\n"
            "- Architectural changes\n"
            "- Bug fixes requiring code logic changes\n"
            "- Anything you're not sure about\n\n"
            "Skip praise, greetings, and non-actionable chatter."
        ),
        messages=[{"role": "user", "content": f"Messages to triage:\n{msgs_text}"}],
    )

    text = resp.content[0].text
    try:
        start = text.index("[")
        end = text.rindex("]") + 1
        items = json.loads(text[start:end])
        actionable = [i for i in items if i.get("actionable")]

        # Enrich with user_id and ts from original messages
        for item in actionable:
            msg_text = item.get("message", "")
            channel = item.get("channel", "")
            for m in messages:
                if m["channel"] == channel and (msg_text in m["text"] or m["text"] in msg_text):
                    item["user_id"] = m["user_id"]
                    item["message_ts"] = m["ts"]
                    item["channel_id"] = m["channel_id"]
                    break

        return actionable
    except (ValueError, json.JSONDecodeError):
        return []


def _create_fix_plan(item: dict) -> dict | None:
    """Create a specific fix plan for an actionable item."""
    # Determine which file to read based on fix type
    fix_type = item.get("fix_type", "")
    target_file = "mcsa/agents.py"  # default
    if fix_type in ("config", "competitor_guidance"):
        target_file = "mcsa/config.py"
    elif fix_type == "threshold":
        target_file = "mcsa/alerts.py"

    # Read the FULL target file so the planner can find exact text
    file_content = ""
    try:
        full_path = PROJECT_ROOT / target_file
        file_content = full_path.read_text(encoding="utf-8")
    except Exception:
        pass

    # For agents.py which is huge, extract just the governance section
    governance_section = ""
    if target_file == "mcsa/agents.py":
        match = re.search(r'def _governance\(\).*?--- END GOVERNANCE ---"', file_content, re.DOTALL)
        if match:
            governance_section = match.group(0)
        # Use governance section as the context (it's the most common edit target)
        file_context = f"TARGET FILE: {target_file}\n\nGOVERNANCE FUNCTION (exact content):\n{governance_section}"
    else:
        # For smaller files, include the full content
        file_context = f"TARGET FILE: {target_file}\n\nFULL FILE CONTENT:\n{file_content[:8000]}"

    resp = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1500,
        system=(
            "You are creating a precise fix plan for an MCSA improvement. "
            "The fix must be MINIMAL and SAFE — only change what's needed.\n\n"
            "Output a JSON plan:\n"
            "```json\n"
            '{"action": "description of exact change", '
            '"file": "path/to/file.py", '
            '"old_text": "exact text to find and replace", '
            '"new_text": "replacement text", '
            '"risk": "LOW|MEDIUM|HIGH", '
            '"rationale": "why this fixes the issue"}\n'
            "```\n\n"
            "RULES:\n"
            "- old_text must be COPIED EXACTLY from the file content provided below\n"
            "- Do NOT paraphrase or guess — copy-paste the exact string\n"
            "- Only modify safe areas: governance blocklist, config values, search queries\n"
            "- NEVER modify core logic, class structures, or function signatures\n"
            "- If the fix requires more than a simple text replacement, set risk=HIGH\n"
            "- The file field must be the relative path shown in TARGET FILE"
        ),
        messages=[{"role": "user", "content": (
            f"ISSUE: {item['summary']}\n"
            f"FIX TYPE: {item['fix_type']}\n"
            f"DESCRIPTION: {item['fix_description']}\n\n"
            f"{file_context}"
        )}],
    )

    text = resp.content[0].text
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        return json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        return None


def _verify_plan(plan: dict, item: dict, round_num: int) -> dict:
    """Verify a fix plan. Returns {"approved": bool, "issues": [...]}."""
    checks = [
        "Does this fix actually address the user's feedback?",
        "Could this break any existing functionality?",
        "Is the old_text an exact match in the file (no hallucinated code)?",
        "Is the change minimal and scoped to the specific issue?",
        "Does this introduce any hallucinated information?",
    ]

    # Verify old_text exists in the file
    file_path = PROJECT_ROOT / plan.get("file", "")
    old_text = plan.get("old_text", "")
    file_exists = file_path.exists()
    text_found = False
    if file_exists and old_text:
        actual = file_path.read_text(encoding="utf-8")
        text_found = old_text in actual

    resp = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=800,
        system=(
            f"You are verification round {round_num}/3 for an auto-fix. Be critical.\n\n"
            f"File exists: {file_exists}\n"
            f"old_text found in file: {text_found}\n\n"
            "Check:\n" + "\n".join(f"- {c}" for c in checks) + "\n\n"
            "Output JSON: {\"approved\": true/false, \"issues\": [\"issue1\", ...]}"
        ),
        messages=[{"role": "user", "content": (
            f"USER FEEDBACK: {item['summary']}\n"
            f"PLAN: {json.dumps(plan, indent=2)}"
        )}],
    )

    text = resp.content[0].text
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        return json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        return {"approved": False, "issues": ["Could not parse verification"]}


def _execute_fix(plan: dict) -> bool:
    """Execute a verified fix plan."""
    file_path = PROJECT_ROOT / plan["file"]
    old_text = plan["old_text"]
    new_text = plan["new_text"]

    try:
        content = file_path.read_text(encoding="utf-8")
        if old_text not in content:
            print(f"  ERROR: old_text not found in {plan['file']}")
            return False

        updated = content.replace(old_text, new_text, 1)
        file_path.write_text(updated, encoding="utf-8")

        # Verify the module still imports
        module = plan["file"].replace("/", ".").replace(".py", "")
        result = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            # Revert
            file_path.write_text(content, encoding="utf-8")
            print(f"  ERROR: Import failed after edit, reverted: {result.stderr[:200]}")
            return False

        return True
    except Exception as e:
        print(f"  ERROR: {e}")
        return False


def _is_railway() -> bool:
    """Check if running on Railway (no git available)."""
    return bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_SERVICE_NAME"))


def _commit_and_push(plan: dict, item: dict) -> bool:
    """Commit and push the fix. On Railway, skip git and return True (fix is applied to running instance)."""
    if _is_railway():
        print("    Running on Railway — fix applied to running instance (no git commit)")
        return True
    try:
        file_path = plan["file"]
        msg = (
            f"Auto-fix: {item['summary']}\n\n"
            f"Triggered by {item.get('user', '?')} in #{item.get('channel', '?')}:\n"
            f'"{item.get("message", "")[:200]}"\n\n'
            f"Fix: {plan['action']}\n\n"
            f"Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
        )
        subprocess.run(["git", "add", file_path], cwd=PROJECT_ROOT, check=True)
        subprocess.run(["git", "commit", "-m", msg], cwd=PROJECT_ROOT, check=True)
        subprocess.run(["git", "push"], cwd=PROJECT_ROOT, check=True)
        return True
    except Exception as e:
        print(f"  Git error: {e}")
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Plan only, don't execute")
    args = parser.parse_args()

    if not BOT_TOKEN or not ANTHROPIC_API_KEY:
        print("Missing SLACK_MCSA_BOT_TOKEN or ANTHROPIC_API_KEY")
        sys.exit(1)

    print(f"[{datetime.now().strftime('%H:%M')}] MCSA Auto-Improvement Agent starting...")

    # Step 1: Fetch new messages
    messages = _fetch_new_messages()
    if not messages:
        print("  No new messages. Done.")
        return

    print(f"  {len(messages)} new message(s) found")

    # Step 2: Triage
    actionable = _triage_messages(messages)
    if not actionable:
        print("  Nothing actionable. Done.")
        return

    auto_fixable = [i for i in actionable if i.get("auto_fixable")]
    needs_review = [i for i in actionable if not i.get("auto_fixable")]

    print(f"  {len(auto_fixable)} auto-fixable, {len(needs_review)} need human review")

    # Step 3: Process auto-fixable items
    for item in auto_fixable:
        print(f"\n  Processing: {item['summary']}")

        # Create plan
        plan = _create_fix_plan(item)
        if not plan:
            print("    Could not create plan, skipping")
            continue

        if plan.get("risk") == "HIGH":
            print(f"    HIGH risk — moving to human review")
            needs_review.append(item)
            continue

        print(f"    Plan: {plan['action']}")

        # Verify 3 times
        all_approved = True
        for round_num in range(1, 4):
            verification = _verify_plan(plan, item, round_num)
            if not verification.get("approved"):
                print(f"    Verification {round_num}/3 FAILED: {verification.get('issues', [])}")
                all_approved = False
                break
            print(f"    Verification {round_num}/3 PASSED")

        if not all_approved:
            print("    Failed verification — moving to human review")
            needs_review.append(item)
            continue

        if args.dry_run:
            print(f"    DRY RUN — would execute: {plan['action']}")
            continue

        # Execute
        if _execute_fix(plan):
            print("    Fix applied successfully")

            # Commit and push
            if _commit_and_push(plan, item):
                print("    Committed and pushed")

                # Reply to user in Slack
                user_id = item.get("user_id", "")
                reply = (
                    f":white_check_mark: <@{user_id}> Thanks for the feedback. "
                    f"I've automatically updated my system to address this:\n\n"
                    f"*Fix:* {plan['action']}\n\n"
                    f"This will take effect in the next report cycle. "
                    f"_— MCSA Auto-Improvement Agent_ :robot_face:"
                )
                ch_id = item.get("channel_id") or MCSA_CHANNELS.get(item.get("channel"), "")
                msg_ts = item.get("message_ts")
                if ch_id:
                    _post_slack(ch_id, reply, thread_ts=msg_ts)

                # DM Johannes
                railway_note = ""
                if _is_railway():
                    railway_note = (
                        "\n\n:warning: *Railway ephemeral fix* — applied to running instance only. "
                        "To make permanent, apply the same change locally and push to git."
                    )
                _dm_dev(
                    f":gear: *MCSA Auto-Fix Applied*\n\n"
                    f"*Triggered by:* {item.get('user_name', '?')} in #{item.get('channel', '?')}\n"
                    f"*Feedback:* \"{item.get('message', '')[:200]}\"\n"
                    f"*Fix:* {plan['action']}\n"
                    f"*File:* `{plan['file']}`\n"
                    f"*old_text:* `{plan.get('old_text', '')[:100]}...`\n"
                    f"*new_text:* `{plan.get('new_text', '')[:100]}...`\n"
                    f"*Risk:* {plan.get('risk', '?')}\n"
                    f"*Verified:* 3/3 checks passed"
                    f"{railway_note}"
                )

                _log_action({
                    "type": "auto_fix",
                    "item": item,
                    "plan": plan,
                    "status": "executed",
                })
            else:
                print("    Git push failed")
        else:
            print("    Fix failed to apply")

    # Step 4: Notify about items needing human review
    if needs_review:
        review_text = (
            f":clipboard: *MCSA — {len(needs_review)} item(s) need human review*\n\n"
        )
        for item in needs_review:
            review_text += (
                f"• *[{item.get('category', '?')}]* {item['summary']}\n"
                f"  From: {item.get('user_name', '?')} in #{item.get('channel', '?')}\n"
                f"  Suggested fix: {item.get('fix_description', 'N/A')}\n\n"
            )
        _dm_dev(review_text)
        print(f"\n  Notified dev about {len(needs_review)} items needing review")

    print(f"\n[{datetime.now().strftime('%H:%M')}] Done.")


if __name__ == "__main__":
    main()
