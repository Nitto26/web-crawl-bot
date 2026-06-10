import os
import time
from playwright.sync_api import sync_playwright, Page, Locator
import re

BASE_DIR = "Churchsoft_Downloads"
os.makedirs(BASE_DIR, exist_ok=True)

def human_click(page: Page, locator: Locator, timeout: int = 10_000) -> None:
    """
    Move the mouse to the center of `locator` and click to make actions visible
    in the visible browser (helps with debugging UI automation).
    """
    locator.wait_for(state="visible", timeout=timeout)
    try:
        locator.scroll_into_view_if_needed()
    except Exception:
        pass
    box = locator.bounding_box()
    if box:
        x = box["x"] + box["width"] / 2
        y = box["y"] + box["height"] / 2
        page.mouse.move(x, y)
        page.mouse.click(x, y)
    else:
        locator.click()


def select_family_unit_and_search(page: Page, timeout: int = 30_000) -> None:
    """
    Locate the "Family Unit" <select>, choose the first real option, click the
    "Search" button below it, then wait for network idle and table rows to load.

    Args:
        page: Playwright `Page` instance (sync API).
        timeout: maximum wait time in milliseconds for load/wait operations.
    """

    # Try to locate the select by its visible label first
    family_label = page.locator('label:has-text("Family Unit")')
    family_select: Locator = page.get_by_label("Family Unit")

    if family_select.count() == 0 and family_label.count() > 0:
        # Try to find a native <select> inside the label's parent
        parent = family_label.locator('xpath=..')
        candidate = parent.locator('select')
        if candidate.count() > 0:
            family_select = candidate.first
        else:
            # Try common custom select containers (bootstrap-select, select2, chosen)
            bs = parent.locator('.bootstrap-select')
            if bs.count() > 0:
                family_select = bs.first
            else:
                s2 = parent.locator('.select2-container')
                if s2.count() > 0:
                    family_select = s2.first

    # Final fallback: first select on page
    if family_select.count() == 0:
        family_select = page.locator('select').first

    # If this is a native <select>, use select_option; otherwise handle custom widgets
    try:
        tag = family_select.evaluate('el => el.tagName.toLowerCase()')
    except Exception:
        tag = 'select'

    selected_value = None

    if tag == 'select':
        option_locator = family_select.locator('option')
        option_count = option_locator.count()

        for i in range(option_count):
            opt = option_locator.nth(i)
            opt_value = opt.get_attribute('value') or ''
            opt_disabled = opt.get_attribute('disabled')
            opt_text = (opt.inner_text() or '').strip()
            if opt_value.strip() and opt_disabled is None and 'select' not in opt_text.lower():
                selected_value = opt_value
                break

        if selected_value is None and option_count > 0:
            for i in range(option_count):
                opt = option_locator.nth(i)
                if opt.get_attribute('disabled') is None:
                    selected_value = opt.get_attribute('value') or ''
                    if selected_value:
                        break

        if not selected_value:
            raise RuntimeError("No selectable option found in 'Family Unit' dropdown.")

        print(f"[debug] Selecting native <select> value={selected_value}")
        family_select.select_option(value=selected_value)
        try:
            human_click(page, family_select)
        except Exception:
            pass

    else:
        # Handle bootstrap-select: click toggle then choose first dropdown item
        print("[debug] Handling custom dropdown widget for Family Unit")
        # Try bootstrap-select pattern first
        try:
            toggle = family_select.locator('.dropdown-toggle')
            if toggle.count() == 0:
                toggle = family_select.locator('.select2-choice')
            if toggle.count() == 0:
                # fallback to clicking the label area
                toggle = family_label

            human_click(page, toggle)
            # Wait for dropdown menu and pick first enabled item
            try:
                page.wait_for_selector('.dropdown-menu li a, .select2-results li', timeout=3000)
                opt = page.locator('.dropdown-menu li:not(.disabled) a, .select2-results li:not(.disabled)').first
                human_click(page, opt)
            except Exception:
                print('[debug] Could not find custom dropdown option; trying to click first visible option')
                opts = page.locator('li a, .select2-results li').first
                human_click(page, opts)
        except Exception as e:
            print(f"[debug] Custom dropdown handling failed: {e}")

    # Prefer role-based lookup; fallback to text-based button lookup for Search
    search_button: Locator = page.get_by_role('button', name='Search')
    if search_button.count() == 0:
        search_button = page.locator('button:has-text("Search")').first

    # Click Search with visible mouse movement and wait for table update
    print('[debug] Clicking Search to reload table')
    human_click(page, search_button)

    # Wait for network idle and for table rows to appear
    page.wait_for_load_state('networkidle', timeout=timeout)
    page.wait_for_selector('table tbody tr', state='visible', timeout=timeout)

