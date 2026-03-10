#!/usr/bin/env python3
"""
Kit.com Creator Network Scraper
- Fully automated, headless
- Auto-login with 1Password TOTP
- Discovers account switch URLs from Kit's switcher dropdown
- Only scrapes accounts in allowed_accounts (is_active=true)
- Saves data to Railway Postgres
- Reports results to Telegram
"""

import os
import sys
import json
import time
import subprocess
import traceback
import psycopg2
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ── Config ──────────────────────────────────────────────────────────────────
SESSION_FILE  = Path(__file__).parent / "sessions" / "kit_account" / "state.json"
SCREENSHOT_DIR = Path(__file__).parent / "screenshots"
DATABASE_URL  = os.environ.get("KIT_DATABASE_URL", "")
OP_TOKEN      = os.environ.get("OP_SERVICE_ACCOUNT_TOKEN", "")
TELEGRAM_CHAT_ID = "-1003810267230"
TELEGRAM_TOPIC   = "4"

# ── Helpers ──────────────────────────────────────────────────────────────────
def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def get_totp():
    env = os.environ.copy()
    r = subprocess.run(
        ["op", "item", "get", "kit.com", "--vault", "Automation Access", "--otp"],
        capture_output=True, text=True, env=env
    )
    code = r.stdout.strip()
    if not code or len(code) != 6:
        raise Exception(f"Failed to get TOTP: {r.stderr}")
    return code

def get_credentials():
    env = os.environ.copy()
    r = subprocess.run(
        ["op", "item", "get", "kit.com", "--vault", "Automation Access",
         "--fields", "label=username,label=password", "--reveal"],
        capture_output=True, text=True, env=env
    )
    parts = r.stdout.strip().split(",")
    if len(parts) < 2:
        raise Exception(f"Failed to get credentials: {r.stderr}")
    return parts[0].strip(), parts[1].strip()

def screenshot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(SCREENSHOT_DIR / f"{name}.png"))
    except:
        pass

