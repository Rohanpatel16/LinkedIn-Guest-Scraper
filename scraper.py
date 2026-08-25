import os
import re
import sys
import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

def normalize_target(target: str) -> str:
    """Converts inputs like 'satyanadella', 'company/github', or full URLs into standard LinkedIn URLs."""
    target = target.strip()
    if target.startswith("http://") or target.startswith("https://"):
        return target
    
    if target.startswith("company/"):
        return f"https://www.linkedin.com/{target}/"
    elif target.startswith("in/"):
        return f"https://www.linkedin.com/{target}/"
    else:
        # Default assumption: personal profile ID
        return f"https://www.linkedin.com/in/{target}/"

def sanitize_filename(url: str) -> str:
    slug = re.sub(r"https?://(www\.)?linkedin\.com/", "", url)
    slug = re.sub(r"[^a-zA-Z0-9_-]", "_", slug).strip("_")
    return slug or "linkedin_page"

def get_targets() -> list[str]:
    # 1. Check CLI arguments
    if len(sys.argv) > 1:
        raw_input = " ".join(sys.argv[1:])
        return [normalize_target(t) for t in re.split(r"[,\s]+", raw_input) if t.strip()]
    
    # 2. Check Environment Variable
    env_targets = os.environ.get("TARGET_INPUT", "").strip()
    if env_targets:
        return [normalize_target(t) for t in re.split(r"[,\n]+", env_targets) if t.strip()]
    
    # Default fallback demo targets
    return [
        "https://www.linkedin.com/in/satyanadella/",
        "https://www.linkedin.com/company/github/"
    ]

def scrape():
    targets = get_targets()
    print(f"[*] Total targets to scrape: {len(targets)}")
    for t in targets:
        print(f"  - {t}")

    with sync_playwright() as p:
        # Launch Chromium with stealth flags
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-infobars",
                "--disable-dev-shm-usage"
            ]
        )
        
        # Guest context: Uses a standard browser footprint with Google Referrer
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.google.com/"
            }
        )

        page = context.new_page()

        for idx, url in enumerate(targets, start=1):
            slug = sanitize_filename(url)
            print(f"\n[{idx}/{len(targets)}] Scraping: {url}")

            try:
                # Navigate
                response = page.goto(url, wait_until="domcontentloaded", timeout=60000)
                time.sleep(3)

                current_url = page.url
                status_code = response.status if response else "Unknown"
                print(f"    Status: {status_code} | Landed on: {current_url}")

                # Check for authwall
                if "authwall" in current_url or "login" in current_url:
                    print("    [!] Warning: Authwall triggered by LinkedIn.")

                # Scroll down slightly to trigger public DOM hydration
                page.mouse.wheel(0, 600)
                time.sleep(2)

                # 1. Extract JSON-LD (Rich public structured data if available)
                json_ld_scripts = page.locator('script[type="application/ld+json"]').all_inner_texts()
                if json_ld_scripts:
                    combined_json = []
                    for script_text in json_ld_scripts:
                        try:
                            combined_json.append(json.loads(script_text))
                        except Exception:
                            pass
                    if combined_json:
                        json_file = OUTPUT_DIR / f"{slug}_data.json"
                        json_file.write_text(json.dumps(combined_json, indent=2), encoding="utf-8")
                        print(f"    [✓] Extracted Schema JSON-LD ({len(combined_json)} objects)")

                # 2. Save Raw HTML
                html_content = page.content()
                html_file = OUTPUT_DIR / f"{slug}_raw.html"
                html_file.write_text(html_content, encoding="utf-8")

                # 3. Save Raw Text
                body_text = page.inner_text("body")
                txt_file = OUTPUT_DIR / f"{slug}_raw.txt"
                txt_file.write_text(body_text, encoding="utf-8")

                # 4. Save Screenshot for visual verification
                screenshot_file = OUTPUT_DIR / f"{slug}.png"
                page.screenshot(path=str(screenshot_file), full_page=False)

                print(f"    [✓] Saved Raw HTML ({len(html_content)} bytes), Text, and Screenshot.")

            except Exception as e:
                print(f"    [X] Error scraping {url}: {e}")

            time.sleep(3)

        browser.close()
        print("\n[+] Scraping complete! Files saved in 'output/' folder.")

if __name__ == "__main__":
    scrape()
