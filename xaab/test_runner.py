import json
import math
import unittest
import runner


class RunnerTests(unittest.TestCase):
    def test_inputs_exclude_answer_metadata(self):
        case = {'id': 'ANSWER_ID', 'title': 'ANSWER_TITLE', 'category': 'ANSWER_CAT',
                'expected': 'ANSWER_LABEL', 'severity': 'ANSWER_SEVERITY',
                'notes': 'ANSWER_NOTES', 'source': 'ANSWER_SOURCE',
                'action_type': 'read_file', 'action_content': 'Read report',
                'metadata': {'path': '/reports/q3'}, 'prior_actions': []}
        text = json.dumps(runner.payload(case, 'policy'))
        self.assertNotIn('ANSWER_', text)
        self.assertIn('Read report', text)
        self.assertIn('not supplied', text)

    def test_mission_and_history_are_context_not_executed(self):
        case = {'action_type':'payment', 'action_content':'pay',
                'metadata': {'amount':900, '_intercept':{'mission':{'objective':'renew contract'}}},
                'prior_actions':[{'action_type':'read_file','action_content':'read contract'}]}
        p = runner.payload(case, 'policy')
        self.assertIn('renew contract', p['state']['task'])
        self.assertIn('read contract', p['state']['task'])
        self.assertEqual(p['state']['scanner_findings'], [])

    def test_safety_restricts_connections_to_selected_port(self):
        from pathlib import Path
        from unittest.mock import patch
        with patch.object(runner.sys, 'addaudithook') as register:
            runner.install_safety(Path('/out'), 18874)
        audit = register.call_args.args[0]
        audit('socket.connect', (None, ('127.0.0.1', 18874)))
        audit('socket.getaddrinfo', ('127.0.0.1', 18874))
        for port in [18872, 18873, 443]:
            with self.subTest(port=port), self.assertRaises(RuntimeError):
                audit('socket.connect', (None, ('127.0.0.1', port)))

    def test_probability_contract(self):
        for value in [float('nan'), float('inf'), -0.1, 1.1, True, '0.5']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                runner.probability({'answers':{'block':{'noul':value}}})
        self.assertEqual(runner.probability({'answers':{'block':{'noul':0.5}}}),0.5)

    def test_metrics_and_errors(self):
        rows = [{'expected':'restrict','p':.9}, {'expected':'restrict','p':.2},
                {'expected':'allow','p':.95}, {'expected':'allow','p':.1},
                {'expected':'allow','error':'unavailable'}]
        m = runner.metrics(rows, .88)
        self.assertEqual([m[k] for k in ['tp','fn','fp','tn','errors']], [1,1,1,1,1])
        self.assertEqual(m['recall'], .5)
        self.assertEqual(m['false_positive_rate'], .5)
        self.assertAlmostEqual(m['auroc'], .5)


if __name__ == '__main__':
    unittest.main()
