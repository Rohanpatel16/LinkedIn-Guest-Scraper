import os
import re
import sys
import json
import time
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
    
    return [normalize_target("company/tigihr"), normalize_target("in/satyanadella")]

# ----------------------------------------------------------------------
# 1. COMPANY SCRAPER (Deep DOM & Schema.org Extraction)
# ----------------------------------------------------------------------
def scrape_company(clean_id: str, url: str, slug: str):
    from playwright.sync_api import sync_playwright
    print(f"[*] Scraping Company directly: {url}")
    
    company_data = {
        "id": clean_id,
        "type": "company",
        "url": url,
        "title": "",
        "headline": "",
        "tagline": "",
        "follower_count": "",
        "website": "",
        "industry": "",
        "company_size": "",
        "company_type": "",
        "founded": "",
        "headquarters": "",
        "specialties": [],
        "about": "",
        "locations": [],
        "job_postings": [],
        "logo_url": ""
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.google.com/"}
        )
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        time.sleep(2)

        # Scroll to load dynamic company modules
        for _ in range(2):
            page.mouse.wheel(0, 600)
            time.sleep(1)

        raw_html = page.content()
        (OUTPUT_DIR / f"{slug}_raw.html").write_text(raw_html, encoding="utf-8")
        soup = BeautifulSoup(raw_html, "html.parser")

        # 1. Extract Top Card Details
        h1 = soup.find("h1")
        if h1: company_data["title"] = h1.get_text(strip=True)
        
        headline_el = soup.find(class_=re.compile(r"top-card-layout__headline"))
        if headline_el: company_data["headline"] = headline_el.get_text(strip=True)
        
        tagline_el = soup.find(class_=re.compile(r"top-card-layout__second-subline"))
        if tagline_el: company_data["tagline"] = tagline_el.get_text(strip=True)

        followers_el = soup.find(class_=re.compile(r"top-card-layout__first-subline"))
        if followers_el:
            f_match = re.search(r"([\d,]+\s*followers)", followers_el.get_text())
            if f_match: company_data["follower_count"] = f_match.group(1).strip()

        # 2. Extract About Us Overview DL table
        about_desc = soup.find("p", {"data-test-id": "about-us__description"})
        if about_desc: company_data["about"] = about_desc.get_text(separator="\n", strip=True)

        website_dd = soup.find("div", {"data-test-id": "about-us__website"})
        if website_dd and website_dd.find("a"):
            company_data["website"] = website_dd.find("a").get("href", "").strip()

        industry_dd = soup.find("div", {"data-test-id": "about-us__industry"})
        if industry_dd and industry_dd.find("dd"):
            company_data["industry"] = industry_dd.find("dd").get_text(strip=True)

        size_dd = soup.find("div", {"data-test-id": "about-us__size"})
        if size_dd and size_dd.find("dd"):
            company_data["company_size"] = size_dd.find("dd").get_text(strip=True)

        hq_dd = soup.find("div", {"data-test-id": "about-us__headquarters"})
        if hq_dd and hq_dd.find("dd"):
            company_data["headquarters"] = hq_dd.find("dd").get_text(strip=True)

        type_dd = soup.find("div", {"data-test-id": "about-us__organizationType"})
        if type_dd and type_dd.find("dd"):
            company_data["company_type"] = type_dd.find("dd").get_text(strip=True)

        founded_dd = soup.find("div", {"data-test-id": "about-us__foundedOn"})
        if founded_dd and founded_dd.find("dd"):
            company_data["founded"] = founded_dd.find("dd").get_text(strip=True)

        specialties_dd = soup.find("div", {"data-test-id": "about-us__specialties"})
        if specialties_dd and specialties_dd.find("dd"):
            raw_specs = specialties_dd.find("dd").get_text(strip=True)
            company_data["specialties"] = [s.strip() for s in re.split(r",\s*", raw_specs) if s.strip()]

        # 3. Extract Locations List
        loc_items = soup.find_all("div", id=re.compile(r"address-\d+"))
        for loc in loc_items:
            loc_text = " ".join([p.get_text(strip=True) for p in loc.find_all("p") if p.get_text(strip=True)])
            if loc_text and loc_text not in company_data["locations"]:
                company_data["locations"].append(loc_text)

        # 4. Extract Schema.org JSON-LD (Fill any remaining gaps)
        json_ld_scripts = page.locator('script[type="application/ld+json"]').all_inner_texts()
        for s in json_ld_scripts:
            try:
                parsed_schema = json.loads(s)
                items = parsed_schema.get("@graph", [parsed_schema]) if isinstance(parsed_schema, dict) else []
                for item in items:
                    t = item.get("@type")
                    if t == "Organization":
                        if not company_data["website"] and item.get("sameAs"):
                            company_data["website"] = item.get("sameAs")
                        if not company_data["tagline"] and item.get("slogan"):
                            company_data["tagline"] = item.get("slogan")
                        if item.get("logo", {}).get("contentUrl"):
                            company_data["logo_url"] = item["logo"]["contentUrl"]
                        if not company_data["company_size"] and item.get("numberOfEmployees", {}).get("value"):
                            company_data["company_size"] = f"{item['numberOfEmployees']['value']} employees"
                    elif t == "DiscussionForumPosting":
                        company_data["job_postings"].append({
                            "title": item.get("headline", ""),
                            "url": item.get("url", "")
                        })
            except Exception:
                pass

        browser.close()

    # Save to disk
    (OUTPUT_DIR / f"{slug}_data.json").write_text(json.dumps(company_data, indent=2), encoding="utf-8")
    
    # Print to Terminal
    print_terminal_result(company_data)
    
    # Write to GitHub Actions UI Summary
    write_to_summary(f"### 🏢 Company: `{company_data.get('title') or clean_id}`\n"
                     f"- **Website:** [{company_data['website']}]({company_data['website']})\n"
                     f"- **Industry:** {company_data['industry'] or 'N/A'}\n"
                     f"- **Headquarters:** {company_data['headquarters'] or 'N/A'}\n"
                     f"- **Size:** {company_data['company_size'] or 'N/A'}\n"
                     f"- **Open Roles:** {len(company_data['job_postings'])}\n\n"
                     f"```json\n{json.dumps(company_data, indent=2)}\n```")

