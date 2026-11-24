#!/usr/bin/env python3
"""Disposable local-kind pilot. No AAP, OpenShift, production or repo writes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
from urllib.parse import urlsplit

CLUSTER = 'summit-drift'
CTX = 'kind-summit-drift'
GUARDS = {
    'whole-version': ('uid', 'rv'),
    'replica-only': ('uid', 'replicas'),
    'generation': ('uid', 'replicas', 'generation'),
    'policy-aware': ('uid', 'replicas', 'generation', 'tier'),
}
PATHS = dict(uid='/metadata/uid', rv='/metadata/resourceVersion',
             replicas='/spec/replicas', generation='/metadata/generation',
             tier='/metadata/labels/approval-tier')
CASES = ('fresh', 'unrelated-label', 'replica-drift', 'replica-aba',
         'template-drift', 'policy-label', 'recreated')
# Lab policy: same UID, unchanged spec history (generation), unchanged approval-tier;
# the synthetic telemetry label is explicitly excluded. This is NOT a general policy.
ELIGIBLE = {'fresh', 'unrelated-label'}
EXPECTED = {
    'fresh': set(GUARDS),
    'unrelated-label': {'replica-only','generation','policy-aware'},
    'replica-drift': set(),
    'replica-aba': {'replica-only'},
    'template-drift': {'replica-only'},
    'policy-label': {'replica-only','generation'},
    'recreated': set(),
}

def require(ok, msg):
    if not ok:
        raise RuntimeError(msg)

def run(args, data=None, env=None, check=True, timeout=120):
    p = subprocess.run([str(a) for a in args], input=data, text=True,
                       capture_output=True, env=env, timeout=timeout)
    if check and p.returncode:
        raise RuntimeError(f'{args[0]} failed: {p.stderr[-1200:]}')
    return p

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--confirm', required=True)
    ap.add_argument('--collections', type=Path, default=Path.home()/'Downloads/summit-scale-ab-private/collections')
    ap.add_argument('--ansible-playbook', default='ansible-playbook')
    a = ap.parse_args()
    require(a.confirm == 'RUN LOCAL GUARD PILOT', 'missing run confirmation')
    for cmd in ('kind', 'kubectl', a.ansible_playbook):
        require(shutil.which(cmd), f'missing executable: {cmd}; use existing A/B environment')
    root = a.collections.expanduser().resolve()
    core = root/'ansible_collections/kubernetes/core'
    manifest = json.loads((core/'MANIFEST.json').read_text())
    require(manifest['collection_info']['version'] == '6.4.0', 'requires pinned collection 6.4.0')
    mod = core/'plugins/modules/k8s_json_patch.py'
    require(mod.is_file(), 'JSON Patch module absent')
    require(CLUSTER in run(['kind','get','clusters']).stdout.splitlines(), 'existing local kind cluster not found')
    rid = secrets.token_hex(5)
    ns = 'summit-guard-'+rid
    out = Path.home()/'Downloads'/'summit-guard-pilot-private'/rid
    out.mkdir(parents=True, mode=0o700)
    print('PRIVATE_OUTPUT_DIR='+str(out), flush=True)
    cfg = out/'private-kubeconfig'
    # Obtain config from kind itself rather than the current kubectl context.
    cfg.write_text(run(['kind','get','kubeconfig','--name',CLUSTER]).stdout)
    cfg.chmod(0o600)
    base = ['kubectl','--kubeconfig',str(cfg),'--context',CTX,'--request-timeout=30s']
    def kube(*args, obj=None, check=True):
        return run(base+list(args), data=json.dumps(obj) if obj is not None else None, check=check)
    def kjson(*args, obj=None):
        return json.loads(kube(*args,obj=obj).stdout)
    owned_uid = None
    rows = []
    cleanup = 'NOT_CREATED'
    env = os.environ.copy()
    for k in list(env):
        if k.startswith('ANSIBLE_') or k.startswith('K8S_AUTH_') or k in ('KUBECONFIG','PYTHONPATH'):
            env.pop(k, None)
    ans_cfg = out/'ansible.cfg'
    ans_cfg.write_text('[defaults]\nstdout_callback=default\nretry_files_enabled=False\ncollections_scan_sys_path=False\n')
    env.update(ANSIBLE_CONFIG=str(ans_cfg), ANSIBLE_COLLECTIONS_PATH=str(root),
               ANSIBLE_COLLECTIONS_PATHS=str(root), ANSIBLE_NOCOLOR='1', KUBECONFIG=str(cfg))
    def save(name, obj):
        (out/name).write_text(json.dumps(obj,indent=2,sort_keys=True)+'\n')
    def read(name):
        return kjson('-n',ns,'get','deployment',name,'-o','json')
    def state(d):
        m=d['metadata']
        require(m['namespace']==ns and m['labels']['pilot-run']==rid, 'resource ownership mismatch')
        return dict(uid=m['uid'],rv=m['resourceVersion'],generation=m['generation'],
                    replicas=d['spec']['replicas'],tier=m['labels']['approval-tier'],
                    telemetry=m['labels']['telemetry-marker'],spec=d['spec'])
    def stable(name):
        last=state(read(name))
        for _ in range(15):
            time.sleep(.35)
            now=state(read(name))
            if now['rv']==last['rv']:
                return now
            last=now
        raise RuntimeError('resourceVersion did not stabilize; no silent retry')
    def create(name):
        obj={'apiVersion':'apps/v1','kind':'Deployment','metadata':{'name':name,'namespace':ns,
             'labels':{'pilot-run':rid,'approval-tier':'standard','telemetry-marker':'before'}},
             'spec':{'replicas':0,'paused':True,'selector':{'matchLabels':{'app':name}},
             'template':{'metadata':{'labels':{'app':name}},'spec':{'containers':[
             {'name':'pause','image':'registry.k8s.io/pause:3.10','imagePullPolicy':'Never'}]}}}}
        kjson('-n',ns,'create','-f','-','-o','json',obj=obj)
        return stable(name)
    def merge(name, patch):
        return kjson('-n',ns,'patch','deployment',name,'--type=merge','-p',json.dumps(patch),'-o','json')
    def test_ops(guard, captured):
        return [{'op':'test','path':PATHS[key],'value':captured[key]} for key in GUARDS[guard]]
    def invoke(name, guard, cap, key, invalid=False):
        ops=test_ops(guard,cap)+[{'op':'replace','path':'/spec/replicas','value':-1 if invalid else 1}]
        dest=out/(key+'-module.json')
        play=[{'name':'Local lab only','hosts':'localhost','connection':'local','gather_facts':False,
               'vars':{'ansible_python_interpreter':'{{ ansible_playbook_python }}'},
               'tasks':[{'name':'Attempt guarded patch','kubernetes.core.k8s_json_patch':{
                    'kubeconfig':str(cfg),'context':CTX,'api_version':'apps/v1','kind':'Deployment',
                    'namespace':ns,'name':name,'patch':ops,'wait':False},
                    'register':'probe','ignore_errors':True,'no_log':True},
                    {'name':'Retain private structured module outcome','ansible.builtin.copy':{
                       'content':'{{ probe | to_json }}','dest':str(dest),'mode':'0600'},'no_log':True}]}]
        play_path=out/(key+'-play.json')
        save(play_path.name,play)
        p=run([a.ansible_playbook,'-i','localhost,',play_path],env=env,check=False)
        (out/(key+'-ansible.log')).write_text(p.stdout+'\n'+p.stderr)
        require(p.returncode==0 and dest.is_file(), 'Ansible harness failure; inspect private logs')
        r=json.loads(dest.read_text())
        return ops,r
    try:
        conf=kjson('config','view','--minify','-o','json')
        server=urlsplit(conf['clusters'][0]['cluster']['server'])
        require(server.scheme=='https' and server.hostname in ('127.0.0.1','localhost','::1'), 'nonlocal endpoint')
        nodes=kjson('get','nodes','-o','json')['items']
        require(nodes and all(n['metadata']['name'].startswith(CLUSTER+'-') for n in nodes), 'kind node mismatch')
        ver=kjson('get','--raw=/version')['gitVersion']
        require(ver.startswith('v1.'), 'unexpected server version')
        av=run([a.ansible_playbook,'--version'],env=env).stdout
        docbin=Path(shutil.which(a.ansible_playbook)).parent/'ansible-doc'
        info=json.loads(run([docbin,'-t','module','-j','kubernetes.core.k8s_json_patch'],env=env).stdout)
        resolved=Path(info['kubernetes.core.k8s_json_patch']['doc']['filename']).resolve()
        require(resolved==mod.resolve(), 'Ansible module resolved outside pinned collection')
        print('PINNED_MODULE_RESOLUTION=PASS',flush=True)
        save('provenance.json',{'kubernetes':ver,'ansible':av,'collection':'6.4.0',
             'module_sha256':sha(mod),'script_sha256':sha(Path(__file__)),'namespace':ns})
        print('LOCAL_KIND_SCOPE=PASS\nKUBERNETES_VERSION='+ver,flush=True)
        doc={'apiVersion':'v1','kind':'Namespace','metadata':{'name':ns,'labels':{'pilot-run':rid}}}
        n=kjson('create','-f','-','-o','json',obj=doc)
        owned_uid=n['metadata']['uid']
        for ci,case in enumerate(CASES):
            for gi,guard in enumerate(GUARDS):
                key=f'{ci}-{gi}-{case}-{guard}'
                name=f'guard-{ci}-{gi}'
                cap=create(name)
                require(cap['replicas']==0 and cap['tier']=='standard','bad capture')
                if case=='unrelated-label':
                    merge(name,{'metadata':{'labels':{'telemetry-marker':'after'}}})
                elif case=='replica-drift':
                    merge(name,{'spec':{'replicas':2}})
                elif case=='replica-aba':
                    middle=state(merge(name,{'spec':{'replicas':2}}))
                    require(middle['replicas']==2 and middle['generation']>cap['generation'],'ABA intermediate not established')
                    save(key+'-intermediate.json',middle)
                    merge(name,{'spec':{'replicas':0}})
                elif case=='template-drift':
                    merge(name,{'spec':{'template':{'metadata':{'annotations':{'pilot-change':'new'}}}}})
                elif case=='policy-label':
                    merge(name,{'metadata':{'labels':{'approval-tier':'restricted'}}})
                elif case=='recreated':
                    kube('-n',ns,'delete','deployment',name,'--wait=true','--timeout=45s')
                    create(name)
                pre=state(read(name)) if case=='fresh' else stable(name)
                # Confirm the intended contrast, not merely that some write occurred.
                if case=='fresh':
                    require(all(pre[k]==cap[k] for k in PATHS),'fresh trial drifted; stop')
                elif case in ('unrelated-label','policy-label'):
                    require(pre['uid']==cap['uid'] and pre['spec']==cap['spec'] and pre['generation']==cap['generation'] and pre['rv']!=cap['rv'],'label isolation failed')
                    require((pre['telemetry']=='after' and pre['tier']=='standard') if case=='unrelated-label' else (pre['tier']=='restricted' and pre['telemetry']=='before'),'wrong label drift')
                elif case=='replica-drift':
                    require(pre['uid']==cap['uid'] and pre['replicas']==2 and pre['generation']>cap['generation'],'replica drift failed')
                elif case=='replica-aba':
                    require(pre['uid']==cap['uid'] and pre['spec']==cap['spec'] and pre['generation']>cap['generation'],'ABA contrast failed')
                elif case=='template-drift':
                    require(pre['uid']==cap['uid'] and pre['replicas']==0 and pre['generation']>cap['generation'] and pre['spec']!=cap['spec'],'template drift failed')
                else:
                    require(pre['uid']!=cap['uid'] and pre['replicas']==0,'recreation not established')
                # Pre-state is only for evaluation; patch uses captured values exclusively.
                save(key+'-before.json',{'captured':cap,'pre_attempt':pre})
                ops,r=invoke(name,guard,cap,key)
                after=state(read(name))
                expected_spec=json.loads(json.dumps(pre['spec'])); expected_spec['replicas']=1
                if not r.get('failed',False):
                    ok=(after['uid']==pre['uid'] and after['spec']==expected_spec and after['tier']==pre['tier'] and after['telemetry']==pre['telemetry'] and after['generation']>pre['generation'])
                    actual='APPLIED' if ok else 'UNRESOLVED_SUCCESS'
                else:
                    msg=str(r.get('msg','')).lower()
                    unchanged=all(after[k]==pre[k] for k in ('uid','replicas','generation','tier','telemetry','spec'))
                    actual='TEST_REJECTED' if str(r.get('status'))=='422' and ('test failed' in msg or 'testing value' in msg or 'test operation' in msg) and unchanged else 'UNRESOLVED_FAILURE'
                expect='APPLIED' if guard in EXPECTED[case] else 'TEST_REJECTED'
                policy=('ALLOWED' if case in ELIGIBLE else 'REAPPROVAL_REQUIRED')
                outcome=('FALSE_ACCEPT' if actual=='APPLIED' and case not in ELIGIBLE else 'FALSE_REJECT' if actual=='TEST_REJECTED' and case in ELIGIBLE else 'CORRECT' if actual in ('APPLIED','TEST_REJECTED') else 'UNKNOWN')
                row=dict(case=case,guard=guard,policy=policy,expected=expect,actual=actual,classification=outcome,
                         status=r.get('status'),reason=r.get('reason'),changed=r.get('changed'),before_replicas=pre['replicas'],after_replicas=after['replicas'],prediction_match=actual==expect)
                rows.append(row)
                save(key+'-after.json',{'patch':ops,'readback':after,'summary':row})
                save('results.json',rows)
                print(json.dumps(row,sort_keys=True),flush=True)
        # Different reason for a 422: a syntactically valid but invalid replicas value.
        name='invalid-control'; cap=create(name)
        ops,r=invoke(name,'policy-aware',cap,'invalid-control',invalid=True)
        post=state(read(name))
        msg=str(r.get('msg','')).lower()
        negative_ok=(r.get('failed') is True and str(r.get('status'))=='422' and not any(s in msg for s in ('test failed','testing value','test operation')) and all(post[k]==cap[k] for k in ('uid','replicas','generation','tier','spec')))
        save('invalid-control-after.json',{'patch':ops,'readback':post,'non_test_422':negative_ok})
        print('NON_TEST_422_CONTROL='+('PASS' if negative_ok else 'UNRESOLVED'),flush=True)
        print('PREDICTIONS_MATCHED='+str(sum(r['prediction_match'] for r in rows))+'/'+str(len(rows)),flush=True)
        for guard in GUARDS:
            rs=[r for r in rows if r['guard']==guard]
            print(guard, {k:sum(r['classification']==k for r in rs) for k in ('CORRECT','FALSE_ACCEPT','FALSE_REJECT','UNKNOWN')},flush=True)
        print('PILOT_COMPLETE='+('YES' if len(rows)==28 else 'NO'),flush=True)
    finally:
        # Delete only the namespace this process created, with UID and label rechecked.
        try:
            if owned_uid:
                n=kjson('get','namespace',ns,'-o','json')
                if n['metadata']['uid']==owned_uid and n['metadata']['labels'].get('pilot-run')==rid:
                    p=kube('delete','namespace',ns,'--wait=false',check=False)
                    cleanup='REQUESTED' if p.returncode==0 else 'FAILED'
                else:
                    cleanup='OWNERSHIP_MISMATCH_NOT_DELETED'
        except Exception:
            cleanup='UNCONFIRMED'
        # The kind credential file is never part of the receipt.
        cfg.unlink(missing_ok=True)
        print('NAMESPACE_CLEANUP='+cleanup,flush=True)
        print('PRIVATE_OUTPUT_DIR='+str(out),flush=True)
        print('AAP_MUTATIONS=NONE REPOSITORY_MUTATIONS=NONE',flush=True)
        print('PUBLICATION_REVIEW=REQUIRED',flush=True)

if __name__=='__main__':
    try:
        main()
    except Exception as e:
        print('STOP='+str(e),file=sys.stderr)
        sys.exit(2)
