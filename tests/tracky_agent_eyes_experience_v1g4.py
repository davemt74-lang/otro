"""Consent delivery explanations never create a camera or remote authority."""
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.services import tracky_agent_eyes_shared_scene as shared
for enabled,delivery,reason,expected in [
 (False,'not_requested','never_shared','No scene has been shared'),
 (False,'pending','owner_revoked','revocation is pending'),
 (False,'acknowledged','owner_revoked','confirmed sharing is off'),
 (True,'pending','recent_observation','not acknowledged'),
 (True,'acknowledged','observation_expired','does not make an expired scene current'),
 (True,'acknowledged','model_review_required','model integrity'),
 (True,'pending','session_stopped','camera release'),
]:
 e=shared.experience(enabled=enabled,delivery=delivery,reason=reason)
 assert expected in e['guidance'],e
 assert e['capture_authority'] is False and e['automatic_recovery'] is False
package={'revision':4,'fingerprint':'a'*64,'summary':{'state':'revoked','reason':'owner_revoked'}}
with patch.object(shared,'snapshot',return_value=package),patch.object(shared,'_read',return_value={'acknowledged_revision':3}),patch.object(shared,'_PROCESS_CONSENT',False):
 s=shared.status();assert s['delivery']=='pending' and s['reason']=='owner_revoked'
 assert 'revocation is pending' in s['experience']['guidance']
 assert 'objects' not in s and s['raw_media_exported'] is False
print('TRACKY_AGENT_EYES_EXPERIENCE_V1G4: local consent, pending revocation, acknowledgment, expiry/review/recovery guidance and no authority PASS')
