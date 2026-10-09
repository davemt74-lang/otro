"""Real native writes and durable Cloud queue; model and HTTP responses are controlled."""
import hashlib, json, os, sys, tempfile, time, uuid
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

with tempfile.TemporaryDirectory(prefix='a5c4-') as tmp:
    os.environ['HOMESERVER_DATA_DIR']=tmp
    from app.database import db,initialize_database
    from app.services import agent_mission_runtime as mission,agent_mission_tool_contracts as contracts
    from app.services import agent_mission_orchestration as run,agent_mission_actions as actions
    from app.services import agent_mission_outcomes as outcomes,agent_mission_execution as inference
    from app.services import agent_tools,contacts,workspace_actions as queue,workspace_sync as sync
    initialize_database();mission._route=lambda *args:('ollama','fixture',False);agent_tools.save_policy(True,3,True)
    with db() as conn:
        agent=conn.execute('SELECT id FROM agents WHERE is_primary=1').fetchone()[0]
        conn.execute("INSERT INTO conversations(id,agent_id,source_app_key,title) VALUES('outcomes',?,'owner','Outcome verification')",(agent,))
        conn.execute("INSERT INTO conversation_context_settings(conversation_id,cloud_allowed) VALUES('outcomes',1)")
    def uid():return str(uuid.uuid4())
    def prepare(tool,args):
        record=mission.create_mission('owner',conversation_id='outcomes',objective='Verify an approved result',client_request_id=uid(),parent_agent_id=agent,owner=True,tasks=[{'role':'editor','title':'Editor','objective':'Prepare exact change','depends_on':[]}])
        assignments={'max_parallel':1,'assignments':[{'task_id':record['tasks'][0]['id'],'tools':[],'max_calls':0,'output':'analysis','browser_url':'','actions':[tool],'max_actions':1}]}
        contracts.configure('owner',record['id'],assignments,request_id=uid(),expected_revision=0,confirmed=True)
        inference.execute=lambda *a:(json.dumps({'kind':'analysis','title':'Prepared','body':'Pending owner review','citations':[],'actions':[{'tool':tool,'arguments':args}]}),'ollama','fixture')
        run.start('owner',record['id'],request_id=uid(),expected_revision=1,confirmed=True)
        deadline=time.monotonic()+15
        while mission.get_mission('owner',record['id'])['status']=='running' and time.monotonic()<deadline:time.sleep(.02)
        result=mission.get_mission('owner',record['id']);assert result['status']=='completed',result
        change=actions.list_actions('owner',record['id'])[0]
        assert change['outcome_state']=='awaiting_review' and not result['completion_report']['execution_verified']
        return record,change
    def approve(record,change):
        return actions.review('owner',record['id'],change['id'],expected_hash=change['payload_hash'],decision='approve',request_id=uid(),confirmed=True)[0]
    def report(record):return mission.get_mission('owner',record['id'])['completion_report']
    def denied(fn,code):
        try:fn()
        except mission.MissionError as e:assert e.status_code==code,(str(e),e.status_code)
        else:raise AssertionError('Expected rejection')
    # Independent readback uses original write receipt/revision for every native family.
    for tool,args in [('contacts.create',{'display_name':'Verified contact','email':'verified@example.test'}),('knowledge.create',{'title':'Verified document','content':'Saved content'}),('tasks.create',{'title':'Verified task'}),('calendar.create',{'title':'Verified event','start_at':'2030-01-02T10:00:00Z','end_at':'2030-01-02T11:00:00Z'})]:
        record,change=prepare(tool,args);saved=approve(record,change)
        assert saved['outcome_state']=='verified',(tool,saved)
        assert saved['receipt_revision']==saved['observed_revision'] and saved['verified_at'] and saved['checked_at']
        summary=report(record);assert summary['state']=='complete' and summary['verified_changes']==1 and summary['execution_verified'] and not summary['content_verified']
        with db() as conn:before=conn.execute("SELECT count(*) FROM agent_mission_events_v1 WHERE mission_id=? AND kind='worker.action_outcome'",(record['id'],)).fetchone()[0]
        report(record);report(record)
        with db() as conn:assert conn.execute("SELECT count(*) FROM agent_mission_events_v1 WHERE mission_id=? AND kind='worker.action_outcome'",(record['id'],)).fetchone()[0]==before
        kind=tool.split('.')[0];table,field=outcomes.NATIVE[kind]
        with db() as conn:
            mutation=json.loads(conn.execute('SELECT arguments_json FROM action_requests WHERE id=?',(change['approval_id'],)).fetchone()[0])['mutation_id']
            original=json.loads(conn.execute(f'SELECT result_json FROM {table} WHERE mutation_id=?',(mutation,)).fetchone()[0])[field]
        update_args={'canonical_id':original['canonical_id'],'expected_revision':original['record_revision'],('display_name' if kind=='contacts' else 'title'):'Verified update'}
        update_record,update_change=prepare(kind+'.update',update_args)
        updated=approve(update_record,update_change)
        assert updated['outcome_state']=='verified' and updated['receipt_revision']!=saved['receipt_revision'],updated
        initialize_database()
        assert actions.list_actions('owner',update_record['id'])[0]['verified_at']==updated['verified_at']
        if tool=='contacts.create':
            with db() as conn:
                stored=conn.execute('SELECT arguments_json FROM action_requests WHERE id=?',(change['approval_id'],)).fetchone()
                mutation=json.loads(stored['arguments_json'])['mutation_id']
                receipt=json.loads(conn.execute('SELECT result_json FROM federated_contact_mutations WHERE mutation_id=?',(mutation,)).fetchone()[0])['contact']
            contacts.update_contact(receipt['id'],{'display_name':'Changed later','email':'later@example.test'})
            assert actions.list_actions('owner',record['id'])[0]['outcome_state']=='superseded'
            assert report(record)['attention_changes']==1 and not report(record)['execution_verified']
            with db() as conn:conn.execute('DELETE FROM contacts WHERE id=?',(receipt['id'],))
            assert actions.list_actions('owner',record['id'])[0]['outcome_state']=='missing'
    # A source-scoped mutation receipt is mandatory; a successful approval flag alone is insufficient.
    record,change=prepare('contacts.create',{'display_name':'Receipt required'});approve(record,change)
    with db() as conn:
        args=json.loads(conn.execute('SELECT arguments_json FROM action_requests WHERE id=?',(change['approval_id'],)).fetchone()[0])
        conn.execute('DELETE FROM federated_contact_mutations WHERE mutation_id=?',(args['mutation_id'],))
    assert actions.list_actions('owner',record['id'])[0]['outcome_state']=='unverified'
    denied(lambda:outcomes.recover('owner',record['id'],change['id'],expected_hash=change['payload_hash'],request_id=uid(),confirmed=True),409)
    # Cloud approval, delivery acknowledgement and replica verification are separate milestones.
    binding={'peer_id':'fixture|42','session_hash':'s'*64,'authority_hash':'a'*64};replica={'record_revision':'b'*64,'data':{'name':'Original'}};session={'fixture':True}
    with patch.object(queue,'binding',return_value=binding),patch.object(queue,'record',return_value=replica),patch.object(sync,'wake') as wake:
        record,change=prepare('workspace.update',{'dataset':'contacts','key':'crm_contacts:1','expected_revision':'b'*64,'fields':{'display_name':'Verified Cloud edit'}})
        saved=approve(record,change);assert saved['outcome_state']=='queued' and not report(record)['execution_verified']
        with db() as conn:
            before=conn.execute('SELECT count(*) FROM workspace_sync_actions').fetchone()[0]
            row=dict(conn.execute('SELECT * FROM workspace_sync_actions ORDER BY rowid DESC LIMIT 1').fetchone())
        request=uid();recover=lambda rid=request:outcomes.recover('owner',record['id'],change['id'],expected_hash=change['payload_hash'],request_id=rid,confirmed=True)
        with db() as conn:conn.execute('UPDATE workspace_sync_actions SET fields_json=? WHERE mutation_id=?',(json.dumps({'display_name':'Tampered'}),row['mutation_id']))
        assert actions.list_actions('owner',record['id'])[0]['outcome_state']=='unverified'
        denied(lambda:recover(),409)
        with db() as conn:conn.execute('UPDATE workspace_sync_actions SET fields_json=? WHERE mutation_id=?',(row['fields_json'],row['mutation_id']))
        agent_tools.save_policy(True,3,False)
        denied(lambda:recover(),403)
        agent_tools.save_policy(True,3,True)
        for state in ('conflict','blocked','failed','cancelled'):
            with db() as conn:conn.execute('UPDATE workspace_sync_actions SET state=? WHERE mutation_id=?',(state,row['mutation_id']))
            assert not actions.list_actions('owner',record['id'])[0]['can_recover']
            denied(lambda:recover(),409)
        with db() as conn:conn.execute("UPDATE workspace_sync_actions SET state='queued' WHERE mutation_id=?",(row['mutation_id'],))
        denied(lambda:outcomes.recover('owner',record['id'],change['id'],expected_hash='0'*64,request_id=uid(),confirmed=True),409)
        denied(lambda:outcomes.recover('owner',record['id'],change['id'],expected_hash=change['payload_hash'],request_id=uid(),confirmed=False),422)
        wake.reset_mock()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:recover(),range(2)))
        assert all(r[0]['outcome_state']=='queued' for r in results) and wake.call_count==1
        with db() as conn:assert conn.execute('SELECT count(*) FROM workspace_sync_actions').fetchone()[0]==before
        denied(lambda:outcomes.recover('owner',record['id'],uid(),expected_hash=change['payload_hash'],request_id=request,confirmed=True),404)
        sent=[]
        def lost(*a):sent.append(a[-1]);raise sync.WorkspaceSyncError('Lost acknowledgement',503)
        with patch.object(sync,'_binding_active',return_value=True),patch.object(queue,'load_https_session',return_value=session),patch.object(sync,'_request',side_effect=lost):queue.deliver(None,session)
        assert actions.list_actions('owner',record['id'])[0]['outcome_state']=='queued'
        receipt={'account_id':'42','mutation_id':row['mutation_id'],'record_key':row['record_key'],'applied':True,'record_revision':'c'*64}
        def acknowledged(*a):sent.append(a[-1]);return receipt
        with patch.object(sync,'_binding_active',return_value=True),patch.object(queue,'load_https_session',return_value=session),patch.object(sync,'_request',side_effect=acknowledged):queue.deliver(None,session)
        assert len(sent)==2 and sent[0]==sent[1] and sent[0]['mutation_id']==row['mutation_id']
        assert actions.list_actions('owner',record['id'])[0]['outcome_state']=='applied' and not report(record)['execution_verified']
        payload={'records':[{'table':'crm_contacts','source_id':'1','record_revision':'c'*64,'data':{'name':'Verified Cloud edit'}}]}
        with db() as conn:conn.execute('INSERT INTO workspace_sync_snapshots(peer_id,dataset,revision,body_json,record_count,synced_at) VALUES(?,?,?,?,?,?)',(binding['peer_id'],'contacts','r1',json.dumps(payload),1,sync.now()))
        queue.reconcile(binding['peer_id'],'contacts',payload)
        assert actions.list_actions('owner',record['id'])[0]['outcome_state']=='verified' and report(record)['execution_verified']
        wake.reset_mock();recover();assert wake.call_count==0
        denied(lambda:recover(uid()),409)
        # Later source change keeps the write history but revokes current verification.
        payload['records'][0]['record_revision']='d'*64
        with db() as conn:conn.execute('UPDATE workspace_sync_snapshots SET body_json=? WHERE peer_id=? AND dataset=?',(json.dumps(payload),binding['peer_id'],'contacts'))
        assert actions.list_actions('owner',record['id'])[0]['outcome_state']=='superseded'
        # Changed pairing prevents manual recovery, even with a durable request ID.
        with patch.object(queue,'binding',return_value={**binding,'session_hash':'x'*64}):
            assert actions.list_actions('owner',record['id'])[0]['outcome_state']=='blocked'
            denied(lambda:recover(),403)
        # Privacy projection does not export completion details from a private conversation.
        from app.services import agent_mission_cloud_v1 as cloud
        with patch.object(cloud,'_cloud_export_allowed',return_value=False):
            projected=cloud._projection(mission.get_mission('owner',record['id']),detailed=True)
            assert projected['completion_report'] is None and not projected['action_summaries']
    # A worker failure cannot be summarized as successful mission completion.
    record,change=prepare('contacts.create',{'display_name':'Rejected summary'})
    actions.review('owner',record['id'],change['id'],expected_hash=change['payload_hash'],decision='deny',request_id=uid(),confirmed=True)
    assert report(record)['rejected_changes']==1 and not report(record)['execution_verified']
    with db() as conn:conn.execute("UPDATE agent_mission_tasks_v1 SET status='failed' WHERE id=?",(record['tasks'][0]['id'],))
    assert report(record)['state']=='attention' and report(record)['failed_workers']==1
    if mission._pool:mission._pool.shutdown(wait=True)
    print('A5C4 PASS: receipt-bound native readback, changed/missing receipts, durable outcomes, Cloud queued/applied/verified separation, lost acknowledgements, concurrent recovery, same-ID delivery, binding revocation and private projection')