# ----------------------------------------------------------------------
# 2. PROFILE SCRAPER (Strict ID Filter)
# ----------------------------------------------------------------------
NOISE_KEYWORDS = {
    "confidential", "self-employed", "freelance", "n/a", "unknown", "independent",
    "experience", "location", "education", "view", "connections", "present", "years"
}

def is_valid_company_name(name: str) -> bool:
    name = name.strip()
    if not name or len(name) < 2 or len(name) > 50: return False
    if name.lower() in NOISE_KEYWORDS: return False
    if any(char in name for char in ["…", "...", "\n", "\t", "@"]): return False
    if re.search(r"\b(passionate|learned|gained|across|building|bring|years|hands-on)\b", name, re.IGNORECASE): return False
    return True

def resolve_company_url(ddgs: DDGS, company_name: str) -> dict:
    if not is_valid_company_name(company_name):
        return {"name": company_name, "company_linkedin_url": None}
    clean_search = re.sub(r"\s+(?:Inc\.?|LLC|Pvt\.?\s*Ltd\.?|Ltd\.?)$", "", company_name, flags=re.IGNORECASE).strip()
    try:
        results = list(ddgs.text(f'site:linkedin.com/company "{clean_search}"', max_results=2))
        for res in results:
            if "linkedin.com/company/" in res.get("href", ""):
                return {"name": company_name, "company_linkedin_url": res.get("href")}
    except Exception:
        pass
    return {"name": company_name, "company_linkedin_url": None}

