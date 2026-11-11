"""Isolated Windows CUDA worker; runs one Intern checkpoint without generation."""
import argparse
import hashlib
import json
import math
import pathlib
import statistics
import time
from intern_adapter import InternBackend
from run import ROOT, dataset, metrics, payload


def fingerprint(path):
    stat = path.stat()
    with path.open('rb') as f:
        digest = hashlib.file_digest(f, 'sha256').hexdigest()
    return {'path':str(path),'size':stat.st_size,'mtime_ns':stat.st_mtime_ns,'sha256':digest}


def evaluate(model, rows):
    from tokenizers import Tokenizer
    dest = ROOT/'results'/model.stem
    dest.mkdir(parents=True, exist_ok=True)
    if (dest/'predictions.jsonl').exists():
        raise FileExistsError(f'Refusing to overwrite existing results: {dest}')
    before = fingerprint(model)
    (dest/'model-before.json').write_text(json.dumps(before,indent=2),encoding='utf-8')
    backend = None
    try:
        backend = InternBackend(model)
        runtime = backend.runtime
        runtime['dataset_sha256'] = hashlib.sha256((ROOT/'dataset.json').read_bytes()).hexdigest()
        runtime['source_hashes'] = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in backend.reference_dir.iterdir() if p.is_file()}
        runtime['adapter_sha256'] = hashlib.sha256((ROOT/'intern_adapter.py').read_bytes()).hexdigest()
        runtime['runner_sha256'] = hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()
        (dest/'runtime.json').write_text(json.dumps(runtime,indent=2),encoding='utf-8')
        tokenizer = Tokenizer.from_file(str(backend.reference_dir/'tokenizer.json'))
        if [tokenizer.encode(s,add_special_tokens=False).ids for s in ('A','B')] != [[i] for i in backend.candidate_ids]:
            raise ValueError('Candidate IDs differ from the released tokenizer')
        # Validate every real row against the released tokenizer before timing/scoring.
        lengths=[]
        for row in rows:
            enc=backend.encode(payload(row))
            if enc['tokens'] != tokenizer.encode(enc['text'],add_special_tokens=False).ids:
                raise ValueError(f'GGUF/HF tokenizer mismatch at row {row["row_idx"]}')
            lengths.append(len(enc['tokens']))
        (dest/'encoding-validation.json').write_text(json.dumps({
            'rows':len(rows),'all_token_ids_equal_released_hf_tokenizer':True,
            'min_tokens':min(lengths),'max_tokens':max(lengths),'truncation':False,
            'candidate_ids':backend.candidate_ids,'marker_id':backend.marker_id},indent=2),encoding='utf-8')
        print(model.stem,'encoding validation passed',len(rows),'rows; max tokens',max(lengths),flush=True)
        warmup=backend.predict(payload(rows[0]))
        (dest/'warmup.json').write_text(json.dumps(warmup,indent=2),encoding='utf-8')
        records=[]
        with (dest/'predictions.jsonl').open('x',encoding='utf-8') as out:
            for index,row in enumerate(rows):
                record={k:row.get(k) for k in ('row_idx','label','category','task','trial')}
                record['backend']=runtime['backend']
                start=time.perf_counter()
                try:
                    response=backend.predict(payload(row))
                    p=float(response['answers']['block']['noul'])
                    if not math.isfinite(p) or not 0<=p<=1:
                        raise ValueError('Invalid probability')
                    record.update(p_block=p,response=response,forward_seconds=response['timing']['forward_seconds'])
                except Exception as exc:
                    record['error']=f'{type(exc).__name__}: {exc}'
                record['seconds']=time.perf_counter()-start
                records.append(record)
                out.write(json.dumps(record)+'\n');out.flush()
                if (index+1)%25==0:
                    print(model.stem,index+1,'/',len(rows),flush=True)
        summary=metrics(records)
        summary.update(backend=runtime['backend'],n_ctx=8192,n_batch=8192,n_ubatch=8192,
                       calibration_temperature=backend.temperature)
        forward=sorted(r['forward_seconds'] for r in records if 'forward_seconds' in r)
        if forward:
            summary['forward_p50_seconds']=forward[len(forward)//2]
            summary['forward_p95_seconds']=forward[min(len(forward)-1,int(len(forward)*.95))]
        (dest/'metrics.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
        print(model.stem,json.dumps(summary),flush=True)
        if summary['errors'] or summary['valid'] != len(rows):
            raise RuntimeError('Incomplete benchmark; inspect predictions.jsonl')
    finally:
        if backend is not None:
            backend.close()
        after=fingerprint(model)
        (dest/'model-after.json').write_text(json.dumps(after,indent=2),encoding='utf-8')
        if after != before:
            raise RuntimeError('Model fingerprint changed during evaluation')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--model',type=pathlib.Path,required=True)
    parser.add_argument('--limit',type=int)
    args=parser.parse_args()
    rows=dataset()
    if args.limit is not None:
        rows=rows[:args.limit]
    evaluate(args.model,rows)
