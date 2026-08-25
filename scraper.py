import os
import re
import sys
import json
import time
import requests
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
# 1. PROFILE SCRAPER (Cookie-Free Gateway)
# ----------------------------------------------------------------------
def parse_markdown_profile(md_text: str, linkedin_url: str, clean_id: str) -> dict:
    """Parses profile markdown into clean structured JSON."""
    data = {
        "id": clean_id,
        "type": "profile",
        "url": linkedin_url,
        "name": "",
        "headline": "",
        "location": "",
        "about": "",
        "raw_content": md_text
    }

    # Extract Name and Title from Header
    title_match = re.search(r"^Title:\s*(.+)$", md_text, re.MULTILINE)
    if title_match:
        full_title = title_match.group(1).replace(" | LinkedIn", "").strip()
        parts = [p.strip() for p in full_title.split(" - ") if p.strip()]
        if len(parts) >= 1:
            data["name"] = parts[0]
        if len(parts) >= 2:
            data["headline"] = " - ".join(parts[1:])

    # Extract About Section
    about_match = re.search(r"(?:About|Summary)\s*\n+([\s\S]*?)(?=\n#{1,3}\s|\nExperience|\nEducation|\Z)", md_text, re.IGNORECASE)
    if about_match:
        data["about"] = about_match.group(1).strip()

    return data

def scrape_profile_no_cookies(clean_id: str, url: str, slug: str):
    """Scrapes personal LinkedIn profile without login or cookies."""
    print(f"[*] Scraping Profile via Public Gateway: {url}")
    gateway_url = f"https://r.jina.ai/{url}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "X-Target-Selector": "main, body"
    }

    try:
        res = requests.get(gateway_url, headers=headers, timeout=40)
        if res.status_code == 200 and len(res.text) > 100:
            md_content = res.text
            
            # Save Raw Markdown
            (OUTPUT_DIR / f"{slug}_raw.md").write_text(md_content, encoding="utf-8")
            (OUTPUT_DIR / f"{slug}_raw.txt").write_text(md_content, encoding="utf-8")

            # Parse structured JSON
            parsed_data = parse_markdown_profile(md_content, url, clean_id)
            (OUTPUT_DIR / f"{slug}_data.json").write_text(json.dumps(parsed_data, indent=2), encoding="utf-8")
            
            print(f"    [✓] Success! Name: '{parsed_data['name']}' | Headline: '{parsed_data['headline']}'")
        else:
            print(f"    [X] Gateway returned status {res.status_code}")
    except Exception as e:
        print(f"    [X] Error scraping profile {clean_id}: {e}")

# ----------------------------------------------------------------------
# 2. COMPANY SCRAPER (Playwright Direct Guest Mode)
# ----------------------------------------------------------------------
def scrape_company_direct(page, url: str, slug: str):
    """Scrapes company profile directly using guest browser automation."""
    print(f"[*] Scraping Company: {url}")
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        time.sleep(2)

        # Save Raw HTML & Text
        html_content = page.content()
        (OUTPUT_DIR / f"{slug}_raw.html").write_text(html_content, encoding="utf-8")
        (OUTPUT_DIR / f"{slug}_raw.txt").write_text(page.inner_text("body"), encoding="utf-8")
        page.screenshot(path=str(OUTPUT_DIR / f"{slug}.png"), full_page=False)

        # Extract Schema JSON-LD (Rich structured company info)
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
# MAIN SCRAPER RUNNER
# ----------------------------------------------------------------------
def main():
    targets = get_targets()
    print(f"[*] Total targets to scrape: {len(targets)}")
    for t_type, clean_id, url in targets:
        print(f"  - [{t_type.upper()}] {url}")

    # Initialize browser for company pages
    has_companies = any(t[0] == "company" for t in targets)
    
    browser = None
    context = None
    page = None
    playwright_instance = None

    if has_companies:
        from playwright.sync_api import sync_playwright
        playwright_instance = sync_playwright().start()
        browser = playwright_instance.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.google.com/"}
        )
        page = context.new_page()

    for idx, (t_type, clean_id, url) in enumerate(targets, start=1):
        slug = sanitize_filename(url)
        print(f"\n[{idx}/{len(targets)}] Scraping [{t_type.upper()}]: {clean_id}")

        if t_type == "profile":
            scrape_profile_no_cookies(clean_id, url, slug)
        else:
            scrape_company_direct(page, url, slug)

        time.sleep(2)

    if browser:
        browser.close()
        playwright_instance.stop()

    print("\n[+] All tasks complete! Output files saved to 'output/' folder.")

if __name__ == "__main__":
    main()
