#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Opt-in checks against an installed shared dispatcher, using only our jobs."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from gpu_lab import rpc, TERMINAL


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe',action='store_true',help='also verify real GLX rendering and Vulkan/CUDA isolation')
    parser.add_argument('--wait',type=float,default=300,help='maximum queue wait per check, in seconds')
    args=parser.parse_args()
    own=[]

    def submit(argv,label,timeout=30):
        job=rpc('submit',{'argv':argv,'cwd':str(ROOT),'workload':'light','vram_mb':512,
                          'timeout_seconds':timeout,'label':label})
        own.append(job['id'])
        return job['id']

    def until(ident,predicate,seconds=None):
        end=time.monotonic()+(args.wait if seconds is None else seconds)
        while time.monotonic()<end:
            job=rpc('job',{'job_id':ident})
            if predicate(job): return job
            if job['status'] in TERMINAL:
                raise RuntimeError('Job ended before check: '+json.dumps(rpc('logs',{'job_id':ident})))
            time.sleep(.5)
        raise TimeoutError('Wait expired; see gpu-lab job '+ident)

    try:
        status=rpc('status')
        if status.get('mode')!='shared-queue' or status.get('scheduler_version')!=4:
            raise RuntimeError('Install the v4 shared dispatcher first')
        if not status.get('device_isolation'):
            raise RuntimeError('Worker device isolation is unavailable')
        ident=submit(['/usr/bin/python3','-c','import os; print(os.environ["CUDA_VISIBLE_DEVICES"])'],'GPU Lab source release environment check')
        finished=until(ident,lambda j:j['status'] in TERMINAL)
        assert finished['status']=='completed',finished
        assert status['gpus'][finished['gpu_slot']]['gpu']['uuid'] in rpc('logs',{'job_id':ident})['stdout']

        ident=submit(['/usr/bin/xmessage','-geometry','640x200+0+0','-buttons','PASS:0','-default','PASS','GPU Lab smoke test'],'GPU Lab source release display check',40)
        until(ident,lambda j:j['status']=='running')
        until(ident,lambda j:'"xmessage"' in rpc('windows',{'job_id':ident})['windows'],10)
        screen=rpc('screenshot',{'job_id':ident})
        from PIL import Image
        image=Image.open(screen['path'])
        assert image.width>=640 and image.height>=200
        assert image.crop((0,0,640,200)).convert('L').getextrema()[1]>0
        rpc('input',{'job_id':ident,'events':[{'type':'move','x':100,'y':80},{'type':'click','button':1},{'type':'key','key':'Return'}]})
        finished=until(ident,lambda j:j['status'] in TERMINAL,10)
        assert finished['status']=='completed',finished
        result={'passed':True,'checks':['GPU environment','window','screenshot','keyboard input','logs'], 'gpu':finished['gpu_slot']}
        if args.probe:
            ident=submit(['/usr/bin/python3',str(ROOT/'scripts/probe_gpu.py')],'GPU Lab source release GLX Vulkan CUDA probe',30)
            finished=until(ident,lambda j:j['status'] in TERMINAL)
            logs=rpc('logs',{'job_id':ident})
            assert finished['status']=='completed',logs
            probe=json.loads(logs['stdout'])
            expected=status['gpus'][finished['gpu_slot']]['gpu']['name']
            assert probe['cuda']==[expected] and probe['vulkan']==[expected],probe
            result['probe']=probe
        print(json.dumps(result,indent=2))
    finally:
        for ident in own:
            if rpc('job',{'job_id':ident})['status'] not in TERMINAL:
                rpc('cancel',{'job_id':ident})


if __name__=='__main__':
    main()
