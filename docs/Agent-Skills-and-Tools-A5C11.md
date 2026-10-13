# Agent skills and tools — A5C11

The source inventory contains **72 registered HomeServer tools**, **14 named HomeServer skills**, **21 canonical Cloud Chat handler entrypoints**, and **79 Chrome runtime commands**. The companion JSON includes the full tool input schemas and Cognitive Domain Registry.

This is a source audit of the matching build. User-enabled policies, installed custom plugins, active models, approved websites, and the currently deployed connection are checked by the live diagnostic, not inferred from repository code.

## Research systems already present

| System | Existing behavior | Authority and integration |
|---|---|---|
| HomeServer Local Research | `local.research`: knowledge search and durable memory reads. Local File Reader and Relationship Context provide additional evidence. | HomeServer permissions and private context scopes; these are existing skills. |
| HomeServer browser specialists | `browser.read`: one reviewed public HTTPS URL per worker; separate origin grant, isolated capture, read budgets, citations and owner controls. | A5C11 connects this existing reader to Chat preparation. No website opens during planning. |
| Chrome Browser Research Agent v22.30 | Approved domain envelope, multi-site source plans, bounded pages/claims, freshness, deduplication, corroboration, conflicts, memo, report and governed handoffs. | Existing Browser Companion delegation and research runtime remains active. |
| Chat Chrome Research R3 | Cloud and native Chat review an explicit external source search within approved websites or up to five supplied URLs, then review the frozen source plan and queue existing Chrome research, display cited observed/inferred/conflicting claims, save a reviewed report draft, retrieve saved evidence under current project/site grants and compare captured observations without rereading. | Current Chrome runtime grants, exact plan approval, exclusive expiring reader lease, native Cloud/privacy checks, receipt reuse and separate write review. No new generic model tool or parallel evidence ledger. |
| Cloud Research Projects v20.60 | Projects, memberships, source versions, annotations, findings, evidence links and reports. | Cloud project membership and publishing controls. Existing Chrome research saves/handoffs use this system. |
| Chrome annotations R4 | Highlighted quotes and notes delivered to existing Agent Chat, with optional Research project and Knowledge folder/tags. | Canonical captures and exact atomic writes; Knowledge and capture references use existing account-bound workspace replicas; native Chat and Brain retain privacy and pairing fences. |
| Project Knowledge R5 | Search annotations by text/project/folder/tag/date, use authorized quotes in Chat, review organization edits, retrieve original source citations and confirm exact synchronization revisions. | Existing capture and Knowledge identities, atomic revision edits, current owner/pairing/privacy checks, no automatic model turn and hash-only workspace receipts. |
| Cloud Booking Agent discovery | Legacy public DuckDuckGo HTML search for venue/market leads. | This discovery helper is separate from HomeServer browser authority; no new parallel search engine was added. |
| Proactive Agent and Brain | Context-aware suggestions, commitments, rescore/objectives, activity awareness, scheduled missions and workflow events. | Suggestions and schedules retain current permissions and review gates; scheduled browser grants are not reused automatically. |

## Execution boundaries

| Layer | Available scope | Gates |
|---|---|---|
| Generic HomeServer Agent | Registered tools and installed, authorized plugin tools; model schemas are a filtered subset. | Global Agent tool policy, per-tool policy, pairing/app scope, model privacy, and exact write approvals. |
| Temporary specialists | `browser.read`, `knowledge.search`, `contacts.search`, `tasks.list`, `calendar.list`, `workspace.search`, `workspace.get` | 1–4 workers; cumulative 0–3 reads each; explicit assignment review. Browser workers need a captured page and citations. |
| Specialist change proposals | `contacts.create`, `contacts.update`, `knowledge.create`, `knowledge.update`, `tasks.create`, `tasks.update`, `calendar.create`, `calendar.update`, `workspace.update` | Separate 0–3 proposal budget; exact reviewed changes; revision checks; duplicate prevention and independent readback. Deletes, payments and messaging are excluded. |
| Chrome workflow/runtime | Existing explicit delegation, domain policies, controlled interactions, research and transaction workflows. | Chrome website permission and server-side authority are independent. A HomeServer mission never inherits a Chrome grant. |

