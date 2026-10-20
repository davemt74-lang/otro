"""A lifespan stop drains the Feed database and fences old worker commits."""
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix='vrss-shutdown-') as directory:
    os.environ['HOMESERVER_DATA_DIR']=directory
    from app.database import initialize_database
    from app.services import homeserver_app_prebuilt,vrss as v,vrss_social as s
    initialize_database();homeserver_app_prebuilt.ensure_defaults()
    before=v.request({'action':'profile'})['profile']['display_name']
    entered,release,stopped=threading.Event(),threading.Event(),threading.Event()
    errors=[]
    def blocked_refresh(*args,**kwargs):
        with v.database() as c:
            c.execute("UPDATE vrss_profile SET display_name='obsolete worker commit' WHERE id=1")
            entered.set()
            assert release.wait(10)
    def stop():
        try:v.stop()
        except Exception as exc:errors.append(exc)
        finally:stopped.set()
    # Replace the 15-second scheduler wait only; exercise the real cycle,
    # production database, actual worker/start/stop and transaction cancellation.
    with patch.object(v,'_refresh_loop',side_effect=lambda event:v._refresh_cycle(event)),patch.object(v,'refresh_due',side_effect=blocked_refresh),patch.object(s,'deliver') as social:
        v.start();assert entered.wait(10)
        worker=v._REFRESH_THREAD
        stopper=threading.Thread(target=stop);stopper.start()
        assert not stopped.wait(1.2),'Shutdown returned while Feed still held its database'
        release.set();stopper.join(10)
        assert stopped.is_set() and not errors and not worker.is_alive()
        social.assert_not_called()
    assert v.request({'action':'profile'})['profile']['display_name']==before,'Stopped worker committed stale work'
    assert v.refresh_due(stop_event=v._REFRESH_STOP)==[]
    # Restart uses a fresh cancellation context; an old stop cannot poison it.
    resumed=threading.Event()
    with patch.object(v,'_refresh_loop',side_effect=lambda event:v._refresh_cycle(event)),patch.object(v,'refresh_due',side_effect=lambda **kwargs:resumed.set()),patch.object(s,'deliver'):
        v.start();assert resumed.wait(10);v.stop()
        assert not v._REFRESH_THREAD.is_alive()
    # Windows denies this rename if a worker retained the SQLite handle.
    files=list(Path(directory).glob('app-data/*/sqlite/vrss.db'));assert len(files)==1
    renamed=files[0].with_suffix('.closed');files[0].rename(renamed);renamed.rename(files[0])
    print('PASS Feed shutdown: drain an active SQLite transaction, rollback stopped-worker writes, no later delivery, clean restart and released database handle')
