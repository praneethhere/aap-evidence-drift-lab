import json
import tempfile
import unittest
from pathlib import Path
from evidencedrift.local_target import apply, init, inject, observe

class LocalTargetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / 'target.json'
        self.evidence = Path(self.tmp.name) / 'evidence.json'
        init(self.state)

    def approve(self, event='event-one'):
        self.evidence.write_text(json.dumps(observe(self.state, event)))

    def test_healthy_change(self):
        self.approve()
        code, result = apply(self.state, self.evidence)
        self.assertEqual(code, 0)
        self.assertEqual(result['disposition'], 'verified_success')
        self.assertEqual(json.loads(self.state.read_text())['status'], 'healthy')

    def test_changed_version_denies_action(self):
        self.approve()
        inject(self.state, 'degraded')
        code, result = apply(self.state, self.evidence)
        self.assertEqual(code, 2)
        self.assertEqual(result['reason'], 'evidence_no_longer_matches')
        self.assertEqual(json.loads(self.state.read_text())['restart_count'], 0)

    def test_tampered_evidence_denied(self):
        self.approve()
        data = json.loads(self.evidence.read_text())
        data['observed_version'] = 555
        self.evidence.write_text(json.dumps(data))
        code, result = apply(self.state, self.evidence)
        self.assertEqual(code, 2)
        self.assertEqual(result['reason'], 'invalid_evidence_digest')

    def test_duplicate_not_restarted(self):
        self.approve()
        self.assertEqual(apply(self.state, self.evidence)[0], 0)
        code, outcome = apply(self.state, self.evidence)
        self.assertEqual(code, 0)
        self.assertEqual(outcome['disposition'], 'deduplicated')
        self.assertEqual(json.loads(self.state.read_text())['restart_count'], 1)

    def test_postcondition_failure_fails_closed(self):
        self.approve()
        code, result = apply(self.state, self.evidence, postaction_failure=True)
        self.assertEqual(code, 3)
        self.assertEqual(result['disposition'], 'postcondition_failed')
        self.assertEqual(json.loads(self.state.read_text())['status'], 'degraded')

    def test_new_evidence_after_external_change(self):
        self.approve()
        inject(self.state, 'degraded')
        self.assertEqual(apply(self.state, self.evidence)[0], 2)
        self.approve('new-event')
        self.assertEqual(apply(self.state, self.evidence)[0], 0)

if __name__ == '__main__':
    unittest.main()
