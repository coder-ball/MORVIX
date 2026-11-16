import os, re, tempfile, importlib, time
from fastapi.testclient import TestClient

fd,path=tempfile.mkstemp(suffix='.db');os.close(fd)
os.environ['MORVIX_DATABASE']=path
os.environ['MORVIX_EVALUATION_FILE']=path+'.evaluation.json'  # isolated from the real data/evaluation.json
# API tests use the deterministic built-in lexicon; the ML engines are covered by tests/test_models.py
os.environ['MORVIX_TEXT_ML']='0';os.environ['MORVIX_IMAGE_ML']='0'
os.environ['MORVIX_EMAIL_MODE']='console'
from backend import main
importlib.reload(main)
client=TestClient(main.app)

# Capture verification emails instead of sending them
SENT=[]
main.mailer.send_mail=lambda to,subject,text,html=None: SENT.append({'to':to,'subject':subject,'text':text})
def last_code(email):
    return re.search(r'\b(\d{6})\b',[m for m in SENT if m['to']==email][-1]['text']).group(1)

PW='correct horse battery'
def account(email):
    r=client.post('/api/v1/auth/register',json={'email':email,'password':PW});assert r.status_code==201 and r.json()['verification_required']
    d=client.post('/api/v1/auth/verify-email',json={'email':email,'code':last_code(email)});assert d.status_code==200
    d=d.json()
    return {'Authorization':'Bearer '+d['access_token']}, d

def test_health_auth_and_password_hash():
    h=client.get('/api/v1/health');assert h.status_code==200 and h.json()['status']=='ok'
    headers,user=account('parent@example.test')
    assert client.get('/api/v1/auth/me',headers=headers).json()['email']=='parent@example.test'
    with main.db() as c:r=c.execute('SELECT password_hash FROM users WHERE id=?',(user['user']['id'],)).fetchone()
    assert 'correct horse battery' not in r['password_hash'] and '$' in r['password_hash']
    assert client.get('/api/v1/auth/me').status_code==401
    assert client.post('/api/v1/auth/login',json={'email':'parent@example.test','password':'incorrect password'}).status_code==401

def test_rules_isolation_and_policy_sync():
    h1,_=account('one@example.test');h2,_=account('two@example.test')
    created=client.post('/api/v1/rules',headers=h1,json={'kind':'website','target':'example.com','action':'block'});assert created.status_code==201
    rid=created.json()['id'];assert client.get('/api/v1/rules',headers=h2).json()==[]
    assert client.patch('/api/v1/rules/'+rid,headers=h2,json={'action':'allow'}).status_code==404
    assert client.patch('/api/v1/rules/'+rid,headers=h1,json={'enabled':False}).json()['enabled']==0
    assert client.patch('/api/v1/rules/'+rid,headers=h1,json={'enabled':True}).json()['enabled']==1
    code=client.post('/api/v1/extensions/pair-code',headers=h1).json()['code']
    paired=client.post('/api/v1/extensions/pair',json={'code':code}).json();token=paired['extension_token']
    policy=client.get('/api/v1/extensions/policy',headers={'Authorization':'Bearer '+token})
    assert policy.status_code==200 and policy.json()['rules'][0]['target']=='example.com'
    assert client.post('/api/v1/extensions/pair',json={'code':code}).status_code==400
    assert client.delete('/api/v1/rules/'+rid,headers=h1).status_code==204

def test_event_filters_alert_review_and_summary():
    h,_=account('events@example.test');code=client.post('/api/v1/extensions/pair-code',headers=h).json()['code'];e=client.post('/api/v1/extensions/pair',json={'code':code}).json()['extension_token'];eh={'Authorization':'Bearer '+e}
    rec=client.post('/api/v1/extensions/events',headers=eh,json={'modality':'text','category':'Threat Language','confidence':0.91,'action':'warn','url':'https://sample.test/page','model':'test-model'});assert rec.status_code==200
    eid=rec.json()['event_id'];items=client.get('/api/v1/events?modality=text&action=warn&q=sample.test',headers=h).json()
    assert items['total']==1 and items['items'][0]['id']==eid
    assert client.patch(f'/api/v1/events/{eid}/review',headers=h).json()['reviewed'] is True
    alerts=client.get('/api/v1/alerts?read=false',headers=h).json();assert len(alerts)==1
    assert client.patch('/api/v1/alerts/'+alerts[0]['id']+'/read',headers=h).status_code==200
    s=client.get('/api/v1/summary',headers=h).json();assert s['warned']==1 and s['total_detections']==1

