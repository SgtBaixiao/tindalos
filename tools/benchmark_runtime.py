"""Reproducible microbenchmark; synthetic delays never stand in for model quality."""
from __future__ import annotations
import argparse
import json
import os
import platform
import statistics
import tempfile
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from tindalos.generator import DeterministicGenerator
from tindalos.pipeline import run_pipeline


def percentile(values,p):
    return sorted(values)[max(0,int(len(values)*p)-1)]


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--out',type=Path,required=True); parser.add_argument('--runtime',action='store_true'); args=parser.parse_args()
    result={'python':platform.python_version(),'platform':platform.platform(),'repetitions':12,'synthetic_model_delay_ms':20}
    class Counter(DeterministicGenerator):
        def __init__(self): super().__init__(seed='benchmark'); self.calls={'acts':0,'npcs':0,'scene':0}
        def generate_acts(self,*a,**kw): self.calls['acts']+=1;time.sleep(.02);return super().generate_acts(*a,**kw)
        def generate_npcs(self,*a,**kw): self.calls['npcs']+=1;time.sleep(.02);return super().generate_npcs(*a,**kw)
        def generate_scene(self,*a,**kw): self.calls['scene']+=1;time.sleep(.02);return super().generate_scene(*a,**kw)
    from tindalos.config import Settings
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.store.memory import InMemoryStore
    with tempfile.TemporaryDirectory(prefix='tindalos-bench-') as temp:
        base=Path(temp);settings=Settings(store_dir=base/'store'); timings=[];calls=[];edges=[]
        for _ in range(12):
            gen=Counter(); start=time.perf_counter();out=run_pipeline('Harbour investigation',settings=settings,generator=gen,checkpointer=InMemorySaver(),store=InMemoryStore());timings.append((time.perf_counter()-start)*1000);calls.append(gen.calls);edges.append(sum(len(e.next_event_ids) for a in out['campaign'].acts for s in a.scenes for e in s.events))
        result['generation']={'p50_ms':statistics.median(timings),'p95_ms':percentile(timings,.95),'samples_ms':timings,'calls':calls,'event_edges':edges}
        if args.runtime:
            from tindalos import history
            from tindalos.jobs import JobRuntime
            os.environ['TINDALOS_SITE_DB']=str(base/'site.db')
            runtime=JobRuntime(lambda req,emit:(time.sleep(.5),{'id':req['id'],'title':'Benchmark'})[1],workers=2,capacity=16)
            try:
                jobs=[runtime.submit({'id':f'c-{i}'}) for i in range(12)]
                def read(_):
                    started=time.perf_counter();history.list_campaigns();runtime.get(jobs[0]['id']);return (time.perf_counter()-started)*1000
                with ThreadPoolExecutor(max_workers=20) as pool: reads=list(pool.map(read,range(400)))
                result['read_during_generation']={'clients':20,'samples':len(reads),'p50_ms':statistics.median(reads),'p95_ms':percentile(reads,.95),'max_ms':max(reads),'errors':0}
            finally:runtime.close()
            import sqlite3
            result['sqlite']=sqlite3.sqlite_version
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in result.items() if k not in ('generation',)},ensure_ascii=False));print({k:v for k,v in result['generation'].items() if k not in ('samples_ms','calls','event_edges')},result['generation']['calls'][0])
if __name__=='__main__':main()
