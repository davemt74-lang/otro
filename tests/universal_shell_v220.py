from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
read=lambda p:(ROOT/p).read_text(encoding="utf-8")

index=read("ui/index.html")
tasks=read("ui/tasks.html")
system=read("ui/system.html")
remote=read("ui/remote.html")
css=read("ui/universal-shell-v220.css")
shell=read("ui/universal-shell-v220.js")
legacy_shell=read("ui/shell.js")
brain=read("ui/brain.js")
voice=read("ui/chat-enhancements.js")
bridge=read("app/bridge.py")
config=read("app/config.py")
installer=read("installer/HomeServer.iss")

checks=[
 ("product version is 2.2",
  'version: str = "2.2"' in config and '#define MyAppVersion "2.2"' in installer),
 ("Cloud visual dimensions are preserved in the HomeServer shell",
  "--hs-v220-sidebar:272px" in css and "--hs-v220-topbar:58px" in css and "--hs-v220-canvas:790px" in css),
 ("Cloud visual tokens are used",
  "--hs-v220-side:#f8fafc" in css and "--hs-v220-line:#e5e7eb" in css and "--hs-v220-text:#111827" in css),
 ("persistent HomeServer pages load the universal shell",
  all("universal-shell-v220.css" in page and "universal-shell-v220.js" in page for page in (index,tasks,system,remote))),
 ("HomeServer universal navigation intentionally excludes reward tabs",
  all(term not in shell for term in ("INBOX","SENT","CLAIMED","Inbox","Sent","Claimed"))),
 ("standalone pages are wrapped without replacing their functional main element",
  "body > main" in shell and "appendChild(main)" in shell and "hs-v220-content" in shell),
 ("Agent Chat uses Cloud-style message structure",
  "hs-chat-avatar" in brain and "hs-chat-message-body" in brain and "hs-chat-role" in brain and "hs-chat-copy" in brain),
 ("Agent Chat uses a floating Cloud-sized composer",
  "#view-chat .chat-compose" in css and "position:absolute" in css and "var(--hs-v220-canvas)" in css),
 ("connection state becomes a deduplicated Agent Chat update",
  "homeserver:v2.2:cloud-presence-state" in legacy_shell and "appendPresenceMessage" in legacy_shell and
  "VP3 Cloud reconnected" in legacy_shell and "VP3 Cloud disconnected" in legacy_shell),
 ("connection update uses existing conversation voice only when voice is enabled",
  "HomeServerConversationVoice" in voice and "isEnabled" in voice and "speakStatus" in voice and
  "voice?.isEnabled?.()" in legacy_shell),
 ("HomeServer advertises unified execution routing",
  '"unified_execution"' in bridge and '"version": "2.2"' in bridge and
  '"unified.execution.routing.v1"' in bridge and '"local_domains"' in bridge),
 ("shared Agent context remains federated under v2.2",
  '"shared_agent_context"' in bridge and '"mode": "federated"' in bridge and '"version": "2.2"' in bridge),
]

for name,ok in checks:
    if not ok:
        raise AssertionError(name)
    print("PASS:",name)
print(f"HomeServer v2.2 universal Agent shell: {len(checks)}/{len(checks)} passed")
