#!/usr/bin/env python3
import os, re, json, smtplib, calendar, feedparser
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import anthropic

try:
    import yfinance as yf
    YFINANCE = True
except ImportError:
    YFINANCE = False

ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
GMAIL_ADDRESS      = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
RECIPIENT_EMAIL    = os.environ.get("RECIPIENT_EMAIL", "jessenlj40@gmail.com")
GITHUB_USERNAME    = os.environ.get("GITHUB_USERNAME", "jessenlj")
REPO_NAME          = os.environ.get("REPO_NAME", "morning_briefing")

TODAY        = datetime.now().strftime("%A, %B %d, %Y")
TODAY_ISO    = datetime.now().strftime("%Y-%m-%d")
SITE         = f"https://{GITHUB_USERNAME}.github.io/{REPO_NAME}"
DATA_DIR     = "docs/data"
BRIEFINGS_F  = f"{DATA_DIR}/briefings.json"
COMPANIES_F  = f"{DATA_DIR}/companies.json"
METRICS_F    = f"{DATA_DIR}/metrics.json"
WEBSITE_F    = "docs/index.html"
LOOKBACK     = 7

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

def load(path, default):
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default

def save(path, data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def fetch_substacks():
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK)
    posts = []
    for name, url in SUBSTACKS.items():
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:3]:
                pub = None
                if hasattr(e, "published_parsed") and e.published_parsed:
                    pub = datetime.fromtimestamp(calendar.timegm(e.published_parsed), tz=timezone.utc)
                if pub is None or pub >= cutoff:
                    posts.append({"author": name, "title": e.get("title",""),
                        "summary": e.get("summary", e.get("description",""))[:600],
                        "link": e.get("link",""), "date": pub.strftime("%b %d") if pub else "recent"})
        except Exception as ex:
            print(f"  [Substack] {name}: {ex}")
    return posts

def news_prompt():
    return f"""Today is {TODAY}. Search the web for today's most important VC/startup/AI/tech news.

For EVERY company mentioned, embed this directly underneath before moving on:

  PROBLEM THEY ARE SOLVING
  Specific pain point, who feels it, how they currently cope.

  DIRECT COMPETITORS
  Every company attacking the same problem - stage, differentiation.

  ADJACENT PROBLEMS
  Related problems and the companies working on each.

  WHAT HAPPENS IF THEY WIN
  Investor lens: winning scenario, risks, who could kill them.
  Founder lens: what opportunities open up in the world they create?

Only use URLs from actual search results.

## TOP STORIES
[Each story + full inline analysis. Source URL at end.]

## VC & FUNDING
[Each deal: company, what they do, founded year, ALL investors in round, amount, why it matters, URL. Full inline analysis.]

## AI DEVELOPMENTS
[What was announced, company context, reactions, URL. Full inline analysis.]

## HACKER NEWS SIGNAL
[What the technical community is debating - sentiment and key arguments only.]

## ONE LINE TO REMEMBER
[Single most important thing today.]

Write for a 19-year-old asking should I invest and could I build something here. No filler."""

def substack_prompt(posts):
    raw = "".join(f"\nAUTHOR: {p['author']}\nDATE: {p['date']}\nTITLE: {p['title']}\nSUMMARY: {p['summary']}\nURL: {p['link']}\n---" for p in posts)
    return f"""Today is {TODAY}. Recent posts from top VC/startup/AI Substack newsletters:
{raw}
Write SUBSTACK THIS WEEK: pick 3-4 most forward-looking pieces, one sentence on what the author argues, one on why it's worth reading. Flag contrarian predictions. End with one sentence on any shared theme. Smart friend tone, not summary bot."""

def extraction_prompt(text):
    return f"""Extract structured data from this briefing. Return ONLY valid JSON, nothing else.

{text}

{{
  "companies": [{{
    "name": "string",
    "what_they_do": "string",
    "industry_vertical": "string",
    "tech_layer": "string",
    "event": "string",
    "event_type": "funding|launch|acquisition|ipo|release|other",
    "stage": "pre-seed|seed|series-a|series-b|series-c|growth|public",
    "amount_m": 0,
    "investors": ["string"],
    "founded_year": 0,
    "location": "string",
    "founder_backgrounds": ["string"],
    "problem_space": "string",
    "is_exit": false,
    "exit_type": null,
    "potential_high_growth": true
  }}],
  "problem_spaces": ["string"],
  "predictions": [{{"source":"string","text":"string","type":"prediction|thesis|warning"}}]
}}"""

def high_growth_prompt(c):
    return f"""Evaluate for high-growth potential. Return ONLY JSON, nothing else.
{json.dumps(c)}
{{"is_high_growth": true, "confidence": "high|medium|low", "reasoning": "2-3 sentences on market size, timing, team signals, competitive position"}}"""

def connections_prompt(today_data, history):
    hist = ""
    for e in history[-180:]:
        hist += f"\nDATE: {e['date']}\n"
        for c in e.get("companies", []):
            hist += f"  COMPANY: {c.get('name','')} | EVENT: {c.get('event','')} | SPACE: {c.get('problem_space','')}\n"
        for ps in e.get("problem_spaces", []):
            hist += f"  SPACE: {ps}\n"
        for p in e.get("predictions", []):
            hist += f"  PREDICTION ({p.get('source','')}): {p.get('text','')}\n"
    return f"""Find meaningful connections between today's news and the historical database.

TODAY: {json.dumps(today_data, indent=2)}
HISTORY: {hist}

For each connection:
CONNECTION: [title]
THEN: [what happened, with date]
NOW: [today's related event]
SIGNIFICANCE: [investor or founder angle]
PAST_DATE: [YYYY-MM-DD]

Separate with ---
If none: respond exactly NONE"""

def sonnet_search(prompt, max_tokens=6000):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    messages = [{"role": "user", "content": prompt}]
    result = ""
    for _ in range(10):
        r = client.messages.create(model="claude-sonnet-4-20250514", max_tokens=max_tokens,
            tools=[{"type": "web_search_20250305", "name": "web_search"}], messages=messages)
        texts = [b.text for b in r.content if hasattr(b, "text")]
        if texts:
            result = "\n".join(texts)
        if r.stop_reason == "end_turn":
            break
        elif r.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": r.content})
        else:
            break
    return result

def haiku(prompt, max_tokens=1500):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]).content[0].text

def sonnet(prompt, max_tokens=2000):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return client.messages.create(model="claude-sonnet-4-20250514", max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]).content[0].text

