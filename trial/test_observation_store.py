"""Private observation storage tests; inert fixture text only."""
import concurrent.futures
import json
import os
from pathlib import Path
import tempfile
import unittest

from observation import Store, new_capture, canonical_hash


class StoreTests(unittest.TestCase):
    def test_secure_append_and_exact_full_precision_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp)/'private')
            payload={'state':{'task':'inert private fixture'},'questions':{'block':{'instructions':'fixed'}}}
            capture=new_capture(payload,.123456789123456,.88,{'name':'fixture'})
            store.write_text('.gitignore','*\n')
            self.assertEqual((store.path/'.gitignore').stat().st_mode&0o777,0o600)
            store.append('calls.jsonl',capture)
            loaded=store.read('calls.jsonl')[0]
            self.assertEqual(loaded['payload'],payload)
            self.assertEqual(loaded['score'],.123456789123456)
            self.assertEqual(loaded['input_sha256'],canonical_hash(payload))
            self.assertEqual(loaded['policy_sha256'],canonical_hash(payload['questions']))
            self.assertFalse(loaded['would_block'])
            self.assertEqual(store.path.stat().st_mode&0o777,0o700)
            self.assertEqual((store.path/'calls.jsonl').stat().st_mode&0o777,0o600)
            with self.assertRaises(ValueError): store.read('../outside')
            (store.path/'link.jsonl').symlink_to(Path(temp)/'outside')
            with self.assertRaises(OSError):store.append('link.jsonl',capture)

    def test_concurrent_appends_preserve_each_json_record(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp)/'private')
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda i:store.append('calls.jsonl',{'i':i}),range(40)))
            self.assertEqual(sorted(r['i'] for r in store.read('calls.jsonl')),list(range(40)))

    def test_failures_are_not_allow_and_invalid_probabilities_rejected(self):
        event=new_capture(None,None,.88,{},error='ValueError')
        self.assertIsNone(event['score'])
        self.assertIsNone(event['would_block'])
        self.assertEqual(event['error'],'ValueError')
        for value in [True,float('nan'),float('inf'),-.1,1.1,'0.5']:
            with self.subTest(value=value),self.assertRaises(ValueError):
                new_capture({},value,.88,{})


if __name__=='__main__':unittest.main()
