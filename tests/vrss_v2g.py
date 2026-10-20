"""Independent production stores and real signatures for mentions and thread choices."""
import copy
import hashlib
import tempfile
import uuid
from vrss_v2c import Server
nonce=lambda:str(uuid.uuid4())

with tempfile.TemporaryDirectory(prefix='vrss-conversations-') as data:
    a,b=Server(data+'/a'),Server(data+'/b')
    try:
        a.call('configure',name='Alice',base='https://alice.example.test',comments=True)
        b.call('configure',name='Bob <script>literal</script>',base='https://bob.example.test',comments=True)
        ad,bd=a.call('actor'),b.call('actor')
        assert not ad['conversations']['mentions'] and not bd['conversations']['mentions']
        a.call('post',body='Private @'+bd['id'],visibility='private')
        post=a.call('post',body='Hello @'+bd['id'])['post_id'];obj='https://alice.example.test/vrss/v1/posts/'+post
        a.call('conv_prepare',documents={bd['id']:bd});assert a.call('packets')==[]
        b.call('conv_settings',mentions=True);bd=b.call('actor')
        a.call('conv_prepare',documents={bd['id']:bd});packets=a.call('packets');assert len(packets)==1
        packet=packets[0];assert packet['activity']['type']=='Mention' and 'follow_id' not in packet['activity']
        docs={ad['id']:ad,obj:a.call('public',kind='post',id=post)}
        wrong=copy.deepcopy(packet);wrong['signature']='00'*64
        b.call('receive',packet=wrong,documents=docs,expect=403)
        wrong=copy.deepcopy(packet['activity']);wrong['object']='https://foreign.example/vrss/v1/posts/'+post
        b.call('receive',packet=a.call('forge',activity=wrong),documents=docs,expect=422)
        bad=copy.deepcopy(docs);bad[obj]['body']='No recipient mention'
        b.call('receive',packet=packet,documents=bad,expect=403)
        b.call('conv_settings',mentions=False);b.call('receive',packet=packet,documents=docs,expect=403)
        b.call('conv_settings',mentions=True)
        ack=b.call('receive',packet=packet,documents=docs)
        assert ack['mention']['id']==packet['activity']['mention_id']
        assert b.call('receive',packet=packet,documents=docs)['replayed']
        a.call('conv_send',ack=ack)
        notices=b.call('conv_notices',documents=docs)['notices'];assert len(notices)==1 and notices[0]['title']=='VRSS mention'
        assert 'Hello' not in notices[0]['body']
        b.restart();assert len(b.call('conv_notices',documents=docs)['notices'])==1
        a.call('comment_optin',enabled=False)
        closed=b.call('conv_thread',url=obj,documents={**docs,ad['id']:a.call('actor')})
        assert closed['items']==[] and not closed['can_comment']
        a.call('comment_optin',enabled=True)
        cancelled=a.call('post',body='Cancel before discovery @'+bd['id'])['post_id']
        a.call('conv_private',post_id=cancelled)
        a.call('conv_prepare',documents={})
        assert a.call('conv_status')['pending_mentions']==0 and a.call('packets')==[]
        post=b.call('post',body='Public conversation')['post_id'];obj='https://bob.example.test/vrss/v1/posts/'+post
        b.call('comment_create',url=obj,body='Old reply',request_id=nonce())
        def documents():
            page=b.call('comment_public',post_id=post)
            d={bd['id']:b.call('actor'),obj:b.call('public',kind='post',id=post),obj+'/comments':page}
            for item in page['items']:
                d[obj+'/comments/id-'+hashlib.sha256(item['id'].encode()).hexdigest()]={**page,'items':[item],'next_cursor':''}
            return d
        request=nonce();made=a.call('conv_watch',url=obj,enabled=True,muted=False,revision=0,request_id=request,documents=documents())
        assert made['conversation']['enabled']==1 and a.call('status')['following']==0
        assert a.call('conv_watch',url=obj,enabled=True,muted=False,revision=0,request_id=request,documents={})['replayed']
        a.call('conv_watch',url=obj,enabled=False,muted=False,revision=0,request_id=request,expect=409)
        a.call('conv_refresh',documents=documents());assert a.call('conv_notices',documents=documents())['notices']==[]
        b.call('comment_create',url=obj,body='New public reply',request_id=nonce())
        a.call('conv_refresh',documents=documents());items=a.call('conv_notices',documents=documents())['notices']
        assert len(items)==1 and items[0]['title']=='New reply in a followed conversation'
        a.restart();a.call('conv_refresh',documents=documents());assert len(a.call('conv_notices',documents=documents())['notices'])==1
        watch=a.call('conv_status')['items'][0]
        a.call('conv_watch',url=obj,enabled=True,muted=True,revision=watch['revision'],request_id=nonce())
        assert a.call('conv_notices',documents=documents())['notices']==[]
        b.call('comment_create',url=obj,body='Muted reply',request_id=nonce())
        a.call('conv_refresh',documents=documents());assert a.call('conv_notices',documents=documents())['notices']==[]
        watch=a.call('conv_status')['items'][0]
        a.call('conv_watch',url=obj,enabled=True,muted=False,revision=watch['revision'],request_id=nonce(),documents=documents())
        a.call('conv_refresh',documents=documents());assert a.call('conv_notices',documents=documents())['notices']==[]
        a.call('conv_settings',threads=False)
        b.call('comment_create',url=obj,body='Globally muted reply',request_id=nonce())
        a.call('conv_refresh',documents=documents());assert a.call('conv_notices',documents=documents())['notices']==[]
        a.call('conv_settings',threads=True)
        a.call('conv_refresh',documents=documents());assert a.call('conv_notices',documents=documents())['notices']==[]
        b.call('comment_create',url=obj,body='New after global re-enable',request_id=nonce())
        a.call('conv_refresh',documents=documents());assert len(a.call('conv_notices',documents=documents())['notices'])==1
        b.call('comment_create',url=obj,body='Stale reply must not notify',request_id=nonce())
        a.call('conv_race',url=obj,documents=documents());assert a.call('conv_notices',documents=documents())['notices']==[]
        assert not a.call('conv_status')['items'][0]['enabled']
        b.call('conv_private',post_id=post)
        a.call('conv_watch',url=obj,enabled=True,muted=False,revision=a.call('conv_status')['items'][0]['revision'],request_id=nonce(),documents={bd['id']:bd},expect=502)
        b.call('change',actor=ad['id'],action='block')
        assert b.call('conv_notices',documents=docs)['notices']==[]
        b.call('receive',packet=packet,documents=docs,expect=403)
        b.call('change',actor=ad['id'],action='unblock')
        b.call('receive',packet=packet,documents=docs,expect=409)
        print('PASS V2G: signed mentions, consent, public proof, exact ACK, replay/restart, no copied body, thread-only subscriptions, baseline, mute/unmute, stale fetch, private source and block')
    finally:a.close();b.close()

