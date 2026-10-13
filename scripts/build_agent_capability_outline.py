"""Build a complete source registry outline without touching an installed private database."""
import argparse, ast, json, re, subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--cloud-root',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()

def constants(path):
    values={}
    class Substitute(ast.NodeTransformer):
        def visit_Tuple(self,node):
            node=self.generic_visit(node)
            node.elts=[part for item in node.elts for part in (item.value.elts if isinstance(item,ast.Starred) and isinstance(item.value,(ast.Tuple,ast.List)) else [item])]
            return node
        def visit_Name(self,node):
            if node.id in values:return ast.parse(repr(values[node.id]),mode='eval').body
            return node
    for node in ast.parse(path.read_text()).body:
        if isinstance(node,(ast.Assign,ast.AnnAssign)):
            targets=node.targets if isinstance(node,ast.Assign) else [node.target]
            try:value=ast.literal_eval(Substitute().visit(node.value))
            except (ValueError,TypeError,SyntaxError):continue
            for target in targets:
                if isinstance(target,ast.Name):values[target.id]=value
    return values

tool_catalog={};skills=[];modules=[('tools.py','TOOL_DEFINITIONS','SKILL_DEFINITIONS'),('vp3_scheduling_tools.py','DEFINITIONS','SKILLS'),('vp3_commerce_agent_tools.py','DEFINITIONS','SKILLS'),('local_file_actions_tools.py','FILE_ACTION_DEFINITIONS','FILE_ACTION_SKILL')]
for filename,tools_key,skills_key in modules:
    path=ROOT/'app/services'/filename;values=constants(path)
    for key,definition in values[tools_key].items():
        assert key not in tool_catalog,key
        tool_catalog[key]={**definition,'source':'app/services/'+filename,'registered':True,'installed_availability':'requires runtime diagnostic'}
    entries=values[skills_key];entries=[entries] if isinstance(entries,dict) else entries
    skills.extend({**entry,'source':'app/services/'+filename} for entry in entries)
assert len({s['key'] for s in skills})==len(skills)
for skill in skills:assert all(key in tool_catalog for key in skill['tools']),skill['key']
contracts=constants(ROOT/'app/services/agent_mission_tool_contracts.py');actions=constants(ROOT/'app/services/agent_mission_actions.py')
agent_tools=constants(ROOT/'app/services/agent_tools.py')
assert all(key=='browser.read' or key in tool_catalog for key in contracts['CAPABILITIES'])
assert all(key in tool_catalog for key in actions['ACTION_TOOLS'])
cloud=args.cloud_root.resolve();index={}
for path in sorted((cloud/'includes').glob('*.php')):
    for name in re.findall(r'^function\s+(\w+)\s*\(',path.read_text(),re.M):index[name]=path.relative_to(cloud).as_posix()
boundary=(cloud/'includes/agent-tool-authorization-v400.php').read_text().split('function vp3_agent_tool_execute_query_v400',1)[1]
handlers=sorted(set(name for name in re.findall(r"function_exists\('([^']+)'\)",boundary) if re.search(r'query|followthrough|execute',name)))
handler_catalog=[{'entrypoint':name,'source':index.get(name),'authorization':'Cloud canonical authorization and handler-specific permissions/confirmation'} for name in handlers]
commands=sorted(set(re.findall(r"case '([^']+)'",(cloud/'browser-companion/background.js').read_text().split('chrome.runtime.onMessage.addListener',1)[1])))
def php_catalog(path,function):
    prefix=""
    if function=="vp3_cognitive_domain_registry_v2600":
        prefix="require "+json.dumps(str(cloud/"includes/cognitive-domain-manifest-v2370.php"))+";require "+json.dumps(str(cloud/"includes/cognitive-runtime-v500.php"))+";"
    result=subprocess.run(['php','-r',prefix+"require "+json.dumps(str(cloud/path))+";echo json_encode("+function+"());"],capture_output=True,text=True,check=True)
    return json.loads(result.stdout)
plugins=php_catalog('includes/plugin-registry-v320.php','vp3_plugin_catalog_v320')
domains=php_catalog('includes/cognitive-domain-registry-v2600.php','vp3_cognitive_domain_registry_v2600')
result={'section':'A5C11','research_section':'R2','scope':'source definitions; installed user settings, custom plugins and live connectivity are not inferred',
        'homeserver_tools':sorted(tool_catalog.values(),key=lambda t:t['key']), 'homeserver_skills':sorted(skills,key=lambda s:s['key']),
        'generic_model_read_bindings':agent_tools['MODEL_TOOL_NAMES'],
        'specialist_reads':list(contracts['CAPABILITIES']),'specialist_proposals':list(actions['ACTION_TOOLS']),
        'cloud_chat_handlers':handler_catalog,'chrome_commands':commands,'cloud_plugins':plugins,'cognitive_registry':domains,
        'chat_chrome_research':{'endpoint':'api/chat-browser-research-v1.php','owner_operations':['list','prepare','get','start','cancel','save'],
           'chrome_worker_operations':['worker.list','worker.claim','worker.step'],'source_urls':5,'evidence_ledger':'browser_research_v2230',
           'source_discovery':{'provider':'existing DuckDuckGo public-search helper','canvas_query_consent':True,'domain_scope':'first_five_sorted_existing_grants','candidates_are_evidence':False,'followup_auto_execution':False},
           'native_proxy':'app/services/chat_browser_research.py','separate_website_and_exact_plan_approvals':True}}
