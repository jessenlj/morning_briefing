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
MAX_ITEMS_PER_SOURCE = 5
LOOKBACK_HOURS     = 24

RSS_FEEDS = {
    "TechCrunch":          "https://techcrunch.com/feed/",
    "TechCrunch Startups": "https://techcrunch.com/category/startups/feed/",
    "VentureBeat AI":      "https://venturebeat.com/ai/feed/",
    "Axios":               "https://api.axios.com/feed/",
    "The Rundown AI":      "https://www.therundown.ai/rss",
    "Ars Technica":        "https://feeds.arstechnica.com/arstechnica/technology-lab",
}

def fetch_rss(name, url):
    try:
        feed   = feedparser.parse(url)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
        items  = []
        for entry in feed.entries[:20]:
            published = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published = datetime.fromtimestamp(
                    calendar.timegm(entry.published_parsed), tz=timezone.utc
                )
            if published is None or published >= cutoff:
                items.append({
                    "source":  name,
                    "title":   entry.get("title", ""),
                    "summary": entry.get("summary", entry.get("description", ""))[:500],
                    "link":    entry.get("link", ""),
                })
            if len(items) >= MAX_ITEMS_PER_SOURCE:
                break
        return items
    except Exception as e:
        print(f"  [RSS ERROR] {name}: {e}")
        return []

def fetch_hacker_news(n=15):
    try:
        top_ids = requests.get(
            "https://hacker-news.firebaseio.com/v0/topstories.json", timeout=10
        ).json()[:n]
        items = []
        for sid in top_ids:
            story = requests.get(
                f"https://hacker-news.firebaseio.com/v0/item/{sid}.json", timeout=5
            ).json()
            if story and story.get("type") == "story":
                items.append({
                    "source":  "Hacker News",
                    "title":   story.get("title", ""),
                    "summary": story.get("text", "")[:300] if story.get("text") else "",
                    "link":    story.get("url", f"https://news.ycombinator.com/item?id={sid}"),
                })
        return items
    except Exception as e:
        print(f"  [HN ERROR] {e}")
        return []

def build_prompt(items):
    stories = ""
    for item in items:
        stories += (
            f"\nSOURCE: {item['source']}\n"
            f"TITLE: {item['title']}\n"
            f"SUMMARY: {item['summary']}\n"
            f"URL: {item['link']}\n---"
        )
    return f"""You are writing a morning briefing for a finance student and entrepreneur tracking VC, startups, and AI. Be analytical, skip boilerplate. 3-minute read.

RAW HEADLINES:
{stories}

Structure your response exactly like this:

**TOP STORIES**
[3-4 most important items with 2-3 sentence analysis each, include URL]

**VC & FUNDING**
[Notable rounds, exits, investor moves — tight bullet points]

**AI DEVELOPMENTS**
[Model releases, research, product launches — tight bullet points]

**HACKER NEWS SIGNAL**
[What the technical community is discussing — bullet points]

**ONE LINE TO REMEMBER**
[Single most important takeaway from today]"""

def summarize_with_claude(items):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        messages=[{"role": "user", "content": build_prompt(items)}],
    )
    return msg.content[0].text

def build_html(briefing, date_str):
    body = briefing.replace("\n\n", "</p><p>").replace("\n", "<br>")
    body = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", body)
    body = re.sub(r"(https?://[^\s<]+)", r'<a href="\1" style="color:#0066cc;">\1</a>', body)
    return f"""
<html><body style="font-family:Georgia,serif;max-width:680px;margin:0 auto;padding:24px;color:#1a1a1a;">
  <div style="border-bottom:2px solid #1a1a1a;margin-bottom:20px;padding-bottom:12px;">
    <h2 style="margin:0;font-size:16px;letter-spacing:3px;text-transform:uppercase;">Morning Briefing</h2>
    <p style="margin:4px 0 0;color:#888;font-size:12px;">{date_str}</p>
  </div>
  <div style="line-height:1.75;font-size:15px;"><p>{body}</p></div>
  <div style="border-top:1px solid #eee;margin-top:30px;padding-top:10px;font-size:11px;color:#aaa;">
    Sources: TechCrunch · VentureBeat · Axios · Hacker News · Ars Technica
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
    all_items = []
    for name, url in RSS_FEEDS.items():
        print(f"  Fetching {name}...")
        all_items.extend(fetch_rss(name, url))
    print("  Fetching Hacker News...")
    all_items.extend(fetch_hacker_news())
    print(f"  Total: {len(all_items)} items — summarizing...")
    briefing = summarize_with_claude(all_items)
    date_str = datetime.now().strftime("%A, %B %d, %Y")
    send_email(f"Morning Briefing — {date_str}", build_html(briefing, date_str))
    print(f"  Sent to {RECIPIENT_EMAIL}")

if __name__ == "__main__":
    main()
