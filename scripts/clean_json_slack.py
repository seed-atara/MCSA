"""Delete raw JSON dumps from MCSA Slack channels."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()
import requests

token = os.getenv("SLACK_MCSA_BOT_TOKEN")
channels = {
    "Found": "C0AL55MSXD1", "SEED": "C0AL9E1LQES", "Braidr": "C0ALF3NE9QC",
    "Disrupt": "C0AKW3W02NT", "Culture3": "C0AM5QYFE8G",
}

total = 0
for agency, ch_id in channels.items():
    resp = requests.get("https://slack.com/api/conversations.history",
        headers={"Authorization": f"Bearer {token}"},
        params={"channel": ch_id, "limit": 50})

    deleted = 0
    for msg in resp.json().get("messages", []):
        text = msg.get("text", "")
        is_bot = bool(msg.get("bot_id") or msg.get("subtype") == "bot_message")
        is_json_dump = text.startswith("[?]")

        if is_bot and is_json_dump:
            r = requests.post("https://slack.com/api/chat.delete",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"channel": ch_id, "ts": msg["ts"]})
            result = r.json()
            if result.get("ok"):
                deleted += 1
            else:
                print(f"  {agency}: delete failed - {result.get('error')}")
    total += deleted
    print(f"  {agency}: deleted {deleted}")

print(f"Total: {total}")
