#!/usr/bin/env python3
import os, re, json, time, requests
from datetime import datetime, timezone, timedelta

try:
    from defusedxml import ElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET

RECIPIENT_EMAIL    = os.environ.get("RECIPIENT_EMAIL", "jessenlj40@gmail.com")
GITHUB_USERNAME    = os.environ.get("GITHUB_USERNAME", "jessenlj")
REPO_NAME          = os.environ.get("REPO_NAME", "morning_briefing")

TODAY_ISO    = datetime.now().strftime("%Y-%m-%d")
SITE         = f"https://{GITHUB_USERNAME}.github.io/{REPO_NAME}"
DATA_DIR     = "docs/data"
SEC_F            = f"{DATA_DIR}/sec_filings.json"
SEC_UNVERIFIED_F = f"{DATA_DIR}/sec_unverified.json"
WEBSITE_F    = "docs/index.html"

NOISE_INDUSTRIES = {
    "Pooled Investment Fund", "Other Real Estate", "Residential", "Commercial",
    "Investing", "Other Banking and Financial Services", "Commercial Banking",
    "Investment Banking", "REITS and Finance", "Restaurants", "Agriculture",
}

TECH_KEYWORDS = frozenset([
    "software", "platform", "saas", "api", "cloud", "app", "application",
    "open source", "dashboard", "database", "integration", "pipeline",
    "devops", "developer", "sdk", "plugin", "microservice", "serverless",
    "ai", "artificial intelligence", "machine learning", "deep learning",
    "data", "analytics", "algorithm", "automation", "neural", "inference",
    "model", "compute", "intelligence", "llm", "generative", "nlp",
    "computer vision", "prediction", "training", "dataset",
    "infrastructure", "hardware", "firmware", "embedded", "semiconductor",
    "sensor", "chip", "processor", "network", "protocol", "satellite",
    "drone", "robotics", "photonic", "quantum", "battery", "energy storage",
    "cybersecurity", "security", "encryption", "authentication", "identity",
    "compliance", "privacy", "firewall", "threat",
    "fintech", "healthtech", "biotech", "edtech", "proptech", "insurtech",
    "legaltech", "agtech", "climatetech", "medtech",
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


def categorize_sec_company(name, industry, what_they_do):
    text = (name + " " + industry + " " + what_they_do).lower()
    for cat, keywords in SEC_CATEGORIES:
        if any(kw in text for kw in keywords):
            return cat
    return "Other Tech"


def clearbit_lookup(company_name):
    try:
        r = requests.get(
            "https://autocomplete.clearbit.com/v1/companies/suggest",
            params={"query": company_name},
            headers={"User-Agent": f"FormDBriefing/1.0 {RECIPIENT_EMAIL}"},
            timeout=10)
        if r.status_code == 200:
            results = r.json()
            if results:
                return results[0].get("domain", "")
    except Exception:
        pass
    return None


def safe_scrape(url, max_bytes=50_000):
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
        meta = soup.find("meta", attrs={"name": "description"})
        meta_desc = meta.get("content", "") if meta else ""
        body = re.sub(r"\s+", " ", soup.get_text(separator=" ", strip=True))
        return (meta_desc + " " + body[:2000]).strip()
    except Exception:
        return ""


def is_tech(text, name=""):
    combined = (text + " " + name).lower()
    return any(kw in combined for kw in TECH_KEYWORDS)


def enrich_sec_company(entity, city, state, phone, cik_str, industry=""):
    EXPLICIT_TECH = {"Computers", "Telecommunications", "Other Technology"}
    sec_hdrs = {"User-Agent": f"FormDBriefing/1.0 {RECIPIENT_EMAIL}"}
    website, page_text = "", ""

    try:
        time.sleep(0.1)
        sr = requests.get(f"https://data.sec.gov/submissions/CIK{cik_str}.json",
                          headers=sec_hdrs, timeout=10)
        if sr.status_code == 200:
            website = sr.json().get("website", "") or ""
    except Exception:
        pass

    if not website:
        clean_name = re.sub(
            r',?\s*(Inc\.?|LLC\.?|L\.P\.?|Corp\.?|Co\.?|Ltd\.?|PBC|Holdings?|Group)\s*$',
            '', entity, flags=re.IGNORECASE).strip()
        domain = clearbit_lookup(clean_name) or clearbit_lookup(entity)
        if domain:
            brand_words = [w.lower() for w in re.split(r'\s+', clean_name) if len(w) > 3]
            domain_lower = domain.lower().split(".")[0]
            if not brand_words or not any(w in domain_lower for w in brand_words):
                domain = None
            if domain:
                website = f"https://{domain}"

    if website:
        page_text = safe_scrape(website)

    if website:
        if not is_tech(page_text, entity):
            return None
    else:
        name_lower = entity.lower()
        name_is_tech = any(kw in name_lower for kw in TECH_KEYWORDS)
        sec_says_tech = industry in EXPLICIT_TECH
        if not name_is_tech and not sec_says_tech:
            return None
        return {"website": "", "what_they_do": "No web presence found — flag for review"}

    description = ""
    if page_text:
        first = page_text.split(".")[0].strip()
        if 15 < len(first) < 300:
            description = first + "."
    if not description:
        description = page_text[:200].strip() if page_text else ""

    return {"website": website, "what_they_do": description}


def check_unverified(unverified):
    PRUNE_DAYS = 60
    cutoff = (datetime.now() - timedelta(days=PRUNE_DAYS)).strftime("%Y-%m-%d")
    newly_found = []
    still_unverified = []
    for entry in unverified:
        if entry.get("date_added", "9999") < cutoff:
            print(f"    [Unverified] pruning old entry: {entry['company']}")
            continue
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
    hdrs  = {"User-Agent": f"FormDBriefing/1.0 {RECIPIENT_EMAIL}"}
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        r = requests.get(
            f"https://efts.sec.gov/LATEST/search-index?q=&forms=D"
            f"&dateRange=custom&startdt={today}&enddt={today}&from=0&size=1",
            headers=hdrs, timeout=30)
        total = r.json().get("hits", {}).get("total", {}).get("value", 0)
    except Exception as e:
        print(f"    [SEC] Search failed: {e}")
        return [], []

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
    by_cik = {e["cik"]: e for e in existing}
    for f in new_filings:
        cik = f["cik"]
        if cik not in by_cik or f["date_filed"] >= by_cik[cik]["date_filed"]:
            by_cik[cik] = f
    return sorted(by_cik.values(), key=lambda x: x["date_filed"], reverse=True)


def update_unverified(existing, new_unverified, newly_found):
    found_ciks = {e["cik"] for e in newly_found}
    by_cik = {e["cik"]: e for e in existing if e["cik"] not in found_ciks}
    for f in new_unverified:
        if f["cik"] not in by_cik:
            by_cik[f["cik"]] = f
    return sorted(by_cik.values(), key=lambda x: x.get("date_added", ""), reverse=True)


def generate_website(sec_filings=None, sec_unverified=None):
    os.makedirs("docs", exist_ok=True)
    save(SEC_F, sec_filings or [])
    save(SEC_UNVERIFIED_F, sec_unverified or [])

    html = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Form D Briefing</title>
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
.tag{display:inline-block;font-size:11px;padding:2px 8px;border-radius:12px;margin:2px;background:#f0f0ee;color:#555}
.section-title{font-size:11px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:#999;margin:24px 0 12px;padding-bottom:6px;border-bottom:1px solid #eee}
.briefing-card{background:#fff;border:1px solid #e5e5e5;border-radius:8px;padding:16px 20px;margin-bottom:10px;cursor:pointer;transition:border-color .15s}
.briefing-card:hover{border-color:#999}
.briefing-card .detail{display:none;margin-top:14px;padding-top:14px;border-top:1px solid #f0f0f0}
.briefing-card.open .detail{display:block}
.loading{color:#999;font-size:14px;padding:40px;text-align:center}
</style>
</head>
<body>
<div class="site-header">
  <div><h1>Form D Briefing</h1><p style="font-size:12px;color:#888;margin-top:2px">Updated daily at 8 AM MDT</p></div>
</div>
<div class="nav">
  <button class="active" onclick="tab('sec',this)">SEC Filings</button>
  <button onclick="tab('unverified',this)">Unverified</button>
</div>
<div class="container">
  <div id="tab-sec" class="panel active"><div class="loading">Loading SEC filings...</div></div>
  <div id="tab-unverified" class="panel"><div class="loading">Loading unverified...</div></div>
</div>
<script>
const BASE='SITE_PLACEHOLDER';
let DB={};
async function loadData(){
  const [s,u]=await Promise.all([
    fetch(BASE+'/data/sec_filings.json').then(r=>r.json()).catch(()=>[]),
    fetch(BASE+'/data/sec_unverified.json').then(r=>r.json()).catch(()=>[]),
  ]);
  DB.sec=s;DB.unverified=u;
  renderSEC();
  renderUnverified();
}
function tab(id,btn){
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.nav button').forEach(b=>b.classList.remove('active'));
  document.getElementById('tab-'+id).classList.add('active');
  btn.classList.add('active');
}
function esc(s){
  if(!s)return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}
function safeHref(url){
  if(!url)return '';
  const u=String(url).trim();
  return (u.startsWith('https://')||u.startsWith('http://'))?u:'';
}
function renderSEC(){
  const filings=DB.sec||[];
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
      html+='<div style="display:flex;justify-content:space-between;align-items:center">';
      html+='<div style="font-size:16px;font-weight:600">'+label+'</div>';
      html+='<div style="display:flex;align-items:center;gap:12px"><span style="font-size:12px;color:#666">$'+totalM.toFixed(1)+'M raised &middot; '+dayFilings.length+' co.</span><span style="font-size:12px;color:#aaa;user-select:none">&#8250;</span></div>';
      html+='</div>';
      html+='<div style="margin-top:8px">';
      dayFilings.forEach(function(f){html+='<span class="tag">'+esc(f.company)+'</span>';});
      html+='</div>';
      html+='<div class="detail">';
      Object.keys(byCat).sort().forEach(function(cat){
        html+='<div class="section-title">'+esc(cat)+'</div>';
        byCat[cat].forEach(function(f){
          html+='<div style="display:flex;justify-content:space-between;align-items:flex-start;padding:10px 0;border-bottom:1px solid #f5f5f5">';
          html+='<div style="flex:1;margin-right:16px">';
          html+='<div style="font-weight:500;font-size:13px">'+esc(f.company)+'</div>';
          if(f.what_they_do)html+='<div style="font-size:12px;color:#555;margin:3px 0">'+esc(f.what_they_do)+'</div>';
          html+='<div style="margin-top:4px">';
          if(f.city||f.state)html+='<span class="tag">'+esc([f.city,f.state].filter(Boolean).join(', '))+'</span>';
          if(f.founded)html+='<span class="tag">Est. '+esc(f.founded)+'</span>';
          html+='</div></div>';
          html+='<div style="text-align:right;flex-shrink:0">';
          html+='<div style="font-weight:600;font-size:14px">$'+esc(f.amount_m)+'M</div>';
          const ws=safeHref(f.website);
          if(ws)html+='<div style="margin-top:4px"><a href="'+ws+'" style="font-size:11px;color:#0066cc" target="_blank" onclick="event.stopPropagation()">website ↗</a></div>';
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
      html+='<span style="font-weight:500;font-size:13px">'+esc(f.company)+'</span>';
      html+='<div style="font-size:11px;color:#999;margin-top:2px">';
      if(f.city||f.state)html+=esc([f.city,f.state].filter(Boolean).join(', '))+' &middot; ';
      html+='Added '+esc(f.date_added)+'</div>';
      html+='</div>';
      html+='<div style="text-align:right;font-size:13px;font-weight:600">$'+f.amount_m+'M</div>';
      html+='</div>';
    });
    html+='</div>';
  }
  document.getElementById('tab-unverified').innerHTML=html;
}
loadData();
</script>
</body></html>"""

    html = html.replace("SITE_PLACEHOLDER", SITE)
    with open(WEBSITE_F, "w") as f:
        f.write(html)


def main():
    print(f"Running - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    sec_filings    = load(SEC_F, [])
    sec_unverified = load(SEC_UNVERIFIED_F, [])
    print(f"  Checking {len(sec_unverified)} unverified companies...")
    sec_unverified, newly_found = check_unverified(sec_unverified)
    if newly_found:
        print(f"  {len(newly_found)} companies newly found — moving to verified")
        sec_filings = update_sec_filings(sec_filings, newly_found)
    print("  Fetching SEC Form D filings...")
    new_verified, new_unverified = fetch_sec_form_d(min_amount=99_000)
    sec_filings    = update_sec_filings(sec_filings, new_verified)
    sec_unverified = update_unverified(sec_unverified, new_unverified, newly_found)
    print(f"  {len(new_verified)} new verified, {len(new_unverified)} new unverified, {len(sec_filings)} total verified")
    print("  Generating website...")
    generate_website(sec_filings, sec_unverified)
    print("  Done.")


if __name__ == "__main__":
    main()