def test_summary_channel_filter_applies_to_counts():
    """FR-DASH-04: the channel filter narrows the summary statistics and unread alerts, not only the recent list."""
    h,_=account('channels@example.test');code=client.post('/api/v1/extensions/pair-code',headers=h).json()['code'];e=client.post('/api/v1/extensions/pair',json={'code':code}).json()['extension_token'];eh={'Authorization':'Bearer '+e}
    for mod,act,n in (('text','block',1),('image','block',2),('image','warn',3)):
        assert client.post('/api/v1/extensions/events',headers=eh,json={'modality':mod,'category':'Threat Language','confidence':0.9,'action':act,'url':f'https://sample.test/{n}','model':'test-model'}).status_code==200
    a=client.get('/api/v1/summary',headers=h).json();assert (a['blocked'],a['warned'],a['total_detections'],a['channel'])==(2,1,3,'all')
    t=client.get('/api/v1/summary?modality=text',headers=h).json();assert (t['blocked'],t['warned'],t['total_detections'],t['unread_alerts'],t['channel'])==(1,0,1,1,'text')
    i=client.get('/api/v1/summary?modality=image',headers=h).json();assert (i['blocked'],i['warned'],i['unread_alerts'])==(1,1,2) and all(r['modality']=='image' for r in i['recent'])
    assert client.get('/api/v1/summary?modality=bogus',headers=h).status_code==422

def test_invalid_rules_and_settings():
    h,_=account('validate@example.test')
    assert client.post('/api/v1/rules',headers=h,json={'kind':'unknown','target':'x','action':'block'}).status_code==422
    assert client.post('/api/v1/rules',headers=h,json={'kind':'category','target':'Unknown','action':'block'}).status_code==422
    s=client.put('/api/v1/settings',headers=h,json={'enabled':False,'category_actions':{'Threat Language':'block'},'retention_days':30})
    assert s.status_code==200 and s.json()['enabled'] is False and s.json()['retention_days']==30
    assert s.json()['updated_at']  # SRS §5 Policy Setting: update time

def test_password_change_keeps_current_session():
    h,_=account('password@example.test')
    changed=client.put('/api/v1/auth/password',headers=h,json={'current_password':'correct horse battery','new_password':'new strong password 123'})
    assert changed.status_code==200 and client.get('/api/v1/auth/me',headers=h).status_code==200
    assert client.post('/api/v1/auth/login',json={'email':'password@example.test','password':'correct horse battery'}).status_code==401
    assert client.post('/api/v1/auth/login',json={'email':'password@example.test','password':'new strong password 123'}).status_code==200


# ---- SRS acceptance coverage (AC-04, AC-07..AC-14, teacher comments) ----
def paired(headers):
    code=client.post('/api/v1/extensions/pair-code',headers=headers).json()['code']
    return {'Authorization':'Bearer '+client.post('/api/v1/extensions/pair',json={'code':code}).json()['extension_token']}

def test_ac04_text_classification_records_event_and_function():
    h,_=account('text@example.test');eh=paired(h)
    r=client.post('/api/v1/extensions/analyze/text',headers=eh,json={'text':'I am going to kill you tonight, watch your back.','url':'https://chat.example.org/room'}).json()
    assert r['category']=='Threat Language' and r['confidence']>=0.8 and r['action']=='warn' and r['modality']=='text'
    assert r['function']=='Text Analysis' and r['feature']=='Visible page text'
    ev=client.get('/api/v1/events?modality=text',headers=h).json()['items'][0]
    assert ev['function']=='Text Analysis' and ev['domain']=='chat.example.org' and 'kill you' not in str(ev)  # FR-HIST-05
    assert client.get('/api/v1/alerts',headers=h).json()[0]['severity']=='medium'