def run_visible_scraper():
    with sync_playwright() as p:
        # 1. Open the visible browser for your manual login phase
        # Launch visible browser with a small slow_mo so mouse movement is observable
        visible_browser = p.chromium.launch(headless=False, slow_mo=50)
        visible_context = visible_browser.new_context()
        # Override window.print to prevent the native print dialog from opening and blocking automation
        visible_context.add_init_script("window.print = () => {};")
        visible_page = visible_context.new_page()

        # Handle the "Do you want half print?" confirmation dialogue automatically
        def handle_dialog(dialog):
            print(f"Dialog encountered: '{dialog.message}'. Automatically accepting...")
            try:
                dialog.accept()
            except Exception as e:
                print(f" -> Failed to accept dialog: {e}")

        # Attach dialog handler to the already-created page
        visible_page.on("dialog", handle_dialog)

        # Also attach to the context so any new pages (print previews) inherit the handler
        visible_context.on("page", lambda pg: pg.on("dialog", handle_dialog))

        # Navigate to target
        visible_page.goto("https://churchsoft.in/family_card/family_search")
        
        # Pause script execution here to let you log in and select the correct family unit
        input("\n👉 STEP: Log in, search your Family List table, and then press ENTER here in VS Code terminal...")

        # After manual login, automatically select the first Family Unit option and click Search
        select_family_unit_and_search(visible_page)

        # 2. Open background worker to handle PDF conversion cleanly without print menus blocking us
        headless_browser = p.chromium.launch(headless=True)
        headless_context = headless_browser.new_context()
        headless_page = headless_context.new_page()

        # Ensure headless context also auto-accepts dialogs if any appear
        headless_context.on("page", lambda pg: pg.on("dialog", handle_dialog))

        # Target the rows inside the data table
        visible_page.wait_for_selector("table tbody tr")
        rows = visible_page.locator("table tbody tr").all()
        print(f"\nFound {len(rows)} data entries to download.")

        for row in rows:
            # Extract Code text from column 2
            try:
                code = row.locator("td:nth-child(2)").inner_text().strip()
            except Exception:
                continue

            if not code or "Code" in code or ":" in code or "Family Unit" in code:
                continue

            # Sanitize code for directory name
            safe_code = "".join(c for c in code if c.isalnum() or c in (' ', '_', '-')).strip()
            if not safe_code:
                continue

            print(f"\n[Processing Code: {safe_code}]")
            
            # Setup specific folder hierarchy
            target_folder = os.path.join(BASE_DIR, safe_code)
            os.makedirs(target_folder, exist_ok=True)

            # Sync active session cookies and local/session storage to background worker
            try:
                headless_context.add_cookies(visible_context.cookies())
                local_storage = visible_page.evaluate("() => JSON.stringify(localStorage)")
                session_storage = visible_page.evaluate("() => JSON.stringify(sessionStorage)")
                
                # Navigate headless to the domain first so we can set storage
                headless_page.goto("https://churchsoft.in/family_card/family_search", wait_until="commit")
                headless_page.evaluate(f"val => {{ Object.assign(window.localStorage, JSON.parse(val)); }}", local_storage)
                headless_page.evaluate(f"val => {{ Object.assign(window.sessionStorage, JSON.parse(val)); }}", session_storage)
            except Exception as storage_err:
                print(f" -> Warning (session sync): {storage_err}")

            # --- PROCESS: Print Card ---
            try:
                print_btn = row.locator("a[onclick*='print_card']")
                if print_btn.count() > 0:
                    onclick_val = print_btn.get_attribute("onclick") or ""
                    match = re.search(r"print_card\('([^']+)'\)", onclick_val)
                    if match:
                        cardid = match.group(1)
                        print_url = f"https://churchsoft.in/family_card/print_family_card/{cardid}/half"
                        print(f" -> Generated Print Card URL: {print_url}")
                        
                        print(" -> Headless worker generating Card PDF...")
                        headless_page.goto(print_url, wait_until="networkidle")
                        pdf_path = os.path.join(target_folder, f"{safe_code}_card.pdf")
                        headless_page.pdf(path=pdf_path, format="A4", print_background=True)
                        print(f" -> Saved: {safe_code}_card.pdf")
                    else:
                        print(" -> Failed to extract token for Print Card")
                else:
                    print(" -> 'Print Card' link not found in this row")
            except Exception as e:
                print(f" -> Failed to process Card for {safe_code}: {e}")

            # --- PROCESS: Print Members ---
            try:
                print_members_btn = row.locator("a[onclick*='print_members']")
                if print_members_btn.count() > 0:
                    onclick_val = print_members_btn.get_attribute("onclick") or ""
                    match = re.search(r"print_members\('([^']+)'\)", onclick_val)
                    if match:
                        cardid = match.group(1)
                        print_url = f"https://churchsoft.in/family_card/print_member_page/{cardid}/half"
                        print(f" -> Generated Print Members URL: {print_url}")
                        
                        print(" -> Headless worker downloading Members file...")
                        pdf_path = os.path.join(target_folder, f"{safe_code}_members.pdf")
                        try:
                            with headless_page.expect_download(timeout=15000) as download_info:
                                try:
                                    headless_page.goto(print_url)
                                except Exception as goto_err:
                                    if "Download is starting" not in str(goto_err):
                                        raise goto_err
                            
                            download = download_info.value
                            download.save_as(pdf_path)
                            print(f" -> Saved: {safe_code}_members.pdf (via download)")
                        except Exception as dl_err:
                            # Fallback to standard PDF generation if download was not triggered
                            print(f" -> Download failed or wasn't triggered ({dl_err}). Trying standard PDF generation...")
                            headless_page.goto(print_url, wait_until="networkidle")
                            headless_page.pdf(path=pdf_path, format="A4", print_background=True)
                            print(f" -> Saved: {safe_code}_members.pdf (via standard PDF)")
                    else:
                        print(" -> Failed to extract token for Print Members")
                else:
                    print(" -> 'Print Members' link not found in this row")
            except Exception as e:
                print(f" -> Failed to process Members for {safe_code}: {e}")

            # Safety delay to prevent dashboard rate-limiting
            time.sleep(1)

        # Cleanup
        headless_browser.close()
        visible_browser.close()
        print("\nAll downloads have completed successfully.")

if __name__ == "__main__":
    run_visible_scraper()