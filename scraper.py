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
    """Returns (target_type, clean_id, full_url)"""
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

def scrape_search_fallback(page, clean_id: str, slug: str) -> dict:
    """Fallback engine: Extracts public profile data from search index when LinkedIn blocks direct IP."""
    print(f"    [*] Running Search Index Fallback for profile: {clean_id}...")
    search_query = f"site:linkedin.com/in/{clean_id}"
    search_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(search_query)}"
    
    profile_data = {
        "id": clean_id,
        "type": "profile",
        "url": f"https://www.linkedin.com/in/{clean_id}/",
        "source": "search_index_fallback",
        "title": "",
        "snippet": ""
    }

    try:
        page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)
        
        # Grab first search result
        first_result = page.locator(".result__body").first
        if first_result.count() > 0:
            title = first_result.locator(".result__title").inner_text().strip()
            snippet = first_result.locator(".result__snippet").inner_text().strip()
            
            profile_data["title"] = title
            profile_data["snippet"] = snippet
            print(f"    [✓] Extracted: {title}")
        else:
            print("    [!] No public search index results found.")

        # Save fallback JSON
        json_file = OUTPUT_DIR / f"{slug}_fallback_data.json"
        json_file.write_text(json.dumps(profile_data, indent=2), encoding="utf-8")
        
    except Exception as e:
        print(f"    [X] Fallback extraction failed: {e}")

    return profile_data

def scrape():
    targets = get_targets()
    print(f"[*] Total targets to scrape: {len(targets)}")
    for t_type, clean_id, url in targets:
        print(f"  - [{t_type.upper()}] {url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage"
            ]
        )

        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.google.com/"
            }
        )

        page = context.new_page()

        for idx, (t_type, clean_id, url) in enumerate(targets, start=1):
            slug = sanitize_filename(url)
            print(f"\n[{idx}/{len(targets)}] Scraping [{t_type.upper()}]: {url}")

            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                time.sleep(2)

                current_url = page.url
                status = response.status if response else "Unknown"
                print(f"    Status: {status} | Landed on: {current_url}")

                # Save raw HTML & Text
                html_content = page.content()
                (OUTPUT_DIR / f"{slug}_raw.html").write_text(html_content, encoding="utf-8")
                (OUTPUT_DIR / f"{slug}_raw.txt").write_text(page.inner_text("body"), encoding="utf-8")
                page.screenshot(path=str(OUTPUT_DIR / f"{slug}.png"), full_page=False)

                # Extract Schema JSON-LD (works on company pages)
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

                # If profile was blocked by authwall (status 999 or redirected to /authwall), run fallback
                if t_type == "profile" and (status == 999 or "authwall" in current_url):
                    scrape_search_fallback(page, clean_id, slug)

                print(f"    [✓] Processed {url}")

            except Exception as e:
                print(f"    [X] Error scraping {url}: {e}")

            time.sleep(2)

        browser.close()
        print("\n[+] Scraping complete! Files saved in 'output/' folder.")

if __name__ == "__main__":
    scrape()