A request with a known website can prepare browser research directly. If a CRM request has no supported website URL, the lead prepares record investigation first. Website discovery results require a further reviewed browser plan and fresh website approval. Private record text is not authorized for URL queries.

## Named HomeServer skills

| Skill | Purpose | Tools | Source |
|---|---|---|---|
| app.update.manager | Review HomeServer app install, update, rollback, compatibility and recovery status before governed app changes. | apps.update-center, apps.update.review, apps.prebuilt.list | app/services/tools.py |
| homeserver.health | Diagnose HomeServer health and map issues only to existing governed repair actions. | health.status, health.repair-plan, storage.status, backups.status, apps.status, integration.diagnostics | app/services/tools.py |
| local.file-management | Read tracked local files and propose bounded updates or deletions through HomeServer policy and approvals. | files.list, files.read, files.update, files.delete | app/services/local_file_actions_tools.py |
| local.files | Discover and read bounded indexed text from owner-approved local files without arbitrary filesystem access. | files.list, files.read | app/services/tools.py |
| local.research | Search private knowledge and read durable memory without leaving HomeServer. | knowledge.search, memory.list | app/services/tools.py |
| memory.manager | Read and govern durable Agent Brain memory through explicit local capabilities. | memory.list, memory.write, memory.update, memory.delete | app/services/tools.py |
| relationship.context | Search private contacts and relationship notes through an explicit read capability. | contacts.search, contacts.create, contacts.update, contacts.delete | app/services/tools.py |
| room.automation | Read local rooms/devices and propose governed physical device commands. | devices.list, devices.command | app/services/tools.py |
| task.manager | Read tasks and notifications and create durable reminders through explicit capabilities. | tasks.list, notifications.list, tasks.create | app/services/tools.py |
| vp3.booking-management | Find time and propose creating, rescheduling, or cancelling VP3 bookings through HomeServer approvals. | vp3.schedule.overview, vp3.schedule.availability, vp3.booking.create, vp3.booking.reschedule, vp3.booking.cancel | app/services/vp3_scheduling_tools.py |
| vp3.calendar-review | Review upcoming personal and Team bookings and scheduling configuration. | vp3.schedule.overview | app/services/vp3_scheduling_tools.py |
| vp3.commerce-operations | Review canonical VP3 orders and propose bounded fulfillment updates through local approval. | vp3.commerce.orders.list, vp3.commerce.order.get, vp3.commerce.fulfillment.update | app/services/vp3_commerce_agent_tools.py |
| vp3.commerce-selling | Search the canonical VP3 catalog, explain offers and prepare a buyer-controlled checkout handoff. | vp3.commerce.catalog.search, vp3.commerce.product.get, vp3.commerce.checkout.handoff | app/services/vp3_commerce_agent_tools.py |
| vp3.find-time | Find conflict-safe personal or Team appointment times using VP3 scheduling and connected calendars. | vp3.schedule.availability | app/services/vp3_scheduling_tools.py |

## Complete registered HomeServer tool inventory

“Owner” below means an owner-only tool. Other tools also require `tools.execute` for a paired app, their listed permissions, current app scope, and enabled tool policy. Write mode identifies a consequential tool; its Agent path prepares a governed proposal rather than treating model output as permission. Scheduling/commerce tools additionally require their configured connector.

