"""Paced 60 Hz API ingest plus UI and radio checks. Use an isolated data root."""
import argparse
import asyncio
import json
import re
import statistics
import time

import httpx


async def main():
    parser=argparse.ArgumentParser();parser.add_argument('--url',required=True);parser.add_argument('--seconds',type=int,default=60);args=parser.parse_args()
    async with httpx.AsyncClient(base_url=args.url,timeout=5) as client:
        token=re.search(r'name="pitbox-token" content="([^"]+)"',(await client.get('/')).text)[1]
        client.headers['x-pitbox-key']=token
        r=await client.post('/api/sessions',json={'name':'QA synthetic paced 60 Hz','fuel_burn_green':2,'fuel_burn_yellow':.5})
        r.raise_for_status();sid=r.json()['id']
        start=time.perf_counter();ingest_latency=[];state_latency=[];brief_latency=[]
        accepted=0;failures=[]
        async def read_loop():
            while time.perf_counter()-start<args.seconds:
                then=time.perf_counter()
                try:
                    r=await client.get('/api/state');r.raise_for_status();state_latency.append(time.perf_counter()-then)
                    then=time.perf_counter();r=await client.get('/api/brief/field');r.raise_for_status();brief_latency.append(time.perf_counter()-then)
                    assert 'fuel' in r.json()['text']
                except Exception as exc: failures.append(type(exc).__name__)
                await asyncio.sleep(.2)
        reader=asyncio.create_task(read_loop())
        for sequence in range(args.seconds*60):
            await asyncio.sleep(max(0,start+sequence/60-time.perf_counter()))
            sample={'completed_laps':sequence//120,'fuel_pct':max(1,55-sequence/65),'flag':'green','pit_open':True,
                    'opponents':[{'car':str(n),'position':n,'laps_down':0,'gap_s':n*.5} for n in range(1,41)]}
            before=time.perf_counter()
            try:
                r=await client.post('/api/observations',json={'session_id':sid,'sequence':sequence,'source':'adapter','sample':sample})
                r.raise_for_status();accepted+=int(r.json()['accepted']);ingest_latency.append(time.perf_counter()-before)
            except Exception as exc:failures.append(type(exc).__name__)
        await reader
        snap=(await client.get('/api/state')).json()
        def percentile(values):return round(sorted(values)[min(len(values)-1,int(len(values)*.95))]*1000,2) if values else None
        result={'scheduled':args.seconds*60,'accepted':accepted,'failures':failures,
                'elapsed_s':round(time.perf_counter()-start,2),'ingest_p95_ms':percentile(ingest_latency),
                'dashboard_p95_ms':percentile(state_latency),'brief_p95_ms':percentile(brief_latency),
                'urgent_calls':sum(x['kind']=='urgent' for x in snap['radio']),'recorded_laps':len(snap['laps'])}
        print(json.dumps(result,indent=2))
        assert not failures and accepted==args.seconds*60 and result['urgent_calls']>0


if __name__=='__main__':asyncio.run(main())
