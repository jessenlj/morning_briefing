#!/usr/bin/env python3
"""
One-time seed script. Run once from GitHub Actions.
Populates docs/data/companies.json with top 100 high-growth startups.
"""
import os, json, time, re
import anthropic

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

BATCHES = [
    {"sector": "Foundation Models & AI Labs",
     "companies": ["OpenAI","Anthropic","xAI","Mistral AI","Cohere","AI21 Labs","Inflection AI","Stability AI","Aleph Alpha","Imbue"]},
    {"sector": "AI Infrastructure & MLOps",
     "companies": ["Databricks","Scale AI","Anyscale","Modal Labs","Together AI","Replicate","Weights & Biases","Hugging Face","Weaviate","Pinecone"]},
    {"sector": "AI Applications - Enterprise",
     "companies": ["Harvey","Glean","Writer","Jasper","Runway","Synthesia","ElevenLabs","Perplexity AI","Character.AI","Cognition AI"]},
    {"sector": "AI Agents & Automation",
     "companies": ["Sierra AI","Adept AI","Dust","MultiOn","Lindy AI","Gumloop","Beam AI","Relay.app","Cassidy AI","Lexi AI"]},
    {"sector": "Robotics & Physical AI",
     "companies": ["Figure AI","Physical Intelligence","1X Technologies","Skild AI","Apptronik","Agility Robotics","Wayve","Nuro","Serve Robotics","Zipline"]},
    {"sector": "Fintech",
     "companies": ["Stripe","Plaid","Ramp","Brex","Mercury","Rippling","Deel","Vanta","Sardine","Pipe"]},
    {"sector": "Defense & Dual-Use Tech",
     "companies": ["Anduril Industries","Shield AI","Joby Aviation","Archer Aviation","Hermeus","Hadrian","Vannevar Labs","True Anomaly","Apex Space","Epirus"]},
    {"sector": "Bio & Health Tech",
     "companies": ["Recursion Pharmaceuticals","Insitro","Ginkgo Bioworks","Benchling","Devoted Health","Omada Health","Hinge Health","Nabla Bio","Eikon Therapeutics","Nuvation Bio"]},
    {"sector": "Climate & Energy Tech",
     "companies": ["Commonwealth Fusion Systems","Form Energy","Twelve","Charm Industrial","Pachama","Crusoe Energy","Fervo Energy","Sublime Systems","Turntide Technologies","Verdagy"]},
    {"sector": "Consumer & Productivity",
     "companies": ["Notion","Linear","Webflow","Loom","Miro","ClickUp","Airtable","Coda","Superhuman","Arc Browser"]},
]

PROMPT_TEMPLATE = """Research these {n} startups in the {sector} sector. Return ONLY a JSON array, no other text, no markdown fences.

Companies: {companies}

For each company return exactly this structure:
{{
  "name": "exact company name",
  "industry_vertical": "{sector}",
  "tech_layer": "Foundation Models / Infrastructure / Application / Platform / Hardware / Other",
  "what_they_do": "one concrete sentence on what they build and sell",
  "founded": 2020,
  "location": "City, Country",
  "status": "private or public or acquired",
  "ticker": "TICKER or null",
  "ipo_date": "YYYY-MM or null",
  "ipo_price": null,
  "high_growth": true,
  "high_growth_reasoning": "why this is high-growth",
  "founder_backgrounds": ["ex-Google","repeat-founder","researcher","first-time"],
  "funding_rounds": [
    {{
      "date": "YYYY-MM",
      "stage": "Seed / Series A / etc",
      "amount_m": 100,
      "valuation_b": 1.0,
      "investors": ["Investor 1", "Investor 2"],
      "source": "publication or url"
    }}
  ]
}}

Include every known funding round with every known investor per round. Use null for unknown values."""


def research_batch(client, sector, companies):
    prompt = PROMPT_TEMPLATE.format(
        n=len(companies), sector=sector, companies=", ".join(companies)
    )
    messages = [{"role": "user", "content": prompt}]
    result = ""
    for _ in range(8):
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4000,
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
    match = re.search(r'\[[\s\S]*\]', result)
    if match:
        try:
            return json.loads(match.group(0))
        except:
            pass
    print(f"    Parse failed for {sector}")
    return []


def main():
    print("Seeding company database...")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    os.makedirs("docs/data", exist_ok=True)

    path = "docs/data/companies.json"
    companies = json.load(open(path)) if os.path.exists(path) else {}

    for batch in BATCHES:
        sector = batch["sector"]
        print(f"  {sector}...")

        for attempt in range(3):
            try:
                results = research_batch(client, sector, batch["companies"])
                break
            except Exception as e:
                if "rate_limit" in str(e).lower() or "429" in str(e):
                    wait = 60 * (attempt + 1)
                    print(f"    Rate limited. Waiting {wait}s...")
                    time.sleep(wait)
                else:
                    print(f"    Error: {e}")
                    results = []
                    break
        else:
            results = []

        print(f"    {len(results)} companies returned")
        for c in results:
            name = c.get("name", "")
            if not name:
                continue
            c.update({
                "last_updated": "2026-04-11",
                "first_seen_in_briefing": None,
                "appearances": [],
                "current_price": None,
                "price_change_today_pct": None,
                "price_change_since_ipo_pct": None,
            })
            companies[name] = c
        with open(path, "w") as f:
            json.dump(companies, f, indent=2)
        print(f"    Saved. Total: {len(companies)}")
        print("    Waiting 30s before next batch...")
        time.sleep(30)

    print(f"\nDone. {len(companies)} companies in docs/data/companies.json")


if __name__ == "__main__":
    main()
