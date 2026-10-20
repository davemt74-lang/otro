"""Production Hosting/VRSS integration: real bindings, TLS gates, API and app storage."""
import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def configure_hosting():
    from app.services import hosting_cloud_control, hosting_entitlements, hosting_public
    hosting_entitlements.reconcile({
        'contract': 'vp3.hosting.entitlements.v1', 'revision': 1, 'package_key': 'vrss-test',
        'max_sites': 5, 'max_active_sites': 5, 'max_public_routes': 5,
        'allowed_runtimes': ['static'], 'max_storage_bytes_per_site': 20_000_000,
        'max_sqlite_bytes_per_site': 10_000_000,
    })
    desired = {'cloud_site_id': 'cloud-vrss-existing-0001', 'revision': 1,
               'display_name': 'My existing subdomain', 'requested_hostname': 'dave.example.test',
               'runtime_kind': 'static', 'desired_state': 'active', 'target_app_key': 'vp3.feed',
               'storage_limit_bytes': 10_000_000, 'sqlite_limit_bytes': 5_000_000}
    site = hosting_cloud_control.reconcile(desired)
    route_request = {'revision': 1, 'hostname': desired['requested_hostname'], 'desired_state': 'active',
                     'hostname_verified': True, 'tls_state': 'active',
                     'certificate_not_after': (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()}
    route = hosting_public.reconcile(desired['cloud_site_id'], **route_request)
    return desired, site, route_request, route


def main():
    with tempfile.TemporaryDirectory(prefix='vrss-hosting-') as data:
        os.environ['HOMESERVER_DATA_DIR'] = data
        from app.database import initialize_database, db
        from app.services import vrss, hosting_cloud_control, hosting_public, homeserver_apps, homeserver_app_prebuilt
        initialize_database()
        homeserver_app_prebuilt.ensure_defaults()
        desired, site, route_request, route = configure_hosting()
        site_id = site['site_id']
        from fastapi.testclient import TestClient
        from app.runtime import app
        from app.security import OWNER_CONTROL_TOKEN
        client = TestClient(app)
        edge = {'X-VP3-Route-Token': route['route_token'], 'X-Forwarded-Host': desired['requested_hostname'], 'X-Forwarded-Proto': 'https'}
        ingress = '/__vp3/hosting/'
        assert client.get(ingress, headers=edge).status_code == 404, 'Hosting silently enabled publication'
        assert client.get(ingress, headers={**edge, 'X-VP3-Route-Token': 'bad'}).status_code == 403
        assert client.get(ingress, headers={**edge, 'X-Forwarded-Host': 'other.example.test'}).status_code == 404
        assert client.get(ingress).status_code in {401, 422}
        boot = vrss.request({'action': 'bootstrap'})
        assert boot['hosting']['sites'][0]['public_base'] == 'https://dave.example.test'
        assert boot['hosting']['sites'][0]['ready']
        assert 'route_token' not in json.dumps(boot)
        p = boot['profile']
        settings = {'display_name': 'Dave <music>', 'bio': 'HomeServer profile', 'public_base': 'https://dave.example.test',
                    'enabled': True, 'hosting_site_id': site_id, 'expected_revision': p['revision']}
        def reject_settings(payload):
            try:
                vrss.update_profile(payload)
            except vrss.VRSSError as exc:
                assert exc.status_code == 409
            else:
                raise AssertionError('Untrusted Hosting address accepted')
        reject_settings({**settings, 'hosting_site_id': 'injected'})
        reject_settings({**settings, 'public_base': 'https://unassigned.example.test'})
        p = vrss.update_profile(settings)
        assert p['address_locked'] and p['hosting_site_id'] == site_id
        public = vrss.create_post({'body': 'Public music', 'visibility': 'public', 'media': [], 'request_id': str(uuid.uuid4())})
        private = vrss.create_post({'body': 'PRIVATE POST SECRET', 'visibility': 'private', 'media': [], 'request_id': str(uuid.uuid4())})
        projection=ingress+'vrss/v1/posts/'+public['post_id']+'/interactions'
        assert client.get(projection,headers=edge).status_code==404, 'Upgrade silently opted into likes'
        settings.update(social_enabled=True,reactions_enabled=True)
        p=vrss.update_profile({**settings,'expected_revision':p['revision']})
        projected=client.get(projection,headers=edge)
        assert projected.status_code==200 and projected.json()['likes']==0
        assert projected.json()['object']=='https://dave.example.test/vrss/v1/posts/'+public['post_id']
        assert projected.headers['cache-control']=='no-store' and 'secret' not in projected.text
        assert client.get(projection,headers={**edge,'X-VP3-Route-Token':'bad'}).status_code==403
        assert client.get(ingress+'vrss/v1/posts/'+private['post_id']+'/interactions',headers=edge).status_code==404
        repost_path=ingress+'vrss/v1/posts/'+public['post_id']+'/reposts'
        assert client.get(repost_path,headers=edge).status_code==404
        settings['reposts_enabled']=True
        p=vrss.update_profile({**settings,'expected_revision':p['revision']})
        assert client.get(repost_path,headers=edge).json()['reposts']==0
        assert client.get(repost_path,headers={**edge,'X-VP3-Route-Token':'bad'}).status_code==403
        assert client.post(repost_path,headers=edge,json={}).status_code==405
        assert client.get(ingress+'vrss/v1/posts/'+private['post_id']+'/reposts',headers=edge).status_code==404
        assert client.get(ingress+'vrss/v1/reposts/'+str(uuid.uuid4()),headers=edge).status_code==404
        comments_path=ingress+'vrss/v1/posts/'+public['post_id']+'/comments'
        assert client.get(comments_path,headers=edge).status_code==404
        settings['comments_enabled']=True
        p=vrss.update_profile({**settings,'expected_revision':p['revision']})
        made=vrss.request({'action':'social.comment','url':p['public_base']+'/vrss/v1/posts/'+public['post_id'],'body':'Public owner comment','parent':'','request_id':str(uuid.uuid4())})
        cid=made['comment']['id']
        public_comments=client.get(comments_path,headers=edge)
        assert public_comments.status_code==200 and public_comments.json()['items'][0]['body']=='Public owner comment'
        assert 'state' not in public_comments.json()['items'][0] and public_comments.headers['cache-control']=='no-store'
        import hashlib
        assert client.get(comments_path+'/id-'+hashlib.sha256(cid.encode()).hexdigest(),headers=edge).json()['items'][0]['id']==cid
        assert client.get(comments_path,headers={**edge,'X-VP3-Route-Token':'bad'}).status_code==403
        assert client.get(ingress+'vrss/v1/posts/'+private['post_id']+'/comments',headers=edge).status_code==404
        assert client.post(comments_path,json={},headers=edge).status_code==405
        profile = client.get(ingress, headers=edge)
        assert profile.status_code == 200 and 'Dave &lt;music&gt;' in profile.text
        assert 'Create Post' not in profile.text and '/api/v1/control' not in profile.text
        document = client.get(ingress + 'vrss/v1/profile', headers={**edge, 'Accept': 'application/json'})
        assert document.status_code == 200 and document.json()['rss'] == p['feed_url']
        rss = client.get(ingress + 'vrss/v1/rss', headers=edge)
        assert rss.status_code == 200 and 'application/rss+xml' in rss.headers['content-type']
        assert 'Public music' in rss.text and 'PRIVATE POST SECRET' not in rss.text
        assert 'https://dave.example.test/vrss/v1/posts/' in rss.text
        assert client.get(ingress + 'vrss/v1/posts/' + public['post_id'], headers=edge).status_code == 200
        assert client.get(ingress + 'vrss/v1/posts/' + private['post_id'], headers=edge).status_code == 404
        head = client.head(ingress + 'vrss/v1/rss', headers=edge)
        assert head.status_code == 200 and not head.content
        assert rss.headers['cache-control'] == 'no-store' and rss.headers['x-content-type-options'] == 'nosniff'
        redirect = client.get(ingress, headers={**edge, 'X-Forwarded-Proto': 'http'}, follow_redirects=False)
        assert redirect.status_code == 308 and redirect.headers['location'] == 'https://dave.example.test/'
        # Even an owner cookie on the ingress must not expose the owner UI/API/assets.
        assert client.post('/__owner/session', headers={'X-HomeServer-Owner': OWNER_CONTROL_TOKEN}).status_code == 200
        for path in ['assets/vrss.js', 'index.html', 'api/v1/control/homeserver-apps/vrss/request', '__owner/session',
                     'vrss/v1/subscriptions', 'vrss/v1/../profile', 'vrss%2fv1%2fsubscriptions']:
            assert client.get(ingress + path, headers=edge).status_code == 404, path
        assert client.post(ingress + 'vrss/v1/profile', json={'enabled': True}, headers=edge).status_code == 405
        # Existing v1.1 upgrades preserve identity, binding, posts and stopped state.
        identity = p['actor_id']
        homeserver_apps.transition(vrss.APP_KEY, 'stopped')
        assert client.get(ingress, headers=edge).status_code == 503
        with db() as c:
            c.execute("UPDATE homeserver_apps SET installed_version='1.1.0' WHERE app_key=?", (vrss.APP_KEY,))
        homeserver_app_prebuilt.ensure_defaults()
        assert homeserver_apps.get(vrss.APP_KEY)['installed_version'] == '1.7.0'
        assert homeserver_apps.get(vrss.APP_KEY)['lifecycle_state'] == 'stopped'
        homeserver_apps.transition(vrss.APP_KEY, 'running')
        assert vrss.request({'action': 'bootstrap'})['profile']['actor_id'] == identity
        assert client.get(ingress + 'vrss/v1/rss', headers=edge).status_code == 200
        with vrss.database() as c:
            c.execute('ALTER TABLE vrss_profile DROP COLUMN reposts_enabled')
        homeserver_apps.transition(vrss.APP_KEY,'stopped')
        with db() as c:c.execute("UPDATE homeserver_apps SET installed_version='1.6.0' WHERE app_key=?",(vrss.APP_KEY,))
        homeserver_app_prebuilt.ensure_defaults()
        assert homeserver_apps.get(vrss.APP_KEY)['installed_version']=='1.7.0' and homeserver_apps.get(vrss.APP_KEY)['lifecycle_state']=='stopped'
        homeserver_apps.transition(vrss.APP_KEY,'running')
        assert vrss.request({'action':'profile'})['profile']['actor_id']==identity
        assert vrss.request({'action':'profile'})['profile']['reposts_enabled'] is False
        assert client.get(comments_path,headers=edge).json()['items'][0]['id']==cid
        # Disable publication without changing Hosting or deleting local posts.
        p = vrss.update_profile({**settings, 'enabled': False, 'expected_revision': p['revision']})
        assert client.get(ingress, headers=edge).status_code == 404
        assert len(vrss.request({'action': 'feed'})['feed']['items']) == 2
        p = vrss.update_profile({**settings, 'expected_revision': p['revision']})
        reject_settings({**settings, 'hosting_site_id': '', 'expected_revision': p['revision']})
        # Route deactivation, token rotation and TLS expiry use the existing authority.
        desired['revision'] = 2
        hosting_cloud_control.reconcile(desired)
        route_request.update(revision=2, desired_state='inactive')
        hosting_public.reconcile(desired['cloud_site_id'], **route_request)
        assert client.get(ingress, headers=edge).status_code == 503
        assert client.get('/vrss/v1/rss').status_code == 404
        assert client.get('/vrss/v1/posts/'+public['post_id']+'/interactions').status_code==404
        reject_settings({**settings, 'expected_revision': p['revision']})
        desired['revision'] = 3
        hosting_cloud_control.reconcile(desired)
        route_request.update(revision=3, desired_state='active')
        rotated = hosting_public.reconcile(desired['cloud_site_id'], **route_request)
        assert rotated['route_token'] != edge['X-VP3-Route-Token']
        assert client.get(ingress, headers=edge).status_code == 403
        edge['X-VP3-Route-Token'] = rotated['route_token']
        assert client.get(ingress, headers=edge).status_code == 200
        metadata = hosting_public._load(site_id)
        metadata['certificate_not_after'] = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        hosting_public._write(site_id, metadata)
        assert client.get(ingress, headers=edge).status_code == 503
        assert client.get('/vrss/v1/rss').status_code == 404
        assert client.get('/vrss/v1/posts/'+public['post_id']+'/interactions').status_code==404
        # Allow the owner to turn publishing off even after the route was revoked.
        p = vrss.update_profile({**settings, 'enabled': False, 'expected_revision': p['revision']})
        metadata['certificate_not_after'] = route_request['certificate_not_after']
        hosting_public._write(site_id, metadata)
        p = vrss.update_profile({**settings, 'expected_revision': p['revision']})
        # A second assignment/hostname never publishes this identity; unbind closes the old one.
        second = hosting_cloud_control.reconcile({**desired, 'cloud_site_id': 'cloud-vrss-existing-0002', 'requested_hostname': 'second.example.test'})
        second_route = hosting_public.reconcile('cloud-vrss-existing-0002', **{**route_request, 'hostname': 'second.example.test'})
        second_edge = {**edge, 'X-VP3-Route-Token': second_route['route_token'], 'X-Forwarded-Host': 'second.example.test'}
        assert client.get(ingress, headers=second_edge).status_code == 404
        hosting_cloud_control.reconcile({**desired, 'revision': 4, 'target_app_key': None, 'desired_state': 'configured'})
        assert client.get(ingress, headers=edge).status_code == 503
        assert client.get('/vrss/v1/rss').status_code == 404
        assert client.get('/vrss/v1/posts/'+public['post_id']+'/interactions').status_code==404
        assert vrss.request({'action': 'bootstrap'})['profile']['actor_id'] == identity
        print('PASS VRSS existing Hosting: assigned-address selection, public-only ingress, TLS/token/opt-in gates, private data isolation, disable/unbind, second-host isolation and durable upgrade')


if __name__ == '__main__':
    main()
