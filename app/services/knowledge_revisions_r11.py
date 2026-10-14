"""Current native Chat metadata selects a draft; canonical Cloud validates every source."""
import hashlib,json,re
from ..database import db
from . import knowledge_actions_r9 as r9,workspace_sync
from .remote_identity import load_or_create_remote_identity

def intent(text:str)->bool:
    if re.search(r"\b(?:do not|don't|never|hypothetical|for example|suppose|if we)\b",text,re.I):return False
    match=re.search(r"^(?:(?:please\s+)|(?:(?:can|could|would)\s+you\s+))?(revise|rewrite|rephrase|shorten|expand|refine|make|change|format|rename)\b",text.strip(),re.I)
    if not match or not re.search(r'\b(?:it|draft|brief|document|comparison|summary)\b',text,re.I):return False
    if match[1].lower() in {'make','change'} and not re.search(r'\b(?:it|(?:this|that|the)\s+(?:draft|brief|document|comparison|summary))\b',text,re.I):return False
    return match[1].lower() not in {'make','change'} or bool(re.search(r'\b(?:shorter|longer|concise|formal|informal|bullets|title|tone|wording|paragraphs|length|headings?)\b',text,re.I))

def packet(conversation:str,selection:dict|None=None,text:str="",agent:int=0)->dict|None:
    with db() as c:
        row=c.execute("SELECT metadata_json FROM conversation_messages WHERE conversation_id=? AND role='assistant' ORDER BY id DESC LIMIT 1",(conversation,)).fetchone()
    try:value=json.loads(row['metadata_json'] or '{}').get('knowledge_actions_r9') if row else None
    except (TypeError,ValueError):value=None
    if not isinstance(value,dict):return None
    expected='home:'+hashlib.sha256(load_or_create_remote_identity()['device_id'].encode()).hexdigest()[:32]+':'+str(agent)+':'+conversation
    if value.get('contract')!=r9.CONTRACT or value.get('context_key')!=expected:raise workspace_sync.WorkspaceSyncError('The draft belongs to another current lead or conversation.',409)
    drafts=value.get('drafts')
    if not isinstance(drafts,list) or len(drafts)>3:raise workspace_sync.WorkspaceSyncError('Choose a current document draft.',409)
    if selection is not None:
        if not isinstance(selection,dict) or set(selection)-{'draft_id','fields','target_key'}:raise workspace_sync.WorkspaceSyncError('Choose a current draft.',422)
        if not any(d.get('draft_id')==selection.get('draft_id') for d in drafts if isinstance(d,dict)):raise workspace_sync.WorkspaceSyncError('The selected draft is no longer current.',409)
    if selection is not None:drafts=[d for d in drafts if isinstance(d,dict) and d.get('draft_id')==selection.get('draft_id')]
    elif len(drafts)>1:
        matches=[d for d in drafts if isinstance(d,dict) and d.get('operation')=='knowledge.create' and isinstance(d.get('fields'),dict) and d['fields'].get('title') and any(q+d['fields']['title'].casefold()+end in text.casefold() for q,end in [('"','"'),('“','”')])]
        if len(matches)==1:drafts=matches
        else:drafts=[{**d,'fields':{'title':d.get('fields',{}).get('title','')}} for d in drafts if isinstance(d,dict)]
    # Only bounded canonical identifiers and editable fields cross the paired proxy.
    keys={'draft_id','operation','target_key','target_revision','fields','references','state_revision','draft_fingerprint','revision_root_id','revision_of'}
    return {'contract':r9.CONTRACT,'context_key':value.get('context_key'),'drafts':[{k:v for k,v in d.items() if k in keys} for d in drafts if isinstance(d,dict)]}
