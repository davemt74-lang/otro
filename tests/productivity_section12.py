from __future__ import annotations
import os, sys, tempfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
with tempfile.TemporaryDirectory(prefix='section12-') as folder:
    os.environ['HOMESERVER_DATA_DIR']=folder
    from app.database import db, initialize_database, atomic_write
    from app.services import contacts, tasks, task_calendar_continuity as continuity
    initialize_database()
    def parallel(call):
        with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(lambda _:call(),range(2)))
    cargs={'mutation_id':'s12-contact-create','display_name':'Contact','notes':'n'*7000}
    created=parallel(lambda:contacts.create_federated_contact(cargs,source_app_key='owner'))
    assert created[0]['id']==created[1]['id']
    assert len(contacts.list_contacts())==1
    before=created[0]
    changed=contacts.update_federated_contact(before['canonical_id'],{'mutation_id':'s12-contact-update','expected_revision':before['record_revision'],'notes':'n'*7000+'changed'},source_app_key='owner')
    assert changed['record_revision']!=before['record_revision'],'Full notes must participate in revisions'
    try:contacts.update_contact(before['id'],{'display_name':'Stale'},expected_revision=before['record_revision']);raise AssertionError('stale owner contact accepted')
    except contacts.ContactError as exc:assert exc.status_code==409
    def fail(*args,**kwargs):raise RuntimeError('forced receipt failure')
    with patch.object(contacts,'_record_mutation',side_effect=fail):
        try:contacts.create_federated_contact({'mutation_id':'s12-contact-fail','display_name':'Orphan'},source_app_key='owner');raise AssertionError('forced failure hidden')
        except RuntimeError as exc:assert 'forced receipt' in str(exc)
    assert len(contacts.list_contacts())==1
    with patch.object(contacts,'_record_mutation',side_effect=fail):
        try:contacts.delete_federated_contact(changed['canonical_id'],mutation_id='s12-delete-fail',expected_revision=changed['record_revision'],source_app_key='owner');raise AssertionError('forced delete failure hidden')
        except RuntimeError as exc:assert 'forced receipt' in str(exc)
    assert contacts.get_contact(before['id']) is not None
    args={'mutation_id':'s12-task-create','title':'Task','description':'d'*10000}
    made=parallel(lambda:continuity.create_federated_task(args,source_app_key='owner',created_by_type='owner'))
    assert made[0]['id']==made[1]['id'] and len(tasks.list_tasks())==1
    task=made[0]
    def edit_task(label):
        try:
            continuity.update_federated_task({'canonical_id':task['canonical_id'],'mutation_id':'s12-task-edit-'+label,'expected_revision':task['record_revision'],'title':label},source_app_key='owner');return 'saved'
        except continuity.TaskCalendarContinuityError as exc:assert exc.status_code==409;return 'stale'
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(edit_task,['One','Two']))==['saved','stale']
    try:tasks.update_task(task['id'],{'status':'completed'},expected_revision=task['record_revision']);raise AssertionError('stale owner task accepted')
    except tasks.TaskError as exc:assert exc.status_code==409
    with patch.object(continuity,'_record_mutation',side_effect=fail):
        try:continuity.create_federated_task({'mutation_id':'s12-task-fail','title':'Orphan'},source_app_key='owner');raise AssertionError('forced task failure hidden')
        except RuntimeError as exc:assert 'forced receipt' in str(exc)
    assert len(tasks.list_tasks())==1
    event_args={'mutation_id':'s12-calendar-create','title':'Event','description':'e'*12000,'start_at':'2026-10-05T08:00:00-07:00','end_at':'2026-10-05T09:00:00-07:00','timezone':'America/Phoenix'}
    events=parallel(lambda:continuity.create_federated_calendar(event_args,source_app_key='owner'))
    assert events[0]['id']==events[1]['id'] and len(continuity.list_federated_calendar())==1
    with patch.object(continuity,'_record_mutation',side_effect=fail):
        try:continuity.delete_federated_calendar({'canonical_id':events[0]['canonical_id'],'mutation_id':'s12-calendar-fail','expected_revision':events[0]['record_revision']},source_app_key='owner');raise AssertionError('forced calendar failure hidden')
        except RuntimeError as exc:assert 'forced receipt' in str(exc)
    assert len(continuity.list_federated_calendar())==1
    for invalid in ['x'*129,'s12-id!!!']:
        try:continuity.normalize_task_create_arguments({'mutation_id':invalid,'title':'Invalid'});raise AssertionError('invalid ID accepted')
        except continuity.TaskCalendarContinuityError:pass
    tasks.create_task({'title':'Reminder','remind_at':'2026-10-05T08:00:00Z','recurrence':'daily'})
    results=parallel(lambda:tasks.run_due_reminders(now=datetime(2026,10,5,9,tzinfo=timezone.utc)))
    assert sum(x['fired'] for x in results)==1 and len(tasks.list_notifications())==1
    @atomic_write
    def nested_failure():
        with db() as connection:connection.execute("INSERT INTO contacts(display_name) VALUES ('Before')")
        try:
            with db() as connection:
                connection.execute("INSERT INTO contacts(display_name) VALUES ('Partial')")
                raise ValueError('inner failure')
        except ValueError:pass
    nested_failure()
    assert {x['display_name'] for x in contacts.list_contacts()}=={'Contact','Before'}
print('PRODUCTIVITY_SECTION12=PASS')
