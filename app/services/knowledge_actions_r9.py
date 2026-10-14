"""Ordinary owner Chat drafts through the existing canonical Cloud action proxy."""
import re
from . import knowledge_actions_r8 as actions, workspace_sync, project_knowledge_r6 as r6

CONTRACT='vp3.knowledge-actions.r9'

def intent(text:str)->bool:
    return bool(re.search(r"^(?:(?:please\s+)|(?:(?:can|could|would)\s+you\s+))?(?:use|create|make|draft|prepare|update|change|add|turn)\b",text.strip(),re.I)
        and not re.search(r"\b(?:do not|don't|never|hypothetical|for example|example of|suppose|if we)\b",text,re.I)
        and re.search(r'\b(?:knowledge|highlights?|annotations?|sources?|evidence|notes?|findings|research)\b',text,re.I)
        and re.search(r'\b(?:tasks?|follow[ -]up|contacts?|crm|documents?)\b',text,re.I))

def chat(text:str,conversation:str,agent:int,settings:dict,read_only:bool,include_knowledge:bool,selected:dict|None=None,turn_id:int=0)->dict:
    blocked={'contract':CONTRACT,'drafts':[],'answer':'Enable Cloud and Knowledge access for this conversation to draft a reviewed Knowledge action.'}
    if read_only or not include_knowledge or not settings.get('cloud_allowed') or not settings.get('include_knowledge') or r6.local_mode(conversation)=='off':return blocked
    body={'action':'converse','local_conversation':conversation,'parent_agent_id':agent,'instruction':text,'references':(selected or {}).get('attachments',[]),'request_id':'native-message:'+str(turn_id)}
    try:return actions.request(body)['conversation_draft']
    except workspace_sync.WorkspaceSyncError as exc:raise workspace_sync.WorkspaceSyncError(str(exc),exc.status_code) from exc

def verify(value:dict,conversation:str,agent:int)->None:
    for draft in value.get('drafts',[]):
        keys=('operation','target_key','fields','references','state_revision','draft_fingerprint','target_revision')
        body={k:draft[k] for k in keys}
        actions.request({'action':'validate_draft','local_conversation':conversation,'parent_agent_id':agent,**body})