# ── Database ─────────────────────────────────────────────────────────────────
def get_active_accounts(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT account_name FROM allowed_accounts WHERE is_active = true ORDER BY id")
        return [row[0] for row in cur.fetchall()]

def save_to_db(conn, account_name, recommending_me, my_recommendations):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO referral_data (date, account_name, recommending_me, my_recommendations)
               VALUES (%s, %s, %s, %s)""",
            (datetime.utcnow(), account_name,
             json.dumps(recommending_me), json.dumps(my_recommendations))
        )
    conn.commit()
    log(f"  Saved to DB: {account_name}")

# ── Login ─────────────────────────────────────────────────────────────────────
def login(page, context):
    log("Logging in to Kit.com...")
    email, password = get_credentials()

    page.goto("https://app.kit.com/users/login", wait_until="domcontentloaded", timeout=45000)
    time.sleep(2)

    page.fill('input[name="user[email]"]', email)
    time.sleep(0.3)
    page.fill('input[name="user[password]"]', password)
    time.sleep(0.3)
    page.click('button:has-text("Log in")', timeout=10000)
    time.sleep(5)

    if "verify-token" in page.url or "two_factor" in page.url:
        log("2FA required — fetching TOTP from 1Password...")
        totp = get_totp()
        log(f"Got TOTP: {totp[:2]}****")
        page.fill('input[name="token"]', totp)
        try:
            page.evaluate('document.querySelector(\'input[name="remember_device"]\').checked = true')
        except:
            pass
        page.click('input[type="submit"]')
        time.sleep(5)

    if "login" in page.url or "verify" in page.url:
        screenshot(page, "login_failed")
        raise Exception(f"Login failed — still at: {page.url}")

    log(f"Logged in — at: {page.url}")
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(SESSION_FILE))
    log("Session saved")

def ensure_logged_in(page, context):
    page.goto("https://app.kit.com/dashboard", wait_until="domcontentloaded", timeout=45000)
    time.sleep(2)
    if "login" in page.url or "verify" in page.url:
        log("Session expired — re-logging in")
        login(page, context)
    else:
        log(f"Session valid — at: {page.url}")

# ── Account Switcher ──────────────────────────────────────────────────────────
NAV_SKIP = {
    "Grow", "Send", "Automate", "Earn", "Help", "Menu", "Bulk Notifications",
    "Set up Insights", "Previous Step", "Next Step", "Go to Recommendations",
    "Recommend", "Not interested", "Upgrade", "Start trial"
}

def open_switcher(page):
    """
    Click the account switcher button so the dropdown is visually open.
    Returns True if a visible switch link is now clickable.
    """
    page.goto("https://app.kit.com/dashboard", wait_until="domcontentloaded", timeout=45000)
    time.sleep(2)

    # Click buttons until a switch link becomes visible (not just present in DOM)
    for btn in page.query_selector_all("button"):
        txt = btn.inner_text().strip()
        if not txt or txt in NAV_SKIP or "notification" in txt.lower() or len(txt) < 2:
            continue
        try:
            btn.click()
            time.sleep(1.5)
            # Check visibility — is_visible() confirms the element is actually rendered
            links = page.query_selector_all('a[href*="/account_users/"][href*="/switch"]')
            visible = [l for l in links if l.is_visible()]
            if visible:
                return True
            page.keyboard.press("Escape")
            time.sleep(0.5)
        except:
            continue

    return False

def discover_switch_links(page):
    """
    Open the switcher and return dict of {account_name: href_path}.
    Uses POST-based switch links (clicking required, not direct navigation).
    """
    if not open_switcher(page):
        screenshot(page, "switcher_not_found")
        log("WARNING: Could not open account switcher dropdown")
        return {}
    links = page.query_selector_all('a[href*="/account_users/"][href*="/switch"]')
    accounts = {}
    for link in links:
        name = " ".join(link.inner_text().strip().split())
        href = link.get_attribute("href")
        if name and href:
            accounts[name] = href
    log(f"Discovered {len(accounts)} switchable accounts in dropdown")
    return accounts

def switch_to_account(page, account_name, switch_href):
    """
    Switch accounts by opening the dropdown and clicking the link.
    Kit uses POST for account switching so we must click, not navigate directly.
    """
    log(f"  Opening switcher to switch to: {account_name}")
    if not open_switcher(page):
        raise Exception("Could not open account switcher")

    # Find and click the specific account link
    link = page.query_selector(f'a[href="{switch_href}"]')
    if not link:
        # Try partial href match
        links = page.query_selector_all('a[href*="/account_users/"][href*="/switch"]')
        for l in links:
            if " ".join(l.inner_text().strip().split()).lower() == account_name.lower():
                link = l
                break
    if not link:
        raise Exception(f"Link not found in switcher for: {account_name}")

    link.click()
    time.sleep(4)
    log(f"  Switched — now at: {page.url}")

# ── Scraping ──────────────────────────────────────────────────────────────────
def scrape_table(page, tab_name):
    try:
        page.wait_for_selector("table tbody tr", timeout=10000)
    except PlaywrightTimeout:
        log(f"  No table found for {tab_name}")
        return []

    # Wait for rows to actually contain data (not loading skeleton)
    # Kit renders empty placeholder rows first — wait until at least one has text
    # Large accounts (500+ partners) can take 20-30s to fully render
    for attempt in range(20):
        rows = page.query_selector_all("table tbody tr")
        has_data = any(
            row.query_selector_all("td") and
            any(c.inner_text().strip() for c in row.query_selector_all("td"))
            for row in rows
        )
        if has_data:
            break
        log(f"  {tab_name}: waiting for data... (attempt {attempt+1}/20)")
        time.sleep(2)
    else:
        log(f"  {tab_name}: rows present but no data after 40s — checking if truly empty")
        screenshot(page, f"empty_{tab_name.replace(' ', '_')}")
        # Check for empty state message (account genuinely has no recommendations)
        body = page.inner_text("body")
        if any(x in body.lower() for x in ["no recommendations", "no partners", "get started", "discover creators"]):
            log(f"  {tab_name}: genuinely empty — account has no partners yet")
            return []
        return []

    # Scroll to load all rows
    prev = 0
    for _ in range(8):
        rows = page.query_selector_all("table tbody tr")
        if len(rows) == prev and prev > 0:
            break
        prev = len(rows)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(0.4)

    rows = page.query_selector_all("table tbody tr")
    log(f"  {tab_name}: {len(rows)} rows")

    # Read column headers to map by name — Kit shows different columns per account
    headers = [h.inner_text().strip().lower()
               for h in page.query_selector_all("table thead th, table thead td")]
    log(f"  {tab_name} headers: {headers}")

    def col_idx(names):
        for name in names:
            for i, h in enumerate(headers):
                if name in h:
                    return i
        return None

    creator_idx     = col_idx(["creator", "name"]) or 0
    subscribers_idx = col_idx(["subscribers", "referrals"])
    impressions_idx = col_idx(["impressions", "shown", "views", "audience"])
    conv_idx        = col_idx(["conversion", "rate"])

    # Fallback by column count if headers don't match
    if subscribers_idx is None:
        col_count = len(headers) if headers else 0
        if col_count <= 3:
            # 3 cols: Creator | Subscribers | Conversion Rate
            subscribers_idx = 1
            conv_idx = conv_idx or 2
        else:
            # 4 cols: Creator | Impressions | Subscribers | Conversion Rate
            impressions_idx = impressions_idx or 1
            subscribers_idx = 2
            conv_idx = conv_idx or 3

    log(f"  Column map — creator:{creator_idx} impressions:{impressions_idx} subscribers:{subscribers_idx} conv:{conv_idx}")

    results = []
    for row in rows:
        cells = row.query_selector_all("td")
        if len(cells) < 2:
            continue
        try:
            def cell_val(idx):
                if idx is None or idx >= len(cells):
                    return ""
                return cells[idx].inner_text().strip()

            creator = " ".join(cell_val(creator_idx).split())
            if creator:
                results.append({
                    "creator": creator,
                    "impressions": cell_val(impressions_idx),
                    "subscribers": cell_val(subscribers_idx),
                    "conversion_rate": cell_val(conv_idx)
                })
        except Exception as e:
            log(f"  Row parse error: {e}")
    return results

def scrape_account(page, account_name):
    log(f"Scraping: {account_name}")

    # Recommending Me
    page.goto("https://app.kit.com/creator-network", wait_until="domcontentloaded", timeout=45000)
    time.sleep(3)
    if "login" in page.url or "verify" in page.url:
        raise Exception("Session expired mid-run")
    recommending_me = scrape_table(page, "Recommending Me")

    # My Recommendations
    page.goto("https://app.kit.com/creator-network/recommendations", wait_until="domcontentloaded", timeout=45000)
    time.sleep(3)
    my_recommendations = scrape_table(page, "My Recommendations")

    log(f"  ✓ Done — {len(recommending_me)} recommending me, {len(my_recommendations)} my recs")
    return recommending_me, my_recommendations

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    log("=" * 60)
    log("Kit Creator Network Scraper — Starting")
    log("=" * 60)

    if not DATABASE_URL:
        raise Exception("KIT_DATABASE_URL not set")
    if not OP_TOKEN:
        raise Exception("OP_SERVICE_ACCOUNT_TOKEN not set")

    conn = psycopg2.connect(DATABASE_URL)
    active_accounts = get_active_accounts(conn)
    log(f"Active accounts to scrape: {len(active_accounts)}")
    for a in active_accounts:
        log(f"  - {a}")

    results = {"success": [], "failed": [], "skipped": []}

    ctx_kwargs = {
        "viewport": {"width": 1280, "height": 900},
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    if SESSION_FILE.exists():
        ctx_kwargs["storage_state"] = str(SESSION_FILE)
        log("Using saved session")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()
        page.set_default_timeout(30000)

        try:
            ensure_logged_in(page, context)

            # Discover all switchable accounts from the dropdown
            switch_links = discover_switch_links(page)

            # Fuzzy match: find href for a given account name
            def find_switch_href(account_name):
                if account_name in switch_links:
                    return switch_links[account_name]
                for name, href in switch_links.items():
                    if name.lower() == account_name.lower():
                        return href
                for name, href in switch_links.items():
                    if account_name.lower() in name.lower() or name.lower() in account_name.lower():
                        return href
                return None

            # Get current account name (the one we're already on — no switch needed)
            def get_current_account_name():
                page.goto("https://app.kit.com/dashboard", wait_until="domcontentloaded", timeout=45000)
                time.sleep(1)
                for btn in page.query_selector_all("button"):
                    txt = btn.inner_text().strip()
                    if txt and txt not in NAV_SKIP and "notification" not in txt.lower() and len(txt) > 2:
                        return txt
                return None

            current_account = get_current_account_name()
            log(f"Currently active account: {current_account}")

            for i, account_name in enumerate(active_accounts):
                log(f"\n[{i+1}/{len(active_accounts)}] {account_name}")

                # Check if we're already on this account
                already_here = current_account and (
                    account_name.lower() in current_account.lower() or
                    current_account.lower() in account_name.lower()
                )

                if not already_here:
                    href = find_switch_href(account_name)
                    if not href:
                        log(f"  Not found in switcher — skipping")
                        results["skipped"].append(account_name)
                        continue
                    try:
                        switch_to_account(page, account_name, href)
                        current_account = account_name
                        if "login" in page.url or "verify" in page.url:
                            login(page, context)
                            switch_to_account(page, account_name, href)
                    except Exception as e:
                        log(f"  Switch failed: {e}")
                        results["failed"].append({"account": account_name, "error": f"Switch failed: {e}"})
                        continue
                else:
                    log(f"  Already on this account")

                try:
                    recommending_me, my_recommendations = scrape_account(page, account_name)
                    save_to_db(conn, account_name, recommending_me, my_recommendations)
                    results["success"].append({
                        "account": account_name,
                        "recommending_me": len(recommending_me),
                        "my_recommendations": len(my_recommendations)
                    })
                    current_account = account_name
                except Exception as e:
                    log(f"  ✗ Error: {e}")
                    traceback.print_exc()
                    screenshot(page, f"error_{account_name.replace(' ', '_')[:30]}")
                    results["failed"].append({"account": account_name, "error": str(e)})
                    try:
                        ensure_logged_in(page, context)
                        current_account = get_current_account_name()
                    except:
                        pass

        finally:
            try:
                context.storage_state(path=str(SESSION_FILE))
            except:
                pass
            browser.close()
            conn.close()

    # ── Summary ──
    log("\n" + "=" * 60)
    log(f"DONE — {len(results['success'])} succeeded, {len(results['failed'])} failed, {len(results['skipped'])} skipped")

    lines = ["📊 *Kit Creator Network Scrape Complete*\n"]
    lines.append(f"✅ Scraped: {len(results['success'])}/{len(active_accounts)} accounts")
    if results["success"]:
        lines.append("\n*Results:*")
        for r in results["success"]:
            lines.append(f"• {r['account']}: {r['recommending_me']} recommending, {r['my_recommendations']} recs out")
    if results["failed"]:
        lines.append(f"\n⚠️ Failed ({len(results['failed'])}):")
        for f in results["failed"]:
            lines.append(f"• {f['account']}: {f['error'][:80]}")
    if results["skipped"]:
        lines.append(f"\n⏭ Skipped (not in Kit switcher): {', '.join(results['skipped'])}")

    report = "\n".join(lines)
    log("\n" + report)

    # Send Telegram report via openclaw CLI
    try:
        subprocess.run(
            ["openclaw", "message", "send",
             "--channel", "telegram",
             "--target", TELEGRAM_CHAT_ID,
             "--thread-id", TELEGRAM_TOPIC,
             "--message", report],
            timeout=15
        )
    except Exception as e:
        log(f"Telegram notify failed: {e}")

    return 0 if not results["failed"] else 1

if __name__ == "__main__":
    sys.exit(main())
