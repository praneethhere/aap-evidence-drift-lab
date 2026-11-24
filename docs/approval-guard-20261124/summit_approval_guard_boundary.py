#!/usr/bin/env python3
"""Boundary extension of frozen pilot; separate cases and receipts. Hosted kind only."""
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
CASES = ('fresh', 'benign-annotation', 'policy-label-aba')
# Extension policy: benign annotation edits allowed; any approval-tier change
# invalidates approval even when the original value returns. Distinct from
# endpoint-only tier policy; this is a deliberately history-sensitive oracle.
ELIGIBLE = {'fresh', 'benign-annotation'}
EXPECTED = {
    'fresh': set(GUARDS),
    'benign-annotation': {'replica-only'},
    'policy-label-aba': {'replica-only','generation','policy-aware'},
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
    out = Path.home()/'Downloads'/'summit-guard-boundary-private'/rid
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
                    telemetry=m['labels']['telemetry-marker'],annotations=m.get('annotations',{}),spec=d['spec'])
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
    def invoke(name, guard, cap, key, invalid=False, missing=False):
        ops=test_ops(guard,cap)+[{'op':'replace','path':'/metadata/labels/nonexistent-pilot-key' if missing else '/spec/replicas','value':-1 if invalid else 1}]
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
                if case=='benign-annotation':
                    merge(name,{'metadata':{'annotations':{'pilot-note':'benign'}}})
                elif case=='policy-label-aba':
                    middle=state(merge(name,{'metadata':{'labels':{'approval-tier':'restricted'}}}))
                    require(middle['tier']=='restricted' and middle['uid']==cap['uid'] and middle['spec']==cap['spec'] and middle['generation']==cap['generation'] and middle['rv']!=cap['rv'],'tier ABA intermediate not established')
                    save(key+'-intermediate.json',middle)
                    merge(name,{'metadata':{'labels':{'approval-tier':'standard'}}})
                pre=state(read(name)) if case=='fresh' else stable(name)
                if case=='fresh':
                    require(all(pre[k]==cap[k] for k in PATHS),'fresh trial drifted; stop')
                elif case=='benign-annotation':
                    require(pre['uid']==cap['uid'] and pre['spec']==cap['spec'] and pre['generation']>cap['generation'] and pre['tier']==cap['tier'] and pre['telemetry']==cap['telemetry'] and pre['annotations'].get('pilot-note')=='benign' and 'pilot-note' not in cap['annotations'] and pre['rv']!=cap['rv'],'annotation contrast not established')
                else:
                    require(all(pre[k]==cap[k] for k in ('uid','replicas','generation','tier','telemetry','spec','annotations')) and pre['rv']!=cap['rv'] and pre['rv']!=middle['rv'],'tier ABA contrast not established')
                # Pre-state is only for evaluation; patch uses captured values exclusively.
                save(key+'-before.json',{'captured':cap,'pre_attempt':pre})
                ops,r=invoke(name,guard,cap,key)
                after=state(read(name))
                expected_spec=json.loads(json.dumps(pre['spec'])); expected_spec['replicas']=1
                if not r.get('failed',False):
                    ok=(after['uid']==pre['uid'] and after['spec']==expected_spec and after['tier']==pre['tier'] and after['telemetry']==pre['telemetry'] and after['annotations']==pre['annotations'] and after['generation']>pre['generation'])
                    actual='APPLIED' if ok else 'UNRESOLVED_SUCCESS'
                else:
                    unchanged=all(after[k]==pre[k] for k in ('uid','replicas','generation','tier','telemetry','spec','annotations'))
                    actual='NOT_APPLIED_422' if str(r.get('status'))=='422' and unchanged else 'UNRESOLVED_FAILURE'
                expect='APPLIED' if guard in EXPECTED[case] else 'NOT_APPLIED_422'
                policy=('ALLOWED' if case in ELIGIBLE else 'REAPPROVAL_REQUIRED')
                outcome=('FALSE_ACCEPT' if actual=='APPLIED' and case not in ELIGIBLE else 'FALSE_REJECT' if actual=='NOT_APPLIED_422' and case in ELIGIBLE else 'CORRECT' if actual in ('APPLIED','NOT_APPLIED_422') else 'UNKNOWN')
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
        # A second negative control: valid tests, replace of a missing path.
        # Compare exact structured API response against a stale-test case.
        name='missing-path-control'; cap=create(name)
        pre=state(read(name))
        require(all(pre[k]==cap[k] for k in PATHS),'missing path control drifted')
        ops,r=invoke(name,'policy-aware',cap,'missing-path-control',missing=True)
        post=state(read(name))
        save('missing-path-control-before.json',{'captured':cap,'pre_attempt':pre})
        unchanged=all(post[k]==pre[k] for k in ('uid','replicas','generation','tier','telemetry','spec','annotations'))
        def body(result):
            msg=result.get('msg',''); start=msg.find('{')
            try: return json.JSONDecoder().raw_decode(msg[start:])[0] if start>=0 else None
            except (ValueError,TypeError): return None
        stale=json.loads((out/'1-0-benign-annotation-whole-version-module.json').read_text())
        same=bool(body(r) and body(r)==body(stale) and r.get('failed') and str(r.get('status'))=='422' and unchanged)
        save('missing-path-control-after.json',{'patch':ops,'readback':post,'unchanged':unchanged,'same_api_body_as_stale_test':same})
        print('MISSING_PATH_CONTROL='+json.dumps({'failed':r.get('failed'),'status':r.get('status'),'msg':r.get('msg'),'unchanged':unchanged,'same_api_body_as_stale_test':same}),flush=True)
        print('PREDICTIONS_MATCHED='+str(sum(r['prediction_match'] for r in rows))+'/'+str(len(rows)),flush=True)
        for guard in GUARDS:
            rs=[r for r in rows if r['guard']==guard]
            print(guard, {k:sum(r['classification']==k for r in rs) for k in ('CORRECT','FALSE_ACCEPT','FALSE_REJECT','UNKNOWN')},flush=True)
        print('PILOT_COMPLETE='+('YES' if len(rows)==12 else 'NO'),flush=True)
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
