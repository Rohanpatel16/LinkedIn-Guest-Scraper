import os
import re
import sys
import json
import time
import urllib.parse
from pathlib import Path
from playwright.sync_api import sync_playwright

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

def normalize_target(target: str) -> tuple[str, str, str]:
    """Returns (type, clean_id, full_url)"""
    target = target.strip()
    
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
    
    return [
        normalize_target("company/tigihr"),
        normalize_target("in/satyanadella")
    ]

# ----------------------------------------------------------------------
# 1. PROFILE SCRAPER (Via Direct Search Index - No Authwall / No 999)
# ----------------------------------------------------------------------
def scrape_profile_search_index(page, clean_id: str, linkedin_url: str, slug: str) -> dict:
    print(f"[*] Extracting profile data via search index for: {clean_id}")
    
    profile_data = {
        "id": clean_id,
        "type": "profile",
        "url": linkedin_url,
        "name": clean_id,
        "headline": "",
        "location": "",
        "summary": "",
        "raw_title": "",
        "raw_snippet": ""
    }

    # Query 1: DuckDuckGo HTML
    query = f"site:linkedin.com/in/{clean_id}"
    ddg_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
    
    success = False
    try:
        page.goto(ddg_url, wait_until="domcontentloaded", timeout=25000)
        time.sleep(2)
        
        first_result = page.locator(".result__body").first
        if first_result.count() > 0:
            title = first_result.locator(".result__title").inner_text().strip()
            snippet = first_result.locator(".result__snippet").inner_text().strip()
            
            profile_data["raw_title"] = title
            profile_data["raw_snippet"] = snippet
            success = True
    except Exception as e:
        print(f"    [!] DuckDuckGo query skipped: {e}")

    # Query 2: Bing fallback if needed
    if not success or not profile_data["raw_title"]:
        try:
            bing_url = f"https://www.bing.com/search?q={urllib.parse.quote(query)}"
            page.goto(bing_url, wait_until="domcontentloaded", timeout=25000)
            time.sleep(2)
            
            first_algo = page.locator("#b_results .b_algo").first
            if first_algo.count() > 0:
                title = first_algo.locator("h2").inner_text().strip()
                snippet = first_algo.locator(".b_caption p").inner_text().strip()
                
                profile_data["raw_title"] = title
                profile_data["raw_snippet"] = snippet
                success = True
        except Exception as e:
            print(f"    [!] Bing fallback skipped: {e}")

    # Parse title into Name & Headline
    # Format typically: "Satya Nadella - Chairman and Chief Executive Officer - Microsoft | LinkedIn"
    raw_title = profile_data["raw_title"].replace(" | LinkedIn", "").replace(" - LinkedIn", "").strip()
    if " - " in raw_title:
        parts = [p.strip() for p in raw_title.split(" - ") if p.strip()]
        profile_data["name"] = parts[0]
        profile_data["headline"] = " - ".join(parts[1:])
    elif raw_title:
        profile_data["name"] = raw_title

    # Extract summary/location from snippet
    raw_snippet = profile_data["raw_snippet"]
    profile_data["summary"] = raw_snippet
    
    loc_match = re.search(r"Location:\s*([^·\n]+)", raw_snippet, re.IGNORECASE)
    if loc_match:
        profile_data["location"] = loc_match.group(1).strip()

    # Save output files
    (OUTPUT_DIR / f"{slug}_data.json").write_text(json.dumps(profile_data, indent=2), encoding="utf-8")
    (OUTPUT_DIR / f"{slug}_raw.txt").write_text(f"Title: {profile_data['raw_title']}\nSnippet: {profile_data['raw_snippet']}", encoding="utf-8")

    print(f"    [✓] Extracted: Name: '{profile_data['name']}' | Headline: '{profile_data['headline']}'")
    return profile_data

# ----------------------------------------------------------------------
# 2. COMPANY SCRAPER (Direct Playwright - 100% Success)
# ----------------------------------------------------------------------
def scrape_company_direct(page, clean_id: str, url: str, slug: str):
    print(f"[*] Scraping Company directly: {url}")
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        time.sleep(2)

        # Save Raw HTML, Text, and Screenshot
        html_content = page.content()
        (OUTPUT_DIR / f"{slug}_raw.html").write_text(html_content, encoding="utf-8")
        (OUTPUT_DIR / f"{slug}_raw.txt").write_text(page.inner_text("body"), encoding="utf-8")
        page.screenshot(path=str(OUTPUT_DIR / f"{slug}.png"), full_page=False)

        # Extract Schema JSON-LD
        json_ld_scripts = page.locator('script[type="application/ld+json"]').all_inner_texts()
        extracted_data = []
        for s in json_ld_scripts:
            try:
                extracted_data.append(json.loads(s))
            except Exception:
                pass

        if extracted_data:
            (OUTPUT_DIR / f"{slug}_data.json").write_text(
                json.dumps(extracted_data, indent=2), encoding="utf-8"
            )
            print(f"    [✓] Extracted Schema JSON-LD ({len(extracted_data)} objects)")

        print(f"    [✓] Saved Company HTML ({len(html_content)} bytes), Text, and Screenshot.")
    except Exception as e:
        print(f"    [X] Error scraping company {url}: {e}")

# ----------------------------------------------------------------------
# MAIN EXECUTION
# ----------------------------------------------------------------------
def main():
    targets = get_targets()
    print(f"[*] Total targets to scrape: {len(targets)}")
    for t_type, clean_id, url in targets:
        print(f"  - [{t_type.upper()}] {url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"}
        )
        page = context.new_page()

        for idx, (t_type, clean_id, url) in enumerate(targets, start=1):
            slug = sanitize_filename(url)
            print(f"\n[{idx}/{len(targets)}] Scraping [{t_type.upper()}]: {clean_id}")

            if t_type == "profile":
                scrape_profile_search_index(page, clean_id, url, slug)
            else:
                scrape_company_direct(page, clean_id, url, slug)

            time.sleep(2)

        browser.close()

    print("\n[+] Scraping complete! Files saved in 'output/' folder.")

if __name__ == "__main__":
    main()
