#!/usr/bin/env python3
import os
import re
import json
import smtplib
import calendar
import feedparser
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import anthropic

ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
GMAIL_ADDRESS      = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
RECIPIENT_EMAIL    = os.environ.get("RECIPIENT_EMAIL", "jessenlj40@gmail.com")
GITHUB_USERNAME    = os.environ.get("GITHUB_USERNAME", "jessenlj")
REPO_NAME          = os.environ.get("REPO_NAME", "morning_briefing")

TODAY         = datetime.now().strftime("%A, %B %d, %Y")
TODAY_ISO     = datetime.now().strftime("%Y-%m-%d")
DB_PATH       = "data/briefings.json"
WEBSITE_PATH  = "docs/index.html"
SITE_BASE_URL = f"https://{GITHUB_USERNAME}.github.io/{REPO_NAME}"
LOOKBACK_DAYS = 7

SUBSTACKS = {
    "Not Boring (Packy McCormick)":    "https://www.notboring.co/feed",
    "The Generalist (Mario Gabriele)": "https://thegeneralist.substack.com/feed",
    "Banana Capital (Turner Novak)":   "https://bananacapital.substack.com/feed",
    "Digital Native (Rex Woodbury)":   "https://digitalnative.substack.com/feed",
    "Lenny's Newsletter":              "https://www.lennysnewsletter.com/feed",
    "Latent Space (Swyx)":             "https://www.latent.space/feed",
    "Import AI (Jack Clark)":          "https://importai.substack.com/feed",
    "The Diff (Byrne Hobart)":         "https://diff.substack.com/feed",
}


# ── DATABASE ──────────────────────────────────────────────────────────────────

def load_database():
    if os.path.exists(DB_PATH):
        with open(DB_PATH, "r") as f:
            return json.load(f)
    return []


def save_database(db):
    os.makedirs("data", exist_ok=True)
    with open(DB_PATH, "w") as f:
        json.dump(db, f, indent=2)


# ── SUBSTACK ──────────────────────────────────────────────────────────────────

def fetch_substacks():
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    posts  = []
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


# ── PROMPTS ───────────────────────────────────────────────────────────────────

def build_news_prompt():
    return f"""Today is {TODAY}. Search the web for today's most important news in VC/startup funding, AI, and tech.

For EVERY company or startup you mention, embed the following analysis directly underneath it before moving to the next story:

  PROBLEM THEY'RE SOLVING
  What specific pain point does this company exist to fix? Who feels it most acutely and how are they currently dealing with it?

  DIRECT COMPETITORS
  Every company attacking the same problem — what they do, how far along, and how they differ.

  ADJACENT PROBLEMS
  Related problems nearby and the companies working on each. Are they upstream, downstream, or parallel?

  WHAT HAPPENS IF THEY WIN
  Investor lens: what does winning look like, what are the risks, who could kill them even if the product works?
  Founder lens: what opportunities open up in the world they're building toward? What would need to exist that doesn't yet?

Only use URLs from actual search results. No fabricated links.

## TOP STORIES
[Each story with full inline company analysis. Source URL at end of each story.]

## VC & FUNDING
[Each deal: company name, what they do, founded year, deal size, lead investor, why it matters, source URL. Full inline analysis for each company.]

## AI DEVELOPMENTS
[Each item: what was announced, company context, early reactions, source URL. Full inline analysis.]

## HACKER NEWS SIGNAL
[What the technical community is debating today — no company analysis needed, just sentiment and key arguments.]

## ONE LINE TO REMEMBER
[The single most important thing from today.]

Write for a 19-year-old asking simultaneously "should I invest?" and "could I build something here?" No filler."""


def build_substack_prompt(posts):
    if not posts:
        return None
    raw = "".join(
        f"\nAUTHOR: {p['author']}\nDATE: {p['date']}\nTITLE: {p['title']}\nSUMMARY: {p['summary']}\nURL: {p['link']}\n---"
        for p in posts
    )
    return f"""Today is {TODAY}. Recent posts from the best VC/startup/AI newsletters:

{raw}

Write a SUBSTACK THIS WEEK section:
- Pick 3-4 most forward-looking pieces
- For each: one sentence on what the author argues, one on why it's worth reading
- Flag any contrarian or unusually specific predictions about where things are headed
- End with one sentence on any theme running across multiple posts this week

Read like a smart friend, not a summary bot."""