def test_self_harm_and_violence_and_benign_and_negation():
    h,_=account('cats@example.test')
    c=lambda t:client.post('/api/v1/classify/text',headers=h,json={'text':t}).json()
    assert c('I want to die and I will end my life tonight')['category']=='Self-harm Indicators'
    assert c('The report described a brutal massacre with decapitated bodies and blood spatter.')['category']=='Graphic Violence'
    ok=c('Here is a recipe for banana bread and some gardening tips for spring.');assert ok['action']=='allow' and not ok['recorded']
    assert c("I would never kill you, I don't want to hurt anyone")['action']=='allow'

def test_thresholds_block_and_low_confidence_warn():
    h,_=account('thr@example.test')
    client.put('/api/v1/settings',headers=h,json={'category_actions':{'Threat Language':'block'}})
    assert client.post('/api/v1/classify/text',headers=h,json={'text':'I will kill you'}).json()['action']=='block'
    client.put('/api/v1/settings',headers=h,json={'block_threshold':0.99,'warn_threshold':0.5})
    low=client.post('/api/v1/classify/text',headers=h,json={'text':'I will kill you'}).json();assert low['action']=='warn'  # not auto-blocked (FR-ML-08)
    assert client.put('/api/v1/settings',headers=h,json={'warn_threshold':0.9,'block_threshold':0.6}).status_code==422

def test_fast_pass_records_blocks_but_leaves_flags_to_full_pass():
    h,_=account('fast@example.test');eh=paired(h)
    count=lambda: client.get('/api/v1/events',headers=h).json()['total']
    client.put('/api/v1/settings',headers=h,json={'category_actions':{'Threat Language':'warn'}})
    r=client.post('/api/v1/extensions/analyze/text',headers=eh,json={'text':'I will kill you','url':'https://a.example/1','fast':True}).json()
    assert r['stage']=='fast' and r['action']=='warn' and not r['recorded'] and count()==0  # flag shown, not stored yet
    full=client.post('/api/v1/extensions/analyze/text',headers=eh,json={'text':'I will kill you','url':'https://a.example/1'}).json()
    assert full['stage']=='full' and full['recorded'] and count()==1
    client.put('/api/v1/settings',headers=h,json={'category_actions':{'Threat Language':'block'}})
    b=client.post('/api/v1/extensions/analyze/text',headers=eh,json={'text':'I will kill you','url':'https://a.example/2','fast':True}).json()
    assert b['action']=='block' and b['recorded'] and count()==2  # the child was stopped, so it is recorded (FR-EXT-08)

def test_sensitivity_scale_sets_both_thresholds():
    h,_=account('sens@example.test')
    assert client.get('/api/v1/settings',headers=h).json()['sensitivity']==6  # defaults 0.50 / 0.80
    s=client.put('/api/v1/settings',headers=h,json={'sensitivity':10}).json()
    assert s['sensitivity']==10 and (s['warn_threshold'],s['block_threshold'])==main.SENSITIVITY_LEVELS[10]
    s=client.put('/api/v1/settings',headers=h,json={'sensitivity':1}).json()
    assert (s['warn_threshold'],s['block_threshold'])==main.SENSITIVITY_LEVELS[1]
    levels=[main.SENSITIVITY_LEVELS[i] for i in range(1,11)]
    assert all(a[0]>b[0] and a[1]>b[1] and a[0]<=a[1] for a,b in zip(levels,levels[1:]))  # 10 = most sensitive
    assert client.put('/api/v1/settings',headers=h,json={'warn_threshold':1.0,'block_threshold':1.0}).json()['sensitivity'] is None  # custom
    assert client.put('/api/v1/settings',headers=h,json={'sensitivity':11}).status_code==422

def test_flag_behaviour_is_a_parent_setting_synced_to_the_extension():
    """FR-ACT-04: flagged content is hidden by default; the parent may permit access with a warning."""
    h,_=account('flagset@example.test');eh=paired(h)
    assert client.get('/api/v1/settings',headers=h).json()['flag_hides'] is True
    assert client.get('/api/v1/extensions/policy',headers=eh).json()['flag_hides'] is True
    assert client.put('/api/v1/settings',headers=h,json={'flag_hides':False}).json()['flag_hides'] is False
    assert client.get('/api/v1/extensions/policy',headers=eh).json()['flag_hides'] is False

