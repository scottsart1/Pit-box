import asyncio
import json
import math
import os
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from yourpitbox_nascar.app import create_app, parse_csv
from yourpitbox_nascar.engineer import Engineer
from yourpitbox_nascar.models import OCRConfig, Observation, RaceConfig
from yourpitbox_nascar.observer import parse_reading, recognize
from yourpitbox_nascar.secrets import Credentials


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, token="test-token")
    with TestClient(app) as client:
        client.headers["x-pitbox-key"] = "test-token"
        yield client


def test_product_and_api_auth(client):
    assert client.get('/health').json()['product'] == 'yourpitbox-nascar'
    assert client.get('/api/state',headers={'x-pitbox-key':'bad'}).status_code == 401
    assert client.get('/api/state',headers={'origin':'https://other.example'}).status_code == 403
    assert client.get('/api/state',headers={'host':'attacker.example'}).status_code == 403
    assert client.get('/').status_code == 200


def test_remote_bootstrap_never_contains_secret(tmp_path):
    app = create_app(tmp_path, token='secret-placeholder')
    with TestClient(app,client=('192.168.1.8',1234)) as client:
        assert 'secret-placeholder' not in client.get('/').text
        assert client.get('/api/settings',headers={'x-pitbox-key':'secret-placeholder'}).status_code == 403


def test_create_observe_export_resume_flow(client):
    sid = client.post('/api/sessions',json={'track':'Darlington','name':'Long run','stage_ends':[25,50]}).json()['id']
    assert client.post('/api/observations',json={'session_id':sid,'sequence':1,'sample':{'completed_laps':4,'fuel_pct':80,'flag':'green'}}).json()['accepted']
    assert client.post('/api/laps',json={'number':4,'time_s':31.234,'flag':'green'}).status_code == 200
    assert client.post('/api/pits',json={'tires':'right','fuel_added_pct':10}).status_code == 200
    assert client.post('/api/handling',json={'scope':'radical','balance':'loose'}).status_code == 200
    saved = client.get('/api/sessions/'+sid).json()
    assert saved['laps'][0]['time_s'] == 31.234
    assert [x['kind'] for x in saved['notes']] == ['pit','handling']
    assert client.post('/api/sessions/'+sid+'/resume').status_code == 200
    state = client.get('/api/state').json()
    assert state['last_observed']['fuel_pct'] == 80
    assert state['values']['fuel_pct'] is None


def test_invalid_import_does_not_change_active_session(client):
    sid = client.post('/api/sessions',json={}).json()['id']
    r = client.post('/api/import',json={'config':{},'csv':'number,time_s\n1,nan\n'})
    assert r.status_code == 400
    assert client.get('/api/state').json()['session_id'] == sid
    assert len(client.get('/api/sessions').json()) == 1


def test_csv_import_is_separate_and_historical(client):
    old = client.post('/api/sessions',json={}).json()['id']
    r = client.post('/api/import',json={'config':{'session_type':'practice'},'csv':'number,time_s,fuel_used_pct,flag,clean,stint\n1,31.2,2.1,green,true,1\n2,31.3,2.2,green,false,1\n'})
    assert r.status_code == 200
    assert r.json()['id'] != old
    state = client.get('/api/state').json()
    assert state['run']['count'] == 1
    assert state['fuel']['fuel_pct'] is None


@pytest.mark.parametrize('text',['number,time_s\n1,30\n1,31','number,time_s,clean\n1,30,banana','time_s\n30','number,time_s\n'])
def test_csv_rejects_ambiguous_rows(text):
    with pytest.raises(ValueError): parse_csv(text)


@pytest.mark.parametrize('field,text,expected',[
    ('current_lap','LAP 17 / 100',17),('position','P0S 2',None),
    ('fuel_pct','FUEL 71.5%',71.5),('fuel_pct','FUEL 71.50/0',71.5),
    ('fuel_pct','5.2 L',None),('fuel_pct','101%',None),
    ('last_lap_s','LAST LAP 1:02.521',62.521),('flag','YELLOW FLAG','yellow'),
    ('flag','GREEN FLAG YELLOW FLAG',None),('flag','YELLOW car',None),
    ('pit_open','PIT ROAD CLOSED',False),('pit_open','PIT ROAD OPEN',True),
    ('pit_open','PIT 2',None),('speed_mph','178 MPH',178),('speed_mph','288 KPH',None),
])
def test_ocr_parser_has_strict_units_and_context(field,text,expected):
    assert parse_reading(field,text) == expected


