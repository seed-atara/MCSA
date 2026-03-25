"""Polish the 3 posts that need work, keep the 2 that are approved."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
import anthropic
from supabase import create_client

client = anthropic.Anthropic()
sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))

# Load current posts
cals = sb.table("content_calendar").select("agency_name, week_start, items").order("week_start", desc=True).limit(5).execute()
posts = {}
for cal in cals.data:
    items = cal.get("items", [])
    if items:
        posts[cal["agency_name"]] = {"monday": items[0], "week_start": cal["week_start"], "all_items": items}

# Only rewrite the 3 that need work
fixes = {
    "Culture3": {
        "issues": (
            "Weak hook: too abstract. Lead with the TED partnership (verified real) as proof point. "
            "Suggested direction: 'TED ideas stick for decades while your campaign died in three weeks.' "
            "CTA is generic — make it sharper and more specific."
        ),
    },
    "Found": {
        "issues": (
            "Missing a strong closing CTA question. Hook 'The AI hype bubble is deflating' is decent "
            "but could be sharper. Keep the anti-bullshit voice. Add a provocative closing question "
            "like 'What AI promises has your team stopped believing?'"
        ),
    },
    "SEED": {
        "issues": (
            "Hook 'The AI content debate is over' is declarative and boring. Needs to be provocative. "
            "Suggested direction: 'We fired our AI copywriter last week. Then hired it back with a human supervisor.' "
            "CTA is too wordy. CRITICAL: SEED is UNDER 1 YEAR OLD, London only. "
            "Do NOT claim years of experience, specific client results, or any office locations."
        ),
    },
}

results = {}
for agency, fix in fixes.items():
    post = posts.get(agency, {}).get("monday", {})
    draft = post.get("draft", "")
    topic = post.get("topic", "")
    rationale = post.get("rationale", "")

    resp = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1500,
        system=(
            f"You are a senior LinkedIn copywriter polishing a post for {agency} (Tomorrow Group). "
            f"The post passed fact-checking but needs quality upgrades.\n\n"
            f"SPECIFIC FIXES NEEDED:\n{fix['issues']}\n\n"
            f"RULES:\n"
            f"- Keep the core argument and brand voice\n"
            f"- 150-250 words, max 3 hashtags\n"
            f"- NEVER invent statistics, client names, team locations, or years of experience\n"
            f"- First line must STOP the scroll — be provocative, unexpected, or bold\n"
            f"- Last line must PROVOKE genuine engagement — not 'thoughts?' or 'let us know'\n"
            f"- Sound like a sharp opinionated human, not a marketing bot\n\n"
            f"Return ONLY the polished post text, nothing else."
        ),
        messages=[{"role": "user", "content": f"TOPIC: {topic}\nRATIONALE: {rationale}\n\nCURRENT DRAFT:\n{draft}"}],
    )

    polished = resp.content[0].text.strip()
    for fence in ['"""', "```"]:
        if polished.startswith(fence):
            polished = polished.split("\n", 1)[-1]
        if polished.endswith(fence):
            polished = polished.rsplit(fence, 1)[0]
    polished = polished.strip()
    results[agency] = polished
    print(f"  {agency}: polished ({len(polished)} chars)")

# Build final output
output = ["ALL 5 POSTS — FINAL REVIEW\n"]
for agency in ["Braidr", "Culture3", "Disrupt", "Found", "SEED"]:
    draft = results.get(agency) or posts.get(agency, {}).get("monday", {}).get("draft", "")
    status = "POLISHED" if agency in results else "APPROVED"
    topic = posts.get(agency, {}).get("monday", {}).get("topic", "?")
    output.append(f"===== {agency} [{status}] =====")
    output.append(f"Topic: {topic}")
    output.append(draft)
    output.append("")

with open("tmp.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(output))

# Update Supabase with polished versions
for agency, polished in results.items():
    data = posts.get(agency)
    if data:
        items = data["all_items"]
        items[0]["draft"] = polished
        sb.table("content_calendar").update({"items": items}).eq(
            "agency_name", agency
        ).eq("week_start", data["week_start"]).execute()
        print(f"  {agency}: saved to Supabase")

print("\nAll 5 posts in tmp.txt for final review")
