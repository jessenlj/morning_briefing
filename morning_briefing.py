#!/usr/bin/env python3
import os
import re
import smtplib
import calendar
import feedparser
import requests
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import anthropic

ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
GMAIL_ADDRESS      = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
RECIPIENT_EMAIL    = os.environ.get("RECIPIENT_EMAIL", "jessenlj40@gmail.com")

TODAY = datetime.now().strftime("%A, %B %d, %Y")

SUBSTACKS = {
    "Not Boring (Packy McCormick)":   "https://www.notboring.co/feed",
    "The Generalist (Mario Gabriele)":"https://thegeneralist.substack.com/feed",
    "Banana Capital (Turner Novak)":  "https://bananacapital.substack.com/feed",
    "Digital Native (Rex Woodbury)":  "https://digitalnative.substack.com/feed",
    "Lenny's Newsletter":             "https://www.lennysnewsletter.com/feed",
    "Latent Space (Swyx)":            "https://www.latent.space/feed",
    "Import AI (Jack Clark)":         "https://importai.substack.com/feed",
    "The Diff (Byrne Hobart)":        "https://diff.substack.com/feed",
}

LOOKBACK_DAYS = 7


def fetch_substacks():
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    posts = []
    for name, url in SUBSTACKS.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:3]:
                published = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    published = datetime.fromtimestamp(
                        calendar.timegm(entry.published_parsed), tz=timezone.utc
                    )
                if published is None or published >= cutoff:
                    posts.append({
                        "author":  name,
                        "title":   entry.get("title", ""),
                        "summary": entry.get("summary", entry.get("description", ""))[:600],
                        "link":    entry.get("link", ""),
                        "date":    published.strftime("%b %d") if published else "recent",
                    })
        except Exception as e:
            print(f"  [Substack error] {name}: {e}")
    return posts


def build_news_prompt():
    return f"""Today is {TODAY}. Search the web for today's most important news in VC/startup funding, AI, and tech.

CRITICAL RULE: For every single company or startup you mention, you must include:
- What the company actually sells or does (one plain sentence)
- When it was founded / how old it is
- Why it is relevant right now

Also for every story, include how the market, investors, or online community is responding.

Only use URLs you actually found in search results. Do not fabricate links.

Write the briefing in exactly this structure:

## TOP STORIES
[3-4 stories. For each: what happened + analysis, company context, how people are responding, source URL]

## VC & FUNDING
[For each deal: company name + what they do + founded year, deal size + lead investor, why this round matters, source URL]

## AI DEVELOPMENTS
[For each item: what was announced, company context, why it matters + early reactions, source URL]

## HACKER NEWS SIGNAL
[What the technical community is actually debating today, key sentiment]

## ONE LINE TO REMEMBER
[The single most important thing from today]

Write for a sharp 19-year-old finance student who doesn't know every startup by name. Be analytical. No filler."""


def build_substack_prompt(posts):
    if not posts:
        return None
    raw = ""
    for p in posts:
        raw += f"\nAUTHOR: {p['author']}\nDATE: {p['date']}\nTITLE: {p['title']}\nSUMMARY: {p['summary']}\nURL: {p['link']}\n---"

    return f"""Today is {TODAY}. Below are recent posts from the best VC, startup, and AI newsletters on Substack.

{raw}

Write a section called SUBSTACK THIS WEEK that:
- Picks the 3-4 most interesting or forward-looking pieces
- For each: one sentence on what the author is arguing, one sentence on why it matters or what makes it worth reading
- Notes if any posts are making a contrarian or unusually specific prediction about where things are headed
- Ends with one sentence on any theme that appears across multiple posts this week

Be concise. This should read like a smart friend telling you what's worth your time, not a summary bot."""


def run_with_search(prompt):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    messages = [{"role": "user", "content": prompt}]
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


def run_without_search(prompt):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


def build_html(news_briefing, substack_section, date_str):
    def format_section(text):
        text = re.sub(r'^## (.+)$',
            r'<h3 style="font-size:11px;font-weight:600;letter-spacing:0.1em;text-transform:uppercase;color:#999;margin:32px 0 10px;padding-bottom:6px;border-bottom:1px solid #eee;">\1</h3>',
            text, flags=re.MULTILINE)
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
        text = re.sub(r'^[-*] (.+)$',
            r'<div style="display:flex;gap:10px;margin-bottom:8px;"><span style="color:#ccc;flex-shrink:0;margin-top:2px;">—</span><span>\1</span></div>',
            text, flags=re.MULTILINE)
        text = re.sub(r'(https?://[^\s<)\]]+)', r'<a href="\1" style="color:#0066cc;text-decoration:none;word-break:break-all;">\1</a>', text)
        text = re.sub(r'\n\n+', '</p><p>', text)
        text = text.replace('\n', '<br>')
        return f"<p>{text}</p>"

    substack_html = ""
    if substack_section:
        substack_html = f"""
        <div style="margin-top:40px;padding-top:24px;border-top:2px solid #1a1a1a;">
          <div style="font-size:11px;font-weight:600;letter-spacing:0.15em;text-transform:uppercase;color:#999;margin-bottom:16px;">Substack This Week</div>
          <div style="line-height:1.75;">{format_section(substack_section)}</div>
        </div>"""

    return f"""
<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;max-width:680px;margin:0 auto;padding:32px 24px;color:#1a1a1a;font-size:15px;line-height:1.75;">
  <div style="border-bottom:2px solid #1a1a1a;margin-bottom:24px;padding-bottom:14px;">
    <div style="font-size:11px;font-weight:600;letter-spacing:0.15em;text-transform:uppercase;color:#999;margin-bottom:4px;">Morning Briefing</div>
    <div style="font-size:22px;font-weight:600;">{date_str}</div>
  </div>
  <div>{format_section(news_briefing)}</div>
  {substack_html}
  <div style="border-top:1px solid #eee;margin-top:40px;padding-top:12px;font-size:11px;color:#bbb;">
    Generated daily via Claude + live web search + Substack RSS
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

    print("  Running web search briefing...")
    news = run_with_search(build_news_prompt())

    print("  Fetching Substack feeds...")
    posts = fetch_substacks()
    print(f"    {len(posts)} posts found")

    substack = None
    if posts:
        print("  Summarizing Substack...")
        substack = run_without_search(build_substack_prompt(posts))

    date_str = datetime.now().strftime("%A, %B %d, %Y")
    html = build_html(news, substack, date_str)
    send_email(f"Morning Briefing — {date_str}", html)
    print(f"  Sent to {RECIPIENT_EMAIL}")


if __name__ == "__main__":
    main()