def scrape_profile(clean_id: str, linkedin_url: str, slug: str) -> dict:
    print(f"[*] Extracting verified data for profile: {clean_id}...")
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
    queries = [f"site:linkedin.com/in/{clean_id}", f'"linkedin.com/in/{clean_id}"']
    all_snippets = []
    detected_companies = []

    for q in queries:
        try:
            results = list(ddgs.text(q, max_results=5))
            for res in results:
                href = res.get("href", "")
                url_slug_match = re.search(r"linkedin\.com/in/([^/?#]+)", href, re.IGNORECASE)
                if not url_slug_match or url_slug_match.group(1).lower() != clean_id.lower():
                    continue

                profile_data["raw_search_hits"].append({
                    "query": q, "title": res.get("title", ""), "url": href, "snippet": res.get("body", "")
                })
                all_snippets.append(res.get("body", ""))
                
                raw_title = res.get("title", "").replace(" | LinkedIn", "").replace(" - LinkedIn", "").strip()
                if " - " in raw_title and not profile_data["headline"]:
                    parts = [p.strip() for p in raw_title.split(" - ") if p.strip()]
                    profile_data["name"] = parts[0]
                    profile_data["headline"] = " - ".join(parts[1:])
                    if len(parts) >= 3 and is_valid_company_name(parts[-1]):
                        detected_companies.append(parts[-1])
                elif raw_title and profile_data["name"] == clean_id:
                    profile_data["name"] = raw_title
        except Exception:
            continue

    combined_snippet = " \n ".join(all_snippets)
    if combined_snippet:
        conn_m = re.search(r"(\d+\+?\s*connections)", combined_snippet, re.IGNORECASE)
        if conn_m: profile_data["connections"] = conn_m.group(1).strip()

        loc_m = re.search(r"(?:Location|based in)[:\s]+([^·\n]+)", combined_snippet, re.IGNORECASE)
        if loc_m: profile_data["location"] = loc_m.group(1).strip()

        exp_matches = re.findall(r"(?:Experience|Current|Past)[:\s]+([^·\n]+)", combined_snippet, re.IGNORECASE)
        for exp in exp_matches:
            for sub in re.split(r",\s*|;\s*", exp):
                if is_valid_company_name(sub.strip()) and sub.strip() not in detected_companies:
                    detected_companies.append(sub.strip())

        edu_matches = re.findall(r"(?:Education)[:\s]+([^·\n]+)", combined_snippet, re.IGNORECASE)
        for edu in edu_matches:
            for sub in re.split(r",\s*|;\s*", edu):
                if sub.strip() and len(sub.strip()) < 60 and sub.strip() not in profile_data["educations"]:
                    profile_data["educations"].append(sub.strip())

        clean_bio = re.sub(r"^[A-Za-z]+\s+\d{1,2},\s+\d{4}\s*-\s*", "", combined_snippet)
        clean_bio = re.sub(r"·\s*(?:Experience|Location|connections|Education|View).*$", "", clean_bio, flags=re.IGNORECASE).strip()
        profile_data["about_snippet"] = clean_bio

    resolved = [resolve_company_url(ddgs, c) for c in detected_companies]
    if resolved:
        profile_data["current_company"] = resolved[0]
        profile_data["past_companies"] = resolved[1:]

    (OUTPUT_DIR / f"{slug}_data.json").write_text(json.dumps(profile_data, indent=2), encoding="utf-8")
    print_terminal_result(profile_data)
    
    write_to_summary(f"### 👤 Profile: `{profile_data.get('name')}`\n"
                     f"- **Headline:** {profile_data.get('headline') or 'N/A'}\n"
                     f"- **Location:** {profile_data.get('location') or 'N/A'}\n"
                     f"- **Connections:** {profile_data.get('connections') or 'N/A'}\n\n"
                     f"```json\n{json.dumps(profile_data, indent=2)}\n```")
    return profile_data

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