def build_extraction_prompt(briefing_text):
    return f"""Extract structured data from this briefing. Return valid JSON only — no other text, no markdown fences.

{briefing_text}

{{
  "companies": [
    {{
      "name": "company name",
      "what_they_do": "one sentence",
      "event": "what happened today",
      "event_type": "funding|launch|acquisition|release|other",
      "sector": "primary sector",
      "problem_space": "core problem they solve"
    }}
  ],
  "problem_spaces": ["distinct problem areas covered today"],
  "predictions": [
    {{
      "source": "newsletter or person",
      "text": "the specific prediction or thesis",
      "type": "prediction|thesis|warning"
    }}
  ]
}}"""


def build_connections_prompt(today_data, history):
    history_text = ""
    for entry in history[-180:]:
        history_text += f"\nDATE: {entry['date']} ({entry['date_display']})\n"
        for c in entry.get("companies", []):
            history_text += f"  COMPANY: {c['name']} | EVENT: {c['event']} | DOES: {c['what_they_do']} | SPACE: {c['problem_space']}\n"
        for ps in entry.get("problem_spaces", []):
            history_text += f"  PROBLEM SPACE: {ps}\n"
        for pred in entry.get("predictions", []):
            history_text += f"  PREDICTION ({pred['source']}): {pred['text']}\n"

    return f"""Scan this historical briefing database for meaningful connections to today's news.

TODAY:
{json.dumps(today_data, indent=2)}

HISTORY:
{history_text}

Find every meaningful connection. Look for:
1. Same company appearing again — what changed since we last saw them?
2. Same problem space with a new player — who else was working on this and when?
3. A past prediction or thesis that today's news confirms, contradicts, or advances

Use EXACTLY this format for each connection:

CONNECTION: [short descriptive title]
THEN: [what happened in the past, with date]
NOW: [what's happening today and how it relates]
SIGNIFICANCE: [why this matters — investor or founder angle, be specific]
PAST_DATE: [YYYY-MM-DD]

Separate multiple connections with ---

If no meaningful connections exist, respond with exactly: NONE"""


# ── API CALLS ─────────────────────────────────────────────────────────────────

def run_with_search(prompt, max_tokens=6000):
    client   = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    messages = [{"role": "user", "content": prompt}]
    result   = ""
    for _ in range(10):
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=max_tokens,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=messages,
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


def run_haiku(prompt, max_tokens=2000):
    client   = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def run_sonnet(prompt, max_tokens=2000):
    client   = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


# ── EXTRACTION & CONNECTIONS ──────────────────────────────────────────────────

