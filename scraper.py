import os
import re
import sys
import json
import time
import requests
import urllib.parse
from pathlib import Path
from bs4 import BeautifulSoup

try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)
STEP_SUMMARY_FILE = os.environ.get("GITHUB_STEP_SUMMARY")
SCRAPER_API_KEY = os.environ.get("SCRAPER_API_KEY", "").strip()

def write_to_summary(markdown_text: str):
    if STEP_SUMMARY_FILE:
        with open(STEP_SUMMARY_FILE, "a", encoding="utf-8") as f:
            f.write(markdown_text + "\n\n")

def normalize_target(target: str) -> tuple[str, str, str]:
    """Clean input to (type, clean_id, full_url)"""
    target = target.strip()
    if target.startswith("n/"):  # Fix typo if user entered 'n/username'
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
    
    return [normalize_target("company/tigihr")]

# ----------------------------------------------------------------------
# FULL PROFILE HTML PARSER
# ----------------------------------------------------------------------
def parse_full_profile_html(html_content: str, clean_id: str, linkedin_url: str) -> dict:
    soup = BeautifulSoup(html_content, "html.parser")
    
    data = {
        "id": clean_id,
        "type": "profile",
        "url": linkedin_url,
        "name": "",
        "headline": "",
        "location": "",
        "about": "",
        "experiences": [],
        "educations": []
    }

    # Extract Name & Headline
    h1 = soup.find("h1")
    if h1:
        data["name"] = h1.get_text(strip=True)
    
    headline_el = soup.find(class_=re.compile(r"top-card-layout__headline|text-body-medium"))
    if headline_el:
        data["headline"] = headline_el.get_text(strip=True)

    loc_el = soup.find(class_=re.compile(r"top-card-layout__first-subline|text-body-small"))
    if loc_el:
        data["location"] = loc_el.get_text(strip=True)

    # Extract About Section
    about_sec = soup.find("section", {"data-test-id": "about-us"}) or soup.find(class_=re.compile(r"summary|about"))
    if about_sec:
        data["about"] = about_sec.get_text(separator="\n", strip=True)

    # Extract Experience List
    exp_sections = soup.find_all(class_=re.compile(r"experience-item|profile-section-card"))
    for item in exp_sections:
        title_el = item.find(class_=re.compile(r"title|profile-section-card__title"))
        comp_el = item.find(class_=re.compile(r"subtitle|profile-section-card__subtitle"))
        dates_el = item.find(class_=re.compile(r"date-range|caption"))
        
        if title_el or comp_el:
            data["experiences"].append({
                "title": title_el.get_text(strip=True) if title_el else "",
                "company": comp_el.get_text(strip=True) if comp_el else "",
                "duration": dates_el.get_text(strip=True) if dates_el else ""
            })

    return data

# ----------------------------------------------------------------------
# PROFILE SCRAPER CONTROLLER
# ----------------------------------------------------------------------
def scrape_profile(clean_id: str, linkedin_url: str, slug: str):
    profile_data = None

    # Method 1: If Scraping Proxy is provided in GitHub Secrets
    if SCRAPER_API_KEY:
        print(f"[*] Fetching Full HTML via Scraping Proxy for: {clean_id}...")
        proxy_url = f"http://api.scraperapi.com?api_key={SCRAPER_API_KEY}&url={urllib.parse.quote(linkedin_url)}"
        try:
            res = requests.get(proxy_url, timeout=60)
            if res.status_code == 200 and len(res.text) > 2000:
                profile_data = parse_full_profile_html(res.text, clean_id, linkedin_url)
                (OUTPUT_DIR / f"{slug}_raw.html").write_text(res.text, encoding="utf-8")
        except Exception as e:
            print(f"    [!] Proxy scrape failed: {e}")

    # Method 2: Search Index Fallback (if no proxy key)
    if not profile_data or not profile_data.get("name"):
        print(f"[*] Extracting profile via Search Engine Index for: {clean_id}...")
        profile_data = {
            "id": clean_id,
            "type": "profile",
            "url": linkedin_url,
            "name": clean_id,
            "headline": "",
            "location": "",
            "experiences": [],
            "educations": [],
            "summary": ""
        }
        try:
            ddgs = DDGS()
            results = list(ddgs.text(f"site:linkedin.com/in/{clean_id}", max_results=5))
            for res in results:
                if "linkedin.com/in/" in res.get("href", ""):
                    title = res.get("title", "").replace(" | LinkedIn", "").strip()
                    snippet = res.get("body", "")
                    
                    if " - " in title:
                        parts = [p.strip() for p in title.split(" - ") if p.strip()]
                        profile_data["name"] = parts[0]
                        profile_data["headline"] = " - ".join(parts[1:])
                    else:
                        profile_data["name"] = title

                    profile_data["summary"] = snippet
                    
                    loc_m = re.search(r"Location:\s*([^·\n]+)", snippet)
                    if loc_m: profile_data["location"] = loc_m.group(1).strip()
                    
                    exp_m = re.search(r"Experience:\s*([^·\n]+)", snippet)
                    if exp_m:
                        profile_data["experiences"].append({"company": exp_m.group(1).strip()})
                    break
        except Exception as e:
            print(f"    [X] Search error: {e}")

    # Save to disk & Print directly in Terminal
    (OUTPUT_DIR / f"{slug}_data.json").write_text(json.dumps(profile_data, indent=2), encoding="utf-8")
    print_terminal_result(profile_data)
    
    # Write to GitHub Actions UI Summary
    write_to_summary(f"### 👤 Profile: `{profile_data.get('name', clean_id)}`\n"
                     f"- **URL:** [{linkedin_url}]({linkedin_url})\n"
                     f"- **Headline:** {profile_data.get('headline') or 'N/A'}\n"
                     f"- **Location:** {profile_data.get('location') or 'N/A'}\n"
                     f"```json\n{json.dumps(profile_data, indent=2)}\n```")

# ----------------------------------------------------------------------
# COMPANY SCRAPER (Playwright Direct - 100% Working)
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
        "schema_data": []
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

        (OUTPUT_DIR / f"{slug}_raw.html").write_text(page.content(), encoding="utf-8")

        if page.locator("h1").count() > 0:
            company_data["title"] = page.locator("h1").first.inner_text().strip()
        if page.locator(".top-card-layout__headline").count() > 0:
            company_data["headline"] = page.locator(".top-card-layout__headline").first.inner_text().strip()

        json_ld_scripts = page.locator('script[type="application/ld+json"]').all_inner_texts()
        for s in json_ld_scripts:
            try:
                company_data["schema_data"].append(json.loads(s))
            except Exception:
                pass

        browser.close()

    (OUTPUT_DIR / f"{slug}_data.json").write_text(json.dumps(company_data, indent=2), encoding="utf-8")
    print_terminal_result(company_data)
    
    write_to_summary(f"### 🏢 Company: `{company_data.get('title') or clean_id}`\n"
                     f"- **URL:** [{url}]({url})\n"
                     f"- **Headline:** {company_data.get('headline') or 'N/A'}\n"
                     f"```json\n{json.dumps(company_data, indent=2)}\n```")

def print_terminal_result(data: dict):
    print("\n" + "=" * 65)
    print(f" 🎯 EXTRACTED RESULT: {data.get('url')}")
    print("=" * 65)
    print(json.dumps(data, indent=2, ensure_ascii=False))
    print("=" * 65 + "\n")

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

    print("[+] All tasks completed.")

if __name__ == "__main__":
    main()