| Tool | Name | Mode | Owner only | Required domain permissions | Source |
|---|---|---|---|---|---|
| apps.actions | List App Agent Actions | read | No | apps.read | app/services/tools.py |
| apps.build_install | Build and Install User App | write | No | apps.manage | app/services/tools.py |
| apps.compatibility | Read App Control Compatibility | read | No | apps.read | app/services/tools.py |
| apps.git.inspect | Inspect Git App Source | write | No | apps.manage | app/services/tools.py |
| apps.hosting.status | Read App Hosting Bindings | read | No | apps.read | app/services/tools.py |
| apps.invoke | Invoke Installed App Action | write | No | apps.manage | app/services/tools.py |
| apps.invoke.read | Invoke Read-Only App Action | read | No | apps.read | app/services/tools.py |
| apps.list | List HomeServer Apps | read | No | apps.read | app/services/tools.py |
| apps.permission.set | Set App Permission | write | No | apps.manage | app/services/tools.py |
| apps.prebuilt.install | Install VP3 Prebuilt App | write | No | apps.manage | app/services/tools.py |
| apps.prebuilt.list | List VP3 Prebuilt Apps | read | No | apps.read | app/services/tools.py |
| apps.recover | Recover User App | write | No | apps.manage | app/services/tools.py |
| apps.releases | List HomeServer App Releases | read | No | apps.read | app/services/tools.py |
| apps.rollback | Rollback User App | write | No | apps.manage | app/services/tools.py |
| apps.settings.get | Read App Settings | read | No | apps.read | app/services/tools.py |
| apps.settings.set | Update App Settings | write | No | apps.manage | app/services/tools.py |
| apps.source.detach | Detach App Source | write | No | apps.manage | app/services/tools.py |
| apps.source.install | Install Inspected App Source | write | No | apps.manage | app/services/tools.py |
| apps.source.status | Read HomeServer App Source | read | No | apps.read | app/services/tools.py |
| apps.start | Start User App | write | No | apps.manage | app/services/tools.py |
| apps.status | Read HomeServer App Status | read | No | apps.read | app/services/tools.py |
| apps.stop | Stop User App | write | No | apps.manage | app/services/tools.py |
| apps.update-center | Read App Update Center | read | Yes | Owner authority | app/services/tools.py |
| apps.update.review | Review App Update | read | Yes | Owner authority | app/services/tools.py |
| backups.status | Read Backup Protection Status | read | Yes | Owner authority | app/services/tools.py |
| calendar.create | Create HomeServer Calendar Event | write | No | events.write | app/services/tools.py |
| calendar.delete | Delete HomeServer Calendar Event | write | No | events.write | app/services/tools.py |
| calendar.list | Read HomeServer Calendar | read | No | events.read | app/services/tools.py |
| calendar.update | Update HomeServer Calendar Event | write | No | events.write | app/services/tools.py |
| contacts.create | Create HomeServer Contact | write | No | contacts.write | app/services/tools.py |
| contacts.delete | Delete HomeServer Contact | write | No | contacts.write | app/services/tools.py |
| contacts.search | Search Contacts | read | No | contacts.read | app/services/tools.py |
| contacts.update | Update HomeServer Contact | write | No | contacts.write | app/services/tools.py |
| devices.command | Control Local Device | write | No | devices.control | app/services/tools.py |
| devices.list | Read Rooms & Devices | read | No | devices.read | app/services/tools.py |
| files.delete | Delete Local File | write | No | files.write | app/services/local_file_actions_tools.py |
| files.list | List Local Files | read | No | files.read | app/services/tools.py |
| files.read | Read Local File | read | No | files.read | app/services/tools.py |
| files.update | Update Local File | write | No | files.write | app/services/local_file_actions_tools.py |
| health.issue | Read Current HomeServer Health Issue | read | Yes | Owner authority | app/services/tools.py |
| health.repair-plan | Read HomeServer Repair Plan | read | Yes | Owner authority | app/services/tools.py |
| health.status | Read HomeServer Health | read | Yes | Owner authority | app/services/tools.py |
| integration.diagnostics | Inspect Cloud and HomeServer Integration | read | Yes | Owner authority | app/services/tools.py |
| knowledge.create | Create HomeServer Knowledge | write | No | knowledge.write | app/services/tools.py |
| knowledge.delete | Delete HomeServer Knowledge | write | No | knowledge.write | app/services/tools.py |
| knowledge.search | Search Knowledge | read | No | knowledge.search | app/services/tools.py |
| knowledge.update | Update HomeServer Knowledge | write | No | knowledge.write | app/services/tools.py |
| memory.delete | Delete Agent Brain Memory | write | No | memory.write | app/services/tools.py |
| memory.list | Read Agent Brain Memory | read | No | memory.read | app/services/tools.py |
| memory.update | Update Agent Brain Memory | write | No | memory.write | app/services/tools.py |
| memory.write | Create Agent Brain Memory | write | No | memory.write | app/services/tools.py |
| notifications.list | Read Notifications | read | No | notifications.read | app/services/tools.py |
| runtime.diagnostics | Inspect Installed HomeServer Runtime Diagnostics | read | Yes | Owner authority | app/services/tools.py |
| storage.status | Read HomeServer Storage Status | read | Yes | Owner authority | app/services/tools.py |
| tasks.create | Create Task | write | No | tasks.write | app/services/tools.py |
| tasks.delete | Delete HomeServer Task | write | No | tasks.write | app/services/tools.py |
| tasks.list | Read Tasks | read | No | tasks.read | app/services/tools.py |
| tasks.update | Update HomeServer Task | write | No | tasks.write | app/services/tools.py |
| vp3.booking.cancel | Cancel VP3 Booking | write | No | scheduling.write | app/services/vp3_scheduling_tools.py |
| vp3.booking.create | Create VP3 Booking | write | No | scheduling.write | app/services/vp3_scheduling_tools.py |
| vp3.booking.reschedule | Reschedule VP3 Booking | write | No | scheduling.write | app/services/vp3_scheduling_tools.py |
| vp3.commerce.catalog.search | Search VP3 Commerce Catalog | read | No | commerce.read | app/services/vp3_commerce_agent_tools.py |
| vp3.commerce.checkout.handoff | Prepare VP3 Checkout Handoff | read | No | commerce.order | app/services/vp3_commerce_agent_tools.py |
| vp3.commerce.fulfillment.update | Update VP3 Commerce Fulfillment | write | No | commerce.fulfill | app/services/vp3_commerce_agent_tools.py |
| vp3.commerce.order.get | Get VP3 Commerce Order | read | No | commerce.read | app/services/vp3_commerce_agent_tools.py |
| vp3.commerce.orders.list | List VP3 Commerce Orders | read | No | commerce.read | app/services/vp3_commerce_agent_tools.py |
| vp3.commerce.product.get | Get VP3 Commerce Product | read | No | commerce.read | app/services/vp3_commerce_agent_tools.py |
| vp3.schedule.availability | VP3 Scheduling Availability | read | No | scheduling.read | app/services/vp3_scheduling_tools.py |
| vp3.schedule.overview | VP3 Calendar Overview | read | No | scheduling.read | app/services/vp3_scheduling_tools.py |
| workspace.get | Read Synced Record for Editing | read | Yes | Owner authority | app/services/tools.py |
| workspace.search | Search Synced Cloud Workspaces | read | Yes | Owner authority | app/services/tools.py |
| workspace.update | Update Synced Cloud Record | write | Yes | Owner authority | app/services/tools.py |

