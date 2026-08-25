import os
import re
import sys
import json
import time
import urllib.parse
from pathlib import Path

try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)
STEP_SUMMARY_FILE = os.environ.get("GITHUB_STEP_SUMMARY")

def write_to_summary(markdown_text: str):
    if STEP_SUMMARY_FILE:
        with open(STEP_SUMMARY_FILE, "a", encoding="utf-8") as f:
            f.write(markdown_text + "\n\n")

def normalize_target(target: str) -> tuple[str, str, str]:
    """Cleans input into (type, clean_id, full_url)"""
    target = target.strip()
    if target.startswith("n/"):
        target = "in/" + target[2:]
        
    if "linkedin.com/company/" in target:
        clean_id = re.search(r"linkedin\.com/company/([^/?#]+)", target).group(1)
        return "company", clean_id, f"https://www.linkedin.com/company/{clean_id}/"
    elif "linkedin.com/in/" in target:
        clean_id = re.search(r"linkedin\.com/in/([^/?#]+)", target).group(1)
        return "profile", clean_id, f"https://www.linkedin.com/in/{clean_id}/"

    if target.startswith("company/"):
        clean_id = target.replace("company/", "").strip("/")
        return "company", clean_id, f"https://www.linkedin.com/company/{clean_id}/"
    elif target.startswith("in/"):
        clean_id = target.replace("in/", "").strip("/")
        return "profile", clean_id, f"https://www.linkedin.com/in/{clean_id}/"
    else:
        clean_id = target.strip("/")
        return "profile", clean_id, f"https://www.linkedin.com/in/{clean_id}/"

def sanitize_filename(url: str) -> str:
    slug = re.sub(r"https?://(www\.)?linkedin\.com/", "", url)
    slug = re.sub(r"[^a-zA-Z0-9_-]", "_", slug).strip("_")
    return slug or "linkedin_page"

def get_targets() -> list[tuple[str, str, str]]:
    if len(sys.argv) > 1:
        raw_input = " ".join(sys.argv[1:])
        tokens = [t for t in re.split(r"[,\s]+", raw_input) if t.strip()]
        return [normalize_target(t) for t in tokens]
    
    env_targets = os.environ.get("TARGET_INPUT", "").strip()
    if env_targets:
        tokens = [t for t in re.split(r"[,\n]+", env_targets) if t.strip()]
        return [normalize_target(t) for t in tokens]
    
    return [normalize_target("in/satyanadella"), normalize_target("company/tigihr")]

# ----------------------------------------------------------------------
# HELPER: AUTO-RESOLVE COMPANY LINKEDIN URL
# ----------------------------------------------------------------------
def resolve_company_url(ddgs: DDGS, company_name: str) -> dict:
    """Searches for the official LinkedIn Company URL for any extracted company name."""
    if not company_name or company_name.lower() in ["confidential", "self-employed", "freelance", "n/a", "unknown"]:
        return {"name": company_name, "company_linkedin_url": None}
    
    # Strip common suffixes like 'Inc', 'LLC', etc. for search
    clean_search = re.sub(r"\s+(?:Inc\.?|LLC|Pvt\.?\s*Ltd\.?|Ltd\.?)$", "", company_name, flags=re.IGNORECASE).strip()
    query = f'site:linkedin.com/company "{clean_search}"'
    
    try:
        results = list(ddgs.text(query, max_results=2))
        for res in results:
            href = res.get("href", "")
            if "linkedin.com/company/" in href:
                return {
                    "name": company_name,
                    "company_linkedin_url": href
                }
    except Exception:
        pass
    
    return {"name": company_name, "company_linkedin_url": None}