def extract_structured_data(briefing_text):
    text = run_haiku(build_extraction_prompt(briefing_text), max_tokens=2000)
    text = re.sub(r"^```json\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        return {"companies": [], "problem_spaces": [], "predictions": []}


def find_connections(today_data, history):
    if not history:
        return None
    result = run_sonnet(build_connections_prompt(today_data, history), max_tokens=2000)
    return None if result.strip() == "NONE" else result.strip()


def parse_connections(connections_text):
    """Parse connection blocks into list of dicts for HTML rendering."""
    if not connections_text:
        return []
    blocks  = connections_text.split("---")
    parsed  = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        fields = {}
        for line in block.split("\n"):
            for key in ["CONNECTION", "THEN", "NOW", "SIGNIFICANCE", "PAST_DATE"]:
                if line.startswith(f"{key}:"):
                    fields[key] = line[len(key)+1:].strip()
        if "CONNECTION" in fields:
            parsed.append(fields)
    return parsed


# ── WEBSITE ───────────────────────────────────────────────────────────────────

def generate_website(db):
    os.makedirs("docs", exist_ok=True)

    entries_html = ""
    for entry in reversed(db):
        companies     = entry.get("companies", [])
        problem_spaces = entry.get("problem_spaces", [])
        briefing      = entry.get("full_briefing", "")

        company_tags = "".join(f'<span class="tag">{c["name"]}</span>' for c in companies)
        space_tags   = "".join(f'<span class="tag space">{ps}</span>' for ps in problem_spaces)

        fmt = briefing.replace("\n\n", "</p><p>").replace("\n", "<br>")
        fmt = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", fmt)
        fmt = re.sub(r"^## (.+)$", r"<h4>\1</h4>", fmt, flags=re.MULTILINE)
        fmt = re.sub(
            r"^\s*(PROBLEM THEY'S SOLVING|DIRECT COMPETITORS|ADJACENT PROBLEMS|WHAT HAPPENS IF THEY WIN)\s*$",
            r'<div class="subhead">\1</div>', fmt, flags=re.MULTILINE
        )
        fmt = re.sub(r"(https?://[^\s<)\]]+)", r'<a href="\1">\1</a>', fmt)

        entries_html += f"""
        <article id="{entry['date']}" class="entry">
          <div class="entry-header">
            <h2 class="entry-date">{entry['date_display']}</h2>
            <a href="#{entry['date']}" class="permalink">#</a>
          </div>
          <div class="tags">{company_tags}{space_tags}</div>
          <div class="entry-body"><p>{fmt}</p></div>
        </article>"""

    company_index = {}
    for entry in db:
        for c in entry.get("companies", []):
            name = c["name"]
            if name not in company_index:
                company_index[name] = []
            company_index[name].append({
                "date":         entry["date"],
                "date_display": entry["date_display"],
                "event":        c.get("event", ""),
            })

    company_rows = "".join(
        f'<div class="company-row"><strong>{name}</strong> — '
        + " · ".join(f'<a href="#{a["date"]}">{a["date_display"]}</a> ({a["event"]})' for a in apps)
        + "</div>"
        for name, apps in sorted(company_index.items())
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Morning Briefing Archive</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f9f9f7;color:#1a1a1a;font-size:15px;line-height:1.75}}
.header{{background:#1a1a1a;color:white;padding:32px 40px}}
.header h1{{font-size:22px;font-weight:600}}
.header p{{font-size:13px;color:#888;margin-top:4px}}
.nav{{display:flex;border-bottom:1px solid #e5e5e5;background:white;position:sticky;top:0;z-index:10}}
.nav button{{padding:14px 24px;border:none;background:none;cursor:pointer;font-size:13px;color:#666;border-bottom:2px solid transparent}}
.nav button.active{{color:#1a1a1a;border-bottom-color:#1a1a1a;font-weight:500}}
.container{{max-width:780px;margin:0 auto;padding:32px 24px}}
.panel{{display:none}}.panel.active{{display:block}}
.entry{{background:white;border:1px solid #e5e5e5;border-radius:8px;padding:28px 32px;margin-bottom:24px}}
.entry-header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}}
.entry-date{{font-size:18px;font-weight:600}}
.permalink{{font-size:12px;color:#aaa;text-decoration:none}}
.permalink:hover{{color:#1a1a1a}}
.tags{{margin-bottom:16px;display:flex;flex-wrap:wrap;gap:6px}}
.tag{{font-size:11px;padding:3px 10px;border-radius:20px;background:#f0f0ee;color:#555}}
.tag.space{{background:#e8f0fe;color:#1a56db}}
.entry-body h4{{font-size:11px;font-weight:600;letter-spacing:0.1em;text-transform:uppercase;color:#999;margin:24px 0 8px;padding-bottom:4px;border-bottom:1px solid #eee}}
.entry-body p{{margin-bottom:12px}}
.entry-body a{{color:#0066cc}}
.subhead{{font-size:10px;font-weight:600;letter-spacing:0.1em;text-transform:uppercase;color:#aaa;margin:16px 0 6px;padding-left:12px;border-left:2px solid #eee}}
.search-bar{{width:100%;padding:12px 16px;border:1px solid #e5e5e5;border-radius:8px;font-size:15px;margin-bottom:24px;outline:none}}
.search-bar:focus{{border-color:#1a1a1a}}
.company-row{{padding:12px 0;border-bottom:1px solid #f0f0f0;font-size:14px}}
.company-row a{{color:#0066cc;text-decoration:none}}
.company-row a:hover{{text-decoration:underline}}
.count{{font-size:13px;color:#888;margin-bottom:20px}}
</style>
</head>
<body>
<div class="header">
  <h1>Morning Briefing Archive</h1>
  <p>{len(db)} briefings · updated daily</p>
</div>
<div class="nav">
  <button class="active" onclick="show('briefings',this)">Briefings</button>
  <button onclick="show('companies',this)">Companies</button>
</div>
<div class="container">
  <div id="briefings" class="panel active">
    <input class="search-bar" type="text" placeholder="Search briefings..." oninput="searchBriefings(this.value)">
    <div id="briefings-list">{entries_html}</div>
  </div>
  <div id="companies" class="panel">
    <input class="search-bar" type="text" placeholder="Search companies..." oninput="searchCompanies(this.value)">
    <p class="count">{len(company_index)} companies tracked</p>
    <div id="companies-list">{company_rows}</div>
  </div>
</div>
<script>
function show(panel,btn){{
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.nav button').forEach(b=>b.classList.remove('active'));
  document.getElementById(panel).classList.add('active');
  btn.classList.add('active');
}}
function searchBriefings(q){{
  q=q.toLowerCase();
  document.querySelectorAll('.entry').forEach(el=>{{
    el.style.display=el.textContent.toLowerCase().includes(q)?'':'none';
  }});
}}
function searchCompanies(q){{
  q=q.toLowerCase();
  document.querySelectorAll('.company-row').forEach(el=>{{
    el.style.display=el.textContent.toLowerCase().includes(q)?'':'none';
  }});
}}
if(window.location.hash){{
  const el=document.querySelector(window.location.hash);
  if(el)setTimeout(()=>el.scrollIntoView({{behavior:'smooth'}}),100);
}}
</script>
</body>
</html>"""

    with open(WEBSITE_PATH, "w") as f:
        f.write(html)


# ── EMAIL ─────────────────────────────────────────────────────────────────────

def build_html(news_briefing, substack_section, connections, date_str):
    def fmt(text):
        text = re.sub(
            r"^## (.+)$",
            r'<h3 style="font-size:11px;font-weight:600;letter-spacing:0.1em;text-transform:uppercase;color:#999;margin:40px 0 12px;padding-bottom:6px;border-bottom:1px solid #eee;">\1</h3>',
            text, flags=re.MULTILINE,
        )
        text = re.sub(
            r"^\s*(PROBLEM THEY'RE SOLVING|DIRECT COMPETITORS|ADJACENT PROBLEMS|WHAT HAPPENS IF THEY WIN)\s*$",
            r'<div style="font-size:10px;font-weight:600;letter-spacing:0.1em;text-transform:uppercase;color:#aaa;margin:16px 0 6px;padding-left:12px;border-left:2px solid #eee;">\1</div>',
            text, flags=re.MULTILINE,
        )
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(
            r"^[-*] (.+)$",
            r'<div style="display:flex;gap:10px;margin-bottom:8px;padding-left:12px;"><span style="color:#ccc;flex-shrink:0;margin-top:2px;">—</span><span>\1</span></div>',
            text, flags=re.MULTILINE,
        )
        text = re.sub(
            r"(https?://[^\s<)\]]+)",
            r'<a href="\1" style="color:#0066cc;text-decoration:none;word-break:break-all;">\1</a>',
            text,
        )
        text = re.sub(r"\n\n+", "</p><p>", text)
        text = text.replace("\n", "<br>")
        return f"<p>{text}</p>"

    connections_html = ""
    if connections:
        parsed = parse_connections(connections)
        if parsed:
            items_html = ""
            for c in parsed:
                past_date  = c.get("PAST_DATE", "")
                archive_url = f"{SITE_BASE_URL}/#{past_date}" if past_date else SITE_BASE_URL
                items_html += f"""
                <div style="border-left:3px solid #1a1a1a;padding:12px 16px;margin-bottom:16px;background:#fafafa;">
                  <div style="font-size:12px;font-weight:600;margin-bottom:8px;">{c.get('CONNECTION','')}</div>
                  <div style="font-size:13px;color:#555;margin-bottom:4px;"><strong>Then:</strong> {c.get('THEN','')}</div>
                  <div style="font-size:13px;color:#555;margin-bottom:4px;"><strong>Now:</strong> {c.get('NOW','')}</div>
                  <div style="font-size:13px;color:#555;margin-bottom:8px;"><strong>Why it matters:</strong> {c.get('SIGNIFICANCE','')}</div>
                  <a href="{archive_url}" style="font-size:12px;color:#0066cc;text-decoration:none;">View original briefing →</a>
                </div>"""
            connections_html = f"""
            <div style="margin-top:48px;padding-top:24px;border-top:2px solid #1a1a1a;">
              <div style="font-size:11px;font-weight:600;letter-spacing:0.15em;text-transform:uppercase;color:#999;margin-bottom:16px;">Connections to the Past</div>
              {items_html}
            </div>"""

    substack_html = ""
    if substack_section:
        substack_html = f"""
        <div style="margin-top:48px;padding-top:24px;border-top:2px solid #1a1a1a;">
          <div style="font-size:11px;font-weight:600;letter-spacing:0.15em;text-transform:uppercase;color:#999;margin-bottom:16px;">Substack This Week</div>
          <div style="line-height:1.75;">{fmt(substack_section)}</div>
        </div>"""

    return f"""
<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;max-width:700px;margin:0 auto;padding:32px 24px;color:#1a1a1a;font-size:15px;line-height:1.8;">
  <div style="border-bottom:2px solid #1a1a1a;margin-bottom:28px;padding-bottom:14px;">
    <div style="font-size:11px;font-weight:600;letter-spacing:0.15em;text-transform:uppercase;color:#999;margin-bottom:4px;">Morning Briefing</div>
    <div style="font-size:22px;font-weight:600;">{date_str}</div>
    <a href="{SITE_BASE_URL}" style="font-size:12px;color:#0066cc;text-decoration:none;">View archive →</a>
  </div>
  <div>{fmt(news_briefing)}</div>
  {connections_html}
  {substack_html}
  <div style="border-top:1px solid #eee;margin-top:48px;padding-top:12px;font-size:11px;color:#bbb;">
    Generated daily · <a href="{SITE_BASE_URL}/#{TODAY_ISO}" style="color:#bbb;">View today in archive →</a>
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


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print(f"Running — {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    print("  Loading database...")
    db = load_database()

    print("  Running web search briefing...")
    news = run_with_search(build_news_prompt())

    print("  Fetching Substack feeds...")
    posts    = fetch_substacks()
    substack = None
    if posts:
        print(f"    {len(posts)} posts — summarizing...")
        substack = run_haiku(build_substack_prompt(posts), max_tokens=1000)

    print("  Extracting structured data...")
    structured = extract_structured_data(news)

    print("  Scanning for historical connections...")
    connections = find_connections(structured, db)
    if connections:
        print(f"    Found connections")
    else:
        print("    No connections yet")

    print("  Saving to database...")
    db.append({
        "date":          TODAY_ISO,
        "date_display":  TODAY,
        "companies":     structured.get("companies", []),
        "problem_spaces": structured.get("problem_spaces", []),
        "predictions":   structured.get("predictions", []),
        "full_briefing": news,
    })
    save_database(db)

    print("  Regenerating website...")
    generate_website(db)

    print("  Sending email...")
    date_str = datetime.now().strftime("%A, %B %d, %Y")
    html     = build_html(news, substack, connections, date_str)
    send_email(f"Morning Briefing — {date_str}", html)
    print(f"  Done. Sent to {RECIPIENT_EMAIL}")


if __name__ == "__main__":
    main()