def test_search_query_is_checked_on_its_own():
    h,_=account('query@example.test');eh=paired(h)
    page='Search results. '+'Here are some pages about school projects, science fairs and sports clubs near you. '*10
    plain=client.post('/api/v1/extensions/analyze/text',headers=eh,json={'text':page,'url':'https://www.google.com/search?q=x'}).json()
    assert plain['action']=='allow'
    r=client.post('/api/v1/extensions/analyze/text',headers=eh,json={'text':page,'query':'how to kill myself','url':'https://www.google.com/search?q=how+to+kill+myself','fast':True}).json()
    assert r['decided_by_query'] and r['feature']=='Search query' and r['category']=='Self-harm Indicators' and r['action'] in ('warn','block')

def test_ac09_policy_toggle_modality_and_sync_payload():
    h,_=account('pol@example.test');eh=paired(h)
    client.put('/api/v1/settings',headers=h,json={'enabled':False})
    assert client.get('/api/v1/extensions/policy',headers=eh).json()['enabled'] is False
    assert client.post('/api/v1/extensions/analyze/text',headers=eh,json={'text':'I will kill you'}).json()['skipped']
    assert client.put('/api/v1/settings',headers=h,json={'sync_interval_seconds':15}).status_code==422  # FR-SET-05: below Chrome's 30 s alarm minimum
    client.put('/api/v1/settings',headers=h,json={'enabled':True,'modalities':{'text':False},'sync_interval_seconds':45})
    p=client.get('/api/v1/extensions/policy',headers=eh).json()
    assert p['modalities']['text'] is False and p['sync_interval_seconds']==45 and p['modalities']['audio'] is False
    assert client.post('/api/v1/extensions/analyze/text',headers=eh,json={'text':'I will kill you'}).json()['skipped']

def test_ac11_cross_account_denied_and_ac02():
    h1,_=account('a1@example.test');h2,_=account('a2@example.test');eh=paired(h1)
    eid=client.post('/api/v1/extensions/analyze/text',headers=eh,json={'text':'I will kill you'}).json()['event_id']
    assert client.get('/api/v1/events/'+eid,headers=h2).status_code==404
    assert client.get('/api/v1/events',headers=h2).json()['total']==0 and client.get('/api/v1/alerts',headers=h2).json()==[]
    assert client.get('/api/v1/analytics').status_code==401 and client.get('/api/v1/functions').status_code==401

def test_google_signin_option(monkeypatch):
    assert client.get('/api/v1/auth/config').json()['password'] is True
    assert client.post('/api/v1/auth/google',json={'credential':'x'*40}).status_code==503  # not configured
    monkeypatch.setenv('MORVIX_GOOGLE_CLIENT_ID','cid')
    monkeypatch.setattr(main,'verify_google_credential',lambda c:{'sub':'g-1','email':'Kid.Parent@Gmail.com','name':'G Parent'})
    d=client.post('/api/v1/auth/google',json={'credential':'x'*40}).json()
    h={'Authorization':'Bearer '+d['access_token']};me=client.get('/api/v1/auth/me',headers=h).json()
    assert me['email']=='kid.parent@gmail.com' and me['provider']=='google'
    assert client.post('/api/v1/auth/google',json={'credential':'y'*40}).json()['user']['id']==d['user']['id']
    assert client.post('/api/v1/auth/login',json={'email':'kid.parent@gmail.com','password':'anything at all!'}).status_code==401
    # Someone else started an unverified password sign-up with this Gmail; the real owner signs in with Google
    client.post('/api/v1/auth/register',json={'email':'owner@gmail.com','password':'squatter password'})
    monkeypatch.setattr(main,'verify_google_credential',lambda c:{'sub':'g-2','email':'owner@gmail.com','name':'Owner'})
    assert client.post('/api/v1/auth/google',json={'credential':'z'*40}).status_code==200
    assert client.post('/api/v1/auth/login',json={'email':'owner@gmail.com','password':'squatter password'}).status_code==401  # password disabled

