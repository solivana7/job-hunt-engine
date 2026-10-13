"""ATS form filler. Greenhouse/Lever get Playwright autofill; everything else
is flagged manual_required. Default is --dry-run: the form is filled and a
screenshot is saved, but nothing is ever submitted.

Even with --no-dry-run, the browser stays open for the candidate to review and press
the submit button herself — the engine never clicks submit.
"""
from __future__ import annotations

import os
import re

GREENHOUSE_RE = re.compile(r"(greenhouse\.io|boards\.greenhouse)", re.IGNORECASE)
LEVER_RE = re.compile(r"(lever\.co|jobs\.lever)", re.IGNORECASE)
LINKEDIN_RE = re.compile(r"linkedin\.com", re.IGNORECASE)
MANUAL_RES = re.compile(r"(myworkdayjobs|taleo|successfactors|icims)", re.IGNORECASE)


def detect_ats(url: str) -> str:
    if GREENHOUSE_RE.search(url):
        return "greenhouse"
    if LEVER_RE.search(url):
        return "lever"
    if LINKEDIN_RE.search(url):
        return "linkedin"
    if MANUAL_RES.search(url):
        return "manual"
    return "manual"


def fill(app: dict, posting: dict, candidate: dict, dry_run: bool = True) -> str:
    """Returns a status string. May raise if Playwright/browsers are missing."""
    from playwright.sync_api import sync_playwright  # imported lazily

    url = posting["url"]
    ats = detect_ats(url)
    if ats not in ("greenhouse", "lever"):
        return f"manual_required:{ats}"

    headless = os.environ.get("PLAYWRIGHT_HEADLESS", "false").lower() == "true"
    linkedin = candidate["linkedin"].rstrip("/")
    if linkedin.startswith("http"):
        linkedin = re.sub(r"^https?://(www\.)?", "", linkedin)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1500)

        # Greenhouse boards show a JD page with an Apply button that opens the
        # form (often inside an iframe). Click through when present.
        try:
            apply_btn = page.get_by_role("button", name=re.compile(r"^apply", re.IGNORECASE))
            if apply_btn.count():
                apply_btn.first.click()
                page.wait_for_timeout(2000)
        except Exception:
            pass
        frames = [page] + page.frames[1:]

        def first_locator(css: str):
            for fr in frames:
                loc = fr.locator(css)
                try:
                    if loc.count():
                        return loc.first
                except Exception:
                    continue
            return None

        # Greenhouse and Lever converge on these standard fields.
        fields = {
            "first_name": candidate["name"].split()[0],
            "last_name": candidate["name"].split()[-1],
            "email": candidate["email"],
            "phone": candidate["phone"],
        }
        for fid, val in fields.items():
            loc = first_locator(f"#{fid}") or first_locator(f'[name="{fid}"]')
            if loc:
                loc.fill(val)

        # LinkedIn: greenhouse custom question or lever field
        for sel in ('input[name*="linkedin" i]', '#linkedin_profile',
                    'input[aria-label*="linkedin" i]'):
            loc = first_locator(sel)
            if loc:
                loc.fill(linkedin)
                break

        # Resume upload
        if app.get("resume_path"):
            loc = first_locator("input[type=file]")
            if loc:
                loc.set_input_files(app["resume_path"])

        if dry_run:
            shot = f"artifacts/dryrun_app{app['id']}.png"
            page.screenshot(path=shot, full_page=False)
            browser.close()
            return f"dry_run_filled:{ats} (screenshot: {shot})"

        # Live mode: pause for human review. The human presses submit.
        print("\n>>> Browser is open with the form filled. Review it, press SUBMIT "
              "yourself if it looks right, then return here.")
        input(">>> Press Enter after you have submitted (or closed the tab to cancel)... ")
        browser.close()
        return f"human_submitted:{ats}"
