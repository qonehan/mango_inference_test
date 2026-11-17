import unittest
from analysis_metrics import (exact_binomial_interval, success_difference,
    cost_ratio, decode_rate, stratified_interval, token_share)


class MetricsTests(unittest.TestCase):
    def test_no_failures_does_not_prove_equivalence(self):
        point,lo,hi=success_difference([[(1,1)]*60,[(1,1)]*60,[(1,1)]*32])
        self.assertEqual(point,0)
        self.assertLess(lo,-0.02)
        self.assertGreater(hi,0.02)

    def test_exact_boundaries(self):
        lo,hi=exact_binomial_interval(10,10,0.05)
        self.assertAlmostEqual(lo,0.025**0.1,places=10)
        self.assertEqual(hi,1)
        lo,hi=exact_binomial_interval(0,10,0.05)
        self.assertEqual(lo,0)
        self.assertAlmostEqual(hi,1-0.025**0.1,places=10)
        lo,hi=exact_binomial_interval(5,10,0.05)
        self.assertAlmostEqual(lo,0.1870860284,places=8)
        self.assertAlmostEqual(hi,0.8129139716,places=8)

    def test_failures_have_real_cost(self):
        pairs=[((1,True),(1,True)),((1,False),(1,True))]
        self.assertEqual(cost_ratio(pairs),2)
        self.assertIsNone(cost_ratio([((1,False),(1,True))]))
        self.assertIsNone(cost_ratio([((None,False),(1,True))]))

    def test_hidden_reasoning_is_not_inflated_decode(self):
        self.assertIsNone(decode_rate(1100,1000,False,[5,6]))
        self.assertEqual(decode_rate(1100,1000,True,[0,5,6]),1099/6)
        self.assertIsNone(decode_rate(1,0,False,[1]))
        self.assertIsNone(decode_rate(10,None,False,[1,2]))

    def test_tokens_resample_requests_not_individual_tokens(self):
        rows=[('A',1),('B',99)]
        self.assertEqual(token_share(rows,'A'),0.01)
        ci=stratified_interval([rows],lambda r:token_share(r,'A'),repeats=1000)
        self.assertEqual(ci,(0,1))
        self.assertEqual(ci,stratified_interval([rows],lambda r:token_share(r,'A'),repeats=1000))

    def test_undefined_bootstrap_is_not_dropped(self):
        self.assertIsNone(stratified_interval([[((1,False),(1,True))]],cost_ratio,repeats=10))


if __name__ == '__main__':
    unittest.main()