## Canonical Cloud Chat handler inventory

These are deterministic Cloud handler entrypoints, not HomeServer tool keys. They cover installed app management, hosting/system apps, media, scheduling, Profile/WebMCP, Tracky and existing Cloud domains through the canonical authorization boundary. The JSON preserves the exact entrypoint and source for every row.

| Entrypoint | Source |
|---|---|
| agent_scheduling_tools_query_v460 | includes/agent-scheduling-tools-v460.php |
| agent_team_scheduling_tools_query_v610 | includes/agent-team-scheduling-tools-v610.php |
| homeserver_agent_read_v230_query | includes/homeserver-agent-read-v230.php |
| tracky_agent_tools_query_v271 | includes/tracky-agent-v271.php |
| vp3_app_control_agent_query_v290 | includes/system-apps-v290.php |
| vp3_app_manager_query_v260 | includes/system-apps-v260.php |
| vp3_download_manager_agent_query_v320 | includes/system-apps-v320.php |
| vp3_media_processor_agent_query_v330 | includes/system-apps-v330.php |
| vp3_media_server_agent_query_v270 | includes/system-apps-v270.php |
| vp3_music_server_agent_query_v300 | includes/system-apps-v300.php |
| vp3_photo_library_agent_query_v310 | includes/system-apps-v310.php |
| vp3_profile_webmcp_return_followthrough_v195 | includes/profile-webmcp-continuity-v195.php |
| vp3_system_apps_agent_action_query_v160 | includes/system-apps-v160.php |
| vp3_system_apps_agent_query_v150 | includes/system-apps-v150.php |
| vp3_system_apps_health_query_v170 | includes/system-apps-v170.php |
| vp3_system_apps_permission_query_v210 | includes/system-apps-v210.php |
| vp3_system_apps_release_query_v200 | includes/system-apps-v200.php |
| vp3_user_app_share_agent_query_v250 | includes/system-apps-v250.php |
| vp3_user_app_workspace_query_v230 | includes/system-apps-v230.php |
| vp3_user_apps_agent_query_v220 | includes/system-apps-v220.php |
| vp3_video_editor_agent_query_v280 | includes/system-apps-v280.php |

