import hashlib
import tempfile
from pathlib import Path
import subprocess
from unittest import TestCase
from unittest.mock import patch
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import routing_ab as m

class Tests(TestCase):
    def test_source_hash_is_what_user_verified(self):
        self.assertEqual(m.SCALE_SOURCE_SHA256,'001e5658019f941defeedb7b876205f98605100d84e2cb8b0f61992a13332502')
    def test_namespace_isolated(self):
        self.assertEqual(m.owner_namespace('0123456789'),'summit-scale-ab-0123456789')
        for x in ('production','../bad','123','0123456789/..'):
            with self.assertRaises(m.Stop):m.owner_namespace(x)
    def test_module_comparison_uses_distinct_preconditions(self):
        a=m.module_args('ns','dep','42','scale')
        b=m.module_args('ns','dep','42','patch')
        self.assertEqual(a['resource_version'],'42')
        self.assertNotIn('resourceVersion',a)
        self.assertEqual(b['definition']['metadata']['resourceVersion'],'42')
        self.assertEqual(b['merge_type'],['merge'])
    def test_playbooks_failclosed(self):
        p=m.make_play('ns','dep','42','patch',True)
        s=str(p)
        self.assertIn('NATIVE_PATCH_409_CLASSIFIED=YES',s)
        self.assertIn('EXPECTED_FAILURE_BRANCH_ON_NATIVE_CONFLICT',s)
        self.assertIn('no_log',s)
        self.assertNotIn('AAP_TOKEN',s)
    def test_expected_result_requires_real_status_marker(self):
        p=lambda code,output:subprocess.CompletedProcess([],code,stdout=output,stderr='')
        self.assertTrue(m.parse_case('scale-stale',p(0,'SCALE_STALE_TASK_SUCCESS=YES')))
        self.assertFalse(m.parse_case('scale-stale',p(2,'SCALE_STALE_TASK_SUCCESS=YES')))
        self.assertFalse(m.parse_case('patch-stale',p(2,'EXPECTED_FAILURE_BRANCH_ON_NATIVE_CONFLICT')))
        self.assertTrue(m.parse_case('patch-stale',p(2,'NATIVE_PATCH_409_CLASSIFIED=YES EXPECTED_FAILURE_BRANCH_ON_NATIVE_CONFLICT')))
        self.assertFalse(m.parse_case('patch-stale',p(0,'NATIVE_PATCH_409_CLASSIFIED=YES EXPECTED_FAILURE_BRANCH_ON_NATIVE_CONFLICT')))
    def test_local_preflight_rejects_wrong_kind(self):
        with patch.object(m,'good',return_value='some-other-cluster\n'):
            with self.assertRaises(m.Stop):m.local_preflight()
    def test_namespace_owner_guard(self):
        doc={'metadata':{'name':'summit-scale-ab-0123456789','uid':'abc','labels':
            {m.MARKER_LABEL:'0123456789','app.kubernetes.io/managed-by':'summit-scale-ab'}}}
        with patch.object(m,'kube',return_value=__import__('json').dumps(doc)):
            m.ensure_owner(doc['metadata']['name'],'0123456789','abc')
            with self.assertRaises(m.Stop):m.ensure_owner(doc['metadata']['name'],'wrong','abc')
    def test_isolated_collection_source_strict(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            d=p/'ansible_collections/kubernetes/core/plugins/modules'
            d.mkdir(parents=True)
            (d/'k8s_scale.py').write_text('not genuine')
            info=p/'ansible_collections/kubernetes/core/MANIFEST.json'
            info.write_text('{"collection_info":{"version":"6.4.0"}}')
            with self.assertRaises(m.Stop):m.manifest_version(p)
    def test_no_inherited_k8s_auth(self):
        with patch.dict(m.os.environ,{'K8S_AUTH_HOST':'https://real-cluster','ANSIBLE_CONFIG':'/tmp/custom'},clear=False):
            d=m.an_env(Path('/tmp/isolated'))
            self.assertNotIn('K8S_AUTH_HOST',d)
            self.assertNotIn('ANSIBLE_CONFIG',d)
            self.assertEqual(d['ANSIBLE_COLLECTIONS_PATH'],'/tmp/isolated')
