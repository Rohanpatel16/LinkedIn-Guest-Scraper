import os
import re
import sys
import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

def normalize_target(target: str) -> tuple[str, str]:
    """Returns (target_type, full_url)"""
    target = target.strip()
    
    if "linkedin.com/company/" in target:
        return "company", target
    elif "linkedin.com/in/" in target:
        return "profile", target

    if target.startswith("company/"):
        return "company", f"https://www.linkedin.com/{target}/"
    elif target.startswith("in/"):
        return "profile", f"https://www.linkedin.com/{target}/"
    else:
        # Default assumption: personal profile
        return "profile", f"https://www.linkedin.com/in/{target}/"

def sanitize_filename(url: str) -> str:
    slug = re.sub(r"https?://(www\.)?linkedin\.com/", "", url)
    slug = re.sub(r"[^a-zA-Z0-9_-]", "_", slug).strip("_")
    return slug or "linkedin_page"

def get_targets() -> list[tuple[str, str]]:
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

def scrape():
    targets = get_targets()
    print(f"[*] Total targets to scrape: {len(targets)}")
    for t_type, url in targets:
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

        for idx, (t_type, url) in enumerate(targets, start=1):
            slug = sanitize_filename(url)
            print(f"\n[{idx}/{len(targets)}] Scraping [{t_type.upper()}]: {url}")

            # Different browser fingerprint depending on Company vs Profile
            if t_type == "profile":
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
                    locale="en-US",
                    extra_http_headers={
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.5",
                        "From": "googlebot(at)googlebot.com"
                    }
                )
            else:
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

            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                time.sleep(2)

                current_url = page.url
                status = response.status if response else "Unknown"
                print(f"    Status: {status} | Landed on: {current_url}")

                # Save raw HTML
                html_content = page.content()
                (OUTPUT_DIR / f"{slug}_raw.html").write_text(html_content, encoding="utf-8")

                # Save raw Text
                body_text = page.inner_text("body")
                (OUTPUT_DIR / f"{slug}_raw.txt").write_text(body_text, encoding="utf-8")

                # Extract Structured JSON-LD (if available)
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

                # Save visual screenshot
                page.screenshot(path=str(OUTPUT_DIR / f"{slug}.png"), full_page=False)
                print(f"    [✓] Saved Raw HTML ({len(html_content)} bytes), Text, and Screenshot.")

            except Exception as e:
                print(f"    [X] Error scraping {url}: {e}")

            context.close()
            time.sleep(2)

        browser.close()
        print("\n[+] Scraping complete! Files saved in 'output/' folder.")

if __name__ == "__main__":
    scrape()