def extract(text):
    raw = haiku(extraction_prompt(text), 2000)
    raw = re.sub(r"^```json\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except:
        return {"companies": [], "problem_spaces": [], "predictions": []}

def detect_high_growth(c):
    raw = haiku(high_growth_prompt(c), 300)
    raw = re.sub(r"^```json\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw)
    try:
        r = json.loads(raw)
        return r.get("is_high_growth", False), r.get("reasoning", "")
    except:
        return c.get("potential_high_growth", False), ""

def find_connections(today_data, history):
    if not history:
        return None
    r = sonnet(connections_prompt(today_data, history), 2000)
    return None if r.strip() == "NONE" else r.strip()

def parse_connections(text):
    if not text:
        return []
    parsed = []
    for block in text.split("---"):
        block = block.strip()
        if not block:
            continue
        fields = {}
        for line in block.split("\n"):
            for k in ["CONNECTION","THEN","NOW","SIGNIFICANCE","PAST_DATE"]:
                if line.startswith(f"{k}:"):
                    fields[k] = line[len(k)+1:].strip()
        if "CONNECTION" in fields:
            parsed.append(fields)
    return parsed

def fetch_prices(companies):
    if not YFINANCE:
        return companies
    for name, c in companies.items():
        ticker = c.get("ticker")
        if ticker and c.get("status") == "public":
            try:
                stock = yf.Ticker(ticker)
                hist = stock.history(period="2d")
                if len(hist) >= 1:
                    cur = float(hist["Close"].iloc[-1])
                    prev = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else cur
                    today_pct = ((cur - prev) / prev * 100) if prev else 0
                    ipo_price = c.get("ipo_price")
                    since_ipo = ((cur - ipo_price) / ipo_price * 100) if ipo_price else None
                    c.update({"current_price": round(cur, 2),
                        "price_change_today_pct": round(today_pct, 2),
                        "price_change_since_ipo_pct": round(since_ipo, 1) if since_ipo is not None else None,
                        "price_updated": TODAY_ISO})
            except Exception as ex:
                print(f"    [Stock] {name}: {ex}")
    return companies

def update_companies(companies, extracted, hg_new):
    for c in extracted:
        name = c.get("name", "")
        if not name:
            continue
        is_new = name not in companies
        if is_new:
            is_hg, reasoning = detect_high_growth(c)
            companies[name] = {
                "name": name, "what_they_do": c.get("what_they_do",""),
                "industry_vertical": c.get("industry_vertical","Other"),
                "tech_layer": c.get("tech_layer","Other"),
                "founded": c.get("founded_year"), "location": c.get("location",""),
                "status": "public" if c.get("event_type") == "ipo" else "private",
                "ticker": None, "ipo_date": None, "ipo_price": None,
                "high_growth": is_hg, "high_growth_reasoning": reasoning,
                "founder_backgrounds": c.get("founder_backgrounds",[]),
                "funding_rounds": [], "current_price": None,
                "price_change_today_pct": None, "price_change_since_ipo_pct": None,
                "first_seen_in_briefing": TODAY_ISO, "last_updated": TODAY_ISO, "appearances": [TODAY_ISO],
            }
            if is_hg:
                hg_new.append(name)
        else:
            companies[name]["last_updated"] = TODAY_ISO
            if TODAY_ISO not in companies[name].get("appearances", []):
                companies[name].setdefault("appearances", []).append(TODAY_ISO)

        if c.get("event_type") == "funding" and c.get("amount_m"):
            rnd = {"date": TODAY_ISO[:7], "stage": c.get("stage","Unknown"),
                "amount_m": c.get("amount_m"), "investors": c.get("investors",[]), "source": ""}
            existing = companies[name].get("funding_rounds", [])
            if not any(r.get("date") == rnd["date"] and r.get("stage") == rnd["stage"] for r in existing):
                companies[name].setdefault("funding_rounds",[]).append(rnd)

        if c.get("is_exit") and c.get("exit_type") == "ipo":
            companies[name].update({"status": "public", "ipo_date": TODAY_ISO[:7]})

    return companies

def compute_metrics(briefings, companies):
    now = datetime.now(timezone.utc)
    c30, c90 = now - timedelta(days=30), now - timedelta(days=90)
    s30, s90 = Counter(), Counter()
    timeline = defaultdict(list)

    for e in briefings:
        try:
            ed = datetime.fromisoformat(e["date"]).replace(tzinfo=timezone.utc)
        except:
            continue
        for ps in e.get("problem_spaces", []):
            if ed >= c90:
                s90[ps] += 1
            if ed >= c30:
                s30[ps] += 1
            timeline[ps].append(e["date"])

    inv_deals = defaultdict(list)
    inv_pairs = Counter()
    deal_flow = defaultdict(list)
    exits = []

    for name, co in companies.items():
        for rnd in co.get("funding_rounds", []):
            invs = rnd.get("investors", [])
            stage = rnd.get("stage", "Unknown")
            for inv in invs:
                if inv:
                    inv_deals[inv].append({"company": name, "stage": stage, "date": rnd.get("date","")})
            for i in range(len(invs)):
                for j in range(i+1, len(invs)):
                    if invs[i] and invs[j]:
                        pair = tuple(sorted([invs[i], invs[j]]))
                        inv_pairs[pair] += 1
            deal_flow[stage].append({"company": name, "what_they_do": co.get("what_they_do",""),
                "amount_m": rnd.get("amount_m"), "valuation_b": rnd.get("valuation_b"),
                "investors": invs, "date": rnd.get("date",""),
                "industry_vertical": co.get("industry_vertical","")})

        if co.get("status") in ["public","acquired"]:
            exits.append({"company": name,
                "exit_type": "IPO" if co.get("status") == "public" else "Acquisition",
                "industry_vertical": co.get("industry_vertical",""),
                "tech_layer": co.get("tech_layer",""),
                "date": co.get("ipo_date") or co.get("acquisition_date",""),
                "ticker": co.get("ticker")})

    categories = defaultdict(lambda: {"companies": [], "tech_layers": defaultdict(list)})
    for name, co in companies.items():
        v = co.get("industry_vertical","Other")
        t = co.get("tech_layer","Other")
        categories[v]["companies"].append(name)
        categories[v]["tech_layers"][t].append(name)

    geo = Counter(co.get("location","") for co in companies.values() if co.get("location"))
    founder_patterns = defaultdict(Counter)
    for co in companies.values():
        v = co.get("industry_vertical","Other")
        for bg in co.get("founder_backgrounds",[]):
            founder_patterns[v][bg] += 1

    accelerating = []
    for space, cnt30 in s30.most_common(20):
        cnt90 = s90.get(space, 0)
        expected = cnt90 / 3 if cnt90 > 0 else 0.1
        if cnt30 > expected * 1.4 and cnt30 >= 2:
            pct = int((cnt30 / expected - 1) * 100)
            accelerating.append({"space": space, "count_30": cnt30, "count_90": cnt90, "accel_pct": pct})

    return {
        "narrative_velocity": {
            "30_day": dict(s30.most_common(20)),
            "90_day": dict(s90.most_common(20)),
            "timeline": {k: v[-60:] for k, v in timeline.items()},
            "accelerating": accelerating[:5],
        },
        "investor_activity": {
            "by_deals": dict(Counter({inv: len(d) for inv, d in inv_deals.items()}).most_common(30)),
            "co_investments": [{"pair": list(p), "count": c} for p, c in inv_pairs.most_common(50)],
        },
        "deal_flow": dict(deal_flow),
        "exits": sorted(exits, key=lambda x: x.get("date",""), reverse=True),
        "categories": {v: {"companies": d["companies"], "tech_layers": dict(d["tech_layers"])} for v, d in categories.items()},
        "high_growth": [n for n, c in companies.items() if c.get("high_growth")],
        "geo": dict(geo.most_common(20)),
        "founder_patterns": {v: dict(c) for v, c in founder_patterns.items()},
        "total_companies": len(companies),
        "computed_at": datetime.now().isoformat(),
    }

def build_flags(metrics):
    flags = []
    for a in metrics.get("narrative_velocity",{}).get("accelerating",[])[:2]:
        flags.append(f"Narrative velocity: <strong>{a['space']}</strong> up {a['accel_pct']}% vs 90-day baseline")
    hg_new = metrics.get("high_growth_new_today",[])
    if hg_new:
        flags.append(f"High-growth additions: <strong>{', '.join(hg_new[:3])}</strong>")
    recent_exits = [e for e in metrics.get("exits",[]) if e.get("date","") >= (datetime.now()-timedelta(days=7)).strftime("%Y-%m")]
    if recent_exits:
        flags.append(f"Exit signals: <strong>{', '.join(e['company'] for e in recent_exits[:2])}</strong>")
    top_inv = list(metrics.get("investor_activity",{}).get("by_deals",{}).keys())[:3]
    if top_inv:
        flags.append(f"Most active investors in database: {', '.join(top_inv)}")
    return flags

def generate_website(briefings, companies, metrics):
    os.makedirs("docs", exist_ok=True)
    save(BRIEFINGS_F, briefings)
    save(COMPANIES_F, companies)
    save(METRICS_F, metrics)

    html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Morning Briefing</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f5f5f3;color:#1a1a1a;font-size:14px;line-height:1.6}
.site-header{background:#1a1a1a;color:#fff;padding:20px 32px;display:flex;justify-content:space-between;align-items:center}
.site-header h1{font-size:18px;font-weight:600}
.site-header p{font-size:12px;color:#888;margin-top:2px}
.nav{background:#fff;border-bottom:1px solid #e5e5e5;display:flex;overflow-x:auto;position:sticky;top:0;z-index:100}
.nav button{padding:14px 18px;border:none;background:none;cursor:pointer;font-size:13px;color:#666;border-bottom:2px solid transparent;white-space:nowrap}
.nav button:hover{color:#1a1a1a}.nav button.active{color:#1a1a1a;border-bottom-color:#1a1a1a;font-weight:500}
.container{max-width:1200px;margin:0 auto;padding:24px 20px}
.panel{display:none}.panel.active{display:block}
.card{background:#fff;border:1px solid #e5e5e5;border-radius:8px;padding:20px 24px;margin-bottom:16px}
.card-sm{background:#fff;border:1px solid #e5e5e5;border-radius:8px;padding:12px 16px}
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.grid-3{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
.grid-4{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
@media(max-width:768px){.grid-2,.grid-3,.grid-4{grid-template-columns:1fr}}
.metric{background:#fff;border:1px solid #e5e5e5;border-radius:8px;padding:16px 20px;text-align:center}
.metric-val{font-size:28px;font-weight:600}.metric-label{font-size:11px;color:#999;text-transform:uppercase;letter-spacing:.08em;margin-top:4px}
.tag{display:inline-block;font-size:11px;padding:2px 8px;border-radius:12px;margin:2px;background:#f0f0ee;color:#555}
.tag.v{background:#e8f4fd;color:#1a6bb5}.tag.t{background:#f0fdf4;color:#166534}
.tag.hg{background:#fef3c7;color:#92400e}.tag.f{background:#f3f4f6;color:#374151}
.section-title{font-size:11px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:#999;margin:24px 0 12px;padding-bottom:6px;border-bottom:1px solid #eee}
.search-bar{width:100%;padding:10px 16px;border:1px solid #e5e5e5;border-radius:8px;font-size:14px;margin-bottom:16px;outline:none;background:#fff}
.search-bar:focus{border-color:#1a1a1a}
.filter-bar{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:16px}
.pill{padding:5px 12px;border-radius:20px;border:1px solid #e5e5e5;background:#fff;font-size:12px;cursor:pointer}
.pill:hover{border-color:#999}.pill.active{background:#1a1a1a;color:#fff;border-color:#1a1a1a}
.company-card{background:#fff;border:1px solid #e5e5e5;border-radius:8px;padding:16px 20px;margin-bottom:10px;cursor:pointer;transition:border-color .15s}
.company-card:hover{border-color:#999}
.company-card .detail{display:none;margin-top:14px;padding-top:14px;border-top:1px solid #f0f0f0}
.company-card.open .detail{display:block}
.round{display:flex;gap:10px;align-items:flex-start;padding:8px 0;border-bottom:1px solid #f8f8f8}
.round:last-child{border:none}
.round-stage{font-size:11px;font-weight:600;padding:2px 8px;border-radius:12px;background:#f0f0ee;white-space:nowrap;flex-shrink:0}
.up{color:#16a34a;font-weight:500}.down{color:#dc2626;font-weight:500}
.investor-row{display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:1px solid #f5f5f5}
.investor-row:last-child{border:none}
.bar-track{height:4px;background:#f0f0f0;border-radius:2px;margin-top:4px;flex:1}
.bar-fill{height:100%;background:#1a1a1a;border-radius:2px}
.momentum-row{display:flex;align-items:center;gap:12px;padding:8px 0;border-bottom:1px solid #f5f5f5}
.momentum-row:last-child{border:none}
.exit-row{display:flex;gap:12px;align-items:flex-start;padding:12px 0;border-bottom:1px solid #f5f5f5}
.exit-row:last-child{border:none}
.exit-ipo{background:#dcfce7;color:#166534;font-size:11px;font-weight:600;padding:2px 8px;border-radius:12px;white-space:nowrap}
.exit-acq{background:#dbeafe;color:#1e40af;font-size:11px;font-weight:600;padding:2px 8px;border-radius:12px;white-space:nowrap}
.briefing-text h4{font-size:11px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:#999;margin:20px 0 8px;padding-bottom:4px;border-bottom:1px solid #eee}
.briefing-text .subh{font-size:10px;font-weight:600;text-transform:uppercase;color:#ccc;margin:12px 0 4px;padding-left:10px;border-left:2px solid #eee;letter-spacing:.08em}
.briefing-text p{margin-bottom:10px;line-height:1.75}.briefing-text a{color:#0066cc}
.briefing-text .bul{display:flex;gap:8px;margin-bottom:6px;padding-left:10px}
.briefing-text .dash{color:#ccc;flex-shrink:0}
.cat-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px;margin-bottom:20px}
.cat-card{background:#fff;border:1px solid #e5e5e5;border-radius:8px;padding:16px;cursor:pointer;transition:border-color .15s}
.cat-card:hover{border-color:#999}
.chart-h{position:relative;height:320px;margin:12px 0}
.chart-h-lg{position:relative;height:400px;margin:12px 0}
#netCanvas{width:100%;height:380px;border:1px solid #e5e5e5;border-radius:8px;background:#fafafa}
.loading{color:#999;font-size:14px;padding:40px;text-align:center}
</style>
</head>
<body>
<div class="site-header">
  <div><h1>Morning Briefing</h1><p id="hdr-stats">Loading...</p></div>
  <div style="font-size:12px;color:#666">Updated daily at 8 AM MDT</div>
</div>
<div class="nav">
  <button class="active" onclick="tab('dashboard',this)">Dashboard</button>
  <button onclick="tab('briefings',this)">Briefings</button>
  <button onclick="tab('companies',this)">Companies</button>
  <button onclick="tab('dealflow',this)">Deal Flow</button>
  <button onclick="tab('velocity',this)">Narrative Velocity</button>
  <button onclick="tab('investors',this)">Investors</button>
  <button onclick="tab('exits',this)">Exits</button>
  <button onclick="tab('categories',this)">Categories</button>
</div>
<div class="container">
  <div id="tab-dashboard" class="panel active"><div class="loading">Loading dashboard...</div></div>
  <div id="tab-briefings" class="panel"><div class="loading">Loading briefings...</div></div>
  <div id="tab-companies" class="panel"><div class="loading">Loading companies...</div></div>
  <div id="tab-dealflow" class="panel"><div class="loading">Loading deal flow...</div></div>
  <div id="tab-velocity" class="panel"><div class="loading">Loading charts...</div></div>
  <div id="tab-investors" class="panel"><div class="loading">Loading investors...</div></div>
  <div id="tab-exits" class="panel"><div class="loading">Loading exits...</div></div>
  <div id="tab-categories" class="panel"><div class="loading">Loading categories...</div></div>
</div>
<script>
const BASE='__SITE__';
let DB={},charts={};
async function loadData(){
  const [b,c,m]=await Promise.all([
    fetch(BASE+'/data/briefings.json').then(r=>r.json()).catch(()=>[]),
    fetch(BASE+'/data/companies.json').then(r=>r.json()).catch(()=>({})),
    fetch(BASE+'/data/metrics.json').then(r=>r.json()).catch(()=>({})),
  ]);
  DB.briefings=b;DB.companies=c;DB.metrics=m;
  initAll();
}
function tab(id,btn){
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.nav button').forEach(b=>b.classList.remove('active'));
  document.getElementById('tab-'+id).classList.add('active');
  btn.classList.add('active');
}
function pct(v){
  if(v==null)return '-';
  const s=v>=0?'+':'',cls=v>=0?'up':'down';
  return '<span class="'+cls+'">'+s+v.toFixed(1)+'%</span>';
}
function fmt(text){
  if(!text)return '';
  text=text.replace(/## (.+)/gm,'<h4>$1</h4>');
  text=text.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>');
  text=text.replace(/^\s*(PROBLEM THEY ARE SOLVING|DIRECT COMPETITORS|ADJACENT PROBLEMS|WHAT HAPPENS IF THEY WIN)\s*$/gm,'<div class="subh">$1</div>');
  text=text.replace(/^[-*] (.+)/gm,'<div class="bul"><span class="dash">-</span><span>$1</span></div>');
  text=text.replace(/(https?:\/\/[^\s<)\]]+)/g,'<a href="$1">$1</a>');
  text=text.replace(/\n\n+/g,'</p><p>').replace(/\n/g,'<br>');
  return '<p>'+text+'</p>';
}
function renderDashboard(){
  const {briefings,companies,metrics}=DB;
  const cos=Object.values(companies);
  const hg=metrics.high_growth||[];
  const nv30=metrics.narrative_velocity&&metrics.narrative_velocity['30_day']||{};
  const nv90=metrics.narrative_velocity&&metrics.narrative_velocity['90_day']||{};
  const invDeals=metrics.investor_activity&&metrics.investor_activity.by_deals||{};
  const exits=metrics.exits||[];
  const spaces=[...new Set([...Object.keys(nv30),...Object.keys(nv90)])].slice(0,8);
  const invEntries=Object.entries(invDeals).slice(0,8);
  const maxInv=invEntries[0]?invEntries[0][1]:1;
  const momentumEntries=Object.entries(nv30).sort((a,b)=>b[1]-a[1]).slice(0,8);
  const max30=momentumEntries[0]?momentumEntries[0][1]:1;
  document.getElementById('hdr-stats').textContent=briefings.length+' briefings - '+cos.length+' companies - '+hg.length+' high-growth';
  let metricsHtml='<div class="grid-4" style="margin-bottom:20px">';
  [['Total Companies',cos.length,''],['Briefings',briefings.length,''],['High-Growth',hg.length,'flagged'],['Exits',exits.length,'tracked']].forEach(function(item){
    metricsHtml+='<div class="metric"><div class="metric-val">'+item[1]+'</div><div class="metric-label">'+item[0]+'</div>'+(item[2]?'<div style="font-size:12px;color:#999">'+item[2]+'</div>':'')+'</div>';
  });
  metricsHtml+='</div>';
  let momentumHtml='';
  momentumEntries.forEach(function(entry){
    const sp=entry[0],cnt=entry[1];
    const cnt90=nv90[sp]||0,exp=cnt90/3||0.1,acc=Math.round((cnt/exp-1)*100);
    momentumHtml+='<div class="momentum-row"><div style="flex:1;font-size:13px;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+sp+'</div><div class="bar-track" style="flex:2"><div class="bar-fill" style="width:'+Math.round(cnt/max30*100)+'%"></div></div><div style="font-size:12px;color:#666;min-width:40px;text-align:right">'+cnt+(acc>20?' <span style="color:#16a34a;font-size:10px">up</span>':'')+'</div></div>';
  });
  let invHtml='';
  invEntries.forEach(function(entry){
    const inv=entry[0],cnt=entry[1];
    invHtml+='<div class="investor-row"><div style="flex:1"><div style="font-weight:500">'+inv+'</div><div style="display:flex;align-items:center;gap:8px;margin-top:4px"><div class="bar-track"><div class="bar-fill" style="width:'+Math.round(cnt/maxInv*100)+'%"></div></div></div></div><div style="font-weight:500;margin-left:12px">'+cnt+'</div></div>';
  });
  let hgHtml='';
  hg.slice(0,6).forEach(function(n){
    const c=companies[n]||{};
    hgHtml+='<div style="padding:8px 0;border-bottom:1px solid #f5f5f5"><div style="font-weight:500;font-size:13px">'+n+'</div><div style="font-size:12px;color:#666">'+(c.what_they_do||'')+'</div><div style="margin-top:4px">'+(c.industry_vertical?'<span class="tag v">'+c.industry_vertical+'</span>':'')+(c.tech_layer?'<span class="tag t">'+c.tech_layer+'</span>':'')+'</div></div>';
  });
  if(!hgHtml) hgHtml='<div style="color:#999;font-size:13px">No companies flagged yet.</div>';
  let exitsHtml='';
  exits.slice(0,5).forEach(function(e){
    exitsHtml+='<div class="exit-row"><span class="'+(e.exit_type==='IPO'?'exit-ipo':'exit-acq')+'">'+e.exit_type+'</span><div><div style="font-weight:500">'+e.company+(e.ticker?' ('+e.ticker+')':'')+'</div><div style="font-size:12px;color:#666">'+(e.industry_vertical||'')+(e.date?' - '+e.date:'')+'</div></div></div>';
  });
  if(!exitsHtml) exitsHtml='<div style="color:#999;font-size:13px">No exits yet.</div>';
  document.getElementById('tab-dashboard').innerHTML=metricsHtml+'<div class="grid-2" style="margin-bottom:16px"><div class="card"><div class="section-title" style="margin-top:0">Sector radar - 30d vs 90d</div><div class="chart-h"><canvas id="radarMain"></canvas></div></div><div class="card"><div class="section-title" style="margin-top:0">30-day momentum</div>'+momentumHtml+'</div></div><div class="grid-2" style="margin-bottom:16px"><div class="card"><div class="section-title" style="margin-top:0">Top investors</div>'+invHtml+'</div><div class="card"><div class="section-title" style="margin-top:0">High-growth watchlist</div>'+hgHtml+'</div></div><div class="card"><div class="section-title" style="margin-top:0">Recent exits</div>'+exitsHtml+'</div>';
  if(spaces.length){
    const ctx=document.getElementById('radarMain');
    if(ctx) new Chart(ctx,{type:'radar',data:{labels:spaces.map(function(s){return s.length>20?s.slice(0,18)+'...':s;}),datasets:[{label:'30 days',data:spaces.map(function(s){return nv30[s]||0;}),borderColor:'#1a1a1a',backgroundColor:'rgba(26,26,26,0.1)',pointRadius:3},{label:'90d div3',data:spaces.map(function(s){return (nv90[s]||0)/3;}),borderColor:'#ccc',backgroundColor:'rgba(200,200,200,0.1)',pointRadius:3,borderDash:[4,4]}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'bottom',labels:{font:{size:11}}}},scales:{r:{ticks:{stepSize:1,font:{size:10}},pointLabels:{font:{size:10}}}}}});
  }
}
function renderBriefings(){
  document.getElementById('tab-briefings').innerHTML='<input class="search-bar" placeholder="Search briefings..." oninput="filterBriefings(this.value)"><div id="blist"></div>';
  let html='';
  DB.briefings.slice().reverse().forEach(function(e){
    html+='<div class="card" id="'+e.date+'"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px"><div style="font-size:16px;font-weight:600">'+(e.date_display||e.date)+'</div><a href="#'+e.date+'" style="font-size:12px;color:#aaa;text-decoration:none">#</a></div><div style="margin-bottom:10px">';
    (e.companies||[]).forEach(function(c){html+='<span class="tag">'+c.name+'</span>';});
    html+='</div><div class="briefing-text">'+fmt(e.full_briefing)+'</div></div>';
  });
  document.getElementById('blist').innerHTML=html;
}
function filterBriefings(q){
  q=q.toLowerCase();
  document.querySelectorAll('#blist .card').forEach(function(el){el.style.display=el.textContent.toLowerCase().includes(q)?'':'none';});
}
let activeV=null;
function renderCompanies(){
  const cos=Object.entries(DB.companies);
  const verticals=[...new Set(cos.map(function(x){return x[1].industry_vertical;}).filter(Boolean))].sort();
  let pillsHtml='<button class="pill active" onclick="filterV(null,this)">All</button>';
  verticals.forEach(function(v){pillsHtml+='<button class="pill" onclick="filterV(this.dataset.v,this)" data-v="'+v+'">'+v+'</button>';});
  document.getElementById('tab-companies').innerHTML='<input class="search-bar" placeholder="Search companies..." oninput="filterCos(this.value)" id="cosearch"><div class="filter-bar">'+pillsHtml+'</div><div id="coslist"></div>';
  renderCoslist(cos);
}
function filterV(v,btn){
  activeV=v;
  document.querySelectorAll('.pill').forEach(function(p){p.classList.remove('active');});
  btn.classList.add('active');
  filterCos(document.getElementById('cosearch').value);
}
function filterCos(q){
  q=(q||'').toLowerCase();
  const cos=Object.entries(DB.companies).filter(function(x){
    const n=x[0],c=x[1];
    const mq=!q||n.toLowerCase().includes(q)||(c.what_they_do||'').toLowerCase().includes(q);
    const mv=!activeV||c.industry_vertical===activeV;
    return mq&&mv;
  });
  renderCoslist(cos);
}
function renderCoslist(cos){
  const el=document.getElementById('coslist');
  if(!el)return;
  let html='';
  cos.sort(function(a,b){return (b[1].last_updated||'').localeCompare(a[1].last_updated||'');}).forEach(function(x){
    const n=x[0],c=x[1];
    const rounds=c.funding_rounds||[];
    const total=rounds.reduce(function(s,r){return s+(r.amount_m||0);},0);
    const last=rounds.length?rounds[rounds.length-1]:null;
    let roundsHtml='';
    if(rounds.length){
      rounds.forEach(function(r){
        roundsHtml+='<div class="round"><span class="round-stage">'+(r.stage||'')+'</span><div><div style="font-weight:500">'+(r.amount_m?'$'+r.amount_m+'M':'')+(r.valuation_b?' - $'+r.valuation_b+'B val':'')+'</div><div style="font-size:12px;color:#666">'+((r.investors||[]).join(', ')||'-')+'</div></div><div style="font-size:11px;color:#aaa;margin-left:auto;white-space:nowrap">'+(r.date||'')+'</div></div>';
      });
    } else {
      roundsHtml='<div style="color:#999;font-size:13px">No rounds tracked</div>';
    }
    html+='<div class="company-card" onclick="this.classList.toggle(\'open\')"><div style="display:flex;justify-content:space-between;align-items:flex-start"><div><div style="font-weight:500;font-size:15px;margin-bottom:3px">'+n+(c.high_growth?' <span class="tag hg" style="font-size:10px">HIGH GROWTH</span>':'')+(c.status==='public'?' <span class="tag t" style="font-size:10px">PUBLIC</span>':'')+'</div><div style="font-size:13px;color:#666;margin-bottom:6px">'+(c.what_they_do||'')+'</div><div>'+(c.industry_vertical?'<span class="tag v">'+c.industry_vertical+'</span>':'')+(c.tech_layer?'<span class="tag t">'+c.tech_layer+'</span>':'')+(c.location?'<span class="tag">'+c.location+'</span>':'')+((c.founder_backgrounds||[]).map(function(b){return '<span class="tag f">'+b+'</span>';}).join(''))+'</div></div><div style="text-align:right;flex-shrink:0;margin-left:12px">'+(total>0?'<div style="font-weight:500">$'+(total>=1000?(total/1000).toFixed(1)+'B':total+'M')+'</div>':'')+(last?'<div style="font-size:11px;color:#aaa">'+(last.stage||'')+' - '+(last.date||'')+'</div>':'')+'</div></div><div class="detail"><div class="section-title" style="margin-top:0">Funding history</div>'+roundsHtml+(c.high_growth&&c.high_growth_reasoning?'<div style="margin-top:12px;padding:10px;background:#fffbeb;border-radius:6px;font-size:12px;color:#92400e"><strong>High-growth signal:</strong> '+c.high_growth_reasoning+'</div>':'')+'</div></div>';
  });
  el.innerHTML=html;
}
function renderDealflow(){
  const cos=Object.entries(DB.companies);
  const stageOrder=['Pre-Seed','Seed','Series A','Series B','Series C','Series D','Growth','Unknown'];
  const byStage={};
  cos.filter(function(x){return x[1].status!=='public';}).forEach(function(x){
    const n=x[0],c=x[1];
    (c.funding_rounds||[]).forEach(function(r){
      const s=r.stage||'Unknown';
      if(!byStage[s])byStage[s]=[];
      byStage[s].push({n:n,c:c,r:r});
    });
  });
  let privateHtml='';
  stageOrder.forEach(function(stage){
    if(!byStage[stage]||!byStage[stage].length)return;
    const sorted=byStage[stage].sort(function(a,b){return (b.r.date||'').localeCompare(a.r.date||'');});
    privateHtml+='<div class="section-title">'+stage+'</div>';
    sorted.forEach(function(item){
      const n=item.n,c=item.c,r=item.r;
      privateHtml+='<div class="card-sm" style="margin-bottom:8px"><div style="display:flex;justify-content:space-between;align-items:flex-start"><div><div style="font-weight:500">'+n+(c.high_growth?' <span class="tag hg" style="font-size:10px">HG</span>':'')+'</div><div style="font-size:12px;color:#666;margin:2px 0">'+(c.what_they_do||'')+'</div><div style="font-size:12px;color:#555">Investors: '+((r.investors||[]).join(', ')||'-')+'</div><div style="margin-top:4px">'+(c.industry_vertical?'<span class="tag v">'+c.industry_vertical+'</span>':'')+(c.tech_layer?'<span class="tag t">'+c.tech_layer+'</span>':'')+'</div></div><div style="text-align:right;flex-shrink:0;margin-left:12px">'+(r.amount_m?'<div style="font-weight:600">$'+r.amount_m+'M</div>':'')+(r.valuation_b?'<div style="font-size:12px;color:#666">$'+r.valuation_b+'B val</div>':'')+'<div style="font-size:11px;color:#aaa">'+(r.date||'')+'</div></div></div></div>';
    });
  });
  const publicCos=cos.filter(function(x){return x[1].status==='public';});
  let publicHtml='';
  if(publicCos.length){
    publicHtml='<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse"><thead><tr style="border-bottom:2px solid #e5e5e5"><th style="text-align:left;padding:8px 12px;font-size:11px;color:#999;text-transform:uppercase">Company</th><th style="text-align:left;padding:8px 12px;font-size:11px;color:#999;text-transform:uppercase">Ticker</th><th style="text-align:left;padding:8px 12px;font-size:11px;color:#999;text-transform:uppercase">Price</th><th style="text-align:left;padding:8px 12px;font-size:11px;color:#999;text-transform:uppercase">Today</th><th style="text-align:left;padding:8px 12px;font-size:11px;color:#999;text-transform:uppercase">Since IPO</th><th style="text-align:left;padding:8px 12px;font-size:11px;color:#999;text-transform:uppercase">IPO Date</th></tr></thead><tbody>';
    publicCos.forEach(function(x){
      const n=x[0],c=x[1];
      publicHtml+='<tr style="border-bottom:1px solid #f5f5f5"><td style="padding:10px 12px;font-weight:500">'+n+'</td><td style="padding:10px 12px;font-size:12px;color:#666">'+(c.ticker||'-')+'</td><td style="padding:10px 12px">'+(c.current_price?'$'+c.current_price:'-')+'</td><td style="padding:10px 12px">'+pct(c.price_change_today_pct)+'</td><td style="padding:10px 12px">'+pct(c.price_change_since_ipo_pct)+'</td><td style="padding:10px 12px;font-size:12px;color:#666">'+(c.ipo_date||'-')+'</td></tr>';
    });
    publicHtml+='</tbody></table></div>';
  } else {
    publicHtml='<div style="color:#999">No public companies tracked yet.</div>';
  }
  document.getElementById('tab-dealflow').innerHTML='<div class="section-title" style="margin-top:0">Private - all funding rounds</div>'+privateHtml+'<div class="section-title" style="margin-top:32px">Gone public</div>'+publicHtml;
}
function renderVelocity(){
  const nv30=DB.metrics.narrative_velocity&&DB.metrics.narrative_velocity['30_day']||{};
  const nv90=DB.metrics.narrative_velocity&&DB.metrics.narrative_velocity['90_day']||{};
  const spaces=[...new Set([...Object.keys(nv30),...Object.keys(nv90)])].slice(0,10);
  const topSpaces=Object.entries(nv90).sort(function(a,b){return b[1]-a[1];}).slice(0,6).map(function(x){return x[0];});
  document.getElementById('tab-velocity').innerHTML='<div class="grid-2" style="margin-bottom:16px"><div class="card"><div class="section-title" style="margin-top:0">Sector radar - 30d vs 90d</div><div class="chart-h-lg"><canvas id="radarFull"></canvas></div></div><div class="card"><div class="section-title" style="margin-top:0">Bubble - size=total mentions, position=acceleration</div><div class="chart-h-lg"><canvas id="bubbleMain"></canvas></div></div></div><div class="card"><div class="section-title" style="margin-top:0">Activity stream - top problem spaces over time</div><div class="chart-h-lg"><canvas id="riverMain"></canvas></div></div>';
  if(spaces.length){
    new Chart(document.getElementById('radarFull'),{type:'radar',data:{labels:spaces.map(function(s){return s.length>22?s.slice(0,20)+'...':s;}),datasets:[{label:'30 days',data:spaces.map(function(s){return nv30[s]||0;}),borderColor:'#1a1a1a',backgroundColor:'rgba(26,26,26,0.15)',pointRadius:4},{label:'90d normalized',data:spaces.map(function(s){return (nv90[s]||0)/3;}),borderColor:'#999',backgroundColor:'rgba(150,150,150,0.1)',pointRadius:4,borderDash:[5,5]}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'bottom'}},scales:{r:{ticks:{stepSize:1},pointLabels:{font:{size:11}}}}}});
  }
  const bData=Object.entries(nv30).map(function(x){
    const sp=x[0],c30=x[1];
    const c90=nv90[sp]||0,exp=c90/3||0.5,acc=(c30/exp)-1;
    return {x:c30,y:acc,r:Math.min(Math.sqrt(c90)*4+4,30),label:sp};
  }).filter(function(d){return d.x>0;});
  if(bData.length){
    new Chart(document.getElementById('bubbleMain'),{type:'bubble',data:{datasets:[{label:'Problem spaces',data:bData,backgroundColor:'rgba(26,26,26,0.6)',borderColor:'#1a1a1a'}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{callbacks:{label:function(ctx){return ctx.raw.label+': '+ctx.raw.x+' mentions, '+(ctx.raw.y*100).toFixed(0)+'% accel';}}}},scales:{x:{title:{display:true,text:'30-day mentions'}},y:{title:{display:true,text:'Acceleration vs baseline'},ticks:{callback:function(v){return (v*100).toFixed(0)+'%';}}}}}});
  }
  const dateMap={};
  DB.briefings.forEach(function(e){
    dateMap[e.date]=dateMap[e.date]||{};
    (e.problem_spaces||[]).forEach(function(ps){if(topSpaces.includes(ps))dateMap[e.date][ps]=(dateMap[e.date][ps]||0)+1;});
  });
  const dates=Object.keys(dateMap).sort().slice(-60);
  const colors=['#1a1a1a','#555','#888','#aaa','#bbb','#ddd'];
  if(dates.length&&topSpaces.length){
    new Chart(document.getElementById('riverMain'),{type:'line',data:{labels:dates,datasets:topSpaces.map(function(sp,i){
      const rgb=i===0?'26,26,26':i===1?'85,85,85':i===2?'136,136,136':'170,170,170';
      return {label:sp,data:dates.map(function(d){return dateMap[d]&&dateMap[d][sp]||0;}),fill:true,borderColor:colors[i],backgroundColor:'rgba('+rgb+',0.25)',tension:0.4,pointRadius:0};
    })},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'bottom',labels:{font:{size:11}}}},scales:{x:{ticks:{maxTicksLimit:10,font:{size:10}}},y:{stacked:false}}}});
  }
}
function renderInvestors(){
  const invDeals=DB.metrics.investor_activity&&DB.metrics.investor_activity.by_deals||{};
  const coInvest=DB.metrics.investor_activity&&DB.metrics.investor_activity.co_investments||[];
  const entries=Object.entries(invDeals).slice(0,20);
  const maxD=entries[0]?entries[0][1]:1;
  let invHtml='';
  entries.forEach(function(x){
    const inv=x[0],cnt=x[1];
    invHtml+='<div class="investor-row"><div style="flex:1"><div style="font-weight:500">'+inv+'</div><div style="display:flex;align-items:center;gap:8px;margin-top:4px"><div class="bar-track" style="flex:1"><div class="bar-fill" style="width:'+Math.round(cnt/maxD*100)+'%"></div></div></div></div><div style="font-weight:500;margin-left:12px">'+cnt+'</div></div>';
  });
  document.getElementById('tab-investors').innerHTML='<div class="grid-2"><div class="card"><div class="section-title" style="margin-top:0">Most active investors</div>'+invHtml+'</div><div class="card"><div class="section-title" style="margin-top:0">Co-investment network</div><canvas id="netCanvas"></canvas></div></div>';
  const canvas=document.getElementById('netCanvas');
  const ctx=canvas.getContext('2d');
  canvas.width=canvas.offsetWidth||400;canvas.height=380;
  const W=canvas.width,H=canvas.height;
  const nodeSet=new Set();
  coInvest.slice(0,40).forEach(function(d){nodeSet.add(d.pair[0]);nodeSet.add(d.pair[1]);});
  const nodes=[...nodeSet].slice(0,25).map(function(id,i){
    const a=i/25*Math.PI*2;
    return {id:id,x:W/2+Math.cos(a)*(W*0.35),y:H/2+Math.sin(a)*(H*0.38),vx:0,vy:0,r:6};
  });
  const nIdx={};
  nodes.forEach(function(n){nIdx[n.id]=n;});
  const edges=coInvest.slice(0,40).filter(function(d){return nIdx[d.pair[0]]&&nIdx[d.pair[1]];});
  if(!nodes.length){ctx.fillStyle='#999';ctx.font='13px Arial';ctx.fillText('Not enough data yet.',W/2-80,H/2);return;}
  let tick=0;
  function draw(){
    ctx.clearRect(0,0,W,H);
    edges.forEach(function(e){
      const a=nIdx[e.pair[0]],b=nIdx[e.pair[1]];
      ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);
      ctx.strokeStyle='rgba(0,0,0,'+Math.min(e.count/6,0.35)+')';ctx.lineWidth=Math.min(e.count,3);ctx.stroke();
    });
    nodes.forEach(function(n){
      ctx.beginPath();ctx.arc(n.x,n.y,n.r,0,Math.PI*2);ctx.fillStyle='#1a1a1a';ctx.fill();
      ctx.font='10px Arial';ctx.fillStyle='#444';ctx.textAlign='center';
      ctx.fillText(n.id.length>14?n.id.slice(0,12)+'...':n.id,n.x,n.y+n.r+11);
    });
  }
  function sim(){
    if(tick++>250){draw();return;}
    nodes.forEach(function(n){
      nodes.forEach(function(m){
        if(n===m)return;
        const dx=n.x-m.x,dy=n.y-m.y,d=Math.sqrt(dx*dx+dy*dy)||1,f=250/(d*d);
        n.vx+=dx*f;n.vy+=dy*f;
      });
    });
    edges.forEach(function(e){
      const a=nIdx[e.pair[0]],b=nIdx[e.pair[1]],dx=b.x-a.x,dy=b.y-a.y,d=Math.sqrt(dx*dx+dy*dy)||1,f=(d-80)*0.05;
      a.vx+=dx*f;a.vy+=dy*f;b.vx-=dx*f;b.vy-=dy*f;
    });
    nodes.forEach(function(n){
      n.vx=(n.vx+(W/2-n.x)*0.01)*0.85;n.vy=(n.vy+(H/2-n.y)*0.01)*0.85;
      n.x=Math.max(20,Math.min(W-20,n.x+n.vx));n.y=Math.max(20,Math.min(H-20,n.y+n.vy));
    });
    draw();requestAnimationFrame(sim);
  }
  sim();
}
function renderExits(){
  const exits=DB.metrics.exits||[];
  const bySector={};
  exits.forEach(function(e){const s=e.industry_vertical||'Other';if(!bySector[s])bySector[s]=[];bySector[s].push(e);});
  let html='<div class="card"><div class="section-title" style="margin-top:0">All exits by sector</div>';
  const sectors=Object.keys(bySector);
  if(sectors.length){
    sectors.forEach(function(sector){
      html+='<div class="section-title">'+sector+'</div>';
      bySector[sector].forEach(function(e){
        html+='<div class="exit-row"><span class="'+(e.exit_type==='IPO'?'exit-ipo':'exit-acq')+'">'+e.exit_type+'</span><div><div style="font-weight:500">'+e.company+(e.ticker?' ('+e.ticker+')':'')+'</div><div style="font-size:12px;color:#666">'+(e.tech_layer||'')+(e.date?' - '+e.date:'')+'</div></div></div>';
      });
    });
  } else {
    html+='<div style="color:#999">No exits tracked yet.</div>';
  }
  html+='</div>';
  document.getElementById('tab-exits').innerHTML=html;
}
function renderCategories(){
  const cats=DB.metrics.categories||{};
  let html='<div class="cat-grid">';
  Object.entries(cats).forEach(function(x){
    const v=x[0],d=x[1];
    html+='<div class="cat-card" onclick="showCat(\''+v.replace(/'/g,"\\'")+'\')">';
    html+='<div style="font-weight:500;font-size:14px;margin-bottom:4px">'+v+'</div>';
    html+='<div style="font-size:12px;color:#999;margin-bottom:8px">'+((d.companies||[]).length)+' companies</div>';
    html+='<div>'+Object.keys(d.tech_layers||{}).map(function(tl){return '<span class="tag t" style="font-size:10px">'+tl+'</span>';}).join('')+'</div>';
    html+='</div>';
  });
  html+='</div><div id="catdetail"></div>';
  document.getElementById('tab-categories').innerHTML=html;
}
function showCat(vertical){
  const cats=DB.metrics.categories||{};
  const data=cats[vertical]||{};
  const companies=data.companies||[];
  const techLayers=data.tech_layers||{};
  const nv30=DB.metrics.narrative_velocity&&DB.metrics.narrative_velocity['30_day']||{};
  const vertInv={};
  companies.forEach(function(n){
    const c=DB.companies[n]||{};
    (c.funding_rounds||[]).forEach(function(r){
      (r.investors||[]).forEach(function(inv){vertInv[inv]=(vertInv[inv]||0)+1;});
    });
  });
  const topInv=Object.entries(vertInv).sort(function(a,b){return b[1]-a[1];}).slice(0,5);
  const relatedSpaces=Object.entries(nv30).filter(function(x){return x[0].toLowerCase().includes(vertical.toLowerCase().split(' ')[0]);}).slice(0,3);
  let html='<div class="card" style="margin-top:16px"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px"><div style="font-size:18px;font-weight:600">'+vertical+'</div><button onclick="document.getElementById(\'catdetail\').innerHTML=\'\'" style="font-size:12px;color:#999;background:none;border:none;cursor:pointer">close</button></div>';
  html+='<div class="grid-3" style="margin-bottom:20px"><div class="card-sm"><div class="metric-val">'+companies.length+'</div><div class="metric-label">Companies</div></div><div class="card-sm"><div class="metric-val">'+Object.keys(techLayers).length+'</div><div class="metric-label">Tech Layers</div></div><div class="card-sm"><div class="metric-val">'+topInv.length+'</div><div class="metric-label">Active Investors</div></div></div>';
  if(topInv.length){
    html+='<div class="section-title">Key investors</div><div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:16px">';
    topInv.forEach(function(x){html+='<span class="tag">'+x[0]+' ('+x[1]+')</span>';});
    html+='</div>';
  }
  if(relatedSpaces.length){
    html+='<div class="section-title">Related problem spaces (30-day)</div><div style="margin-bottom:16px">';
    relatedSpaces.forEach(function(x){html+='<span class="tag" style="margin:3px">'+x[0]+' ('+x[1]+')</span>';});
    html+='</div>';
  }
  html+='<div class="section-title">Companies by tech layer</div>';
  Object.entries(techLayers).forEach(function(x){
    const tl=x[0],cos=x[1];
    html+='<div style="margin-bottom:16px"><span class="tag t" style="margin-bottom:8px;display:inline-block">'+tl+'</span><div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:6px">';
    cos.forEach(function(n){
      const c=DB.companies[n]||{};
      html+='<div class="card-sm" style="min-width:200px;flex:1"><div style="font-weight:500;font-size:13px">'+n+(c.high_growth?' <span class="tag hg" style="font-size:10px">HG</span>':'')+'</div><div style="font-size:11px;color:#666;margin-top:2px">'+(c.what_they_do||'')+'</div><div style="font-size:11px;color:#999;margin-top:2px">'+(c.location||'')+(c.founded?' - est. '+c.founded:'')+'</div></div>';
    });
    html+='</div></div>';
  });
  html+='</div>';
  document.getElementById('catdetail').innerHTML=html;
  document.getElementById('catdetail').scrollIntoView({behavior:'smooth'});
}
function initAll(){
  renderDashboard();
  renderBriefings();
  renderCompanies();
  renderDealflow();
  renderVelocity();
  renderInvestors();
  renderExits();
  renderCategories();
  if(window.location.hash){
    const el=document.querySelector(window.location.hash);
    if(el)setTimeout(function(){el.scrollIntoView({behavior:'smooth'});},300);
  }
}
loadData();
</script>
</body></html>"""

    html = html.replace("__SITE__", SITE)

    with open(WEBSITE_F, "w") as f:
        f.write(html)


def build_email(news, substack, connections, flags, date_str):
    def fmt(text):
        if not text:
            return ''
        text = re.sub(r'^## (.+)$', r'<h3 style="font-size:11px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:#999;margin:40px 0 12px;padding-bottom:6px;border-bottom:1px solid #eee;">\1</h3>', text, flags=re.MULTILINE)
        text = re.sub(r"^\s*(PROBLEM THEY ARE SOLVING|DIRECT COMPETITORS|ADJACENT PROBLEMS|WHAT HAPPENS IF THEY WIN)\s*$", r'<div style="font-size:10px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:#aaa;margin:16px 0 6px;padding-left:12px;border-left:2px solid #eee;">\1</div>', text, flags=re.MULTILINE)
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
        text = re.sub(r'^[-*] (.+)$', r'<div style="display:flex;gap:10px;margin-bottom:8px;padding-left:12px;"><span style="color:#ccc;flex-shrink:0;">-</span><span>\1</span></div>', text, flags=re.MULTILINE)
        text = re.sub(r'(https?://[^\s<)\]]+)', r'<a href="\1" style="color:#0066cc;text-decoration:none;word-break:break-all;">\1</a>', text)
        text = re.sub(r'\n\n+', '</p><p>', text)
        return f'<p>{text.replace(chr(10), "<br>")}</p>'

    flags_html = ""
    if flags:
        items = "".join(f'<div style="font-size:13px;margin-bottom:6px;color:#555">-> {f} &nbsp;<a href="{SITE}" style="color:#0066cc;font-size:12px">view dashboard</a></div>' for f in flags)
        flags_html = f'<div style="margin-bottom:24px;padding:14px 18px;background:#f9f9f7;border-radius:8px;border:1px solid #eee"><div style="font-size:10px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:#999;margin-bottom:10px">Today\'s signals</div>{items}</div>'

    conn_html = ""
    if connections:
        parsed = parse_connections(connections)
        if parsed:
            items = "".join(f'<div style="border-left:3px solid #1a1a1a;padding:12px 16px;margin-bottom:12px;background:#fafafa"><div style="font-size:12px;font-weight:600;margin-bottom:8px">{c.get("CONNECTION","")}</div><div style="font-size:13px;color:#555;margin-bottom:4px"><strong>Then:</strong> {c.get("THEN","")}</div><div style="font-size:13px;color:#555;margin-bottom:4px"><strong>Now:</strong> {c.get("NOW","")}</div><div style="font-size:13px;color:#555;margin-bottom:8px"><strong>Why it matters:</strong> {c.get("SIGNIFICANCE","")}</div><a href="{SITE}/#{c.get("PAST_DATE","")}" style="font-size:12px;color:#0066cc;text-decoration:none">View original briefing</a></div>' for c in parsed)
            conn_html = f'<div style="margin-top:48px;padding-top:24px;border-top:2px solid #1a1a1a"><div style="font-size:11px;font-weight:600;letter-spacing:.15em;text-transform:uppercase;color:#999;margin-bottom:16px">Connections to the past</div>{items}</div>'

    sub_html = ""
    if substack:
        sub_html = f'<div style="margin-top:48px;padding-top:24px;border-top:2px solid #1a1a1a"><div style="font-size:11px;font-weight:600;letter-spacing:.15em;text-transform:uppercase;color:#999;margin-bottom:16px">Substack this week</div><div style="line-height:1.75">{fmt(substack)}</div></div>'

    return f"""<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;max-width:700px;margin:0 auto;padding:32px 24px;color:#1a1a1a;font-size:15px;line-height:1.8">
  <div style="border-bottom:2px solid #1a1a1a;margin-bottom:24px;padding-bottom:14px">
    <div style="font-size:11px;font-weight:600;letter-spacing:.15em;text-transform:uppercase;color:#999;margin-bottom:4px">Morning Briefing</div>
    <div style="font-size:22px;font-weight:600">{date_str}</div>
    <a href="{SITE}" style="font-size:12px;color:#0066cc;text-decoration:none">View archive and dashboard</a>
  </div>
  {flags_html}
  <div>{fmt(news)}</div>
  {conn_html}
  {sub_html}
  <div style="border-top:1px solid #eee;margin-top:48px;padding-top:12px;font-size:11px;color:#bbb">
    Generated daily - <a href="{SITE}/#{TODAY_ISO}" style="color:#bbb">View today in archive</a>
  </div></body></html>"""


def send_email(subject, html):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = RECIPIENT_EMAIL
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        s.sendmail(GMAIL_ADDRESS, RECIPIENT_EMAIL, msg.as_string())


def main():
    print(f"Running - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    briefings = load(BRIEFINGS_F, [])
    companies = load(COMPANIES_F, {})
    print("  Web search briefing...")
    news = sonnet_search(news_prompt())
    print("  Substacks...")
    posts = fetch_substacks()
    sub = haiku(substack_prompt(posts), 1000) if posts else None
    print(f"    {len(posts)} posts")
    print("  Extracting data...")
    structured = extract(news)
    print("  Updating companies...")
    hg_new = []
    companies = update_companies(companies, structured.get("companies",[]), hg_new)
    companies = fetch_prices(companies)
    save(COMPANIES_F, companies)
    print("  Connections scan...")
    connections = find_connections(structured, briefings)
    print("  Computing metrics...")
    metrics = compute_metrics(briefings, companies)
    metrics["total_briefings"] = len(briefings) + 1
    metrics["high_growth_new_today"] = hg_new
    save(METRICS_F, metrics)
    flags = build_flags(metrics)
    briefings.append({
        "date": TODAY_ISO, "date_display": TODAY,
        "companies": structured.get("companies",[]),
        "problem_spaces": structured.get("problem_spaces",[]),
        "predictions": structured.get("predictions",[]),
        "full_briefing": news,
    })
    save(BRIEFINGS_F, briefings)
    print("  Generating website...")
    generate_website(briefings, companies, metrics)
    print("  Sending email...")
    html = build_email(news, sub, connections, flags, datetime.now().strftime("%A, %B %d, %Y"))
    send_email(f"Morning Briefing - {datetime.now().strftime('%A, %B %d, %Y')}", html)
    print("  Done.")


if __name__ == "__main__":
    main()
