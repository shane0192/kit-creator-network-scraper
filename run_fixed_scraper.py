#!/usr/bin/env python3

import os
import sys
import traceback
from playwright.sync_api import sync_playwright

# Add the project root to the path to import the modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Set environment variable for cookie expiration
os.environ["KIT_COOKIE_EXPIRATION_DAYS"] = "365"

def main():
    """Run the ConvertKit scraper directly without using the class with syntax errors"""
    print("Starting Kit.com Creator Network Scraper")
    
    try:
        # Initialize playwright
        playwright = sync_playwright().start()
        
        # Launch the browser in non-headless mode
        browser = playwright.chromium.launch(headless=False)
        
        # Create context with viewport
        context = browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/101.0.4951.64 Safari/537.36"
        )
        
        # Create a new page
        page = context.new_page()
        
        # Navigate to Kit.com
        print("Navigating to Kit.com...")
        page.goto("https://app.kit.com/dashboard")
        
        # Check if we need to log in
        if "login" in page.url:
            print("\n" + "=" * 80)
            print("MANUAL LOGIN REQUIRED")
            print("=" * 80)
            print("\nPlease complete the login process manually, including:")
            print("1. Enter your email and password")
            print("2. Complete any two-factor authentication steps if needed")
            print("3. Wait until you see your dashboard")
            print("\nThe browser window will stay open during this process.")
            
            # Wait for the user to complete login
            input("\nPress Enter after you have successfully logged in...")
            
            # Save storage state for future sessions with long expiration
            project_root = os.path.dirname(os.path.abspath(__file__))
            profile_dir = os.path.join(project_root, 'sessions', 'kit_account')
            os.makedirs(profile_dir, exist_ok=True)
            storage_path = os.path.join(profile_dir, "state.json")
            
            print(f"Saving login state to {storage_path}...")
            context.storage_state(path=storage_path)
            print("Login state saved!")
        else:
            print("Already logged in!")
        
        # Navigate to Creator Network
        print("\nNavigating to Creator Network...")
        page.goto("https://app.kit.com/creator-network")
        page.wait_for_load_state("networkidle")
        
        # Create a screenshot
        page.screenshot(path="creator_network.png")
        print("Screenshot saved to creator_network.png")
        
        # Switch accounts if needed
        print("\nChecking available accounts...")
        # Click the account menu
        page.click("button:has-text('Account')")
        
        # Wait for the dropdown
        account_links = page.query_selector_all("a[role='menuitem']")
        
        if account_links:
            print(f"Found {len(account_links)} account links")
            for i, link in enumerate(account_links):
                text = link.inner_text()
                if text and text not in ["Settings", "Log out"]:
                    print(f"  {i+1}. {text}")
            
            # Ask user if they want to switch accounts
            account_choice = input("\nEnter account number to switch to (or press Enter to skip): ")
            if account_choice and account_choice.isdigit():
                idx = int(account_choice) - 1
                if 0 <= idx < len(account_links):
                    account_name = account_links[idx].inner_text()
                    print(f"Switching to account: {account_name}")
                    account_links[idx].click()
                    # Wait for navigation after account switch
                    page.wait_for_load_state("networkidle")
                    print("Account switched!")
                    
                    # Navigate back to Creator Network
                    page.goto("https://app.kit.com/creator-network")
                    page.wait_for_load_state("networkidle")
        
        # Ask if user wants to see Recommending Me data
        view_recommending = input("\nView 'Recommending Me' data? (y/n): ").lower() == 'y'
        if view_recommending:
            print("\nViewing 'Recommending Me' data...")
            # Ensure we're on the right page
            page.goto("https://app.kit.com/creator-network")
            page.wait_for_load_state("networkidle")
            page.screenshot(path="recommending_me.png")
            
            # Find all rows
            rows = page.query_selector_all("table tbody tr")
            print(f"Found {len(rows)} entries in 'Recommending Me' view")
            
            # Display first 5 entries
            for i, row in enumerate(rows[:5]):
                cells = row.query_selector_all("td")
                if len(cells) >= 3:
                    creator = cells[0].inner_text()
                    subscribers = cells[1].inner_text()
                    conversion = cells[2].inner_text()
                    print(f"  {i+1}. {creator} - {subscribers} - {conversion}")
        
        # Ask if user wants to see My Recommendations data
        view_recommendations = input("\nView 'My Recommendations' data? (y/n): ").lower() == 'y'
        if view_recommendations:
            print("\nViewing 'My Recommendations' data...")
            # Navigate to My Recommendations
            page.goto("https://app.kit.com/creator-network/recommendations")
            page.wait_for_load_state("networkidle")
            page.screenshot(path="my_recommendations.png")
            
            # Find all rows
            rows = page.query_selector_all("table tbody tr")
            print(f"Found {len(rows)} entries in 'My Recommendations' view")
            
            # Display first 5 entries
            for i, row in enumerate(rows[:5]):
                cells = row.query_selector_all("td")
                if len(cells) >= 3:
                    creator = cells[0].inner_text()
                    subscribers = cells[1].inner_text()
                    conversion = cells[2].inner_text()
                    print(f"  {i+1}. {creator} - {subscribers} - {conversion}")
        
        # Ask if user wants to keep the browser open
        keep_open = input("\nKeep browser open? (y/n): ").lower() == 'y'
        if keep_open:
            print("\nBrowser window will remain open. Press Enter to close it...")
            input()
        
        # Clean up
        context.close()
        browser.close()
        playwright.stop()
        
        print("ConvertKit scraper completed successfully!")
        return 0
    
    except Exception as e:
        print(f"Error: {e}")
        traceback.print_exc()
        
        try:
            # Keep browser open on error for debugging
            print("\nBrowser window will remain open for debugging. Press Enter to close it...")
            input()
            
            # Try to clean up
            if 'context' in locals() and context:
                context.close()
            if 'browser' in locals() and browser:
                browser.close()
            if 'playwright' in locals() and playwright:
                playwright.stop()
        except:
            pass
        
        return 1

if __name__ == "__main__":
    sys.exit(main()) 