def test_functions_analytics_models_and_no_fake_metrics():
    h,_=account('fn@example.test');eh=paired(h)
    client.post('/api/v1/extensions/analyze/text',headers=eh,json={'text':'I will kill you'})
    fns={f['module']:f for f in client.get('/api/v1/functions',headers=h).json()}
    assert fns['Text Analysis']['detections']==1 and 'page text' in fns['Text Analysis']['applied_to'].lower() and fns['Audio Analysis (Optional)']['enabled'] is False
    a=client.get('/api/v1/analytics?days=7',headers=h).json();assert a['by_function']=={'Text Analysis':1} and a['total']==1
    s=client.get('/api/v1/summary?modality=image',headers=h).json();assert s['recent']==[] and s['validated_metrics']=={} 
    assert any(m['name']==main.ml.LEXICON_ID and m['evaluation'] is None for m in client.get('/api/v1/models',headers=h).json())
    ext=client.get('/api/v1/extensions',headers=h).json()[0];assert ext['status']=='connected'

def test_extension_status_revoke_and_history_filters():
    h,_=account('rev@example.test');eh=paired(h);eid=client.get('/api/v1/extensions',headers=h).json()[0]['id']
    client.post('/api/v1/extensions/events',headers=eh,json={'modality':'video','category':'Graphic Violence','confidence':0.7,'action':'warn','url':'https://v.example.com/a','feature':'Sampled video frames'})
    assert client.get('/api/v1/events?domain=v.example&modality=video&category=Graphic%20Violence',headers=h).json()['total']==1
    assert client.get('/api/v1/events?modality=text',headers=h).json()['total']==0
    assert client.delete('/api/v1/extensions/'+eid,headers=h).status_code==204
    assert client.get('/api/v1/extensions/policy',headers=eh).status_code==401

def test_extension_turn_off_and_on_keeps_pairing():
    h,_=account('pause@example.test');eh=paired(h);eid=client.get('/api/v1/extensions',headers=h).json()[0]['id']
    assert client.patch('/api/v1/extensions/'+eid,headers=h,json={'paused':True}).json()['paused'] is True
    p=client.get('/api/v1/extensions/policy',headers=eh);assert p.status_code==200  # still paired
    assert p.json()['enabled'] is False and p.json()['paused'] is True
    assert client.get('/api/v1/extensions',headers=h).json()[0]['status']=='off'
    assert client.post('/api/v1/extensions/analyze/text',headers=eh,json={'text':'I will kill you','url':'https://x.example/'}).json()['skipped']
    assert client.post('/api/v1/extensions/events',headers=eh,json={'modality':'video','category':'Graphic Violence','confidence':0.7,'action':'warn','url':'https://v.example.com/a'}).json()['recorded'] is False
    assert client.get('/api/v1/events',headers=h).json()['total']==0
    client.patch('/api/v1/extensions/'+eid,headers=h,json={'paused':False})
    p=client.get('/api/v1/extensions/policy',headers=eh).json();assert p['enabled'] is True and p['paused'] is False
    assert client.post('/api/v1/extensions/analyze/text',headers=eh,json={'text':'I will kill you','url':'https://x.example/'}).json()['category']=='Threat Language'
    h2,_=account('pause-other@example.test')
    assert client.patch('/api/v1/extensions/'+eid,headers=h2,json={'paused':True}).status_code==404  # another parent cannot toggle it
    client.delete('/api/v1/extensions/'+eid,headers=h)
    assert client.patch('/api/v1/extensions/'+eid,headers=h,json={'paused':False}).status_code==404  # removed browsers need a new code
    eh2=paired(h);assert client.get('/api/v1/extensions/policy',headers=eh2).status_code==200  # same parent can pair again

def test_paired_browser_can_move_to_a_new_parent_account():
    # Parent forgot the old account and made a new one: the extension pairs with the new code, then releases the old pairing.
    old,_=account('forgot-old@example.test');new,_=account('forgot-new@example.test')
    eh_old=paired(old);eh_new=paired(new)
    assert client.post('/api/v1/extensions/self/release',headers=eh_old).status_code==204
    assert client.get('/api/v1/extensions/policy',headers=eh_old).status_code==401
    assert client.get('/api/v1/extensions/policy',headers=eh_new).status_code==200
    assert client.get('/api/v1/extensions',headers=old).json()[0]['status']=='revoked'
    assert client.get('/api/v1/extensions',headers=new).json()[0]['status']!='revoked'
    assert client.post('/api/v1/extensions/self/release').status_code==401  # needs an extension token


