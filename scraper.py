import os
import re
import sys
import json
import time
from pathlib import Path
from duckduckgo_search import DDGS
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
# 1. PROFILE EXTRACTION (Via Direct API - Zero Ads, Zero Cookies)
# ----------------------------------------------------------------------
def scrape_profile_api(clean_id: str, linkedin_url: str, slug: str) -> dict:
    print(f"[*] Fetching Profile via Search API for: {clean_id}")
    
    profile_data = {
        "id": clean_id,
        "type": "profile",
        "url": linkedin_url,
        "name": clean_id,
        "headline": "",
        "current_company_or_role": "",
        "location": "",
        "summary": "",
        "raw_title": "",
        "raw_snippet": ""
    }

    try:
        ddgs = DDGS()
        # Query specifically for this LinkedIn handle
        queries = [
            f"site:linkedin.com/in/{clean_id}",
            f'"{clean_id}" site:linkedin.com/in',
            f"{clean_id} linkedin profile"
        ]

        found_result = None
        for q in queries:
            results = list(ddgs.text(q, max_results=5))
            for res in results:
                href = res.get("href", "")
                if "linkedin.com/in/" in href:
                    found_result = res
                    break
            if found_result:
                break

        if found_result:
            raw_title = found_result.get("title", "")
            raw_snippet = found_result.get("body", "")

            profile_data["raw_title"] = raw_title
            profile_data["raw_snippet"] = raw_snippet
            profile_data["summary"] = raw_snippet

            # Parse title: "Name - Headline - Company | LinkedIn"
            cleaned_title = re.sub(r"\s*[-|]\s*LinkedIn.*$", "", raw_title, flags=re.IGNORECASE).strip()
            if " - " in cleaned_title:
                parts = [p.strip() for p in cleaned_title.split(" - ") if p.strip()]
                profile_data["name"] = parts[0]
                profile_data["headline"] = " - ".join(parts[1:])
                if len(parts) >= 3:
                    profile_data["current_company_or_role"] = parts[-1]
            elif cleaned_title:
                profile_data["name"] = cleaned_title

            # Parse Location and Experience from snippet
            loc_match = re.search(r"(?:Location|based in)[:\s]+([^·\n]+)", raw_snippet, re.IGNORECASE)
            if loc_match:
                profile_data["location"] = loc_match.group(1).strip()

            exp_match = re.search(r"(?:Experience|Current)[:\s]+([^·\n]+)", raw_snippet, re.IGNORECASE)
            if exp_match:
                profile_data["current_company_or_role"] = exp_match.group(1).strip()

            print(f"    [✓] Extracted: Name='{profile_data['name']}' | Headline='{profile_data['headline']}'")
        else:
            print(f"    [!] No public record found on search index for '{clean_id}'")

    except Exception as e:
        print(f"    [X] Search API Error: {e}")

    # Save outputs
    (OUTPUT_DIR / f"{slug}_data.json").write_text(json.dumps(profile_data, indent=2), encoding="utf-8")
    (OUTPUT_DIR / f"{slug}_raw.txt").write_text(
        f"Title: {profile_data['raw_title']}\nSnippet: {profile_data['raw_snippet']}\n",
        encoding="utf-8"
    )

    return profile_data

# ----------------------------------------------------------------------
# 2. COMPANY SCRAPER (Playwright Direct - 100% Working)
# ----------------------------------------------------------------------
def scrape_company_direct(page, clean_id: str, url: str, slug: str):
    print(f"[*] Scraping Company directly: {url}")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        time.sleep(2)

        html_content = page.content()
        (OUTPUT_DIR / f"{slug}_raw.html").write_text(html_content, encoding="utf-8")
        (OUTPUT_DIR / f"{slug}_raw.txt").write_text(page.inner_text("body"), encoding="utf-8")
        page.screenshot(path=str(OUTPUT_DIR / f"{slug}.png"), full_page=False)

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
    print(f"[*] Total targets: {len(targets)}")
    for t_type, clean_id, url in targets:
        print(f"  - [{t_type.upper()}] {url}")

    # Launch Playwright only if there are company targets
    has_companies = any(t[0] == "company" for t in targets)
    
    browser = None
    page = None
    playwright_instance = None

    if has_companies:
        playwright_instance = sync_playwright().start()
        browser = playwright_instance.chromium.launch(
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
            scrape_profile_api(clean_id, url, slug)
        else:
            scrape_company_direct(page, clean_id, url, slug)

        time.sleep(1.5)

    if browser:
        browser.close()
        playwright_instance.stop()

    print("\n[+] Done! Output files saved in 'output/' directory.")

if __name__ == "__main__":
    main()
