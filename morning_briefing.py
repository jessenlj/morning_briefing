#!/usr/bin/env python3
import os, re, json, smtplib, calendar, feedparser, time, xml.etree.ElementTree as ET
import requests
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

TODAY        = datetime.now().strftime("%A, %B %d, %Y")
TODAY_ISO    = datetime.now().strftime("%Y-%m-%d")
SITE         = f"https://{GITHUB_USERNAME}.github.io/{REPO_NAME}"
DATA_DIR     = "docs/data"
BRIEFINGS_F  = f"{DATA_DIR}/briefings.json"
SEC_F            = f"{DATA_DIR}/sec_filings.json"
SEC_UNVERIFIED_F = f"{DATA_DIR}/sec_unverified.json"
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

For EVERY company mentioned, embed this directly underneath:

  WHAT THEY BUILD
  One sentence: what it is, who uses it, what problem it solves.

  COMPETITION
  Key competitors and how this company differs.

Only use URLs from actual search results.

## TOP STORIES
[Each story + inline analysis. Source URL at end.]

## VC & FUNDING
[Each deal: company, what they do, founded year, ALL investors, amount, why it matters, URL. Inline analysis.]

## AI DEVELOPMENTS
[What was announced, company context, reactions, URL. Inline analysis.]

## KEY TAKEAWAY
[Single most important thing today.]

No filler."""

def substack_prompt(posts):
    raw = "".join(f"\nAUTHOR: {p['author']}\nDATE: {p['date']}\nTITLE: {p['title']}\nSUMMARY: {p['summary']}\nURL: {p['link']}\n---" for p in posts)
    return f"""Today is {TODAY}. Recent posts from top VC/startup/AI Substack newsletters:
{raw}
Write SUBSTACK THIS WEEK: pick 3-4 most forward-looking pieces, one sentence on what the author argues, one on why it's worth reading. Flag contrarian predictions. End with one sentence on any shared theme. Smart friend tone, not summary bot."""


def sonnet_search(prompt, max_tokens=6000):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    messages = [{"role": "user", "content": prompt}]
    result = ""
    for _ in range(10):
        r = client.messages.create(model="claude-sonnet-4-6", max_tokens=max_tokens,
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
    for attempt in range(3):
        try:
            return client.messages.create(model="claude-sonnet-4-6", max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}]).content[0].text
        except Exception as e:
            if "rate_limit" in str(e).lower() or "429" in str(e):
                wait = 60 * (attempt + 1)
                print(f"    Rate limited. Waiting {wait}s...")
                time.sleep(wait)
            else:
                raise
    return ""




# Industries to skip outright — clearly not tech startups
NOISE_INDUSTRIES = {
    "Pooled Investment Fund", "Other Real Estate", "Residential", "Commercial",
    "Investing", "Other Banking and Financial Services", "Commercial Banking",
    "Investment Banking", "REITS and Finance", "Restaurants", "Agriculture",
}

# Keywords that suggest a tech/software/AI company
TECH_KEYWORDS = frozenset([
    # Software & cloud
    "software", "platform", "saas", "api", "cloud", "app", "application",
    "open source", "dashboard", "database", "integration", "pipeline",
    "devops", "developer", "sdk", "plugin", "microservice", "serverless",
    # AI & data
    "ai", "artificial intelligence", "machine learning", "deep learning",
    "data", "analytics", "algorithm", "automation", "neural", "inference",
    "model", "compute", "intelligence", "llm", "generative", "nlp",
    "computer vision", "prediction", "training", "dataset",
    # Infrastructure & hardware
    "infrastructure", "hardware", "firmware", "embedded", "semiconductor",
    "sensor", "chip", "processor", "network", "protocol", "satellite",
    "drone", "robotics", "photonic", "quantum", "battery", "energy storage",
    # Security & identity
    "cybersecurity", "security", "encryption", "authentication", "identity",
    "compliance", "privacy", "firewall", "threat",
    # Industry verticals
    "fintech", "healthtech", "biotech", "edtech", "proptech", "insurtech",
    "legaltech", "agtech", "climatetech", "medtech",
    # General signals
    "tech", "technology", "digital", "mobile", "startup", "workflow",
    "marketplace", "network", "monitoring", "simulation", "optimization",
    "visualization", "interface", "engine", "system", "solution",
])


