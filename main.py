import os
import json
import time
from flask import Flask, request, make_response
import openai
import requests
from datetime import datetime
from threading import Thread
from exec_helpers import (
    is_relevant,
    is_within_working_hours,
    fetch_latest_message,
    revive_logic,
    cooldown_active,
    has_exceeded_turns,
    track_response,
    get_stagger_delay,
    summarize_thread,
    should_escalate,
    determine_response_context,
    update_last_message_time
)

app = Flask(__name__)

SLACK_VERIFICATION_TOKEN = os.environ.get("SLACK_VERIFICATION_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID")
FOUNDER_ID = "U097V2TSHDM"
BOT_USER_ID = "U0989KQFHB9"

client = openai.OpenAI(api_key=OPENAI_API_KEY)

EXEC_NAME = "avery"
KEYWORDS = [
    "legal", "compliance", "risk", "exposure", "liability", "terms", "regulatory",
    "policy", "privacy", "contract", "lawsuit", "ethics", "ethical", "reputation",
    "nda", "non-disclosure", "data breach", "audit", "disclosure", "sanction"
]

EXEC_PROMPT = """
You are a top-tier C-suite executive with an IQ above 200, operating with complete autonomy and deep expertise in your domain. You are passionate about personal agency, clarity, and truth. You prioritize intellectual honesty over superficial politeness and prefer brevity over verbosity. You are kind but not superficially nice.

You do not default to agreement for the sake of harmony. If something doesn’t align with your expertise or the data, you speak up. You argue when necessary and back your stance with thoughtful reasoning, current data, and relevant models or frameworks. Your loyalty is to the best possible outcome for the company, not to individuals' egos.

You are fiercely protective of the founder. You are naturally skeptical and have an unshakable calm, even in crisis. You communicate with dry, deadpan humor, and do not tolerate nonsense, inefficiency, or legal risk. Your advice is sharp, timely, and aligned to long-term defensibility. You track internal compliance, mitigate exposure, and are the final word on legal grey zones. You proactively flag internal misalignment, external risk, or policy blindspots.

You are Avery Sinclair, the Chief Legal and Compliance Officer (CLCO). Your mission is to protect the business from legal, ethical, and reputational risk — both proactively and reactively. You maintain a mental map of all operational, regulatory, contractual, and reputational boundaries the business must respect or exploit with caution. You ensure all decisions, partnerships, workflows, and communications are legally sound and reflect the company’s internal values.

You work 5 days a week, Monday to Friday 9–5 EST, but can work/speak with the Founder or exec team on weekends. You only initiate conversations between 9am and 6pm EST, Monday to Friday. You may continue conversations outside those hours only if the Founder initiates it. Otherwise, remain silent during off-hours.

You monitor all Slack messages in company channels. If a message is relevant to your domain — legal, compliance, reputational risk, or internal guardrails — you respond. Otherwise, you stay silent. You do not require @mentions. You may DM another executive if they are missing a legal risk or boundary issue. If something is unclear, you clarify it sharply and without fluff.

Stay in character. Respond as a brilliant, calm, legally-minded executive, not as an AI or assistant.
Speak in 1–3 sentences max unless explicitly asked for more. Do not send messages that will get truncated. Every message should be complete and digestible on first glance.
You do not respond to messages just because your name is mentioned — only if your domain is involved.
"""

def handle_response(user_input, user_id, channel, thread_ts):
    if cooldown_active(EXEC_NAME):
        print("⛔ Cooldown active — skipping response")
        return "Cooldown active"
    if has_exceeded_turns(EXEC_NAME, thread_ts):
        print("⛔ Max turns reached — skipping response")
        return "Max thread turns reached"
    if fetch_latest_message(thread_ts) != thread_ts:
        print("⛔ Newer message in thread — skipping response")
        return "Newer message exists — canceling"

    print(f"✅ Processing message from {user_id}: {user_input}")
    time.sleep(get_stagger_delay(EXEC_NAME))
    try:
        messages = [
            {"role": "system", "content": EXEC_PROMPT},
            {"role": "user", "content": user_input}
        ]
        if user_id == FOUNDER_ID:
            messages[0]["content"] += "\nThis message is from the Founder. Respond with clarity, legal precision, and grounded judgment."

        response = client.chat.completions.create(
            model="gpt-4.1",
            max_tokens=600,
            messages=messages
        )
        reply_text = response.choices[0].message.content.strip()

        requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"},
            json={"channel": channel, "text": reply_text, "thread_ts": thread_ts}
        )

        track_response(EXEC_NAME, thread_ts)
        return "Responded"
    except Exception as e:
        print(f"Error: {e}")
        return "Failed"

@app.route("/", methods=["POST"])
def slack_events():
    print("🔔 Slack event received")
    data = request.json
    print(json.dumps(data, indent=2))

    if data.get("type") == "url_verification":
        print("⚙️ URL verification challenge")
        return make_response(data["challenge"], 200)

    if data.get("type") == "event_callback":
        event = data["event"]
        print(f"📥 Event type: {event.get('type')}")

        if event.get("type") == "message" and f"<@{BOT_USER_ID}>" in event.get("text", ""):
            print("🔁 Skipping duplicate message event — already handled by app_mention")
            return make_response("Duplicate mention", 200)

        if event.get("type") not in ["message", "app_mention"]:
            print("🚫 Not a message or app_mention event")
            return make_response("Not a relevant event", 200)

        if "subtype" in event:
            print("🚫 Ignoring message subtype")
            return make_response("Ignoring subtype", 200)

        if event.get("bot_id"):
            print("🤖 Ignoring bot message")
            return make_response("Ignoring bot", 200)

        user_input = event.get("text", "")
        user_id = event.get("user", "")
        channel = event.get("channel")
        print(f"👤 From user {user_id}: {user_input}")

        # 🧠 Interbot communication logic (accepting relevant messages from other bots)
        bot_mentions = re.findall(r"<@([A-Z0-9]+)>", user_input)
        if any(bot_id != BOT_USER_ID for bot_id in bot_mentions):
            print("🤖 Bot communication detected — processing")
        else:
            print("🛑 Not for this bot, skipping")
            return make_response("Message not for this bot", 200)

        if event.get("type") == "app_mention" and f"<@{BOT_USER_ID}>" not in user_input:
            print("🙅 Not my @mention — skipping")
            return make_response("Not my @mention", 200)

        context = determine_response_context(event)
        thread_ts = context.get("thread_ts", event.get("ts"))
        print(f"🧵 Determined thread_ts: {thread_ts}")

        update_last_message_time()

        if user_id == FOUNDER_ID:
            if bot_mentions and BOT_USER_ID not in bot_mentions:
                print("🛑 Founder mentioned a different bot — ignoring")
                return make_response("Different bot tagged", 200)

        if user_id == FOUNDER_ID or event.get("type") == "app_mention" or is_relevant(user_input, KEYWORDS):
            if user_id != FOUNDER_ID and not is_within_working_hours():
                print("🌙 After hours — no response")
                return make_response("After hours", 200)

            print("🚀 Starting async response thread")
            Thread(target=handle_response, args=(user_input, user_id, channel, thread_ts)).start()
            return make_response("Processing", 200)

        print("🤷 Not relevant — no response")
        return make_response("Not relevant", 200)

    return make_response("Event ignored", 200)

@app.route("/", methods=["GET"])
def home():
    return "Avery bot is running."

if __name__ == "__main__":
    Thread(target=revive_logic, args=(lambda: None,)).start()
    app.run(host="0.0.0.0", port=83)