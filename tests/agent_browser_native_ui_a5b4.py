"""Real Chromium native canvas/Brain acceptance with a mocked owner API."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
root=Path(__file__).resolve().parents[1]
with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True)
    try:
        page=browser.new_page(viewport={'width':1200,'height':900});errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.set_content('<div><form id="chatForm"><textarea></textarea></form></div><aside id="chatBrainDrawer"><div id="chatBrainContent"></div></aside>')
        page.evaluate('''() => {
            window.calls=[];
            window.mission={id:'11111111-1111-4111-8111-111111111111',objective:'Public research',status:'planned',tasks:[{id:'22222222-2222-4222-8222-222222222222',title:'Researcher',status:'queued',role:'research'}],events:[{task_id:'22222222-2222-4222-8222-222222222222',kind:'browser.owner_took_control',created_at:'2026-10-08 10:00:00'}]};
            window.live={session_active:true,status:'live',revision:1,visit_count:1,max_visits:5,current_url:'https://example.com/search',page_title:'Public reports',page_text:'<img src=x onerror=alert(1)>',proposed_link:{},proposed_action:{},controls:[{index:0,kind:'fill',fingerprint:'aaaaaaaaaaaaaaaaaaaaaaaa',label:'Query'}],owner_takeover:{mode:'owner',lease_id:'88888888-8888-4888-8888-888888888888',actions_used:0,max_actions:8,forms:[{index:0,fingerprint:'bbbbbbbbbbbbbbbbbbbbbbbb',action:'https://example.com/search',label:'Search'}],pending_form:{}}};
            window.confirm=()=>true;
            Object.defineProperty(window,'crypto',{configurable:true,value:{randomUUID:()=> '99999999-9999-4999-8999-000000000001'}});
            window.fetch=async (_url,options)=>{
                const body=JSON.parse(options.body);calls.push(body);
                if(body.action==='list')return {ok:true,json:async()=>({ok:true,items:[mission]})};
                if(body.action==='get')return {ok:true,json:async()=>({ok:true,mission})};
                if(body.action==='decisions')return {ok:true,json:async()=>({ok:true,items:[]})};
                if(body.action==='browser.owner.search.review'){
                    live.owner_takeover.pending_form={id:'77777777-7777-4777-8777-777777777777'};
                    live.owner_takeover.review={id:'77777777-7777-4777-8777-777777777777',query:'reviewed query',destination:'https://example.com/search?q=reviewed+query',required_fields:[{name:'q',valid:true}]};
                }
                if(body.action==='browser.owner.release')live.owner_takeover.mode='agent';
                return {ok:true,json:async()=>({ok:true,live_browser:structuredClone(live)})};
            };
        }''')
        page.add_script_tag(content=(root/'ui/agent-browser-workspaces.js').read_text())
        page.locator('[data-agent-teams-a3]>summary').click()
        page.get_by_role('button',name='View',exact=True).click()
        page.evaluate('() => window.VP3_AGENT_TEAMS_A5B4_STATUS()')
        page.get_by_text('Worker browser · supervised read-only',exact=True).click()
        assert page.get_by_role('button',name='Ask agent for next link').count()==0
        page.locator('[data-owner-value-task]').fill('draft value')
        page.evaluate('() => window.VP3_AGENT_TEAMS_A5B4_STATUS()')
        assert page.locator('[data-owner-value-task]').input_value()=='draft value'
        page.get_by_role('button',name='Apply owner control').click()
        page.wait_for_function('calls.some(c=>c.action==="browser.owner.control")')
        control=page.evaluate('calls.find(c=>c.action==="browser.owner.control")')
        assert control['request_id'] and control['lease_id']=='88888888-8888-4888-8888-888888888888'
        page.get_by_role('button',name='Review GET search').click()
        page.wait_for_function('document.querySelector(".vp3-owner-takeover").textContent.includes("Exact query: reviewed query")')
        page.evaluate('() => window.VP3_AGENT_TEAMS_A3_BRAIN(document.getElementById("chatBrainContent"))')
        page.locator('#chatBrainContent .vp3-brain-worker summary').click()
        assert 'Public reports' in page.locator('#chatBrainContent').inner_text()
        assert 'form awaiting confirmation' in page.locator('#chatBrainContent').inner_text()
        assert page.locator('#chatBrainContent img').count()==0,'Evidence injected markup'
        page.get_by_role('button',name='Return control to agent').click()
        page.wait_for_function('document.querySelector(".vp3-owner-takeover").textContent.includes("Take exclusive control")')
        assert page.get_by_role('button',name='Run browser plan').count()==1
        assert not errors,errors
        print('A5B4_NATIVE_UI PASS: native canvas, per-worker Brain, exact review, stable drafts and lease/operation identities')
    finally:browser.close()
