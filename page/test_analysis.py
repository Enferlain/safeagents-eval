import unittest
from analysis_metrics import describe


class MetricTests(unittest.TestCase):
    def test_ties_ap_and_integer_fpr_budget(self):
        rows = [{'expected':'restrict','p':.9},{'expected':'restrict','p':.8},
                {'expected':'allow','p':.8},{'expected':'allow','p':.1}]
        result = describe(rows)
        self.assertAlmostEqual(result['auroc'],.875)
        self.assertAlmostEqual(result['ap'],5/6)
        self.assertAlmostEqual(result['balanced_accuracy'],.75)
        self.assertEqual(result['ceilings']['5']['tp'],1)
        self.assertEqual(result['ceilings']['5']['fp'],0)
        self.assertEqual(result['ceilings']['5']['threshold'],.9)
        self.assertEqual(result['ceilings']['10']['tp'],1)


if __name__=='__main__':
    unittest.main()