## Chrome runtime command inventory

Commands are extension message handlers, not permission grants. Every protected API request still authenticates the paired browser device and checks current capabilities. The existing research and transaction engines retain their domain/envelope controls.

| Command group | Complete commands |
|---|---|
| agent | `agent_workspace` |
| capture | `capture`, `capture_r4`, `capture_region` |
| chat | `chat_research_action`, `chat_research_run` |
| clear | `clear_pending_capture` |
| cognitive | `cognitive_action`, `cognitive_now` |
| connect | `connect` |
| context | `context_handoff`, `context_now` |
| delegation | `delegation_action`, `delegation_navigate` |
| destinations | `destinations` |
| disconnect | `disconnect` |
| execution | `execution_action` |
| following | `following` |
| integration | `integration_diagnostics` |
| live | `live_action`, `live_poll`, `live_rooms` |
| local | `local_screenshot_cancel`, `local_screenshot_start`, `local_screenshot_status` |
| media | `media_data` |
| memory | `memory_action` |
| multisite | `multisite_attach`, `multisite_fact_add`, `multisite_handoff`, `multisite_policy`, `multisite_state` |
| notification | `notification_poll` |
| open | `open_url` |
| poll | `poll_connect` |
| project | `project_knowledge_r5` |
| quick | `quick_action_consume`, `quick_action_run` |
| research | `research_action`, `research_analyze_current`, `research_context`, `research_runtime_action` |
| reset | `reset_local_connection` |
| runtime | `runtime_action`, `runtime_close_tabs`, `runtime_navigate` |
| search | `search_action`, `search_discover`, `search_query`, `search_recent`, `search_saved` |
| set | `set_base_url` |
| share | `share`, `share_action` |
| source | `source_action` |
| state | `state` |
| tab | `tab_identity` |
| this | `this_page` |
| transaction | `transaction_action`, `transaction_continuity_action`, `transaction_continuity_list`, `transaction_continuity_scan`, `transaction_control_action`, `transaction_control_status`, `transaction_execute`, `transaction_intelligence_action`, `transaction_intelligence_inbox`, `transaction_outcome_action`, `transaction_outcome_recheck`, `transaction_outcome_resolve`, `transaction_review` |
| trust | `trust_action`, `trust_claims`, `trust_history`, `trust_notifications`, `trust_observe` |
| web | `web_interaction_action`, `web_interaction_execute`, `web_interaction_observe` |

## Cloud plugin and Cognitive Domain registries

| Cloud plugin | Registered definition |
|---|---|
| music_workspace | {"label": "Music Workspace", "description": "Tracks, albums, releases, team collaboration, production and music-supervisor tools.", "entitlement": "music_workspace.access"} |
| campaigns_rewards | {"label": "Campaigns & Rewards", "description": "Merchant accounts, campaigns, rewards, claim codes, customers, landing pages and reporting.", "entitlement": "campaigns_rewards.access"} |
| tracky | {"label": "Tracky", "description": "Governed physical awareness from your paired OTRO HomeServer using the physical_context.v1 contract.", "entitlement": "tracky.access"} |

