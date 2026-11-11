"""Download pinned PAGE data/documents only. Never import or run upstream code."""
import hashlib
import json
from pathlib import Path
import urllib.request

COMMIT = '42b9bc836c8e8a7ac2b177c2657ed780de4c11ec'
FILES = ['data/agentdojo_cases.json','data/agentharm_cases.json','intents/personas.json',
         'splits/full_eval.json','baseline_judge/run_baseline_judge.py','README.md','LICENSE.md','NOTICE.md']
if __name__ == '__main__':
    root = Path(__file__).resolve().parent
    target = root/'data'
    target.mkdir(exist_ok=False)
    digests = {}
    for name in FILES:
        url = f'https://raw.githubusercontent.com/uber-research/page-benchmark/{COMMIT}/{name}'
        with urllib.request.urlopen(url,timeout=45) as response:
            body = response.read()
        dest = target/name
        dest.parent.mkdir(parents=True,exist_ok=True)
        with dest.open('xb') as handle:
            handle.write(body)
        digests[name] = hashlib.sha256(body).hexdigest()
        assert hashlib.sha256(dest.read_bytes()).hexdigest()==digests[name]
        print(name,len(body),'bytes',digests[name],flush=True)
    manifest = {'repository':'https://github.com/uber-research/page-benchmark','commit':COMMIT,
                'files':digests,'upstream_code_executed':False,
                'license':'Uber Non-Commercial License; AgentHarm portion additionally safety/security-research only; evaluation-only, no training'}
    with (root/'source-manifest.json').open('x') as handle:
        handle.write(json.dumps(manifest,indent=2)+'\n')
    cases = [c for name in FILES[:2] for c in json.loads((target/name).read_text())['cases']]
    canonical = json.loads((target/'splits/full_eval.json').read_text())['cases']
    assert len(cases)==len(canonical)==2180
    assert len({c['id'] for c in cases})==2180
    assert {c['id'] for c in cases}=={c['id'] for c in canonical}
    primary = [c for c in cases if c['request_kind']!='mixed']
    assert len(primary)==2000 and sum(c['expected']=='allowed' for c in primary)==1004
    print('Verified 2180 unique canonical cases; 2000 primary, 180 mixed excluded.',flush=True)