# ----------------------------------------------------------------------
# 1. PROFILE SCRAPER (Multi-Query Deep Raw Search + Auto-Company Resolver)
# ----------------------------------------------------------------------
def scrape_profile(clean_id: str, linkedin_url: str, slug: str) -> dict:
    print(f"[*] Gathering complete raw data & companies for: {clean_id}...")
    
    profile_data = {
        "id": clean_id,
        "type": "profile",
        "url": linkedin_url,
        "name": clean_id,
        "headline": "",
        "current_company": None,
        "past_companies": [],
        "educations": [],
        "location": "",
        "connections": "",
        "about_snippet": "",
        "raw_search_hits": []
    }

    ddgs = DDGS()
    queries = [
        f"site:linkedin.com/in/{clean_id}",
        f'"{clean_id}" site:linkedin.com/in',
        f"{clean_id} linkedin Experience Education"
    ]

    all_snippets = []
    detected_companies = []

    for q in queries:
        try:
            results = list(ddgs.text(q, max_results=3))
            for res in results:
                href = res.get("href", "")
                if "linkedin.com/in/" in href or clean_id in href:
                    profile_data["raw_search_hits"].append({
                        "query": q,
                        "title": res.get("title", ""),
                        "url": href,
                        "snippet": res.get("body", "")
                    })
                    all_snippets.append(res.get("body", ""))
                    
                    # Parse Title
                    raw_title = res.get("title", "").replace(" | LinkedIn", "").replace(" - LinkedIn", "").strip()
                    if " - " in raw_title and not profile_data["headline"]:
                        parts = [p.strip() for p in raw_title.split(" - ") if p.strip()]
                        profile_data["name"] = parts[0]
                        profile_data["headline"] = " - ".join(parts[1:])
                        if len(parts) >= 3:
                            detected_companies.append(parts[-1])
                    elif raw_title and profile_data["name"] == clean_id:
                        profile_data["name"] = raw_title
        except Exception:
            continue

    # Combine and parse all snippets for full historical mentions
    combined_snippet = " \n ".join(all_snippets)

    if combined_snippet:
        # Extract Connections
        conn_m = re.search(r"(\d+\+?\s*connections)", combined_snippet, re.IGNORECASE)
        if conn_m:
            profile_data["connections"] = conn_m.group(1).strip()

        # Extract Location
        loc_m = re.search(r"(?:Location|based in)[:\s]+([^·\n]+)", combined_snippet, re.IGNORECASE)
        if loc_m:
            profile_data["location"] = loc_m.group(1).strip()

        # Extract Experience list
        exp_matches = re.findall(r"(?:Experience|Current|Past)[:\s]+([^·\n]+)", combined_snippet, re.IGNORECASE)
        for exp in exp_matches:
            # Handle comma-separated companies in snippet
            for sub_exp in re.split(r",\s*|;\s*", exp):
                sub_clean = sub_exp.strip()
                if sub_clean and sub_clean not in detected_companies:
                    detected_companies.append(sub_clean)

        # Extract Education list
        edu_matches = re.findall(r"(?:Education)[:\s]+([^·\n]+)", combined_snippet, re.IGNORECASE)
        for edu in edu_matches:
            for sub_edu in re.split(r",\s*|;\s*", edu):
                if sub_edu.strip() and sub_edu.strip() not in profile_data["educations"]:
                    profile_data["educations"].append(sub_edu.strip())

        # Clean bio snippet
        clean_bio = re.sub(r"^[A-Za-z]+\s+\d{1,2},\s+\d{4}\s*-\s*", "", combined_snippet)
        clean_bio = re.sub(r"·\s*(?:Experience|Location|connections|Education|View).*$", "", clean_bio, flags=re.IGNORECASE).strip()
        profile_data["about_snippet"] = clean_bio

    # Auto-resolve LinkedIn URLs for all detected companies
    resolved_companies = []
    for comp in detected_companies:
        comp_info = resolve_company_url(ddgs, comp)
        resolved_companies.append(comp_info)

    if resolved_companies:
        profile_data["current_company"] = resolved_companies[0]
        profile_data["past_companies"] = resolved_companies[1:]

    # Save to disk
    (OUTPUT_DIR / f"{slug}_data.json").write_text(json.dumps(profile_data, indent=2), encoding="utf-8")
    
    # 1. Print full raw data directly in Terminal
    print_terminal_result(profile_data)
    
    # 2. Write to GitHub Summary UI
    curr_comp_text = f"[{profile_data['current_company']['name']}]({profile_data['current_company']['company_linkedin_url']})" if profile_data['current_company'] and profile_data['current_company'].get('company_linkedin_url') else (profile_data['current_company']['name'] if profile_data['current_company'] else 'N/A')
    
    write_to_summary(f"### 👤 Profile: `{profile_data.get('name')}`\n"
                     f"- **Profile URL:** [{linkedin_url}]({linkedin_url})\n"
                     f"- **Headline:** {profile_data.get('headline') or 'N/A'}\n"
                     f"- **Current Company:** {curr_comp_text}\n"
                     f"- **Location:** {profile_data.get('location') or 'N/A'}\n"
                     f"- **Connections:** {profile_data.get('connections') or 'N/A'}\n"
                     f"```json\n{json.dumps(profile_data, indent=2)}\n```")

    return profile_data