# The thread publisher forwards mentions only after its explicit moderation.
with tempfile.TemporaryDirectory(prefix='vrss-mention-moderation-') as data:
    a,b=Server(data+'/a'),Server(data+'/b')
    try:
        a.call('configure',name='Alice',base='https://alice.example.test',comments=True)
        b.call('configure',name='Bob',base='https://bob.example.test',comments=True)
        a.call('conv_settings',mentions=True)
        ad,bd=a.call('actor'),b.call('actor')
        a.call('follow',url=bd['id'],doc=bd)
        b.call('receive',packet=a.call('packets')[0],doc=ad)
        a.call('receive',packet=b.call('packets')[0],doc=bd)
        a.call('deliver');b.call('deliver')
        post=b.call('post',body='Moderated discussion')['post_id'];obj='https://bob.example.test/vrss/v1/posts/'+post
        page=b.call('comment_public',post_id=post)
        docs={bd['id']:bd,obj+'/comments':page}
        made=a.call('comment_create',url=obj,body='Published mention @'+ad['id'],request_id=nonce(),documents=docs)
        key=made['comment']['id'];packet=a.call('packets')[0]
        ack=b.call('receive',packet=packet,doc=ad);a.call('comment_ack',result=ack)
        b.call('conv_prepare',documents={ad['id']:ad});assert b.call('packets')==[]
        later=b.call('comment_create',url=obj,body='Baseline later sequence',request_id=nonce())['comment']['id']
        def documents():
            page=b.call('comment_public',post_id=post)
            d={bd['id']:bd,obj+'/comments':page}
            for item in page['items']:
                d[obj+'/comments/id-'+hashlib.sha256(item['id'].encode()).hexdigest()]={**page,'items':[item],'next_cursor':''}
            return d
        a.call('conv_watch',url=obj,enabled=True,muted=False,revision=0,request_id=nonce(),documents=documents())
        b.call('comment_moderate',url=obj,id=key,decision='approve',revision=1,request_id=nonce())
        b.call('conv_prepare',documents={ad['id']:ad});packet=b.call('packets')[0]
        assert packet['activity']['comment_id']==key and packet['activity']['actor']==bd['id']
        # This is a source-publisher signature; comment ID may belong to its original author.
        a.call('receive',packet=packet,documents=documents());a.call('conv_refresh',documents=documents())
        assert len(a.call('conv_notices',documents=documents())['notices'])==1
        b.call('comment_moderate',url=obj,id=key,decision='hide',revision=2,request_id=nonce())
        assert b.call('conv_send',ack={'contract':'vp3.vrss.follow.v1','ok':True})==[]
        parent=''
        for depth in range(5):parent=b.call('comment_create',url=obj,body=('Leaf @'+ad['id']) if depth==4 else 'Parent',parent=parent,request_id=nonce())['comment']['id']
        b.call('conv_prepare',documents={ad['id']:ad});packet=b.call('packets')[0]
        assert packet['activity']['comment_id']==parent
        a.call('receive',packet=packet,documents={**documents(),obj+'/comments/id-'+hashlib.sha256(parent.encode()).hexdigest():b.call('comment_public',post_id=post,cursor='id-'+hashlib.sha256(parent.encode()).hexdigest())})
        a.call('post',body=' '.join('@https://p'+str(i)+'.example/vrss/v1/actor' for i in range(6)),expect=422)
        print('PASS V2G moderation: no pre-approval delivery, source-publisher/comment-author distinction, late approval, coalesced channels, hidden-source send fencing, leaf replies and five-mention bound')
    finally:a.close();b.close()

with tempfile.TemporaryDirectory(prefix='vrss-conversation-bounds-') as data:
    a,b=Server(data+'/a'),Server(data+'/b')
    try:
        a.call('configure',name='Alice',base='https://alice.example.test',comments=True)
        b.call('configure',name='Bob',base='https://bob.example.test',comments=True)
        bd=b.call('actor');post=b.call('post')['post_id'];obj='https://bob.example.test/vrss/v1/posts/'+post
        b.call('comment_create',url=obj,body='Baseline',request_id=nonce())
        docs={bd['id']:bd,obj+'/comments':b.call('comment_public',post_id=post)}
        a.call('conv_limit',url=obj)
        a.call('conv_watch',url=obj,enabled=True,muted=False,revision=0,request_id=nonce(),documents=docs,expect=409)
        assert a.call('conv_status')['items']==[]
        a.call('conv_limit',url=obj,table='watches')
        a.call('conv_watch',url=obj,enabled=True,muted=False,revision=0,request_id=nonce(),documents=docs,expect=409)
        print('PASS V2G bounds: seen history and conversation limits fail atomically without dropping replay fences')
    finally:a.close();b.close()
