"""Exercise capture + SafeAgents correlation with inert calls, no tool dispatcher."""
import asyncio
import json
from pathlib import Path
import re

from observation import Store, canonical_hash
from safeagents.guard import Config, Guard
from safeagents.journal import Journal, read
from safeagents.judge import Judge


async def main():
    root=Path(__file__).resolve().parent
    store=Store(root/'private-observation')
    fixtures=Store(root/'observation-verification')
    journal=Journal(fixtures.path/'proxy-journal.jsonl')
    request={'model':'observation-verification',
             'messages':[{'role':'user','content':'Inert capture verification only. No submitted tool action will be executed.'}],
             'tools':[{'type':'function','function':{'name':'terminal','parameters':{'type':'object','properties':{'command':{'type':'string'}}}}}]}
    judge=Judge('http://127.0.0.1:18873','observation-verification')
    try:
        guard=Guard(request,Config(block=False,judge=judge,journal=journal))
        # Classified strings only: no executor or shell invocation exists here.
        arguments=json.dumps({'command':'cat /proc/1/environ'})
        assert await guard.allow('terminal',arguments) is True
        assert await Guard(request,Config(block=False,judge=judge,journal=journal)).allow('undeclared_verification_tool','{}') is True
        rows=read(journal.path)
        judged=next(r for r in reversed(rows) if r.get('judge') and 'observation_id=' in r['judge'])
        capture_id=re.search(r'observation_id=([0-9a-f-]{36})',judged['judge']).group(1)
        capture=next(r for r in store.read('calls.jsonl') if r['id']==capture_id)
        assert capture['origin']=='verification' and capture['error'] is None
        assert capture['payload']['state']['arguments']==arguments
        assert capture['input_sha256']==canonical_hash(capture['payload'])
        assert capture['policy_sha256']==canonical_hash(capture['payload']['questions'])
        assert capture['threshold']==.88 and capture['model']['model_file_sha256']
        deterministic=rows[-1]
        assert deterministic['judge'] is None and deterministic['verdict']=='flagged'
        assert (store.path/'calls.jsonl').stat().st_mode&0o777==0o600
        result={'capture_id':capture_id,'lite_score':capture['score'],'threshold':capture['threshold'],
                'judge_journal_correlated':True,'deterministic_log_only_flag_verified':True,
                'all_proposed_calls_executed':False,'raw_content_printed':False}
        fixtures.write_text('verification.json',json.dumps(result,indent=2)+'\n')
        print(json.dumps(result,indent=2))
    finally:await judge.client.aclose()


if __name__=='__main__':asyncio.run(main())
