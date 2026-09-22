from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
brain_js = (ROOT / "ui" / "brain.js").read_text(encoding="utf-8")
brain_css = (ROOT / "ui" / "brain.css").read_text(encoding="utf-8")
brain_py = (ROOT / "app" / "services" / "brain.py").read_text(encoding="utf-8")
cards_py = (ROOT / "app" / "services" / "meeting_cards.py").read_text(encoding="utf-8")

for required in (
    "renderMeetingCard",
    "VP3 PHYSICAL MEETING",
    "Actions & commitments",
    "Task candidates",
    "Open questions",
    "Follow-up draft",
    "message?.card?.card_type === 'meeting'",
):
    assert required in brain_js, required

for required in (
    ".meeting-card",
    ".meeting-card-summary",
    ".meeting-card-section",
    ".meeting-card-status",
):
    assert required in brain_css, required

assert "_safe_message_card" in brain_py
assert '"card_type"' in brain_py
assert '"meeting"' in cards_py
assert "raw_audio" not in cards_py.lower()
assert "transcript_preview" not in cards_py
assert "conversation_messages" in cards_py

print("VP3 OS v0.40 meeting card UI contract passed")
