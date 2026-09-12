from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
read = lambda p: (ROOT / p).read_text(encoding="utf-8")

contract_raw = read("contracts/commerce/commerce-agent-v1/contract.json")
contract = json.loads(contract_raw)
digest = hashlib.sha256(contract_raw.encode("utf-8")).hexdigest()
pinned = read("contracts/commerce/commerce-agent-v1/SHA256").strip().split()[0]
connector = read("app/services/vp3_commerce_agent_connector.py")
remote = read("app/services/vp3_commerce_agent_remote.py")
tools = read("app/services/vp3_commerce_agent_tools.py")
approvals = read("app/services/vp3_commerce_agent_approvals.py")
agent = read("app/services/vp3_commerce_agent_agent.py")
tools_api = read("app/tools_api.py")
pairing = read("app/services/pairing.py")

EXPECTED = "b61b1baea945286fb006ef78f7f798b4a604284145a74f71628d43779173bf77"
assert contract["contract"] == "commerce-agent-v1"
assert contract["canonical_owner"] == "vp3-cloud"
assert digest == pinned == EXPECTED
assert contract["permissions"]["vp3.commerce.checkout.handoff"] == "commerce.order"
assert contract["permissions"]["vp3.commerce.fulfillment.update"] == "commerce.fulfill"
assert contract["security"]["buyer_terms_acceptance_cloud_only"] is True
assert contract["security"]["fulfillment_mutation_requires_local_owner_approval"] is True

for permission in ("commerce.read", "commerce.order", "commerce.fulfill"):
    assert f'"{permission}"' in pairing
    assert permission in connector

assert f'CONTRACT_SHA256 = "{EXPECTED}"' in connector
assert '/api/homeserver-commerce-agent-v1000.php' in connector
assert 'follow_redirects=False' in connector
assert '_REQUIRED_PAIRING_PERMISSIONS' in connector
assert 'vp3.commerce.connector.configure' in remote
assert '_ALLOWED_FIELDS' in remote

assert '"vp3.commerce.checkout.handoff"' in tools
assert '"mode":"read"' in tools.replace(" ", "")
assert '"required_permissions":["commerce.order"]' in tools.replace(" ", "")
assert '"vp3.commerce.fulfillment.update"' in tools
assert '"mode":"write"' in tools.replace(" ", "")
assert '"required_permissions":["commerce.fulfill"]' in tools.replace(" ", "")
assert 'does not create an order' in tools.lower()
assert 'move money' in tools.lower()
compact_tools = tools.replace(" ", "")
assert 'original_list_tools(granted_permissions,owner=owner)' in compact_tools, "offline Commerce skill filtering must compose from the prior tool registry"
assert 'item=tool_items.get(tool_key)' in compact_tools, "offline connector filtering must not crash on another connector's skills"

assert 'hs-commerce-action-' in approvals
assert 'payload.pop("idempotency_key",None)' in approvals.replace(" ", "")
assert 'local HomeServer owner approval' in approvals
assert 'review_for_app' in approvals
assert 'fulfillment requires local HomeServer owner approval' in approvals
assert 'APPROVAL_ONLY_WRITE_TOOLS.add("vp3.commerce.fulfillment.update")' in agent
assert 'never invent or alter Cloud price or terms' in agent
assert 'does not create an order' in agent.lower()
assert 'move money' in agent.lower()

for module in (
    'vp3_commerce_agent_tools.install()',
    'vp3_commerce_agent_approvals.install()',
    'vp3_commerce_agent_agent.install()',
    'vp3_commerce_agent_remote.install()',
):
    assert module in tools_api
assert 'vp3_commerce_agent_approvals.ACTIONS' in tools_api

combined = "\n".join((connector, remote, tools, approvals, agent))
for forbidden in ('api.stripe.com', 'connect.squareup.com', 'api-m.paypal.com', 'sk_live_', 'whsec_'):
    assert forbidden not in combined

print("VP3_COMMERCE_AGENT_V061=PASS")
