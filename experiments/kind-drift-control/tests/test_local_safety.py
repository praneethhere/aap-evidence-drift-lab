import json
import unittest
from pathlib import Path
from unittest import mock
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kind_drift_control as d

class Tests(unittest.TestCase):
    def test_manifest_contains_zero_replicas_and_ownership_label(self):
        m = d.deployment('guarded-no-drift', 'summit-drift-public-20261122000000-123ab')
        self.assertEqual(m['spec']['replicas'], 0)
        self.assertEqual(m['metadata']['labels'][d.LABEL_KEY], d.LABEL_VALUE)

    def test_observations_exclude_unneeded_object_content(self):
        self.assertEqual(d.observations({'metadata': {'uid': 'u', 'resourceVersion': '20', 'generation': 3, 'secret': 'S'}, 'spec': {'replicas': 2, 'password': 'S'}}),
                         {'uid': 'u','resourceVersion': '20','generation': 3, 'desired_replicas':2})

    def test_cleanup_refuses_bad_namespace_before_api_call(self):
        with mock.patch.object(d, 'preflight'):
            with self.assertRaisesRegex(RuntimeError, 'naming pattern'):
                d.cleanup('production')

    def test_stage_drift_is_unconditional_but_checks_identity_and_version(self):
        before = {'metadata': {'name': 'x', 'namespace': 'summit-drift-public-20261122000000-123ab',
                               'labels': {d.LABEL_KEY: d.LABEL_VALUE}, 'uid': 'uid-1', 'resourceVersion': '10'},
                  'spec': {'replicas': 0}}
        after = {'metadata': {**before['metadata'], 'resourceVersion': '12'}, 'spec': {'replicas': 2}}
        with mock.patch.object(d, 'patch', return_value=(200, {})) as patch, \
             mock.patch.object(d, 'get_obj', return_value=after):
            status, observed = d.stage_drift(None, before['metadata']['namespace'], 'x', before)
        self.assertEqual(status, 200)
        self.assertEqual(observed['spec']['replicas'], 2)
        patch.assert_called_once_with(None, before['metadata']['namespace'], 'x', 2)

    def test_stage_drift_reports_http_status_and_reason(self):
        with mock.patch.object(d, 'patch', return_value=(409, {'reason': 'Conflict'})):
            with self.assertRaisesRegex(RuntimeError, 'HTTP 409, reason=Conflict'):
                d.stage_drift(None, 'lab', 'x', {})

    def test_stage_drift_rejects_unchanged_version(self):
        before = {'metadata': {'name': 'x', 'namespace': 'lab',
                               'labels': {d.LABEL_KEY: d.LABEL_VALUE}, 'uid': 'uid-1', 'resourceVersion': '10'},
                  'spec': {'replicas': 0}}
        changed = {'metadata': dict(before['metadata']), 'spec': {'replicas': 2}}
        with mock.patch.object(d, 'patch', return_value=(200, {})), \
             mock.patch.object(d, 'get_obj', return_value=changed):
            with self.assertRaisesRegex(RuntimeError, 'did not advance'):
                d.stage_drift(None, 'lab', 'x', before)

    def test_wait_quiet_rejects_changed_uid(self):
        obj = {'metadata': {'name':'x','namespace':'lab','uid':'changed','resourceVersion':'12',
                            'labels': {d.LABEL_KEY: d.LABEL_VALUE}}, 'spec': {'replicas':2}}
        with mock.patch.object(d, 'get_obj', return_value=obj):
            with self.assertRaisesRegex(RuntimeError, 'UID changed'):
                d.wait_for_quiet_version(None, 'lab', 'x', 'original', quiet_seconds=0)

    def test_wait_quiet_returns_checked_state_and_poll_count(self):
        obj = {'metadata': {'name':'x','namespace':'lab','uid':'original','resourceVersion':'12',
                            'labels': {d.LABEL_KEY: d.LABEL_VALUE}}, 'spec': {'replicas':2}}
        with mock.patch.object(d, 'get_obj', return_value=obj), \
             mock.patch.object(d.time, 'sleep'), \
             mock.patch.object(d.time, 'monotonic', side_effect=[0.0, 0.0, 0.4, 0.8]):
            result, stability = d.wait_for_quiet_version(None, 'lab', 'x', 'original',
                                                         quiet_seconds=0.5, timeout_seconds=2.0)
        self.assertEqual(result['metadata']['resourceVersion'], '12')
        self.assertEqual(stability['polls'], 3)
        self.assertEqual(stability['rv_changes_seen'], 0)

    def test_wait_quiet_refuses_replica_change(self):
        obj = {'metadata': {'name':'x','namespace':'lab','uid':'original','resourceVersion':'15',
                            'labels': {d.LABEL_KEY: d.LABEL_VALUE}}, 'spec': {'replicas':3}}
        with mock.patch.object(d, 'get_obj', return_value=obj):
            with self.assertRaisesRegex(RuntimeError, 'expected 2'):
                d.wait_for_quiet_version(None, 'lab', 'x', 'original', quiet_seconds=0)

    def test_preflight_refuses_wrong_context(self):
        with mock.patch.object(d.shutil, 'which', return_value='/usr/bin/tool'), mock.patch.object(d, 'cmd') as run:
            run.return_value.stdout = 'production\n'
            with self.assertRaisesRegex(RuntimeError, 'Unsafe kubecontext'):
                d.preflight()

    def test_preflight_refuses_remote_api_endpoint(self):
        calls = iter(['kind-summit-drift\n', json.dumps({'clusters': [{'cluster': {'server': 'https://example.org:6443'}}]})])
        with mock.patch.object(d.shutil, 'which', return_value='/usr/bin/tool'), mock.patch.object(d, 'cmd') as run:
            run.side_effect = lambda *args, **kwargs: mock.Mock(stdout=next(calls))
            with self.assertRaisesRegex(RuntimeError, 'loopback'):
                d.preflight()

    def test_preflight_refuses_lookalike_loopback_context(self):
        active = {'clusters': [{'cluster': {'server': 'https://127.0.0.1:1234', 'certificate-authority-data': 'CA-A'}}]}
        kind = 'clusters:\n- cluster:\n    certificate-authority-data: CA-B\n    server: https://127.0.0.1:5555\n'
        calls = iter(['kind-summit-drift\n', json.dumps(active), 'summit-drift\n', kind])
        with mock.patch.object(d.shutil, 'which', return_value='/usr/bin/tool'), mock.patch.object(d, 'cmd') as run:
            run.side_effect = lambda *args, **kwargs: mock.Mock(stdout=next(calls))
            with self.assertRaisesRegex(RuntimeError, 'does not match actual kind cluster'):
                d.preflight()

    def test_preflight_accepts_matching_kind_context(self):
        active = {'clusters': [{'cluster': {'server': 'https://127.0.0.1:5555', 'certificate-authority-data': 'CA-OK'}}]}
        kind = 'clusters:\n- cluster:\n    certificate-authority-data: CA-OK\n    server: https://127.0.0.1:5555\n'
        calls = iter(['kind-summit-drift\n', json.dumps(active), 'summit-drift\n', kind, '{}'])
        with mock.patch.object(d.shutil, 'which', return_value='/usr/bin/tool'), mock.patch.object(d, 'cmd') as run:
            run.side_effect = lambda *args, **kwargs: mock.Mock(stdout=next(calls))
            with mock.patch.object(d, 'kubectl', return_value=mock.Mock(stdout='{}')):
                self.assertEqual(d.preflight()['server_scope'], 'verified-local-kind-loopback')

    def test_preflight_refuses_tls_bypass(self):
        active = {'clusters': [{'cluster': {'server': 'https://127.0.0.1:5555',
                                           'insecure-skip-tls-verify': True}}]}
        calls = iter(['kind-summit-drift\n', json.dumps(active)])
        with mock.patch.object(d.shutil, 'which', return_value='/usr/bin/tool'), mock.patch.object(d, 'cmd') as run:
            run.side_effect = lambda *args, **kwargs: mock.Mock(stdout=next(calls))
            with self.assertRaisesRegex(RuntimeError, 'TLS bypass'):
                d.preflight()

    def test_uid_continuity_refuses_replacement(self):
        first = {'metadata': {'uid': 'old'}}
        second = {'metadata': {'uid': 'new'}}
        with self.assertRaisesRegex(RuntimeError, 'UID changed'):
            d.check_uid_continuity(second, first)

    def test_get_checks_uid_and_resource_version(self):
        obj = {'metadata': {'name': 'x', 'namespace': 'ns',
                            'labels': {d.LABEL_KEY: d.LABEL_VALUE}, 'uid': 'u'},
               'spec': {'replicas': 0}}
        with self.assertRaisesRegex(RuntimeError, 'missing UID or resourceVersion'):
            d.check_deployment(obj, 'ns', 'x', 0)

    def test_cleanup_refuses_wrong_ownership_label(self):
        ns = 'summit-drift-public-20261122000000-123ab'
        obj = {'metadata': {'labels': {d.LABEL_KEY: 'someone-else'}}}
        with mock.patch.object(d, 'preflight'), mock.patch.object(d, 'kubectl',
             return_value=mock.Mock(stdout=json.dumps(obj))) as kubectl:
            with self.assertRaisesRegex(RuntimeError, 'ownership label absent'):
                d.cleanup(ns)
            self.assertEqual(kubectl.call_count, 1)

    def test_incomplete_runtime_writes_incomplete_receipt(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(d, 'preflight',
            return_value={'context': d.CONTEXT, 'server_scope': 'verified-local-kind-loopback', 'kind_cluster': 'summit-drift'}),             mock.patch.object(d, 'kubectl') as kc, mock.patch.object(d.LocalProxy, '__enter__',
            side_effect=RuntimeError('deliberate-test-failure')):
            with self.assertRaisesRegex(RuntimeError, 'deliberate-test-failure'):
                d.execute(Path(tmp))
            files = list(Path(tmp).glob('summit-drift-public-*/local_control_receipts.json'))
            self.assertEqual(len(files), 1)
            receipt = json.loads(files[0].read_text())
            self.assertEqual(receipt['run_status'], 'INCOMPLETE')
            self.assertTrue(receipt['namespace_created'])
            self.assertEqual(receipt['cases'], [])
            payload = json.loads(kc.call_args_list[0].kwargs['stdin'])
            self.assertEqual(payload['kind'], 'Namespace')
            self.assertEqual(payload['metadata']['labels'][d.LABEL_KEY], d.LABEL_VALUE)

    def test_guarded_patch_payload_has_version_and_no_implicit_retry(self):
        p = mock.Mock()
        p.request.return_value = (409, {'reason': 'Conflict'})
        result = d.patch(p, 'ns', 'x', 1, 'rv-20')
        self.assertEqual(result[0], 409)
        p.request.assert_called_once_with('PATCH', '/apis/apps/v1/namespaces/ns/deployments/x',
                                          {'spec': {'replicas': 1}, 'metadata': {'resourceVersion': 'rv-20'}},
                                          'application/merge-patch+json')

if __name__ == '__main__':
    unittest.main()
