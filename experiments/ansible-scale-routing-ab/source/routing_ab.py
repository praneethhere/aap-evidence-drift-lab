#!/usr/bin/env python3
"""Controlled local-kind Ansible module comparison. NEVER run on other clusters.

preflight: read-only; prepare: install 6.4.0 to private scratch; run: mutate only a newly
owned kind namespace; delete only that owned namespace. Does not access AAP/OpenShift.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

import yaml

CLUSTER = 'summit-drift'
CTX = 'kind-summit-drift'
COLLECTION_VERSION = '6.4.0'
SCALE_SOURCE_SHA256 = '001e5658019f941defeedb7b876205f98605100d84e2cb8b0f61992a13332502'
MARKER_LABEL = 'summit-scale-ab-run'
PREFIX = 'summit-scale-ab-'
MAX_STDOUT = 3_000_000

class Stop(Exception):
    pass


def call(args, *, stdin=None, env=None, timeout=50):
    try:
        p = subprocess.run(args, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, timeout=timeout, check=False, env=env)
    except (subprocess.SubprocessError, OSError) as exc:
        raise Stop(f'command unavailable or timed out: {Path(args[0]).name}') from exc
    if len(p.stdout) > MAX_STDOUT or len(p.stderr) > MAX_STDOUT:
        raise Stop('command response oversized')
    return p


def good(args, *, stdin=None, env=None, timeout=50):
    p = call(args, stdin=stdin, env=env, timeout=timeout)
    if p.returncode:
        raise Stop(f'command failed safely: {Path(args[0]).name}')
    return p.stdout


def kube(*args, stdin=None, timeout=50):
    return good(['kubectl', '--context', CTX, *args], stdin=stdin, timeout=timeout)


def local_preflight():
    clusters = good(['kind', 'get', 'clusters']).splitlines()
    if CLUSTER not in clusters:
        raise Stop('expected kind cluster missing')
    config_json = good(['kubectl', 'config', 'view', '--raw', '--minify', '--context', CTX, '-o', 'json'])
    try:
        cfg = json.loads(config_json)
        kind_cfg = yaml.safe_load(good(['kind', 'get', 'kubeconfig', '--name', CLUSTER]))
        a = cfg['clusters'][0]['cluster']
        b = kind_cfg['clusters'][0]['cluster']
        server = a['server']
        url = urlsplit(server)
        if (a['server'] != b['server'] or
                a.get('certificate-authority-data') != b.get('certificate-authority-data') or
                url.scheme != 'https' or url.hostname not in ('127.0.0.1', 'localhost', '::1')):
            raise Stop('kind endpoint or CA mismatch / not local loopback')
        if cfg['contexts'][0]['name'] != CTX:
            raise Stop('unexpected kubectl context')
        nodes = json.loads(kube('get', 'nodes', '-o', 'json'))['items']
        if not nodes or any(not n['metadata']['name'].startswith(CLUSTER + '-') for n in nodes):
            raise Stop('kind cluster nodes do not match expected prefix')
        version = json.loads(kube('get', '--raw=/version'))
        if not isinstance(version.get('gitVersion'), str):
            raise Stop('unexpected Kubernetes version response')
        return version['gitVersion']
    except (KeyError, IndexError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise Stop('unable to verify local kind cluster identity') from exc


def manifest_version(collection_root: Path):
    info = collection_root / 'ansible_collections/kubernetes/core/MANIFEST.json'
    src = collection_root / 'ansible_collections/kubernetes/core/plugins/modules/k8s_scale.py'
    if not info.is_file() or not src.is_file() or info.is_symlink() or src.is_symlink():
        raise Stop('isolated collection not installed')
    try:
        version = json.loads(info.read_text())['collection_info']['version']
    except (ValueError, KeyError) as exc:
        raise Stop('invalid isolated collection manifest') from exc
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    if version != COLLECTION_VERSION or digest != SCALE_SOURCE_SHA256:
        raise Stop('isolated collection version/source digest mismatch (do not run)')
    return digest


def prepare(collection_root: Path):
    if collection_root.is_symlink():
        raise Stop('collection directory is symlink')
    collection_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        manifest_version(collection_root)
        print('ISOLATED_COLLECTION_ALREADY_PINNED=PASS')
        return
    except Stop:
        if (collection_root / 'ansible_collections/kubernetes/core').exists():
            raise Stop('collection exists but version/digest mismatch; do not overwrite')
    p = call(['ansible-galaxy', 'collection', 'install', f'kubernetes.core:{COLLECTION_VERSION}',
              '-p', str(collection_root)], timeout=180)
    if p.returncode:
        raise Stop('isolated Ansible Galaxy installation failed; existing 6.6.0 unchanged')
    manifest_version(collection_root)
    print('ISOLATED_KUBERNETES_CORE_6_4_0=PASS')


def owner_namespace(run_id):
    if not re.fullmatch(r'[0-9a-f]{10}', run_id):
        raise Stop('invalid run identifier')
    return PREFIX + run_id


def namespace_doc(ns, rid):
    return {'apiVersion':'v1','kind':'Namespace',
            'metadata':{'name':ns,'labels':{'app.kubernetes.io/managed-by':'summit-scale-ab',
                  MARKER_LABEL:rid}}}


def deployment_doc(ns, rid, name):
    return {'apiVersion':'apps/v1','kind':'Deployment',
            'metadata':{'namespace':ns,'name':name,
                        'labels':{'app.kubernetes.io/managed-by':'summit-scale-ab',MARKER_LABEL:rid}},
            'spec':{'replicas':0, 'selector':{'matchLabels':{'app':name}},
                    'template':{'metadata':{'labels':{'app':name}},
                    'spec':{'containers':[{'name':'pause','image':'registry.k8s.io/pause:3.10',
                                           'imagePullPolicy':'Never'}]}}}}


def read_dep(ns, name):
    d=json.loads(kube('-n',ns,'get','deployment',name,'-o','json'))
    m=d['metadata']
    if m['name']!=name or m['namespace']!=ns or not m.get('uid') or not m.get('resourceVersion'):
        raise Stop('wrong Deployment returned')
    return {'replicas':int(d['spec']['replicas']), 'uid':m['uid'], 'rv':str(m['resourceVersion'])}


def ensure_owner(ns, rid, uid):
    d=json.loads(kube('get','namespace',ns,'-o','json'))
    m=d['metadata']
    labels=m.get('labels',{})
    if m['name']!=ns or m['uid']!=uid or labels.get(MARKER_LABEL)!=rid or labels.get('app.kubernetes.io/managed-by')!='summit-scale-ab':
        raise Stop('namespace ownership mismatch; refusing deletion')


def module_args(ns,name,rv,mode):
    if mode=='scale':
        return {'api_version':'apps/v1','kind':'Deployment','namespace':ns,'name':name,
                'replicas':1,'resource_version':rv,'context':CTX,'wait':False}
    if mode=='patch':
        return {'state':'patched','api_version':'apps/v1','kind':'Deployment','namespace':ns,
                'name':name,'context':CTX,'merge_type':['merge'],
                'definition':{'apiVersion':'apps/v1','kind':'Deployment',
                              'metadata':{'name':name,'namespace':ns,'resourceVersion':rv},
                              'spec':{'replicas':1}}}
    raise Stop('invalid module mode')


def strict_conflict_expression():
    """Strict module-error classification, derived from diagnostic v1's validated flags.

    Normalize all whitespace in JSON message text; unlike v1's replace(' ', ''),
    newlines and tabs are removed too. Never accept top-level error codes alone.
    Also require the actual failing task to be the stale native patch.
    """
    msg = "(ansible_failed_result.msg | default('', true) | string)"
    compact = f"({msg} | regex_replace('\\\\s+', ''))"
    code_key = repr('"code":409')
    reason_key = repr('"reason":"Conflict"')
    return (
        "((ansible_failed_task.name | default('')) == "
        "'Attempt stale version-bound merge patch') and "
        f"('Failed to patch object:' in {msg}) and "
        f"({code_key} in {compact}) and "
        f"({reason_key} in {compact})"
    )


def make_play(ns,name,rv,mode,stale):
    args=module_args(ns,name,rv,mode)
    if mode=='scale':
        tasks=[{'name':'Scale with client-side version comparison',
                'kubernetes.core.k8s_scale':args,'register':'scale_result','no_log':True},
               {'name':'Require task reported no modification' if stale else 'Require task reported a modification',
                'ansible.builtin.assert':{'that':[
                     'not (scale_result.changed | default(false) | bool)' if stale else
                     '(scale_result.changed | default(false) | bool)']},'no_log':True},
               {'name':'Print bounded marker', 'ansible.builtin.debug':{
                   'msg':'SCALE_STALE_TASK_SUCCESS=YES' if stale else 'SCALE_FRESH_TASK_SUCCESS=YES'}}]
    elif not stale:
        tasks=[{'name':'Apply fresh version-bound merge patch',
                'kubernetes.core.k8s':args,'register':'patch_result','no_log':True},
               {'name':'Print bounded marker','ansible.builtin.debug':{'msg':'PATCH_FRESH_TASK_SUCCESS=YES'}}]
    else:
        # no_log on sensitive module errors; only emit safe Boolean control marker
        probe={'name':'Attempt stale version-bound merge patch','kubernetes.core.k8s':args,'no_log':True}
        expr = strict_conflict_expression()
        # Quoting in Jinja is generated from YAML/JSON, never shell-interpolated.
        tasks=[{'name':'Demonstrate failing job required for AAP failure edge',
                'block':[probe,{'name':'Stop on unexpected patch success','ansible.builtin.fail':{'msg':'UNEXPECTED_PATCH_SUCCESS'}}],
                'rescue':[{'name':'Private conflict classification','ansible.builtin.set_fact':
                           {'native_conflict':'{{ '+expr+' }}'},'no_log':True},
                          {'name':'Require exact Kubernetes module conflict','ansible.builtin.assert':
                           {'that':['native_conflict | bool']},'no_log':True},
                          {'name':'Emit safe classification','ansible.builtin.debug':
                           {'msg':'NATIVE_PATCH_409_CLASSIFIED=YES'}},
                          {'name':'Deliberately fail action job','ansible.builtin.fail':
                           {'msg':'EXPECTED_FAILURE_BRANCH_ON_NATIVE_CONFLICT'}}]}]
    return [{'name':'One isolated Ansible experiment case', 'hosts':'localhost',
             'connection':'local','gather_facts':False,
             'vars':{'ansible_python_interpreter':sys.executable},'tasks':tasks}]


def parse_case(case, proc):
    text=proc.stdout
    if case=='scale-fresh':
        expected=(proc.returncode==0 and 'SCALE_FRESH_TASK_SUCCESS=YES' in text)
    elif case=='patch-fresh':
        expected=(proc.returncode==0 and 'PATCH_FRESH_TASK_SUCCESS=YES' in text)
    elif case=='scale-stale':
        expected=(proc.returncode==0 and 'SCALE_STALE_TASK_SUCCESS=YES' in text)
    elif case=='patch-stale':
        expected=(proc.returncode!=0 and 'NATIVE_PATCH_409_CLASSIFIED=YES' in text and
                  'EXPECTED_FAILURE_BRANCH_ON_NATIVE_CONFLICT' in text)
    else:
        raise Stop('unknown case')
    return expected


def an_env(collection_root):
    env=os.environ.copy()
    env['ANSIBLE_COLLECTIONS_PATH']=str(collection_root)
    env['ANSIBLE_NOCOLOR']='1'
    env['ANSIBLE_DEPRECATION_WARNINGS']='False'
    env['ANSIBLE_RETRY_FILES_ENABLED']='False'
    env['PYTHONUNBUFFERED']='1'
    # Avoid inherited Ansible/Kubernetes runtime configuration that could redirect operations.
    for k in list(env):
        if k.startswith(('K8S_AUTH_', 'ANSIBLE_CONFIG','ANSIBLE_COLLECTIONS_PATHS')):
            env.pop(k,None)
    env['ANSIBLE_COLLECTIONS_PATH']=str(collection_root)
    return env


def run_case(out, collection_root, ns, name, captured, case):
    stale=case.endswith('stale')
    mode=case.split('-')[0]
    play=make_play(ns,name,captured['rv'],mode,stale)
    playfile=out/(case+'.yml')
    playfile.write_text(yaml.safe_dump(play,sort_keys=False))
    os.chmod(playfile,0o600)
    p=call(['ansible-playbook','-i','localhost,',str(playfile)],env=an_env(collection_root),timeout=150)
    logfile=out/(case+'.private.log')
    logfile.write_text(p.stdout+'\n'+p.stderr)
    os.chmod(logfile,0o600)
    return {'case':case,'ansible_exit_code':p.returncode,'task_expected':parse_case(case,p),
            'private_log_sha256':hashlib.sha256(logfile.read_bytes()).hexdigest()}


def run(collection_root, outbase):
    manifest_version(collection_root)
    version=local_preflight()
    if outbase.is_symlink():
        raise Stop('output folder cannot be symlink')
    outbase.mkdir(mode=0o700,parents=True,exist_ok=True)
    rid=secrets.token_hex(5)
    ns=owner_namespace(rid)
    out=outbase/(ns)
    out.mkdir(mode=0o700)
    created=False
    ns_uid=None
    cases=[]
    cleanup='NOT_NEEDED'
    error_type=None
    try:
        kube('create','-f','-',stdin=json.dumps(namespace_doc(ns,rid)))
        created=True
        obj=json.loads(kube('get','namespace',ns,'-o','json'))
        ns_uid=obj['metadata']['uid']
        ensure_owner(ns,rid,ns_uid)
        for c in ['scale-fresh','patch-fresh','scale-stale','patch-stale']:
            kube('create','-f','-',stdin=json.dumps(deployment_doc(ns,rid,c)))
        for c in ['scale-fresh','patch-fresh','scale-stale','patch-stale']:
            before=read_dep(ns,c)
            if before['replicas']!=0:
                raise Stop('invalid zero-replica baseline')
            if c.endswith('stale'):
                kube('-n',ns,'scale','deployment/'+c,'--replicas=2',
                     '--resource-version='+before['rv'])
                drift=read_dep(ns,c)
                if drift['replicas']!=2 or drift['uid']!=before['uid'] or drift['rv']==before['rv']:
                    raise Stop('controlled precondition drift not observed')
            else:
                drift=before
            case_rec=run_case(out,collection_root,ns,c,before,c)
            after=read_dep(ns,c)
            expected_replicas=2 if c.endswith('stale') else 1
            same_uid=(after['uid']==before['uid'])
            case_rec.update({'initial_replicas':0,'injected_replicas':2 if c.endswith('stale') else None,
                             'expected_final_replicas':expected_replicas,
                             'observed_final_replicas':after['replicas'],
                             'same_uid':same_uid,
                             'result':'PASS' if (case_rec['task_expected'] and
                                after['replicas']==expected_replicas and same_uid) else 'INCOMPLETE'})
            cases.append(case_rec)
            print('CASE='+c+' RESULT='+case_rec['result']+' ANSIBLE_EXIT='+str(case_rec['ansible_exit_code'])+
                  ' FINAL_REPLICAS='+str(after['replicas']))
            if case_rec['result']!='PASS':
                raise Stop('case did not match expected signature')
    except (Stop, KeyError, ValueError) as exc:
        error_type=type(exc).__name__
        print('EXPERIMENT_STOPPED=INCOMPLETE (private logs retained; do not publish as PASS)')
    finally:
        if created:
            try:
                ensure_owner(ns,rid,ns_uid)
                kube('delete','namespace',ns,'--wait=false',timeout=30)
                cleanup='DELETE_REQUESTED_FOR_OWNED_NAMESPACE'
            except (Stop, KeyError, TypeError, ValueError):
                cleanup='NOT_CONFIRMED_MANUAL_REVIEW_REQUIRED'
        receipt={'schema_version':'summit-scale-routing-ab-v2','run_status':
                 'PASS' if len(cases)==4 and all(c['result']=='PASS' for c in cases) and
                      error_type is None and cleanup=='DELETE_REQUESTED_FOR_OWNED_NAMESPACE'
                 else 'INCOMPLETE',
                 'source_collection':'kubernetes.core','collection_version':COLLECTION_VERSION,
                 'scale_module_sha256':SCALE_SOURCE_SHA256,
                 'kubernetes_version':version,'cluster_type':'verified-local-kind',
                 'namespace':ns,'AAP_involved':False,'OpenShift_involved':False,
                 'cleanup_status':cleanup,'cases':cases,
                 'limits':[
                   'One local author-run lab; no independent reproduction',
                   'No AAP workflow or approval routing was executed',
                   'No race-frequency measurement or TOCTOU reproduction',
                   'Stale status is determined from observed post-condition and module task exit',
                   'Private Ansible logs are not included in the sanitized receipt',
                   'Strict conflict classifier requires failing patch task, module message prefix, and nested 409 Conflict',
                   'Namespace deletion is requested, not guaranteed complete at receipt time']}
        file=out/'sanitized_receipt.json'
        file.write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n')
        os.chmod(file,0o600)
        print('RECEIPT_CREATED=PRIVATE_NOT_PUBLICATION_CLEARED')
        print('OWNED_NAMESPACE_CLEANUP='+cleanup)
        print('AB_EXPERIMENT_VERDICT='+receipt['run_status'])
        print('PRIVATE_OUTPUT_DIR='+str(out))
    return receipt['run_status']=='PASS'


def main():
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=['preflight','prepare','run'])
    p.add_argument('--collections',type=Path,default=Path.home()/'Downloads/summit-scale-ab-private/collections')
    p.add_argument('--output',type=Path,default=Path.home()/'Downloads/summit-scale-ab-private/results')
    p.add_argument('--confirm',default='')
    args=p.parse_args()
    try:
        if args.action=='preflight':
            version=local_preflight()
            print('LOCAL_KIND_SCOPE=PASS')
            print('KUBERNETES_VERSION='+version)
            print('AAP_MUTATIONS=NONE KUBERNETES_MUTATIONS=NONE')
            print('PREFLIGHT=PASS')
        elif args.action=='prepare':
            prepare(args.collections.expanduser().resolve())
            print('KUBERNETES_MUTATIONS=NONE')
        else:
            if args.confirm!='RUN ISOLATED KIND A/B':
                raise Stop('explicit run confirmation not supplied')
            if not run(args.collections.expanduser().resolve(),args.output.expanduser()):
                sys.exit(2)
    except Stop as exc:
        print('STOP='+str(exc))
        sys.exit(2)

if __name__=='__main__':
    main()