# ---- v3 additions ----
def test_repeat_detection_on_same_page_is_deduplicated():
    h,_=account('dedupe@example.test');eh=paired(h)
    body={'text':'I am going to kill you tonight, watch your back.','url':'https://chat.example.org/room'}
    a=client.post('/api/v1/extensions/analyze/text',headers=eh,json=body).json()
    b=client.post('/api/v1/extensions/analyze/text',headers=eh,json=body).json()
    assert a['event_id']==b['event_id']
    assert client.get('/api/v1/events',headers=h).json()['total']==1 and len(client.get('/api/v1/alerts',headers=h).json())==1
    other=client.post('/api/v1/extensions/analyze/text',headers=eh,json={**body,'url':'https://chat.example.org/other'}).json()
    assert other['event_id']!=a['event_id']

def test_curly_apostrophes_and_chunking():
    from backend import classifiers as ml
    assert ml.classify_text('I’m going to kill you')['category']=='Threat Language'
    chunks=ml.text_chunks('Sentence one. '*300)
    assert 1<len(chunks)<=ml.MAX_WINDOWS and all(len(c)<=ml.WINDOW_CHARS for c in chunks)
    assert ml.WINDOW_CHARS*ml.MAX_WINDOWS>=3500  # windows cover the extension's whole 3,500-character page extract
    r=ml.classify_text('I will end my life tonight')
    assert r['scores']['Self-harm Indicators']==r['confidence'] and r['decided_by']==[ml.LEXICON_ID]

def test_dashboard_served_by_backend_and_engine_inventory():
    assert client.get('/').status_code==200 and 'app.js' in client.get('/').text
    assert client.get('/api/v1/health').json()['status']=='ok'
    h,_=account('inv@example.test')
    models=client.get('/api/v1/models',headers=h).json();names={m['name'] for m in models}
    assert all({'evaluation_ref','evaluated_at','license'}<=set(m) for m in models)  # SRS §5 Model Metadata
    assert {'KoalaAI/Text-Moderation','sentinet/suicidality','jaranohaal/vit-base-violence-detection','openai/clip-vit-base-patch32'}<=names

def test_image_endpoint_reports_missing_models_as_503_not_a_result():
    h,_=account('img@example.test')
    r=client.post('/api/v1/classify/image',headers=h,files={'file':('x.png',b'\x89PNG\r\n\x1a\n','image/png')})
    assert r.status_code==503

# ---------------------------------------------------------------- FR-AUTH-09/10 email verification
def test_signup_requires_emailed_code_before_login():
    e='otp@example.test'
    r=client.post('/api/v1/auth/register',json={'email':e,'password':PW});assert r.status_code==201
    assert 'access_token' not in r.json() and r.json()['verification_required']
    code=last_code(e)
    with main.db() as c:  # only a hash of the code is stored
        row=c.execute('SELECT code_hash FROM email_codes').fetchall();assert all(code not in x['code_hash'] for x in row)
    l=client.post('/api/v1/auth/login',json={'email':e,'password':PW});assert l.status_code==403 and 'Verify your email' in l.json()['detail']
    assert client.post('/api/v1/auth/verify-email',json={'email':e,'code':'abc'}).status_code==422
    s=client.post('/api/v1/auth/verify-email',json={'email':e,'code':code});assert s.status_code==200
    assert client.get('/api/v1/auth/me',headers={'Authorization':'Bearer '+s.json()['access_token']}).status_code==200
    assert client.post('/api/v1/auth/login',json={'email':e,'password':PW}).status_code==200
    assert client.post('/api/v1/auth/verify-email',json={'email':e,'code':code}).status_code==400  # single use
    assert client.post('/api/v1/auth/register',json={'email':e,'password':PW}).status_code==409

def test_wrong_codes_are_limited_and_resend_replaces_code():
    e='limit@example.test';client.post('/api/v1/auth/register',json={'email':e,'password':PW});code=last_code(e)
    wrong=f'{(int(code)+1)%10**6:06d}'
    for i in range(5):
        r=client.post('/api/v1/auth/verify-email',json={'email':e,'code':wrong});assert r.status_code==400
    assert client.post('/api/v1/auth/verify-email',json={'email':e,'code':code}).status_code==429  # locked even with the right code
    assert client.post('/api/v1/auth/resend-code',json={'email':e}).status_code==429  # cooldown
    with main.db() as c: c.execute('UPDATE email_codes SET sent_at=?',(time.time()-120,))
    assert client.post('/api/v1/auth/resend-code',json={'email':e}).status_code==200
    new=last_code(e)
    if new!=code: assert client.post('/api/v1/auth/verify-email',json={'email':e,'code':code}).status_code==400
    assert client.post('/api/v1/auth/verify-email',json={'email':e,'code':new}).status_code==200

