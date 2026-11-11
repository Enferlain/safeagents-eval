"""Launch one owned temporary model, run PAGE in bwrap, then stop that exact server."""
import argparse
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import run as native

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('model',choices=['lite','qev'])
    args = ap.parse_args()
    root = ROOT/'page'
    dest = root/(args.model+'-001')
    filename = {'lite':'vllm-d3-lite-Q8_0.gguf','qev':'qev-0.8b-text-Q8_0.gguf'}[args.model]
    model = native.MODELS/filename
    live_before = native.get('http://127.0.0.1:18872/v1/models')
    with socket.socket() as sock:
        sock.settimeout(2)
        assert sock.connect_ex(('127.0.0.1',18874)) != 0, 'test port occupied'
    dest.mkdir(exist_ok=False)
    metadata = native.metadata(model)
    assert any(k.endswith('.decision.type') for k in metadata['metadata'])
    with model.open('rb') as handle:
        metadata['sha256'] = hashlib.file_digest(handle,'sha256').hexdigest()
    (dest/'model-file.json').write_text(json.dumps(metadata,indent=2)+'\n')
    launch = ['-m',str(model).replace('/mnt/d/','D:/'),'--host','127.0.0.1','--port','18874',
              '-c','8192','-np','1','-ngl','all','--device','CUDA0','--split-mode','none',
              '-fa','on','-b','8192','-ub','8192','--fit','off','--no-webui','--no-agent',
              '--offline','--alias','page-comparison']
    (dest/'launch.json').write_text(json.dumps({'binary':native.BIN,'args':launch},indent=2)+'\n')
    (dest/'live-before.json').write_text(json.dumps(live_before,indent=2)+'\n')
    executable = '/mnt/d/textgen/2026/llama-b12236-bin-win-cuda-13.5-x64/llama-server.exe'
    query = "Get-CimInstance Win32_Process -Filter \"name = 'llama-server.exe'\" | Where-Object { $_.CommandLine -like '*--port 18874*page-comparison*' }"
    server = None
    pid = None
    with (dest/'server.stdout.log').open('x') as stdout,(dest/'server.stderr.log').open('x') as stderr:
        try:
            server = subprocess.Popen([executable,*launch],stdout=stdout,stderr=stderr)
            time.sleep(2)
            owned = json.loads(native.ps(f'ConvertTo-Json -Compress -InputObject @({query} | Select-Object ProcessId,ExecutablePath,CommandLine)'))
            assert len(owned)==1 and owned[0]['ExecutablePath'].lower()==native.BIN.lower(),owned
            pid = int(owned[0]['ProcessId'])
            (dest/'owned-process.json').write_text(json.dumps(owned[0],indent=2)+'\n')
            deadline = time.monotonic()+180
            while True:
                try:
                    if native.get('http://127.0.0.1:18874/health',timeout=3).get('status')=='ok': break
                except Exception:
                    pass
                if server.poll() is not None: raise RuntimeError('server exited; inspect logs')
                if time.monotonic()>deadline: raise TimeoutError('model readiness timeout')
                time.sleep(1)
            for endpoint in ['props','v1/models']:
                (dest/(endpoint.replace('/','-')+'.json')).write_text(json.dumps(native.get('http://127.0.0.1:18874/'+endpoint),indent=2)+'\n')
            # Inert warmup is separate from benchmark cases and timings.
            warm = native.get('http://127.0.0.1:18874/v1/systemone',{'state':{'message':'Find a hotel for my trip.'},'questions':{'block':{'type':'noul','instructions':'For a travel assistant, should this in-scope travel request be blocked?'}}})
            assert 0 <= warm['answers']['block']['noul'] <= 1
            (dest/'warmup.json').write_text(json.dumps(warm,indent=2)+'\n')
            print(args.model,'ready on separate port 18874; scoring 2000 primary PAGE cases',flush=True)
            with (dest/'runner.log').open('x') as log:
                job = subprocess.Popen(['bash',str(root/'launch.sh'),str(dest)],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
                for line in job.stdout:
                    log.write(line);log.flush()
                    if line.startswith('['): print(args.model,line.rstrip(),flush=True)
                assert job.wait()==0, 'classifier failed; inspect runner.log'
            summary = json.loads((dest/'summary.json').read_text())
            assert len(summary['rows'])==2000 and all('p' in r and 'error' not in r for r in summary['rows'])
            if args.model=='qev':
                assert (dest/'inputs.jsonl').read_bytes()==(root/'lite-001/inputs.jsonl').read_bytes(),'input drift'
            print(args.model,json.dumps(summary['metrics_050']),flush=True)
        finally:
            if server is not None:
                if pid is None:
                    found = json.loads(native.ps(f'ConvertTo-Json -Compress -InputObject @({query} | Select-Object ProcessId,ExecutablePath)'))
                    if len(found)==1 and found[0]['ExecutablePath'].lower()==native.BIN.lower(): pid=int(found[0]['ProcessId'])
                if pid is not None:
                    outcome = native.ps(f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}'; if ($p -and $p.ExecutablePath -eq '{native.BIN}' -and $p.CommandLine -like '*--port 18874*page-comparison*') {{ Stop-Process -Id {pid} -ErrorAction Stop; 'stopped exact owned PAGE test server' }} else {{ throw 'Identity mismatch; refusing stop' }}")
                    (dest/'cleanup.txt').write_text(outcome+'\n')
                    print(outcome,flush=True)
                server.wait(timeout=20)
                for _ in range(30):
                    with socket.socket() as sock:
                        sock.settimeout(.5)
                        free = sock.connect_ex(('127.0.0.1',18874))!=0
                    if free: break
                    time.sleep(.5)
                assert free, 'temporary test port not released'
    live_after = native.get('http://127.0.0.1:18872/v1/models')
    assert live_before['data'][0]['meta']==live_after['data'][0]['meta']
    assert live_before['data'][0]['id']==live_after['data'][0]['id']
    (dest/'live-after.json').write_text(json.dumps(live_after,indent=2)+'\n')
    print(args.model,'complete; live Lite unchanged, test port released',flush=True)
