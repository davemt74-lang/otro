"""Real HTTPS delivery between two independent application servers.

A test CA and deterministic DNS/socket mapping route the public test hostnames to
local TLS listeners. Production exchange(), signatures, APIs and databases run
unchanged. No real public DNS/edge credentials are used.
"""
import ipaddress
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime,timedelta,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def serve(config,name):
    os.environ['HOMESERVER_DATA_DIR']=str(Path(config['directory'])/name)
    from app.database import initialize_database
    from app.services import homeserver_app_prebuilt,vrss as v,vrss_social as s
    initialize_database();homeserver_app_prebuilt.ensure_defaults()
    p=v.request({'action':'profile'})['profile']
    if not p['enabled']:
        v.update_profile({'display_name':name,'bio':'','public_base':'https://'+name+'.example.test','enabled':True,'social_enabled':True,'expected_revision':p['revision']})
    # Preserve DNS validation and TLS/SNI/hostname verification in the real transport.
    real_dns=socket.getaddrinfo;real_connect=socket.create_connection;real_context=s.ssl.create_default_context
    ips={'alice.example.test':'8.8.8.1','bob.example.test':'8.8.8.2'}
    ports={ip:config['ports'][host.split('.')[0]] for host,ip in ips.items()}
    def dns(host,port,*args,**kwargs):
        if host in ips:return [(socket.AF_INET,socket.SOCK_STREAM,6,'',(ips[host],port))]
        return real_dns(host,port,*args,**kwargs)
    def connect(address,timeout=8,source_address=None):
        if address[0] in ports:address=('127.0.0.1',ports[address[0]])
        return real_connect(address,timeout,source_address)
    def context(*args,**kwargs):
        ctx=real_context(*args,**kwargs);ctx.load_verify_locations(config['cert']);return ctx
    socket.getaddrinfo=dns;socket.create_connection=connect;s.ssl.create_default_context=context
    from fastapi import FastAPI
    from app.vrss_api import router
    from app.runtime import OwnerGateway
    from app.security import OWNER_CONTROL_TOKEN
    api=FastAPI();api.include_router(router)
    @api.post('/api/v1/control/social-test-pump')
    def pump():s.deliver();return {'ok':True}
    @api.get('/api/v1/control/social-test-actor')
    def actor():return s.actor_document()
    import uvicorn
    print(json.dumps({'owner':OWNER_CONTROL_TOKEN}),flush=True)
    uvicorn.run(OwnerGateway(api),host='127.0.0.1',port=config['ports'][name],ssl_keyfile=config['key'],ssl_certfile=config['cert'],log_level='error',lifespan='off')


def main():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    import httpx
    with tempfile.TemporaryDirectory(prefix='vrss-real-tls-') as directory:
        base=Path(directory);key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'VRSS test CA')]);stamp=datetime.now(timezone.utc)
        cert=x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(stamp-timedelta(minutes=5)).not_valid_after(stamp+timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True).add_extension(x509.SubjectAlternativeName([x509.DNSName('alice.example.test'),x509.DNSName('bob.example.test'),x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False).sign(key,hashes.SHA256())
        (base/'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM));(base/'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        ports={}
        reserved=[]
        for name in ('alice','bob'):
            sock=socket.socket();sock.bind(('127.0.0.1',0));ports[name]=sock.getsockname()[1];reserved.append(sock)
        for sock in reserved:sock.close()
        config={'directory':directory,'ports':ports,'cert':str(base/'cert.pem'),'key':str(base/'key.pem')}
        children=[];clients=[]
        try:
            for name in ('alice','bob'):
                child=subprocess.Popen([sys.executable,__file__,'--serve',name,json.dumps(config)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                children.append(child);line=child.stdout.readline();assert line,child.stderr.read();owner=json.loads(line)['owner']
                client=httpx.Client(base_url='https://127.0.0.1:'+str(ports[name]),verify=config['cert'],trust_env=False,timeout=45);clients.append(client)
                for _ in range(100):
                    try:
                        response=client.post('/__owner/session',headers={'X-HomeServer-Owner':owner});assert response.status_code==200;break
                    except httpx.TransportError:time.sleep(.05)
                else:raise AssertionError('TLS server did not start')
            a,b=clients
            def request(client,action,**extra):
                r=client.post('/api/v1/control/homeserver-apps/vrss/request',headers={'X-VRSS-Request':'1'},json={'action':action,**extra});assert r.status_code==200,r.text;return r.json()
            ad=a.get('/api/v1/control/social-test-actor').json();bd=b.get('/api/v1/control/social-test-actor').json()
            assert request(a,'social.follow',url='https://bob.example.test/')['state']=='pending'
            assert a.post('/api/v1/control/social-test-pump').status_code==200
            assert request(b,'social.status')['social']['followers']==1
            assert request(a,'social.status')['social']['following']==0
            assert b.post('/api/v1/control/social-test-pump').status_code==200
            assert request(a,'social.status')['social']['following']==1
            assert len(request(a,'subscriptions')['subscriptions'])==1
            assert len(request(b,'notices')['notices'])==1
            assert not request(a,'social.status')['social']['deliveries']
            request(b,'social.block',actor=ad['id']);assert b.post('/api/v1/control/social-test-pump').status_code==200
            assert request(a,'social.status')['social']['following']==0
            assert not request(a,'subscriptions')['subscriptions']
            assert not request(b,'notices')['notices']
            assert a.get('/vrss/v1/actor').json()['id']==ad['id']
            print('PASS real two-server HTTPS: production pinned transport, verified TLS hostnames, signed Follow/Accept, RSS subscription, follower Chat notice and delivered block/Reject')
        finally:
            for client in clients:client.close()
            for child in children:
                child.terminate()
                try:child.wait(timeout=15)
                except subprocess.TimeoutExpired:child.kill();child.wait()

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--serve':serve(json.loads(sys.argv[3]),sys.argv[2])
    else:main()
