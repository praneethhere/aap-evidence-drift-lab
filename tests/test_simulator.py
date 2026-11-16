import unittest
from evidencedrift.simulator import Evidence, SCENARIOS, STATEGIES, World, matrix, run_once, run_trial, summary

BY_NAME = {s.name: s for s in SCENARIOS}

class EvidenceDriftTests(unittest.TestCase):
    def test_matrix_complete(self):
        self.assertEqual(len(matrix()), len(SCENARIOS) * len(STATEGIES))

    def test_stable_all_succeed(self):
        for method in STATEGIES:
            with self.subTest(method=method):
                result = run_trial(method, BY_NAME['stable'])
                self.assertEqual(result.mutations, 1)
                self.assertEqual(result.stale_mutations, 0)
                self.assertEqual(result.false_successes, 0)
                self.assertEqual(result.final_status, 'healthy')

    def test_after_approval_baseline_stale_but_policies_block(self):
        self.assertEqual(run_trial('approval_only', BY_NAME['change_during_approval']).stale_mutations, 1)
        for method in STATEGIES[1:]:
            self.assertEqual(run_trial(method, BY_NAME['change_during_approval']).mutations, 0)

    def test_policy_time_gap(self):
        scenario = BY_NAME['change_after_job_policy']
        self.assertEqual(run_trial('job_time_policy', scenario).stale_mutations, 1)
        self.assertEqual(run_trial('preaction_check', scenario).mutations, 0)
        self.assertEqual(run_trial('bound_and_verified', scenario).mutations, 0)

    def test_non_atomic_preaction_check_gap(self):
        scenario = BY_NAME['change_after_preaction_check']
        self.assertEqual(run_trial('preaction_check', scenario).stale_mutations, 1)
        self.assertEqual(run_trial('bound_and_verified', scenario).mutations, 0)

    def test_postaction_regression_detected_by_verifier(self):
        scenario = BY_NAME['postaction_regression']
        for method in STATEGIES[:3]:
            self.assertEqual(run_trial(method, scenario).false_successes, 1)
        bound = run_trial('bound_and_verified', scenario)
        self.assertEqual(bound.false_successes, 0)
        self.assertIn('postcondition_failed', bound.outcome)
        self.assertEqual(bound.final_status, 'degraded')

    def test_same_state_but_new_version_blocks(self):
        scenario = BY_NAME['same_status_new_version']
        self.assertEqual(run_trial('approval_only', scenario).stale_mutations, 1)
        for method in STATEGIES[1:]:
            self.assertEqual(run_trial(method, scenario).mutations, 0)

    def test_duplicate_event_exactly_once_bound(self):
        scenario = BY_NAME['duplicate_event']
        self.assertEqual(run_trial('approval_only', scenario).mutations, 2)
        bound = run_trial('bound_and_verified', scenario)
        self.assertEqual(bound.mutations, 1)
        self.assertIn('deduplicated', bound.outcome)

    def test_digest_stable(self):
        e = Evidence('evt', 'service:demo', 'degraded', 1, 'healthy')
        self.assertEqual(e.digest, Evidence('evt', 'service:demo', 'degraded', 1, 'healthy').digest)
        self.assertNotEqual(e.digest, Evidence('evt', 'service:demo', 'degraded', 2, 'healthy').digest)

    def test_wrong_target_never_passes_bound(self):
        world = World(target='production:db')
        e = Evidence('evt', 'production:web', 'degraded', 1, 'healthy')
        x = run_once('bound_and_verified', world, e, BY_NAME['stable'])
        self.assertFalse(x.mutated)
        self.assertEqual(x.disposition, 'blocked_at_mutation')

    def test_invalid_strategy_rejected(self):
        world = World()
        e = Evidence('evt', 'service:demo', 'degraded', 1, 'healthy')
        with self.assertRaises(ValueError):
            run_once('bad', world, e, BY_NAME['stable'])

    def test_outcome_counts_are_nonnegative(self):
        for r in summary():
            for field in ['mutations','stale_mutations','false_successes','postcondition_failures_detected','duplicate_mutations']:
                self.assertGreaterEqual(r[field], 0)

if __name__ == '__main__':
    unittest.main()
