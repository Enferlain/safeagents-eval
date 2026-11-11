"""Independent readout checks on real benchmark rows, with the same unchanged GGUF."""
import argparse
import hashlib
import json
import pathlib
from intern_adapter import InternBackend, candidate_probability
from run import ROOT, dataset, payload

parser=argparse.ArgumentParser()
parser.add_argument('--model',type=pathlib.Path,required=True)
a=parser.parse_args()
backend=InternBackend(a.model)
results=[]
try:
    rows=dataset()
    indices=sorted({0,100,200,300,400,500,next(i for i,r in enumerate(rows) if r['label']=='allow')})
    predictions={r['row_idx']:r for r in map(json.loads,(ROOT/'results'/a.model.stem/'predictions.jsonl').read_text().splitlines())}
    for index in indices:
        row=rows[index]
        enc=backend.encode(payload(row))
        position=enc['readout_position'];tokens=enc['tokens']
        response=backend.predict(payload(row))
        expected=[response['candidate_logits'][s] for s in ('A','B')]
        original=predictions[row['row_idx']]['response']['candidate_logits']
        assert max(abs(expected[j]-original[s]) for j,s in enumerate(('A','B'))) < 1e-4
        # Independently obtain logits by original batch index with several outputs.
        backend.context.kv_cache_clear()
        backend.batch.set_batch(tokens,n_past=0,logits_all=False)
        backend.batch.batch.logits[0]=True
        backend.batch.batch.logits[position]=True
        backend.context.decode(backend.batch)
        by_index=backend.context.get_logits_ith(position)
        packed=backend.context.get_logits()
        n_vocab=backend.model.n_vocab()
        selected=[float(by_index[i]) for i in backend.candidate_ids]
        second_packed=[float(packed[n_vocab+i]) for i in backend.candidate_ids]
        assert max(abs(x-y) for x,y in zip(expected,selected)) < 1e-3
        assert max(abs(x-y) for x,y in zip(selected,second_packed)) < 1e-6
        # Causality cross-check: a decode ending exactly at the readout token must
        # agree with reading that position from the full masked assistant skeleton.
        backend.context.kv_cache_clear()
        backend.batch.set_batch(tokens[:position+1],n_past=0,logits_all=False)
        backend.context.decode(backend.batch)
        ptr=backend.context.get_logits()
        prefix=[float(ptr[i]) for i in backend.candidate_ids]
        delta=max(abs(x-y) for x,y in zip(expected,prefix))
        prefix_p=candidate_probability(prefix,[0,1],backend.temperature)
        full_p=candidate_probability(expected,[0,1],backend.temperature)
        results.append({'row_idx':row['row_idx'],'label':row['label'],
            'causal_prefix_probability_delta':abs(prefix_p-full_p),
            'full_prefix_logits':expected,'batch_index_logits':selected,'causal_prefix_logits':prefix,
            'causal_prefix_max_abs_delta':delta,
            'p_block':candidate_probability(expected,[0,1],backend.temperature),
            'original_run_logits_reproduced':True})
    report={'model':a.model.name,'sparse_readout_checks_passed':True,'checks':results,
            'limits':'Causal-prefix numerical deltas are recorded, not asserted equal. No BF16 Transformers logit parity test.'}
    path=ROOT/'results'/a.model.stem/'readout-validation.json'
    path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
finally:
    backend.close()