SEC_CATEGORIES = [
    ("AI / ML",           ["ai", "machine learning", "deep learning", "neural", "llm", "generative", "nlp", "computer vision", "artificial intelligence", "large language"]),
    ("Biotech / Health",  ["bio", "health", "therapeut", "pharma", "medic", "clinical", "drug", "genomic", "protein", "biosignal", "diagnostic", "brain", "neuro", "cell"]),
    ("Fintech",           ["fintech", "payment", "banking", "finance", "financial", "lending", "credit", "trading", "wealth", "insurance", "invest"]),
    ("Hardware",          ["hardware", "semiconductor", "chip", "photon", "sensor", "drone", "robotic", "quantum", "satellite", "firmware", "embedded", "battery", "storage"]),
    ("Energy / Climate",  ["energy", "climate", "solar", "wind", "carbon", "emission", "clean", "sustainable", "grid", "nuclear", "fusion", "ferment"]),
    ("Cybersecurity",     ["security", "cyber", "encryption", "firewall", "threat", "authentication", "privacy", "identity", "compliance"]),
]

def categorize_sec_company(name, industry, what_they_do):
    """Assign a display category based on keywords in name + industry + description."""
    text = (name + " " + industry + " " + what_they_do).lower()
    for cat, keywords in SEC_CATEGORIES:
        if any(kw in text for kw in keywords):
            return cat
    return "Other Tech"


def clearbit_lookup(company_name):
    """Look up a company via Clearbit Autocomplete API (free, no key required).
    Returns the domain string (e.g. 'acme.com') or None if not found."""
    try:
        r = requests.get(
            "https://autocomplete.clearbit.com/v1/companies/suggest",
            params={"query": company_name},
            headers={"User-Agent": f"MorningBriefing/1.0 {RECIPIENT_EMAIL}"},
            timeout=10)
        if r.status_code == 200:
            results = r.json()
            if results:
                return results[0].get("domain", "")
    except Exception:
        pass
    return None