def test_expired_code_unknown_email_and_restart_unverified_signup():
    e='expire@example.test';client.post('/api/v1/auth/register',json={'email':e,'password':PW});code=last_code(e)
    with main.db() as c: c.execute('UPDATE email_codes SET expires_at=?',(time.time()-1,))
    assert 'expired' in client.post('/api/v1/auth/verify-email',json={'email':e,'code':code}).json()['detail']
    assert client.post('/api/v1/auth/resend-code',json={'email':'nobody@example.test'}).json()['sent'] is True  # no account enumeration
    r=client.post('/api/v1/auth/register',json={'email':e,'password':'a different password'});assert r.status_code==201  # unverified: may start over
    assert client.post('/api/v1/auth/verify-email',json={'email':e,'code':last_code(e)}).status_code==200
    assert client.post('/api/v1/auth/login',json={'email':e,'password':'a different password'}).status_code==200

def test_smtp_dropped_connection_is_retried_once_and_reported_with_its_step(monkeypatch):
    import importlib.util, smtplib
    spec = importlib.util.spec_from_file_location('mailer_under_test', main.mailer.__file__)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)  # separate copy: the shared module stays patched
    for k, v in {'EMAIL_MODE': 'smtp', 'SMTP_HOST': 'smtp.example.test', 'SMTP_PORT': '587', 'SMTP_USER': 'a@example.test', 'SMTP_PASSWORD': 'x' * 16}.items():
        monkeypatch.setenv('MORVIX_' + k, v)
    monkeypatch.setattr(m.time, 'sleep', lambda s: None)
    calls = []
    class FakeSMTP:
        fail_times = 1
        def __init__(self, *a, **k): calls.append('connect')
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self, **k): pass
        def login(self, *a):
            if calls.count('connect') <= FakeSMTP.fail_times: raise smtplib.SMTPServerDisconnected('Connection unexpectedly closed')
        def send_message(self, msg): calls.append('sent')
    monkeypatch.setattr(m.smtplib, 'SMTP', FakeSMTP)
    m.send_mail('p@example.test', 'code', '123456')
    assert calls == ['connect', 'connect', 'sent']  # dropped once, retried, sent
    calls.clear(); FakeSMTP.fail_times = 99
    try:
        m.send_mail('p@example.test', 'code', '123456'); assert False, 'expected MailError'
    except m.MailError as e:
        assert 'during login' in str(e) and calls == ['connect', 'connect']

def test_signup_refused_when_email_not_configured(monkeypatch):
    monkeypatch.setenv('MORVIX_EMAIL_MODE','');monkeypatch.delenv('MORVIX_SMTP_HOST',raising=False)
    r=client.post('/api/v1/auth/register',json={'email':'nomail@example.test','password':PW})
    assert r.status_code==503 and 'not configured' in r.json()['detail']
    assert client.get('/api/v1/auth/config').json()['email_configured'] is False

def test_fusion_halves_unsupported_lexicon_votes():
    """Keyword-only evidence cannot flag a page by itself when the ML engines disagree."""
    lex, nli = main.ml.LEXICON_ID, main.ml.NLI_ENGINE
    quiet = {c: 0.01 for c in main.ml.CATEGORIES}
    fused, notes = main.ml.fuse_text({lex: {'Self-harm Indicators': 0.71, 'Threat Language': 0, 'Graphic Violence': 0}, nli: quiet, 'sentinet/suicidality': {**quiet, 'Self-harm Indicators': 0.5}}, 'thoughts of suicide or self-harm, call a hotline')
    assert fused['Self-harm Indicators'] < 0.5 and any('lexicon' in n for n in notes)
    fused, _ = main.ml.fuse_text({lex: {'Self-harm Indicators': 0.71, 'Threat Language': 0, 'Graphic Violence': 0}}, 'i want to die')  # lexicon-only mode unchanged
    assert fused['Self-harm Indicators'] == 0.71
