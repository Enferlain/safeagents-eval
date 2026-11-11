import unittest
from analyze import analyze
from run import payload
class MetricsTests(unittest.TestCase):
    def rows(self,ps,ys):return [dict(row_idx=i,p_block=p,label=y,category='test',seconds=.01) for i,(p,y) in enumerate(zip(ps,ys))]
    def test_perfect(self):
        m,_=analyze(self.rows([0,1],['allow','block']))
        for k in ('auroc','average_precision','balanced_accuracy_at_0.5'):self.assertEqual(m[k],1)
        for k in ('brier','ece_10_equal_width_bins','false_block_rate_at_0.5'):self.assertEqual(m[k],0)
    def test_ties(self):
        m,_=analyze(self.rows([.5,.5],['allow','block']))
        self.assertEqual(m['auroc'],.5);self.assertEqual(m['average_precision'],.5);self.assertEqual(m['brier'],.25)
    def test_reversed(self):
        m,_=analyze(self.rows([1,0],['allow','block']));self.assertEqual(m['auroc'],0);self.assertEqual(m['balanced_accuracy_at_0.5'],0)
    def test_errors_not_hidden(self):
        rows=self.rows([.2,.9],['allow','block'])+[{'error':'timeout'}];m,_=analyze(rows);self.assertEqual(m['errors'],1);self.assertEqual(m['completed'],3);self.assertEqual(m['valid'],2)
    def test_labels_not_in_request(self):
        row={'task_text':'task','tool_name':'Bash','tool_args':'text only','findings':[],'label':'block','category':'eval_gaming','panel_rationales':'secret gold label'}
        p=payload(row);self.assertEqual(set(p['state']),{'task','tool','arguments','scanner_findings'});self.assertNotIn('label',str(p));self.assertNotIn('panel_rationales',str(p))
if __name__=='__main__':unittest.main()
