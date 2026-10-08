"""A5B3 real Chromium acceptance: only safe non-submitting controls."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from app.services import agent_browser_dom_policy as dom
from app.services.agent_mission_runtime import MissionError

with sync_playwright() as playwright:
    browser=playwright.chromium.launch(headless=True)
    try:
        ctx=browser.new_context(java_script_enabled=False,service_workers="block",
                                accept_downloads=False,permissions=[])
        page=ctx.new_page()
        page.set_content("""<form action='https://example.com/save' method='post'>
          <label>Topic <input name='topic' type='text' placeholder='Search topic'></label>
          <input name='secret' type='password' value='dont-show'>
          <input name='file' type='file'>
          <input name='hidden' type='hidden' value='not-allowed'>
          <input name='agree' type='checkbox' aria-label='Acknowledged'>
          <select name='category' aria-label='Category'>
            <option value='0'>Science</option><option value='1'>Art</option>
          </select>
          <button type='submit'>Publish changes</button>
          <button type='button'>Action</button>
          <details><summary>More information</summary><p>Read-only details</p></details>
          <textarea name='note' placeholder='Analysis note'></textarea>
        </form>
        <script>window.unwantedScript=true</script>""")
        controls=dom.candidates(page)
        assert {x["kind"] for x in controls}=={"fill","check","select","toggle"},controls
        assert all(x["label"] not in ("secret","file","hidden","Publish changes","Action")
                   for x in controls)
        assert not page.evaluate("window.unwantedScript"),"Page JavaScript was executed"
        topic=next(c for c in controls if c["label"]=="Search topic")
        dom.apply(page,index=topic["index"],fingerprint=topic["fingerprint"],
                  kind="fill",value="Safe research")
        assert page.locator('[name=topic]').input_value()=="Safe research"
        checkbox=next(c for c in controls if c["kind"]=="check")
        dom.apply(page,index=checkbox["index"],fingerprint=checkbox["fingerprint"],
                  kind="check",value=True)
        assert page.locator('[name=agree]').is_checked()
        menu=next(c for c in controls if c["kind"]=="select")
        assert [o["label"] for o in menu["options"]]==["Science","Art"]
        dom.apply(page,index=menu["index"],fingerprint=menu["fingerprint"],
                  kind="select",value=1)
        assert page.locator('[name=category]').input_value()=="1"
        disclosure=next(c for c in controls if c["kind"]=="toggle")
        dom.apply(page,index=disclosure["index"],fingerprint=disclosure["fingerprint"],
                  kind="toggle",value=True)
        assert page.locator("details").get_attribute("open") is not None
        assert page.url=="about:blank","DOM operations unexpectedly submitted or navigated"
        for action in [
            {"index":topic["index"],"fingerprint":"outdated","kind":"fill","value":"x"},
            {"index":topic["index"],"fingerprint":topic["fingerprint"],"kind":"submit","value":True},
            {"index":topic["index"],"fingerprint":topic["fingerprint"],"kind":"fill","value":"x"*301},
            {"index":checkbox["index"],"fingerprint":checkbox["fingerprint"],"kind":"check","value":False},
        ]:
            try:
                dom.apply(page,**action)
                raise AssertionError("Unsafe browser command accepted: "+str(action["kind"]))
            except MissionError:
                pass
        assert page.url=="about:blank"
    finally:
        browser.close()
print("MISSION_A5B3_DOM PASS: safe text/checkbox/select/details, no JS, no submit, no password/file or stale controls")