args.output.mkdir(parents=True,exist_ok=True)
(args.output/'Agent-Skills-and-Tools-A5C11.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
def cell(value):return str(value).replace('|','\\|').replace('\n',' ')
lines=['# Agent skills and tools — A5C11','',
       f"The source inventory contains **{len(tool_catalog)} registered HomeServer tools**, **{len(skills)} named HomeServer skills**, **{len(handler_catalog)} canonical Cloud Chat handler entrypoints**, and **{len(commands)} Chrome runtime commands**. The companion JSON includes the full tool input schemas and Cognitive Domain Registry.",'',
       'This is a source audit of the matching build. User-enabled policies, installed custom plugins, active models, approved websites, and the currently deployed connection are checked by the live diagnostic, not inferred from repository code.','',
       '## Research systems already present','',
       '| System | Existing behavior | Authority and integration |','|---|---|---|',
       '| HomeServer Local Research | `local.research`: knowledge search and durable memory reads. Local File Reader and Relationship Context provide additional evidence. | HomeServer permissions and private context scopes; these are existing skills. |',
       '| HomeServer browser specialists | `browser.read`: one reviewed public HTTPS URL per worker; separate origin grant, isolated capture, read budgets, citations and owner controls. | A5C11 connects this existing reader to Chat preparation. No website opens during planning. |',
       '| Chrome Browser Research Agent v22.30 | Approved domain envelope, multi-site source plans, bounded pages/claims, freshness, deduplication, corroboration, conflicts, memo, report and governed handoffs. | Existing Browser Companion delegation and research runtime remains active. |',
       '| Chat Chrome Research R2 | Cloud and native Chat review an explicit external source search within approved websites or up to five supplied URLs, then review the frozen source plan and queue existing Chrome research, display one evidence-based brief and save a reviewed report draft. | Current Chrome runtime grants, exact plan approval, exclusive expiring reader lease, native Cloud/privacy checks, receipt reuse and separate write review. No new generic model tool or parallel evidence ledger. |',
       '| Cloud Research Projects v20.60 | Projects, memberships, source versions, annotations, findings, evidence links and reports. | Cloud project membership and publishing controls. Existing Chrome research saves/handoffs use this system. |',
       '| Cloud Booking Agent discovery | Legacy public DuckDuckGo HTML search for venue/market leads. | This discovery helper is separate from HomeServer browser authority; no new parallel search engine was added. |',
       '| Proactive Agent and Brain | Context-aware suggestions, commitments, rescore/objectives, activity awareness, scheduled missions and workflow events. | Suggestions and schedules retain current permissions and review gates; scheduled browser grants are not reused automatically. |','',
       '## Execution boundaries','',
       '| Layer | Available scope | Gates |','|---|---|---|',
       '| Generic HomeServer Agent | Registered tools and installed, authorized plugin tools; model schemas are a filtered subset. | Global Agent tool policy, per-tool policy, pairing/app scope, model privacy, and exact write approvals. |',
       '| Temporary specialists | '+', '.join('`'+k+'`' for k in contracts['CAPABILITIES'])+' | 1–4 workers; cumulative 0–3 reads each; explicit assignment review. Browser workers need a captured page and citations. |',
       '| Specialist change proposals | '+', '.join('`'+k+'`' for k in actions['ACTION_TOOLS'])+' | Separate 0–3 proposal budget; exact reviewed changes; revision checks; duplicate prevention and independent readback. Deletes, payments and messaging are excluded. |',
       '| Chrome workflow/runtime | Existing explicit delegation, domain policies, controlled interactions, research and transaction workflows. | Chrome website permission and server-side authority are independent. A HomeServer mission never inherits a Chrome grant. |','',
       'A request with a known website can prepare browser research directly. If a CRM request has no supported website URL, the lead prepares record investigation first. Website discovery results require a further reviewed browser plan and fresh website approval. Private record text is not authorized for URL queries.','',
       '## Named HomeServer skills','', '| Skill | Purpose | Tools | Source |','|---|---|---|---|']
for skill in sorted(skills,key=lambda s:s['key']):lines.append('| '+ ' | '.join(cell(v) for v in [skill['key'],skill['description'],', '.join(skill['tools']),skill['source']])+' |')
lines+=['','## Complete registered HomeServer tool inventory','',
        '“Owner” below means an owner-only tool. Other tools also require `tools.execute` for a paired app, their listed permissions, current app scope, and enabled tool policy. Write mode identifies a consequential tool; its Agent path prepares a governed proposal rather than treating model output as permission. Scheduling/commerce tools additionally require their configured connector.','',
        '| Tool | Name | Mode | Owner only | Required domain permissions | Source |','|---|---|---|---|---|---|']
for tool in sorted(tool_catalog.values(),key=lambda t:t['key']):lines.append('| '+' | '.join(cell(v) for v in [tool['key'],tool['name'],tool['mode'],'Yes' if tool.get('owner_only') else 'No',', '.join(tool['required_permissions']) or 'Owner authority',tool['source']])+' |')
lines+=['','## Canonical Cloud Chat handler inventory','',
        'These are deterministic Cloud handler entrypoints, not HomeServer tool keys. They cover installed app management, hosting/system apps, media, scheduling, Profile/WebMCP, Tracky and existing Cloud domains through the canonical authorization boundary. The JSON preserves the exact entrypoint and source for every row.','', '| Entrypoint | Source |','|---|---|']
for item in handler_catalog:lines.append('| '+cell(item['entrypoint'])+' | '+cell(item['source'])+' |')
lines+=['','## Chrome runtime command inventory','',
        'Commands are extension message handlers, not permission grants. Every protected API request still authenticates the paired browser device and checks current capabilities. The existing research and transaction engines retain their domain/envelope controls.','', '| Command group | Complete commands |','|---|---|']
groups={}
for command in commands:groups.setdefault(command.split('_')[0],[]).append(command)
for group,items in sorted(groups.items()):lines.append('| '+cell(group)+' | '+', '.join('`'+x+'`' for x in items)+' |')
lines+=['','## Cloud plugin and Cognitive Domain registries','', '| Cloud plugin | Registered definition |','|---|---|']
for key,value in plugins.items():lines.append('| '+cell(key)+' | '+cell(json.dumps(value,ensure_ascii=False))+' |')
lines+=['','| Cognitive domain | Definition summary |','|---|---|']
for key,value in domains['domains'].items():lines.append('| '+cell(key)+' | '+cell(value.get('label') or value.get('name') or value.get('title') or json.dumps(value,ensure_ascii=False))+' |')
lines+=['','## Installed integration diagnostic','',
        'Run **Check integration** on the Cloud or HomeServer Chat canvas, or **Settings → Check Cloud and HomeServer integration** in Chrome. HomeServer also exposes the owner-only `integration.diagnostics` Agent tool.','',
        '| Evidence | What is actually checked |','|---|---|',
        '| Chrome connection | Configured installation folder is preserved; granted host permission; authenticated device-token request; stable extension identity; timeout/network/CORS/HTML/authentication failure classification. |',
        '| Cloud release | Installed A5C11 release manifest and minimum mission schema 79. |',
        '| Cloud ↔ HomeServer | Current heartbeat plus an authenticated round trip with a fresh nonce; matching diagnostic contract. |',
        '| HomeServer runtime | Actual database schema, running build hash, Agent policy, app scope, managed browser verification, configured inference readiness and registered tools/skills. |',
        '| Sync | Account/session-bound sync state and last success; a connected heartbeat does not imply synchronized data. Replicated schedules do not execute twice. |',
        '| Privacy and side effects | No model prompts, browser capture, record edits, repair action, credentials or raw local paths. Diagnostic relay receipts are operational metadata. |','',
        'A blocked or not-verified check stays visible with a next action. Source code availability is not presented as proof of live hardware, network access, model inference, or installed connection health.','',
        '## Connection error recovery','',
        '- For an installation in a folder, save its full base URL in Chrome Settings (for example, `https://your-domain.example/vp3`). The previous extension dropped that folder.','- Missing Chrome host permission is reported without attempting a permission prompt during background polling. Save the site from Settings or use Connect to grant access.','- A temporary network/CORS/timeout/permission error retains the existing token. A server-confirmed 401 revocation clears the revoked pairing and asks for reconnect.','- A login page or other non-JSON API response identifies URL/deployment/redirect trouble. Credentials are not forwarded through redirects.','- If extension-to-Cloud succeeds while HomeServer is disconnected or stale, the integration report keeps those states separate.','- If the saved site is unreachable and a retained token prevents changing it, Settings offers a confirmed local-pairing reset. It preserves unrelated preferences and reports that the old Cloud device authorization is not revoked.','',
        '## Certification scope','',
        'Automated acceptance exercises production PHP mission endpoints and readback, paired dispatcher, real SQLite contacts and approvals, source receipt persistence, duplicate retries, current permissions, Chromium Chat UI, extension connection logic, existing Browser Companion research, and Windows packaging/upgrade gates. Authentication, model responses, and web-navigation content are deterministic test fixtures. The installed-user diagnostic must run on the installed systems to establish their current health.']
(args.output/'Agent-Skills-and-Tools-A5C11.md').write_text('\n'.join(lines)+'\n')
print(json.dumps({'tools':len(tool_catalog),'skills':len(skills),'cloud_handlers':len(handler_catalog),'chrome_commands':len(commands),'cognitive_domains':len(domains['domains'])}))
