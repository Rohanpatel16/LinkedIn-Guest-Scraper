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
# 1. PROFILE SCRAPER (Multi-Engine Search Cascade with Ad-Filtering)
# ----------------------------------------------------------------------
def parse_profile_snippet(raw_title: str, raw_snippet: str, clean_id: str, linkedin_url: str) -> dict:
    """Parses raw search engine title and snippet into clean structured profile data."""
    data = {
        "id": clean_id,
        "type": "profile",
        "url": linkedin_url,
        "name": clean_id,
        "headline": "",
        "current_company_or_role": "",
        "location": "",
        "education": "",
        "summary": raw_snippet,
        "raw_title": raw_title,
        "raw_snippet": raw_snippet
    }

    # Clean title (removes LinkedIn suffix)
    cleaned_title = re.sub(r"\s*[-|]\s*LinkedIn.*$", "", raw_title, flags=re.IGNORECASE).strip()
    
    # Title format typically: "Name - Headline - Company" or "Name - Headline"
    if " - " in cleaned_title:
        parts = [p.strip() for p in cleaned_title.split(" - ") if p.strip()]
        data["name"] = parts[0]
        data["headline"] = " - ".join(parts[1:])
        if len(parts) >= 3:
            data["current_company_or_role"] = parts[-1]
    elif cleaned_title:
        data["name"] = cleaned_title

    # Extract Location
    loc_match = re.search(r"(?:Location|Location:)\s*([^·\n]+)", raw_snippet, re.IGNORECASE)
    if loc_match:
        data["location"] = loc_match.group(1).strip()

    # Extract Experience / Current Role
    exp_match = re.search(r"(?:Experience|Current)[:\s]+([^·\n]+)", raw_snippet, re.IGNORECASE)
    if exp_match:
        data["current_company_or_role"] = exp_match.group(1).strip()

    # Extract Education
    edu_match = re.search(r"(?:Education)[:\s]+([^·\n]+)", raw_snippet, re.IGNORECASE)
    if edu_match:
        data["education"] = edu_match.group(1).strip()

    return data

def scrape_profile_search_index(page, clean_id: str, linkedin_url: str, slug: str) -> dict:
    print(f"[*] Extracting organic profile data for: {clean_id}")
    query = f"site:linkedin.com/in/{clean_id}"
    
    raw_title = ""
    raw_snippet = ""
    found_engine = None

    # ENGINE 1: Google Search
    try:
        google_url = f"https://www.google.com/search?q={urllib.parse.quote(query)}&hl=en"
        page.goto(google_url, wait_until="domcontentloaded", timeout=20000)
        time.sleep(1.5)
        
        # Check for Google Consent modal
        consent_btn = page.locator('button:has-text("Accept all"), button:has-text("I agree"), button:has-text("Stay signed out")')
        if consent_btn.count() > 0:
            consent_btn.first.click()
            time.sleep(1)

        # Look strictly for organic LinkedIn result links
        organic_cards = page.locator('#search .g, div[data-sokoban-container]').all()
        for card in organic_cards:
            link = card.locator('a[href*="linkedin.com/in/"]').first
            if link.count() > 0:
                h3 = card.locator('h3').first
                snippet_el = card.locator('div[style*="-webkit-line-clamp"], .VwiC3b').first
                if h3.count() > 0:
                    raw_title = h3.inner_text().strip()
                    raw_snippet = snippet_el.inner_text().strip() if snippet_el.count() > 0 else ""
                    found_engine = "Google"
                    break
    except Exception as e:
        print(f"    [!] Google query skipped: {e}")

    # ENGINE 2: Bing Search (Fallback)
    if not raw_title:
        try:
            bing_url = f"https://www.bing.com/search?q={urllib.parse.quote(query)}&setlang=en"
            page.goto(bing_url, wait_until="domcontentloaded", timeout=20000)
            time.sleep(1.5)

            algo_cards = page.locator('#b_results .b_algo').all()
            for card in algo_cards:
                link = card.locator('a[href*="linkedin.com/in/"]').first
                if link.count() > 0:
                    h2 = card.locator('h2').first
                    caption = card.locator('.b_caption p').first
                    if h2.count() > 0:
                        raw_title = h2.inner_text().strip()
                        raw_snippet = caption.inner_text().strip() if caption.count() > 0 else ""
                        found_engine = "Bing"
                        break
        except Exception as e:
            print(f"    [!] Bing fallback skipped: {e}")

    # ENGINE 3: DuckDuckGo (Fallback with strict ad exclusion)
    if not raw_title:
        try:
            ddg_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
            page.goto(ddg_url, wait_until="domcontentloaded", timeout=20000)
            time.sleep(1.5)

            results = page.locator('.result:not(.result--ad)').all()
            for res_el in results:
                link = res_el.locator('a[href*="linkedin.com/in/"], a.result__url[href*="linkedin.com/in"]').first
                if link.count() > 0:
                    t_el = res_el.locator('.result__title').first
                    s_el = res_el.locator('.result__snippet').first
                    if t_el.count() > 0:
                        raw_title = t_el.inner_text().strip()
                        raw_snippet = s_el.inner_text().strip() if s_el.count() > 0 else ""
                        found_engine = "DuckDuckGo"
                        break
        except Exception as e:
            print(f"    [!] DuckDuckGo fallback skipped: {e}")

    # Parse extracted strings
    profile_data = parse_profile_snippet(raw_title, raw_snippet, clean_id, linkedin_url)
    profile_data["search_engine_source"] = found_engine or "None"

    # Save output
    (OUTPUT_DIR / f"{slug}_data.json").write_text(json.dumps(profile_data, indent=2), encoding="utf-8")
    (OUTPUT_DIR / f"{slug}_raw.txt").write_text(
        f"Name: {profile_data['name']}\nHeadline: {profile_data['headline']}\nLocation: {profile_data['location']}\nSummary: {profile_data['summary']}\n",
        encoding="utf-8"
    )

    if found_engine:
        print(f"    [✓] Extracted via {found_engine}: Name='{profile_data['name']}' | Headline='{profile_data['headline']}'")
    else:
        print(f"    [!] Warning: No organic search record found for ID '{clean_id}'. Saved placeholder entry.")

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
# MAIN RUNNER
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
