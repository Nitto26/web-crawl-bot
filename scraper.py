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


def get_selected_family_unit_text(page: Page) -> str:
    """
    Find the selected option text in the Family Unit dropdown.
    """
    try:
        # Try native select first
        text = page.evaluate("""() => {
            let select = null;
            const labels = Array.from(document.querySelectorAll('label'));
            const familyLabel = labels.find(l => l.innerText.includes('Family Unit'));
            if (familyLabel) {
                if (familyLabel.htmlFor) {
                    select = document.getElementById(familyLabel.htmlFor);
                }
                if (!select) {
                    select = familyLabel.parentElement.querySelector('select');
                }
            }
            if (!select) {
                select = document.querySelector('select');
            }
            if (select && select.selectedIndex >= 0) {
                return select.options[select.selectedIndex].text.trim();
            }
            return '';
        }""")
        if text:
            return text
    except Exception:
        pass

    # Fallback to common custom dropdown text selectors (bootstrap-select, select2)
    try:
        for selector in ['.filter-option', '.select2-selection__rendered', '.select2-chosen', 'button.dropdown-toggle']:
            elem = page.locator(selector)
            if elem.count() > 0:
                text = elem.first.inner_text().strip()
                if text and 'select' not in text.lower():
                    return text
    except Exception:
        pass

    return "Unknown_Family_Unit"



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
        input("\n👉 STEP: Log in, select the Family Unit/search your Family List table, and then press ENTER here in VS Code terminal...")

        # Detect the selected dropdown option and create a subfolder for it
        selected_unit = get_selected_family_unit_text(visible_page)
        print(f"\n[debug] Detected selected Family Unit: '{selected_unit}'")
        
        # Sanitize for safe directory path
        safe_unit_name = "".join(c for c in selected_unit if c.isalnum() or c in (' ', '_', '-')).strip()
        safe_unit_name = safe_unit_name.replace(" ", "_")
        if not safe_unit_name:
            safe_unit_name = "Unknown_Family_Unit"
            
        unit_dir = os.path.join(BASE_DIR, safe_unit_name)
        os.makedirs(unit_dir, exist_ok=True)
        print(f"[debug] Downloads will be saved to: {unit_dir}\n")

        # 2. Open background worker to handle PDF conversion cleanly without print menus blocking us
        headless_browser = p.chromium.launch(headless=True)
        headless_context = headless_browser.new_context()
        headless_page = headless_context.new_page()

        # Ensure headless context also auto-accepts dialogs if any appear
        headless_context.on("page", lambda pg: pg.on("dialog", handle_dialog))

        # Sync active session cookies and local/session storage to background worker once at the start
        try:
            print("\n[debug] Syncing active session cookies and storage to background worker...")
            headless_context.add_cookies(visible_context.cookies())
            local_storage = visible_page.evaluate("() => JSON.stringify(localStorage)")
            session_storage = visible_page.evaluate("() => JSON.stringify(sessionStorage)")
            
            # Navigate headless to the domain first so we can set storage
            headless_page.goto("https://churchsoft.in/family_card/family_search", wait_until="commit")
            headless_page.evaluate(f"val => {{ Object.assign(window.localStorage, JSON.parse(val)); }}", local_storage)
            headless_page.evaluate(f"val => {{ Object.assign(window.sessionStorage, JSON.parse(val)); }}", session_storage)
            print("[debug] Session sync complete.")
        except Exception as storage_err:
            print(f" -> Warning (session sync failed): {storage_err}")

        page_num = 1
        while True:
            # Target the rows inside the data table
            visible_page.wait_for_selector("table tbody tr")
            rows = visible_page.locator("table tbody tr").all()
            print(f"\n[Page {page_num}] Found {len(rows)} rows on the page. Extracting data...")

            rows_to_process = []
            for row in rows:
                try:
                    # Column 1: Serial
                    serial = row.locator("td:nth-child(1)").inner_text().strip()
                    # Column 2: Code
                    code = row.locator("td:nth-child(2)").inner_text().strip()
                    # Column 3: Family Name
                    family_name = row.locator("td:nth-child(3)").inner_text().strip()
                    # Column 4: Head of Family
                    head_name = row.locator("td:nth-child(4)").inner_text().strip()
                except Exception:
                    continue

                if not code or "Code" in code or ":" in code or "Family Unit" in code:
                    # Skip helper/header rows
                    continue

                # Get cardid from print_card
                cardid = None
                try:
                    print_btn = row.locator("a[onclick*='print_card']")
                    if print_btn.count() > 0:
                        onclick_val = print_btn.get_attribute("onclick") or ""
                        match = re.search(r"print_card\('([^']+)'\)", onclick_val)
                        if match:
                            cardid = match.group(1)
                except Exception:
                    pass

                # Get member cardid if exists
                member_cardid = None
                try:
                    print_members_btn = row.locator("a[onclick*='print_members']")
                    if print_members_btn.count() > 0:
                        onclick_val = print_members_btn.get_attribute("onclick") or ""
                        match = re.search(r"print_members\('([^']+)'\)", onclick_val)
                        if match:
                            member_cardid = match.group(1)
                except Exception:
                    pass

                rows_to_process.append({
                    "serial": serial,
                    "code": code,
                    "family_name": family_name,
                    "head_name": head_name,
                    "cardid": cardid,
                    "member_cardid": member_cardid
                })

            print(f"Extraction complete. Found {len(rows_to_process)} valid families to download on Page {page_num}.")

            for item in rows_to_process:
                serial = item["serial"]
                code = item["code"]
                family_name = item["family_name"]
                head_name = item["head_name"]
                cardid = item["cardid"]
                member_cardid = item["member_cardid"]

                # Generate a safe folder name formatted as: "{si no}. {family name in column} {head name} {code}"
                raw_folder_name = f"{serial}. {family_name} {head_name} {code}"
                safe_folder_name = "".join(c for c in raw_folder_name if c.isalnum() or c in (' ', '.', '_', '-')).strip()
                while "  " in safe_folder_name:
                    safe_folder_name = safe_folder_name.replace("  ", " ")

                print(f"\n[Processing Row {serial} | Code: {code} | Name: {head_name}]")
                
                # Setup specific folder hierarchy
                target_folder = os.path.join(unit_dir, safe_folder_name)
                os.makedirs(target_folder, exist_ok=True)

                # --- PROCESS: Print Card ---
                if cardid:
                    try:
                        print_url = f"https://churchsoft.in/family_card/print_family_card/{cardid}/half"
                        print(f" -> Generating Card PDF for {head_name}...")
                        headless_page.goto(print_url, wait_until="networkidle")
                        pdf_path = os.path.join(target_folder, f"{code}_card.pdf")
                        headless_page.pdf(path=pdf_path, format="A4", print_background=True)
                        print(f" -> Saved: {code}_card.pdf")
                    except Exception as e:
                        print(f" -> Failed to process Card: {e}")
                else:
                    print(" -> 'Print Card' link not found for this family")

                # --- PROCESS: Print Members ---
                if member_cardid:
                    try:
                        print_url = f"https://churchsoft.in/family_card/print_member_page/{member_cardid}/half"
                        print(f" -> Downloading Members PDF for {head_name}...")
                        pdf_path = os.path.join(target_folder, f"{code}_members.pdf")
                        try:
                            with headless_page.expect_download(timeout=15000) as download_info:
                                try:
                                    headless_page.goto(print_url)
                                except Exception as goto_err:
                                    if "Download is starting" not in str(goto_err):
                                        raise goto_err
                            
                            download = download_info.value
                            download.save_as(pdf_path)
                            print(f" -> Saved: {code}_members.pdf (via download)")
                        except Exception as dl_err:
                            # Fallback to standard PDF generation if download was not triggered
                            print(f" -> Download failed or wasn't triggered ({dl_err}). Trying standard PDF generation...")
                            headless_page.goto(print_url, wait_until="networkidle")
                            headless_page.pdf(path=pdf_path, format="A4", print_background=True)
                            print(f" -> Saved: {code}_members.pdf (via standard PDF)")
                    except Exception as e:
                        print(f" -> Failed to process Members: {e}")
                else:
                    print(" -> 'Print Members' link not found for this family")

                # Safety delay to prevent dashboard rate-limiting
                time.sleep(1)

            # Check pagination for Next button
            next_button_li = visible_page.locator("li#example_next")
            if next_button_li.count() == 0:
                print(" -> No 'Next' page button found. Stopping pagination.")
                break

            classes = next_button_li.get_attribute("class") or ""
            if "disabled" in classes:
                print(" -> 'Next' page button is disabled. Reached the end of the tables.")
                break

            next_link = next_button_li.locator("a")
            if next_link.count() > 0:
                # Capture the first valid code on the current page before clicking
                old_first_code = rows_to_process[0]["code"] if rows_to_process else ""

                print(f" -> Clicking 'Next' page button to go to Page {page_num + 1}")
                try:
                    # Using JavaScript click to bypass any scrolling or overlay obstructions
                    next_link.first.evaluate("el => el.click()")
                except Exception as click_err:
                    print(f" -> JS click failed: {click_err}. Trying human_click fallback...")
                    human_click(visible_page, next_link.first)
                
                page_num += 1

                # Wait for the table contents/pagination to update
                print(" -> Waiting for page contents to load...")
                start_time = time.time()
                updated = False
                while time.time() - start_time < 12.0:
                    try:
                        # 1. Check active pagination text
                        active_text = visible_page.locator("ul.pagination li.active").inner_text().strip()
                        
                        # 2. Find the first row in the table that has a valid code
                        current_first_code = ""
                        tr_elements = visible_page.locator("table tbody tr").all()
                        for tr in tr_elements:
                            try:
                                c_val = tr.locator("td:nth-child(2)").inner_text().strip()
                                if c_val and "Code" not in c_val and ":" not in c_val and "Family Unit" not in c_val:
                                    current_first_code = c_val
                                    break
                            except Exception:
                                pass
                        
                        # We are updated if the active page text matches page_num
                        # AND the first valid code has changed from the old page
                        if active_text == str(page_num) and (not old_first_code or current_first_code != old_first_code):
                            updated = True
                            break
                    except Exception:
                        pass
                    time.sleep(0.2)

                if updated:
                    print(f" -> Table successfully loaded Page {page_num}.")
                else:
                    print(" -> Table update timed out, continuing with fallback delay.")
                    time.sleep(2.5)
            else:
                print(" -> 'Next' link anchor not found inside list item. Stopping pagination.")
                break

        # Cleanup
        headless_browser.close()
        visible_browser.close()
        print("\nAll downloads have completed successfully.")

if __name__ == "__main__":
    run_visible_scraper()