@pytest.mark.skipif(os.name!='nt',reason='Windows OCR')
async def test_actual_windows_ocr_on_synthetic_hud():
    pytest.importorskip('winrt.windows.media.ocr')
    from PIL import Image,ImageDraw,ImageFont
    image = Image.new('RGB',(420,100),'black')
    ImageDraw.Draw(image).text((10,10),'FUEL 71.5%',font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',48),fill='white')
    text = await recognize(image)
    assert parse_reading('fuel_pct',text) == 71.5


class Key:
    def read(self): return 'test-key-never-real'


async def test_engineer_uses_field_tool_for_pit_intent(client):
    engine = client.app.state.engine
    engine.start(RaceConfig())
    engine.ingest(Observation(session_id=engine.sid,sequence=1,sample={'completed_laps':60,'opponents':[{'car':'4','position':1,'last_pit_lap':50,'gap_s':2}]}))
    requests = []
    def server(request):
        payload=json.loads(request.content);requests.append(payload)
        if len(requests)==1:
            return httpx.Response(200,json={'output':[{'type':'function_call','name':'field_strategy','call_id':'call_1','arguments':'{}'}]})
        result=json.loads(payload['input'][-1]['output'])
        assert result['unknown_cars']==['4']
        return httpx.Response(200,json={'output':[{'type':'message','content':[{'type':'output_text','text':"I can't tell whether car 4 needs another stop without its fuel range."}]}]})
    engineer=Engineer(engine,Key(),httpx.AsyncClient(transport=httpx.MockTransport(server)))
    result=await engineer.ask('Is the whole field expected to stop again?')
    assert result['tools']==['field_strategy']
    assert 'fuel range' in result['text']
    assert requests[0]['store'] is False
    await engineer.close()


async def test_model_failure_keeps_local_brief(client):
    def fail(request): return httpx.Response(429,json={'error':'rate limit'})
    engineer=Engineer(client.app.state.engine,Key(),httpx.AsyncClient(transport=httpx.MockTransport(fail)))
    result=await engineer.ask('What should we do?')
    assert result['mode']=='unavailable'
    assert 'Local brief' in result['text']
    await engineer.close()


def test_scenario_is_read_only_and_bounded(client):
    client.post('/api/sessions',json={'fuel_burn_green':2})
    engine=client.app.state.engine
    original=engine.config.model_dump()
    assert client.post('/api/scenario',json={'yellow_laps':2,'reserve_laps':1,'saving_percent':5}).status_code==200
    assert engine.config.model_dump()==original
    assert client.post('/api/scenario',json={'yellow_laps':2,'reserve_laps':1,'saving_percent':90}).status_code==422
    assert client.post('/api/scenario',json={'yellow_laps':2}).status_code==422


def test_ragged_csv_optional_fields_are_handled_without_server_error(client):
    result=client.post('/api/import',json={'config':{},'csv':'number,time_s,clean\n1,31.2\n'})
    assert result.status_code==200
    assert client.get('/api/sessions/'+result.json()['id']).json()['laps'][0]['clean'] is True


def test_import_continues_the_latest_stint_after_resume(client):
    result=client.post('/api/import',json={'config':{},'csv':'number,time_s,stint\n1,31.2,1\n2,31.1,2\n'})
    assert result.status_code==200
    assert client.get('/api/state').json()['stint']==2
    client.post('/api/sessions/'+result.json()['id']+'/resume')
    assert client.get('/api/state').json()['stint']==2
    client.post('/api/pits',json={'tires':'four'})
    assert client.get('/api/state').json()['stint']==3


def test_spoken_handling_review_uses_selected_controls_and_survives_resume(client):
    sid=client.post('/api/sessions',json={}).json()['id']
    engineer=client.app.state.engineer
    args={'phase':'entry','balance':'tight','scope':'moderate','timing':'late'}
    assert engineer.execute('handling_review',args,client.app.state.engine.snapshot())['actions']==[]
    client.post('/api/handling',json={'available_controls':['brake_bias'],'goal':'tire_life'})
    client.post('/api/sessions/'+sid+'/resume')
    result=engineer.execute('handling_review',args,client.app.state.engine.snapshot())
    assert [x['control'] for x in result['actions']]==['brake_bias']
    assert result['goal']=='tire_life'
    client.post('/api/sessions',json={})
    assert client.app.state.engine.snapshot()['handling_report'] is None


def test_pairing_has_distinct_invitations_for_each_network(client,tmp_path):
    from urllib.parse import urlparse,parse_qs
    from yourpitbox_nascar.main import pairing_info
    pairing_info(client.app,tmp_path,['192.168.1.4','10.0.0.4'],53261)
    data=client.get('/api/pairing').json()
    assert len(data['connections'])==2
    hosts=[]
    for invitation in data['connections']:
        params=parse_qs(urlparse(invitation['url']).query)
        hosts.append(params['host'][0])
        assert params['fingerprint']==[data['fingerprint']]
        assert invitation['qr_base64']
    assert hosts==['192.168.1.4','10.0.0.4']


def test_credentials_round_trip_and_removal(tmp_path, monkeypatch):
    monkeypatch.delenv('NASCAR_OPENAI_API_KEY',raising=False)
    credentials=Credentials(tmp_path)
    credentials.save('a-test-secret')
    assert credentials.read()=='a-test-secret'
    if os.name=='nt': assert b'a-test-secret' not in credentials.path.read_bytes()
    credentials.save('')
    assert credentials.read()==''


def test_high_rate_ingestion_is_bounded_and_proactive_keeps_working(client):
    engine=client.app.state.engine
    engine.start(RaceConfig(fuel_burn_green=2))
    start=time.perf_counter()
    for n in range(3600):
        engine.ingest(Observation(session_id=engine.sid,sequence=n,source='adapter',sample={'completed_laps':n//120,'fuel_pct':max(2,60-n/60),'flag':'green','pit_open':True}))
        if n%30==0: engine.proactive()
    elapsed=time.perf_counter()-start
    assert engine.metrics['accepted']==3600
    assert len(engine.radio)<=80
    assert len(engine.laps)<=30
    assert any(x['kind']=='urgent' for x in engine.radio)
    assert elapsed<10, f'Ingestion took {elapsed:.2f}s for one minute at 60Hz'