def safe_scrape(url, max_bytes=50_000):
    """Fetch a URL safely — hard timeout, size cap, HTML only."""
    try:
        from bs4 import BeautifulSoup
        r = requests.get(url,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"},
            timeout=10, stream=True, verify=True)
        if "text/html" not in r.headers.get("content-type", ""):
            return ""
        chunks, size = [], 0
        for chunk in r.iter_content(chunk_size=4096):
            chunks.append(chunk)
            size += len(chunk)
            if size >= max_bytes:
                break
        soup = BeautifulSoup(b"".join(chunks).decode("utf-8", errors="ignore"), "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        meta     = soup.find("meta", attrs={"name": "description"})
        meta_desc = meta.get("content", "") if meta else ""
        body     = re.sub(r"\s+", " ", soup.get_text(separator=" ", strip=True))
        return (meta_desc + " " + body[:2000]).strip()
    except Exception:
        return ""


def is_tech(text, name=""):
    """Return True if text + name contain at least 1 tech keyword."""
    combined = (text + " " + name).lower()
    return any(kw in combined for kw in TECH_KEYWORDS)


def enrich_sec_company(entity, city, state, phone, cik_str, industry=""):
    """Find website via Clearbit, scrape it, confirm it's a tech company.
    Returns {'website': ..., 'what_they_do': ...} or None if not tech."""
    EXPLICIT_TECH = {"Computers", "Telecommunications", "Other Technology"}
    sec_hdrs = {"User-Agent": f"MorningBriefing/1.0 {RECIPIENT_EMAIL}"}
    website, page_text = "", ""

    # Step 1: Check EDGAR submissions for a registered website
    try:
        time.sleep(0.1)
        sr = requests.get(f"https://data.sec.gov/submissions/CIK{cik_str}.json",
                          headers=sec_hdrs, timeout=10)
        if sr.status_code == 200:
            website = sr.json().get("website", "") or ""
    except Exception:
        pass

    # Step 2: Clearbit Autocomplete — look up company domain by name
    # Strip common legal suffixes so Clearbit can match the brand name
    if not website:
        clean_name = re.sub(
            r',?\s*(Inc\.?|LLC\.?|L\.P\.?|Corp\.?|Co\.?|Ltd\.?|PBC|Holdings?|Group)\s*$',
            '', entity, flags=re.IGNORECASE).strip()
        domain = clearbit_lookup(clean_name) or clearbit_lookup(entity)
        if domain:
            # Sanity check: at least one word from the brand name should appear in the domain
            brand_words = [w.lower() for w in re.split(r'\s+', clean_name) if len(w) > 3]
            domain_lower = domain.lower().split(".")[0]  # just the SLD, no TLD
            # If no long-enough brand words, or none match the domain, reject
            if not brand_words or not any(w in domain_lower for w in brand_words):
                domain = None  # Clearbit matched a different company
            if domain:
                website = f"https://{domain}"

    # Step 3: Scrape the website if we found one
    if website:
        page_text = safe_scrape(website)

    # Step 4: Tech check
    if website:
        # We have a website — require 2+ tech keywords in scraped content + name
        if not is_tech(page_text, entity):
            return None
    else:
        # No web presence at all — keep if name signals tech OR SEC explicitly labeled it tech
        name_lower = entity.lower()
        name_is_tech = any(kw in name_lower for kw in TECH_KEYWORDS)
        sec_says_tech = industry in EXPLICIT_TECH
        if not name_is_tech and not sec_says_tech:
            return None
        return {"website": "", "what_they_do": "No web presence found — flag for review"}

    # Step 5: Best available description from scraped page
    description = ""
    if page_text:
        first = page_text.split(".")[0].strip()
        if 15 < len(first) < 300:
            description = first + "."
    if not description:
        description = page_text[:200].strip() if page_text else ""

    return {"website": website, "what_they_do": description}


def check_unverified(unverified):
    """Re-check unverified companies for a website. Returns (updated_unverified, newly_found)."""
    PRUNE_DAYS = 60
    cutoff = (datetime.now() - timedelta(days=PRUNE_DAYS)).strftime("%Y-%m-%d")
    newly_found = []
    still_unverified = []
    for entry in unverified:
        # Prune old entries
        if entry.get("date_added", "9999") < cutoff:
            print(f"    [Unverified] pruning old entry: {entry['company']}")
            continue
        domain = None
        clean_name = re.sub(
            r',?\s*(Inc\.?|LLC\.?|L\.P\.?|Corp\.?|Co\.?|Ltd\.?|PBC|Holdings?|Group)\s*$',
            '', entry["company"], flags=re.IGNORECASE).strip()
        domain = clearbit_lookup(clean_name) or clearbit_lookup(entry["company"])
        if domain:
            website = f"https://{domain}"
            page_text = safe_scrape(website)
            if is_tech(page_text, entry["company"]):
                description = ""
                if page_text:
                    first = page_text.split(".")[0].strip()
                    description = (first + ".") if 15 < len(first) < 300 else page_text[:200].strip()
                category = categorize_sec_company(entry["company"], entry.get("industry", ""), description)
                found = dict(entry)
                found.update({
                    "website": website,
                    "what_they_do": description,
                    "category": category,
                    "newly_found": True,
                })
                newly_found.append(found)
                print(f"    [Unverified] found: {entry['company']} -> {website}")
                continue
        still_unverified.append(entry)
    return still_unverified, newly_found


def fetch_sec_form_d(min_amount=99_000, max_enrich=50):
    """Fetch all of today's tech Form D filings from SEC EDGAR, enrich with descriptions."""
    hdrs  = {"User-Agent": f"MorningBriefing/1.0 {RECIPIENT_EMAIL}"}
    today = datetime.now().strftime("%Y-%m-%d")

    # First call to find out the total number of results
    try:
        r = requests.get(
            f"https://efts.sec.gov/LATEST/search-index?q=&forms=D"
            f"&dateRange=custom&startdt={today}&enddt={today}&from=0&size=1",
            headers=hdrs, timeout=30)
        total = r.json().get("hits", {}).get("total", {}).get("value", 0)
    except Exception as e:
        print(f"    [SEC] Search failed: {e}")
        return []

    # Page through all results in batches of 100
    hits = []
    batch = 100
    for offset in range(0, total, batch):
        try:
            time.sleep(0.2)
            r = requests.get(
                f"https://efts.sec.gov/LATEST/search-index?q=&forms=D"
                f"&dateRange=custom&startdt={today}&enddt={today}"
                f"&from={offset}&size={batch}",
                headers=hdrs, timeout=30)
            hits.extend(r.json().get("hits", {}).get("hits", []))
        except Exception as e:
            print(f"    [SEC] Page {offset} failed: {e}")
            break

    print(f"    [SEC] {len(hits)} total filings today")

    enriched_count = 0
    results = []
    unverified = []
    for hit in hits:
        src       = hit.get("_source", {})
        accession = src.get("adsh", "")
        ciks      = src.get("ciks", [])
        names     = src.get("display_names", [])
        file_date = src.get("file_date", "")
        if not accession or not ciks or not names:
            continue

        cik_str    = ciks[0]
        cik_int    = int(cik_str)
        acc_nodash = accession.replace("-", "")
        entity     = re.sub(r'\s*\(CIK\s*\d+\)\s*$', '', names[0]).strip()
        xml_url    = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/primary_doc.xml"

        try:
            time.sleep(0.15)
            xr = requests.get(xml_url, headers=hdrs, timeout=15)
            if xr.status_code != 200:
                continue
            root = ET.fromstring(xr.text)
        except Exception:
            continue

        def gtext(tag):
            el = root.find(f".//{tag}")
            return el.text.strip() if el is not None and el.text else ""

        # Skip obvious non-tech industries upfront
        industry = gtext("industryGroupType")
        if industry in NOISE_INDUSTRIES:
            continue

        try:
            amount = float(gtext("totalAmountSold") or 0)
        except ValueError:
            amount = 0
        if amount < min_amount:
            continue

        city    = gtext("city")
        state   = gtext("stateOrCountry")
        founded = gtext("yearOfInc")
        phone   = gtext("issuerPhoneNumber")

        if enriched_count >= max_enrich:
            print(f"    [SEC] Enrichment cap ({max_enrich}) reached — stopping early")
            break

        enriched = enrich_sec_company(entity, city, state, phone, cik_str, industry)
        enriched_count += 1

        if enriched is None:
            print(f"    [SEC] skip (not tech): {entity}")
            continue

        is_unverified = "flag for review" in enriched["what_they_do"]
        category = categorize_sec_company(entity, industry, enriched["what_they_do"])
        record = {
            "date_filed":   file_date,
            "company":      entity,
            "amount_m":     round(amount / 1e6, 2),
            "city":         city,
            "state":        state,
            "founded":      founded,
            "cik":          cik_str,
            "industry":     industry,
            "category":     category,
            "what_they_do": enriched["what_they_do"],
            "website":      enriched["website"],
        }
        if is_unverified:
            record["date_added"] = file_date
            unverified.append(record)
            print(f"    [SEC] ? {entity} — ${amount/1e6:.1f}M (unverified)")
        else:
            results.append(record)
            print(f"    [SEC] + {entity} — ${amount/1e6:.1f}M ({city}, {state})")

    return results, unverified


def update_sec_filings(existing, new_filings):
    """Merge new verified filings into existing list, deduplicated by CIK."""
    by_cik = {e["cik"]: e for e in existing}
    for f in new_filings:
        cik = f["cik"]
        if cik not in by_cik or f["date_filed"] >= by_cik[cik]["date_filed"]:
            by_cik[cik] = f
    return sorted(by_cik.values(), key=lambda x: x["date_filed"], reverse=True)


def update_unverified(existing, new_unverified, newly_found):
    """Merge new unverified entries; remove any that were just found."""
    found_ciks = {e["cik"] for e in newly_found}
    by_cik = {e["cik"]: e for e in existing if e["cik"] not in found_ciks}
    for f in new_unverified:
        if f["cik"] not in by_cik:
            by_cik[f["cik"]] = f
    return sorted(by_cik.values(), key=lambda x: x.get("date_added", ""), reverse=True)


def generate_website(briefings, sec_filings=None, sec_unverified=None):
    os.makedirs("docs", exist_ok=True)
    save(BRIEFINGS_F, briefings)
    save(SEC_F, sec_filings or [])
    save(SEC_UNVERIFIED_F, sec_unverified or [])

    # Build the HTML using a list to avoid Python escape issues with the JS template
    parts = []
    parts.append(r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Morning Briefing</title>
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
.card-sm{background:#fff;border:1px solid #e5e5e5;border-radius:8px;padding:12px 16px}
.tag{display:inline-block;font-size:11px;padding:2px 8px;border-radius:12px;margin:2px;background:#f0f0ee;color:#555}
.section-title{font-size:11px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:#999;margin:24px 0 12px;padding-bottom:6px;border-bottom:1px solid #eee}
.search-bar{width:100%;padding:10px 16px;border:1px solid #e5e5e5;border-radius:8px;font-size:14px;margin-bottom:16px;outline:none;background:#fff}
.search-bar:focus{border-color:#1a1a1a}
.briefing-card{background:#fff;border:1px solid #e5e5e5;border-radius:8px;padding:16px 20px;margin-bottom:10px;cursor:pointer;transition:border-color .15s}
.briefing-card:hover{border-color:#999}
.briefing-card .detail{display:none;margin-top:14px;padding-top:14px;border-top:1px solid #f0f0f0}
.briefing-card.open .detail{display:block}
.briefing-text h4{font-size:11px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:#999;margin:20px 0 8px;padding-bottom:4px;border-bottom:1px solid #eee}
.briefing-text .subh{font-size:10px;font-weight:600;text-transform:uppercase;color:#ccc;margin:12px 0 4px;padding-left:10px;border-left:2px solid #eee;letter-spacing:.08em}
.briefing-text p{margin-bottom:10px;line-height:1.75}.briefing-text a{color:#0066cc}
.briefing-text .bul{display:flex;gap:8px;margin-bottom:6px;padding-left:10px}
.briefing-text .dash{color:#ccc;flex-shrink:0}
.loading{color:#999;font-size:14px;padding:40px;text-align:center}
</style>
</head>
<body>
<div class="site-header">
  <div><h1>Morning Briefing</h1><p style="font-size:12px;color:#888;margin-top:2px">Updated daily at 8 AM MDT</p></div>
</div>
<div class="nav">
  <button class="active" onclick="tab('briefings',this)">Briefings</button>
  <button onclick="tab('sec',this)">SEC Filings</button>
  <button onclick="tab('unverified',this)">Unverified</button>
</div>
<div class="container">
  <div id="tab-briefings" class="panel active"><div class="loading">Loading briefings...</div></div>
  <div id="tab-sec" class="panel"><div class="loading">Loading SEC filings...</div></div>
  <div id="tab-unverified" class="panel"><div class="loading">Loading unverified...</div></div>
</div>
<script>
const BASE='SITE_PLACEHOLDER';
let DB={},charts={};
async function loadData(){
  const [b,s,u]=await Promise.all([
    fetch(BASE+'/data/briefings.json').then(r=>r.json()).catch(()=>[]),
    fetch(BASE+'/data/sec_filings.json').then(r=>r.json()).catch(()=>[]),
    fetch(BASE+'/data/sec_unverified.json').then(r=>r.json()).catch(()=>[]),
  ]);
  DB.briefings=b;DB.sec=s;DB.unverified=u;
  initAll();
}
function tab(id,btn){
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.nav button').forEach(b=>b.classList.remove('active'));
  document.getElementById('tab-'+id).classList.add('active');
  btn.classList.add('active');
}
function fmt(text){
  if(!text)return '';
  text=text.replace(/## (.+)/gm,'<h4>$1</h4>');
  text=text.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>');
  text=text.replace(/^\s*(WHAT THEY BUILD|COMPETITION)\s*$/gm,'<div class="subh">$1</div>');
  text=text.replace(/^[-*] (.+$)/gm,'<div class="bul"><span class="dash">-</span><span>$1</span></div>');
  text=text.replace(/(https?:\/\/[^\s<)\]]+)/g,'<a href="$1">$1</a>');
  text=text.split(String.fromCharCode(10)+String.fromCharCode(10)).join('</p><p>');
  text=text.split(String.fromCharCode(10)).join('<br>');
  return '<p>'+text+'</p>';
}
function renderBriefings(){
  document.getElementById('tab-briefings').innerHTML='<input class="search-bar" placeholder="Search briefings..." oninput="filterBriefings(this.value)"><div id="blist"></div>';
  let html='';
  DB.briefings.slice().reverse().forEach(function(e){
    html+='<div class="briefing-card" id="'+e.date+'" onclick="this.classList.toggle(&quot;open&quot;)">';
    html+='<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px"><div style="font-size:16px;font-weight:600">'+(e.date_display||e.date)+'</div><span style="font-size:12px;color:#aaa;user-select:none">&#8250;</span></div>';
    html+='<div>';
    (e.companies||[]).forEach(function(c){html+='<span class="tag">'+c.name+'</span>';});
    html+='</div>';
    html+='<div class="detail"><div class="briefing-text">'+fmt(e.full_briefing)+'</div></div>';
    html+='</div>';
  });
  document.getElementById('blist').innerHTML=html;
}
function filterBriefings(q){
  q=q.toLowerCase();
  document.querySelectorAll('#blist .briefing-card').forEach(function(el){el.style.display=el.textContent.toLowerCase().includes(q)?'':'none';});
}
function renderSEC(){
  const filings=DB.sec||[];
  // Group by date
  const byDate={};
  filings.forEach(function(f){
    const d=f.date_filed||'unknown';
    if(!byDate[d])byDate[d]=[];
    byDate[d].push(f);
  });
  const dates=Object.keys(byDate).sort().reverse();
  let html='<div id="seclist">';
  if(!dates.length){
    html+='<div style="color:#999;padding:20px">No SEC Form D filings collected yet. Will populate on next pipeline run.</div>';
  } else {
    dates.forEach(function(date,idx){
      const dayFilings=byDate[date];
      const isToday=(idx===0);
      const label=isToday?'Today\'s Funding':date;
      const totalM=dayFilings.reduce(function(s,f){return s+(f.amount_m||0);},0);
      // Group by category within the day
      const byCat={};
      dayFilings.forEach(function(f){
        const cat=f.category||'Other Tech';
        if(!byCat[cat])byCat[cat]=[];
        byCat[cat].push(f);
      });
      Object.keys(byCat).forEach(function(cat){
        byCat[cat].sort(function(a,b){return (b.amount_m||0)-(a.amount_m||0);});
      });
      html+='<div class="briefing-card'+(isToday?' open':'')+'" onclick="this.classList.toggle(\'open\')">';
      // Header row
      html+='<div style="display:flex;justify-content:space-between;align-items:center">';
      html+='<div style="font-size:16px;font-weight:600">'+label+'</div>';
      html+='<div style="display:flex;align-items:center;gap:12px"><span style="font-size:12px;color:#666">$'+totalM.toFixed(1)+'M raised &middot; '+dayFilings.length+' co.</span><span style="font-size:12px;color:#aaa;user-select:none">&#8250;</span></div>';
      html+='</div>';
      // Company name tags (always visible)
      html+='<div style="margin-top:8px">';
      dayFilings.forEach(function(f){html+='<span class="tag">'+f.company+'</span>';});
      html+='</div>';
      // Expanded detail
      html+='<div class="detail">';
      Object.keys(byCat).sort().forEach(function(cat){
        html+='<div class="section-title">'+cat+'</div>';
        byCat[cat].forEach(function(f){
          html+='<div style="display:flex;justify-content:space-between;align-items:flex-start;padding:10px 0;border-bottom:1px solid #f5f5f5">';
          html+='<div style="flex:1;margin-right:16px">';
          html+='<div style="font-weight:500;font-size:13px">'+f.company+'</div>';
          if(f.what_they_do)html+='<div style="font-size:12px;color:#555;margin:3px 0">'+f.what_they_do+'</div>';
          html+='<div style="margin-top:4px">';
          if(f.city||f.state)html+='<span class="tag">'+[f.city,f.state].filter(Boolean).join(', ')+'</span>';
          if(f.founded)html+='<span class="tag">Est. '+f.founded+'</span>';
          html+='</div></div>';
          html+='<div style="text-align:right;flex-shrink:0">';
          html+='<div style="font-weight:600;font-size:14px">$'+f.amount_m+'M</div>';
          if(f.website)html+='<div style="margin-top:4px"><a href="'+f.website+'" style="font-size:11px;color:#0066cc" target="_blank" onclick="event.stopPropagation()">website ↗</a></div>';
          html+='</div></div>';
        });
      });
      html+='</div></div>';
    });
  }
  html+='</div>';
  document.getElementById('tab-sec').innerHTML=html;
}
function renderUnverified(){
  const entries=DB.unverified||[];
  let html='<div style="font-size:12px;color:#999;margin-bottom:16px">Companies from SEC Form D filings with no confirmed website. Checked daily — a dot appears when one is found.</div>';
  if(!entries.length){
    html+='<div style="color:#999;padding:20px">No unverified companies yet.</div>';
  } else {
    html+='<div>';
    entries.forEach(function(f){
      const isNew=f.newly_found;
      html+='<div style="display:flex;justify-content:space-between;align-items:center;padding:12px 0;border-bottom:1px solid #f0f0f0">';
      html+='<div>';
      if(isNew)html+='<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#16a34a;margin-right:6px;vertical-align:middle"></span>';
      html+='<span style="font-weight:500;font-size:13px">'+f.company+'</span>';
      html+='<div style="font-size:11px;color:#999;margin-top:2px">';
      if(f.city||f.state)html+=[f.city,f.state].filter(Boolean).join(', ')+' &middot; ';
      html+='Added '+f.date_added+'</div>';
      html+='</div>';
      html+='<div style="text-align:right;font-size:13px;font-weight:600">$'+f.amount_m+'M</div>';
      html+='</div>';
    });
    html+='</div>';
  }
  document.getElementById('tab-unverified').innerHTML=html;
}
function initAll(){
  renderBriefings();
  renderSEC();
  renderUnverified();
  if(window.location.hash){
    const el=document.querySelector(window.location.hash);
    if(el)setTimeout(function(){el.scrollIntoView({behavior:'smooth'});},300);
  }
}
loadData();
</script>
</body></html>""")

    html = parts[0].replace("SITE_PLACEHOLDER", SITE)

    with open(WEBSITE_F, "w") as f:
        f.write(html)


def build_email(news, substack, date_str):
    def fmt(text):
        if not text:
            return ''
        text = re.sub(r'^## (.+)$', r'<h3 style="font-size:11px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:#999;margin:40px 0 12px;padding-bottom:6px;border-bottom:1px solid #eee;">\1</h3>', text, flags=re.MULTILINE)
        text = re.sub(r"^\s*(WHAT THEY BUILD|COMPETITION)\s*$", r'<div style="font-size:10px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:#aaa;margin:16px 0 6px;padding-left:12px;border-left:2px solid #eee;">\1</div>', text, flags=re.MULTILINE)
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
        text = re.sub(r'^[-*] (.+)$', r'<div style="display:flex;gap:10px;margin-bottom:8px;padding-left:12px;"><span style="color:#ccc;flex-shrink:0;">-</span><span>\1</span></div>', text, flags=re.MULTILINE)
        text = re.sub(r'(https?://[^\s<)\]]+)', r'<a href="\1" style="color:#0066cc;text-decoration:none;word-break:break-all;">\1</a>', text)
        text = re.sub(r'\n\n+', '</p><p>', text)
        return f'<p>{text.replace(chr(10), "<br>")}</p>'

    sub_html = ""
    if substack:
        sub_html = f'<div style="margin-top:48px;padding-top:24px;border-top:2px solid #1a1a1a"><div style="font-size:11px;font-weight:600;letter-spacing:.15em;text-transform:uppercase;color:#999;margin-bottom:16px">Substack this week</div><div style="line-height:1.75">{fmt(substack)}</div></div>'

    return f"""<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;max-width:700px;margin:0 auto;padding:32px 24px;color:#1a1a1a;font-size:15px;line-height:1.8">
  <div style="border-bottom:2px solid #1a1a1a;margin-bottom:24px;padding-bottom:14px">
    <div style="font-size:11px;font-weight:600;letter-spacing:.15em;text-transform:uppercase;color:#999;margin-bottom:4px">Morning Briefing</div>
    <div style="font-size:22px;font-weight:600">{date_str}</div>
    <a href="{SITE}" style="font-size:12px;color:#0066cc;text-decoration:none">View archive</a>
  </div>
  <div>{fmt(news)}</div>
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


def dedupe_briefings(briefings):
    """Keep one entry per date — the one with the most company tags, then longest text."""
    by_date = {}
    for e in briefings:
        date = e.get("date", "")
        if not date:
            continue
        existing = by_date.get(date)
        if existing is None:
            by_date[date] = e
        else:
            e_score = (len(e.get("companies", [])), len(e.get("full_briefing", "")))
            ex_score = (len(existing.get("companies", [])), len(existing.get("full_briefing", "")))
            if e_score > ex_score:
                by_date[date] = e
    return [by_date[d] for d in sorted(by_date)]


def main():
    print(f"Running - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    briefings = load(BRIEFINGS_F, [])
    briefings = dedupe_briefings(briefings)

    if any(e.get("date") == TODAY_ISO for e in briefings):
        print(f"  Briefing for {TODAY_ISO} already exists. Skipping.")
        return
    print("  Web search briefing...")
    news = sonnet_search(news_prompt())
    print("  Substacks...")
    posts = fetch_substacks()
    sub = haiku(substack_prompt(posts), 1000) if posts else None
    print(f"    {len(posts)} posts")
    briefings.append({
        "date": TODAY_ISO, "date_display": TODAY,
        "full_briefing": news,
    })
    save(BRIEFINGS_F, briefings)
    print("  Fetching SEC Form D filings...")
    sec_filings   = load(SEC_F, [])
    sec_unverified = load(SEC_UNVERIFIED_F, [])
    print(f"    Checking {len(sec_unverified)} unverified companies...")
    sec_unverified, newly_found = check_unverified(sec_unverified)
    if newly_found:
        print(f"    {len(newly_found)} companies newly found — moving to verified")
        sec_filings = update_sec_filings(sec_filings, newly_found)
    new_verified, new_unverified = fetch_sec_form_d(min_amount=99_000)
    sec_filings   = update_sec_filings(sec_filings, new_verified)
    sec_unverified = update_unverified(sec_unverified, new_unverified, newly_found)
    print(f"    {len(new_verified)} new verified, {len(new_unverified)} new unverified, {len(sec_filings)} total verified")
    print("  Generating website...")
    generate_website(briefings, sec_filings, sec_unverified)
    print("  Sending email...")
    html = build_email(news, sub, datetime.now().strftime("%A, %B %d, %Y"))
    send_email(f"Morning Briefing - {datetime.now().strftime('%A, %B %d, %Y')}", html)
    print("  Done.")


if __name__ == "__main__":
    main()