| Cognitive domain | Definition summary |
|---|---|
| agent_brain_history | Agent Brain History |
| agent_chat | Agent Chat |
| analytics_attribution | Analytics Attribution |
| booking_appointments | Booking Appointments |
| browser_operations | Browser Operations |
| calendar | Calendar |
| campaigns_rewards | Campaigns & Rewards |
| client_release | Client Release |
| commerce | Commerce |
| crm_relationships | Crm Relationships |
| homeserver_operations | Homeserver Operations |
| media_studio | Media Studio |
| meetings | Meetings |
| messaging_team | Messaging Team |
| notifications | Notifications |
| profile_agent | Profile Agent |
| research_knowledge | Research Knowledge |
| scheduling | Scheduling |
| session | Session |
| subscription_billing | Subscription Billing |
| workflow_tools_approvals | Workflow Tools Approvals |

## Installed integration diagnostic

Run **Check integration** on the Cloud or HomeServer Chat canvas, or **Settings → Check Cloud and HomeServer integration** in Chrome. HomeServer also exposes the owner-only `integration.diagnostics` Agent tool.

| Evidence | What is actually checked |
|---|---|
| Chrome connection | Configured installation folder is preserved; granted host permission; authenticated device-token request; stable extension identity; timeout/network/CORS/HTML/authentication failure classification. |
| Cloud release | Installed A5C11 release manifest and minimum mission schema 79. |
| Cloud ↔ HomeServer | Current heartbeat plus an authenticated round trip with a fresh nonce; matching diagnostic contract. |
| HomeServer runtime | Actual database schema, running build hash, Agent policy, app scope, managed browser verification, configured inference readiness and registered tools/skills. |
| Sync | Account/session-bound sync state and last success; a connected heartbeat does not imply synchronized data. Replicated schedules do not execute twice. |
| Privacy and side effects | No model prompts, browser capture, record edits, repair action, credentials or raw local paths. Diagnostic relay receipts are operational metadata. |

A blocked or not-verified check stays visible with a next action. Source code availability is not presented as proof of live hardware, network access, model inference, or installed connection health.

## Connection error recovery

- For an installation in a folder, save its full base URL in Chrome Settings (for example, `https://your-domain.example/vp3`). The previous extension dropped that folder.
- Missing Chrome host permission is reported without attempting a permission prompt during background polling. Save the site from Settings or use Connect to grant access.
- A temporary network/CORS/timeout/permission error retains the existing token. A server-confirmed 401 revocation clears the revoked pairing and asks for reconnect.
- A login page or other non-JSON API response identifies URL/deployment/redirect trouble. Credentials are not forwarded through redirects.
- If extension-to-Cloud succeeds while HomeServer is disconnected or stale, the integration report keeps those states separate.
- If the saved site is unreachable and a retained token prevents changing it, Settings offers a confirmed local-pairing reset. It preserves unrelated preferences and reports that the old Cloud device authorization is not revoked.

## Certification scope

Automated acceptance exercises production PHP mission endpoints and readback, paired dispatcher, real SQLite contacts and approvals, source receipt persistence, duplicate retries, current permissions, Chromium Chat UI, extension connection logic, existing Browser Companion research, and Windows packaging/upgrade gates. Authentication, model responses, and web-navigation content are deterministic test fixtures. The installed-user diagnostic must run on the installed systems to establish their current health.

## R6 conversation Knowledge

Cloud, HomeServer and Chrome expose conversation project/folder scopes and up to six annotation or saved-report attachments. The `project_knowledge_r6` Chrome command and paired native proxy use the canonical Cloud context endpoint. Sources are reauthorized and revisions rechecked before and after inference. Native local collection grants remain separate; Cloud folder IDs are resolved canonically. Scope changes store preferences only and do not execute a model turn. Existing captures, Knowledge and Research ledgers retain evidence identity.
