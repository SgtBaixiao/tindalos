"""Measure real loopback HTTP reads while twelve bounded generation jobs run."""
from __future__ import annotations
import argparse
import json
import os
import socket
import statistics
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
import uvicorn
from tindalos import web


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--out',type=Path,required=True); args=parser.parse_args()
    original=web.default_generate
    def delayed(text,llm,emit,*,campaign_id=None,persist_memory=False,**kwargs):
        time.sleep(.5)  # synthetic external model latency; real offline graph runs afterward
        return original(text,llm,emit,campaign_id=campaign_id,persist_memory=persist_memory)
    with tempfile.TemporaryDirectory(prefix='tindalos-http-') as temp:
        os.environ['TINDALOS_DATA_DIR']=temp; os.environ['TINDALOS_SITE_DB']=str(Path(temp)/'site.db')
        web.default_generate=delayed
        sock=socket.socket(); sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(web.create_app(),log_level='error'))
        thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True); thread.start()
        base=f'http://127.0.0.1:{port}'
        try:
            deadline=time.monotonic()+10
            while not server.started:
                if time.monotonic()>deadline: raise RuntimeError('server did not start')
                time.sleep(.01)
            jobs=[]
            for i in range(12):
                response=requests.post(base+'/api/jobs',json={'module_text':f'Harbour {i}'},timeout=10)
                response.raise_for_status(); jobs.append(response.json()['job']['id'])
            def client(_):
                samples=[]
                with requests.Session() as session:
                    for i in range(20):
                        path='/api/campaigns' if i%2==0 else f'/api/jobs/{jobs[0]}'
                        start=time.perf_counter(); response=session.get(base+path,timeout=10); response.raise_for_status()
                        samples.append((time.perf_counter()-start)*1000)
                return samples
            with ThreadPoolExecutor(max_workers=20) as pool:
                samples=[value for group in pool.map(client,range(20)) for value in group]
            samples.sort()
            result={'transport':'real loopback HTTP; uvicorn/FastAPI','clients':20,'reads':len(samples),
                'jobs':12,'synthetic_model_delay_ms':500,'p50_ms':statistics.median(samples),
                'p95_ms':samples[int(len(samples)*.95)-1],'max_ms':max(samples),'errors':0,
                'paths':['/api/campaigns','/api/jobs/{id}'],'samples_ms':samples}
            args.out.write_text(json.dumps(result,indent=2)); print({k:v for k,v in result.items() if k!='samples_ms'})
        finally:
            server.should_exit=True; thread.join(15); sock.close(); web.default_generate=original
            if thread.is_alive(): raise RuntimeError('server shutdown exceeded 15 seconds')


if __name__=='__main__': main()