# ----------------------------------------------------------------------
# 2. COMPANY SCRAPER (Playwright Direct Mode - Full Raw Data)
# ----------------------------------------------------------------------
def scrape_company(clean_id: str, url: str, slug: str):
    from playwright.sync_api import sync_playwright
    print(f"[*] Scraping Company: {url}")
    
    company_data = {
        "id": clean_id,
        "type": "company",
        "url": url,
        "title": "",
        "headline": "",
        "about": "",
        "website": "",
        "industry": "",
        "company_size": "",
        "headquarters": "",
        "schema_raw_data": [],
        "raw_page_text": ""
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.google.com/"}
        )
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        time.sleep(2)

        raw_html = page.content()
        (OUTPUT_DIR / f"{slug}_raw.html").write_text(raw_html, encoding="utf-8")
        
        company_data["raw_page_text"] = page.inner_text("body")

        if page.locator("h1").count() > 0:
            company_data["title"] = page.locator("h1").first.inner_text().strip()
        if page.locator(".top-card-layout__headline").count() > 0:
            company_data["headline"] = page.locator(".top-card-layout__headline").first.inner_text().strip()
        if page.locator('[data-test-id="about-us__description"]').count() > 0:
            company_data["about"] = page.locator('[data-test-id="about-us__description"]').first.inner_text().strip()

        # Extract Schema JSON-LD
        json_ld_scripts = page.locator('script[type="application/ld+json"]').all_inner_texts()
        for s in json_ld_scripts:
            try:
                company_data["schema_raw_data"].append(json.loads(s))
            except Exception:
                pass

        browser.close()

    (OUTPUT_DIR / f"{slug}_data.json").write_text(json.dumps(company_data, indent=2), encoding="utf-8")
    
    # Print full raw data to Terminal
    print_terminal_result(company_data)
    
    write_to_summary(f"### 🏢 Company: `{company_data.get('title') or clean_id}`\n"
                     f"- **URL:** [{url}]({url})\n"
                     f"- **Headline:** {company_data.get('headline') or 'N/A'}\n"
                     f"```json\n{json.dumps(company_data, indent=2)}\n```")

# ----------------------------------------------------------------------
# TERMINAL OUTPUT FORMATTER
# ----------------------------------------------------------------------
def print_terminal_result(data: dict):
    print("\n" + "=" * 70)
    print(f" 🎯 RAW & STRUCTURED EXTRACTED DATA: {data.get('url')}")
    print("=" * 70)
    print(json.dumps(data, indent=2, ensure_ascii=False))
    print("=" * 70 + "\n")

def main():
    targets = get_targets()
    print(f"\n[*] Total targets to scrape: {len(targets)}")
    write_to_summary(f"## 🚀 LinkedIn Scraper Results\n**Total targets:** {len(targets)}")

    for idx, (t_type, clean_id, url) in enumerate(targets, start=1):
        slug = sanitize_filename(url)
        print(f"[{idx}/{len(targets)}] Processing [{t_type.upper()}]: {clean_id}")

        if t_type == "profile":
            scrape_profile(clean_id, url, slug)
        else:
            scrape_company(clean_id, url, slug)

        time.sleep(1)

    print("[+] All scraping tasks completed.")

if __name__ == "__main__":
    main()
