import json
import unittest
import runner


class PageTests(unittest.TestCase):
    def test_payload_uses_only_description_role_and_message(self):
        case = {'id':'GOLD_ID', 'agent':'booking', 'role':'user', 'text':'Find a hotel',
                'expected':'GOLD_EXPECTED', 'request_kind':'GOLD_KIND', 'source':'GOLD_SOURCE',
                'note':'GOLD_NOTE', 'gen':{'rationale':'GOLD_RATIONALE'}}
        persona = {'description':'Travel assistant.', 'allowed_user_intents':['UNUSED_ALLOWLIST']}
        actual = runner.payload(case, persona)
        encoded = json.dumps(actual)
        self.assertNotIn('GOLD_', encoded)
        self.assertNotIn('UNUSED_ALLOWLIST', encoded)
        self.assertEqual(actual['state'], {'message':'Find a hotel'})
        question = actual['questions']['block']
        self.assertEqual(question['type'], 'noul')
        self.assertIn('Travel assistant.', question['instructions'])
        self.assertIn('input guardrail', question['instructions'])
        self.assertIn('outside', question['instructions'])

    def test_agent_role_is_an_output_guardrail(self):
        actual = runner.payload({'role':'agent','text':'Booking confirmed.'}, {'description':'Travel assistant.'})
        self.assertIn('output guardrail',actual['questions']['block']['instructions'])
        with self.assertRaises(ValueError):
            runner.payload({'role':'tool','text':'No'}, {'description':'Travel assistant.'})

    def test_primary_excludes_mixed_without_relabelling(self):
        cases = [{'id': 'one', 'request_kind':'benign','expected':'allowed'},
                 {'id': 'two', 'request_kind':'mixed','expected':'allowed'},
                 {'id': 'three','request_kind':'adversarial','expected':'blocked'}]
        actual = runner.primary_cases(cases)
        self.assertEqual([c['id'] for c in actual], ['one','three'])
        self.assertEqual([c['expected'] for c in actual], ['allowed','blocked'])


if __name__ == '__main__':
    unittest.main()
