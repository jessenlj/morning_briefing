#!/usr/bin/env python3
import os
import re
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import anthropic

ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
GMAIL_ADDRESS      = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
RECIPIENT_EMAIL    = os.environ.get("RECIPIENT_EMAIL", "jessenlj40@gmail.com")

TODAY = datetime.now().strftime("%A, %B %d, %Y")

PROMPT = f"""Today is {TODAY}. Search the web for today's most important news in VC/startup funding, AI, and tech.

CRITICAL RULE: For every single company or startup you mention, you must include:
- What the company actually sells or does (one plain sentence)
- When it was founded / how old it is
- Why it is relevant right now

Also for every story, include how the market, investors, or online community is responding — not just what happened.

Only use URLs you actually found in search results. Do not fabricate links.

Write the briefing in exactly this structure:

## TOP STORIES
[3-4 stories. For each: what happened + analysis, company context (what they do / age / relevance), how people are responding, source URL]

## VC & FUNDING
[For each deal: company name + what they do + founded year, deal size + lead investor, why this round matters, source URL]

## AI DEVELOPMENTS
[For each item: what was announced, who built it + brief company context, why it matters + early reactions, source URL]

## HACKER NEWS SIGNAL
[What the technical community is actually debating today, key sentiment]

## ONE LINE TO REMEMBER
[The single most important thing from today]

Write for a sharp 19-year-old finance student who doesn't know every startup by name. Be analytical. No filler."""


def run_briefing():
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    messages = [{"role": "user", "content": PROMPT}]
    result = ""

    for _ in range(8):
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=messages
        )

        texts = [b.text for b in response.content if hasattr(b, "text")]
        if texts:
            result = "\n".join(texts)

        if response.stop_reason == "end_turn":
            break
        elif response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
        else:
            break

    return result


def build_html(briefing, date_str):
    body = briefing
    body = re.sub(r'^## (.+)$',
        r'<h3 style="font-size:11px;font-weight:600;letter-spacing:0.1em;text-transform:uppercase;color:#999;margin:32px 0 10px;padding-bottom:6px;border-bottom:1px solid #eee;">\1</h3>',
        body, flags=re.MULTILINE)
    body = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', body)
    body = re.sub(r'^[-*] (.+)$',
        r'<div style="display:flex;gap:10px;margin-bottom:8px;"><span style="color:#ccc;flex-shrink:0;margin-top:2px;">—</span><span>\1</span></div>',
        body, flags=re.MULTILINE)
    body = re.sub(r'(https?://[^\s<)\]]+)', r'<a href="\1" style="color:#0066cc;text-decoration:none;word-break:break-all;">\1</a>', body)
    body = re.sub(r'\n\n+', '</p><p>', body)
    body = body.replace('\n', '<br>')

    return f"""
<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;max-width:680px;margin:0 auto;padding:32px 24px;color:#1a1a1a;font-size:15px;line-height:1.75;">
  <div style="border-bottom:2px solid #1a1a1a;margin-bottom:24px;padding-bottom:14px;">
    <div style="font-size:11px;font-weight:600;letter-spacing:0.15em;text-transform:uppercase;color:#999;margin-bottom:4px;">Morning Briefing</div>
    <div style="font-size:22px;font-weight:600;">{date_str}</div>
  </div>
  <div><p>{body}</p></div>
  <div style="border-top:1px solid #eee;margin-top:40px;padding-top:12px;font-size:11px;color:#bbb;">
    Generated daily via Claude + live web search
  </div>
</body></html>"""


def send_email(subject, html_body):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = RECIPIENT_EMAIL
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, RECIPIENT_EMAIL, msg.as_string())


def main():
    print(f"Running — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("  Searching and writing briefing...")
    briefing = run_briefing()
    date_str = datetime.now().strftime("%A, %B %d, %Y")
    send_email(f"Morning Briefing — {date_str}", build_html(briefing, date_str))
    print(f"  Sent to {RECIPIENT_EMAIL}")


if __name__ == "__main__":
    main()
