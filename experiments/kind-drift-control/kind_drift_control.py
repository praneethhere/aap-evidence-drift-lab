#!/usr/bin/env python3
"""Isolated, locally runnable Kubernetes control; NOT an AAP workflow reproduction.

Only operates on a local kind cluster named summit-drift. Uses kubectl proxy so PATCH
HTTP statuses are observable without reading or storing kubeconfig credentials.
"""
import argparse
import datetime as dt
import json
import os
import re
import select
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

CONTEXT = 'kind-summit-drift'
LABEL_KEY = 'summit-lab.example/experiment'
LABEL_VALUE = 'aap-approval-drift-public-control'
NAMESPACE_PREFIX = 'summit-drift-public-'


def cmd(args, *, stdin=None, must=True):
    p = subprocess.run(args, input=stdin, text=True, capture_output=True, timeout=45)
    if must and p.returncode:
        raise RuntimeError(f"Command failed (exit {p.returncode}): {' '.join(args[:5])}: {p.stderr.strip()[:300]}")
    return p


def kubectl(*args, stdin=None):
    return cmd(['kubectl', '--context', CONTEXT, *args], stdin=stdin)


def preflight():
    if not shutil.which('kubectl') or not shutil.which('kind'):
        raise RuntimeError('Install kubectl and kind first. No cluster changes were made.')
    current = cmd(['kubectl', 'config', 'current-context']).stdout.strip()
    if current != CONTEXT:
        raise RuntimeError(f'Unsafe kubecontext: {current!r}. Required: {CONTEXT!r}. No changes made.')
    # Context *names* are user-editable. Match this context's actual server AND
    # certificate authority to the kubeconfig exported by the local kind cluster.
    # Credentials are inspected only in memory; never print either kubeconfig.
    active = json.loads(cmd(['kubectl', 'config', 'view', '--raw', '--minify', '-o', 'json']).stdout)
    entries = active.get('clusters', [])
    if len(entries) != 1:
        raise RuntimeError('Unsafe kubeconfig: expected one resolved cluster. No changes made.')
    cluster = entries[0].get('cluster', {})
    server = cluster.get('server', '')
    parsed = urllib.parse.urlsplit(server)
    if parsed.scheme != 'https' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or parsed.port is None:
        raise RuntimeError('The kind context must point to a loopback HTTPS API endpoint. No changes made.')
    if cluster.get('insecure-skip-tls-verify') or cluster.get('proxy-url') or cluster.get('tls-server-name'):
        raise RuntimeError('Unsafe kubeconfig: TLS bypass, proxy, or server override. No changes made.')
    clusters = cmd(['kind', 'get', 'clusters']).stdout.splitlines()
    if 'summit-drift' not in clusters:
        raise RuntimeError('kind cluster summit-drift not listed. No changes made.')
    kind_config = cmd(['kind', 'get', 'kubeconfig', '--name', 'summit-drift']).stdout

    def sole_yaml_value(key):
        hits = re.findall(r'(?m)^\s*' + re.escape(key) + r':\s*(\S+)\s*$', kind_config)
        if len(hits) != 1:
            raise RuntimeError(f'Cannot uniquely identify kind {key}. No changes made.')
        return hits[0]

    expected_server = sole_yaml_value('server')
    expected_ca = sole_yaml_value('certificate-authority-data')
    if server != expected_server or cluster.get('certificate-authority-data') != expected_ca:
        raise RuntimeError('Unsafe kubeconfig: current context does not match actual kind cluster endpoint and CA. No changes made.')
    kubectl('get', '--raw', '/version')
    return {'context': current, 'server_scope': 'verified-local-kind-loopback', 'kind_cluster': 'summit-drift'}


class LocalProxy:
    def __enter__(self):
        self.p = subprocess.Popen(
            ['kubectl', '--context', CONTEXT, 'proxy', '--port=0', '--address=127.0.0.1'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
        )
        assert self.p.stdout is not None
        readable, _, _ = select.select([self.p.stdout], [], [], 15)
        if not readable:
            self.__exit__(None, None, None)
            raise RuntimeError('kubectl proxy did not announce a local port within 15 seconds.')
        line = self.p.stdout.readline().strip()
        m = re.search(r'127\.0\.0\.1:(\d+)', line)
        if not m:
            self.__exit__(None, None, None)
            raise RuntimeError(f'Unexpected local proxy response: {line[:160]}')
        self.base = f'http://127.0.0.1:{m.group(1)}'
        return self

    def __exit__(self, *_):
        self.p.terminate()
        try:
            self.p.wait(timeout=4)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.wait(timeout=4)

    def request(self, method, path, payload=None, content_type='application/json'):
        data = None if payload is None else json.dumps(payload).encode()
        headers = {'Accept': 'application/json'}
        if data is not None:
            headers['Content-Type'] = content_type
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as res:
                return res.status, json.load(res)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode('utf-8', 'replace')
            try:
                body = json.loads(raw)
            except ValueError:
                body = {'reason': 'unparsed_response'}
            return exc.code, body


def deployment(name, namespace):
    return {
        'apiVersion': 'apps/v1', 'kind': 'Deployment',
        'metadata': {'name': name, 'namespace': namespace, 'labels': {LABEL_KEY: LABEL_VALUE}},
        'spec': {'replicas': 0, 'selector': {'matchLabels': {'app': name}},
                 'template': {'metadata': {'labels': {'app': name}},
                              'spec': {'containers': [{'name': 'pause', 'image': 'registry.k8s.io/pause:3.9'}]}}}
    }


def observations(obj):
    meta, spec = obj.get('metadata', {}), obj.get('spec', {})
    return {'uid': meta.get('uid'), 'resourceVersion': meta.get('resourceVersion'),
            'generation': meta.get('generation'), 'desired_replicas': spec.get('replicas')}


def get_obj(proxy, ns, name):
    code, val = proxy.request('GET', f'/apis/apps/v1/namespaces/{ns}/deployments/{name}')
    if code != 200:
        raise RuntimeError(f'GET deployment failed: {code} {val.get("reason")}')
    return val


def check_deployment(obj, ns, name, replicas=None):
    meta = obj.get('metadata', {})
    if meta.get('name') != name or meta.get('namespace') != ns or meta.get('labels', {}).get(LABEL_KEY) != LABEL_VALUE:
        raise RuntimeError('Safety check failed: unexpected deployment identity/label.')
    if not meta.get('uid') or not meta.get('resourceVersion'):
        raise RuntimeError('Safety check failed: missing UID or resourceVersion.')
    if replicas is not None and obj.get('spec', {}).get('replicas') != replicas:
        raise RuntimeError(f'Unexpected desired replicas; expected {replicas}.')
    return obj


def check_uid_continuity(obj, captured):
    if obj['metadata']['uid'] != captured['metadata']['uid']:
        raise RuntimeError('Deployment UID changed; no successful result can be claimed.')
    return obj


def patch(proxy, ns, name, replicas, rv=None):
    payload = {'spec': {'replicas': replicas}}
    if rv is not None:
        payload['metadata'] = {'resourceVersion': rv}
    return proxy.request('PATCH', f'/apis/apps/v1/namespaces/{ns}/deployments/{name}',
                         payload, 'application/merge-patch+json')


def create(ns, name):
    kubectl('create', '-f', '-', stdin=json.dumps(deployment(name, ns)))


def stage_drift(proxy, ns, name, before):
    # A simulated external edit must not depend on the captured version. A real
    # Deployment controller can update resourceVersion between GET and PATCH.
    # Only the later guarded experimental write uses the deliberately stale RV.
    code, body = patch(proxy, ns, name, 2)
    if code != 200:
        reason = body.get('reason', 'unknown') if isinstance(body, dict) else 'unknown'
        raise RuntimeError(f'Could not stage controlled drift: HTTP {code}, reason={reason}.')
    changed = check_deployment(get_obj(proxy, ns, name), ns, name, 2)
    if changed['metadata']['uid'] != before['metadata']['uid']:
        raise RuntimeError('Object identity changed unexpectedly during drift.')
    if changed['metadata']['resourceVersion'] == before['metadata']['resourceVersion']:
        raise RuntimeError('Drift did not advance the captured resourceVersion.')
    return code, changed


def wait_for_quiet_version(proxy, ns, name, expected_uid, *, desired=2, quiet_seconds=1.5, timeout_seconds=20.0):
    """Wait for a stable version before *one* fresh conditional write.

    This is lab timing stabilization, not an approval and not an automatic
    retry after a conflict. A no-change window does not guarantee the next
    PATCH will succeed: the API server is authoritative.
    """
    deadline = time.monotonic() + timeout_seconds
    last_rv = None
    quiet_since = None
    observations_count = 0
    rv_transitions = 0
    while True:
        obj = check_deployment(get_obj(proxy, ns, name), ns, name, desired)
        if obj['metadata'].get('uid') != expected_uid:
            raise RuntimeError('Recapture refused: deployment UID changed.')
        rv = obj['metadata'].get('resourceVersion')
        if not rv:
            raise RuntimeError('Recapture refused: missing resourceVersion.')
        now = time.monotonic()
        observations_count += 1
        if rv != last_rv:
            if last_rv is not None:
                rv_transitions += 1
            last_rv, quiet_since = rv, now
        assert quiet_since is not None
        if now - quiet_since >= quiet_seconds:
            return obj, {'polls': observations_count, 'rv_changes_seen': rv_transitions,
                         'quiet_seconds_required': quiet_seconds}
        if now >= deadline:
            raise RuntimeError('Recapture timed out waiting for a quiet resourceVersion; '
                               f'polls={observations_count}, changes={rv_transitions}; no write attempted.')
        time.sleep(0.3)


def result_row(case, before, drift, response_status, response_body, after, expected, drift_http_status=None):
    return {'case': case, 'captured_state': observations(before),
            'intervening_state': observations(drift) if drift else None,
            'intervening_patch_http_status': drift_http_status,
            'patch_http_status': response_status,
            'patch_reason': response_body.get('reason') if isinstance(response_body, dict) else None,
            'after_state': observations(after), 'expected': expected}


def execute(output_dir):
    safety = preflight()
    ns = NAMESPACE_PREFIX + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d%H%M%S') + '-' + uuid.uuid4().hex[:5]
    rows = []
    completed = False
    namespace_created = False
    try:
        # The ownership label must be present atomically at namespace creation.
        ns_doc = {'apiVersion': 'v1', 'kind': 'Namespace',
                  'metadata': {'name': ns, 'labels': {LABEL_KEY: LABEL_VALUE}}}
        kubectl('create', '-f', '-', stdin=json.dumps(ns_doc))
        namespace_created = True
        with LocalProxy() as proxy:
            # Positive control: no intervening change.
            name = 'guarded-no-drift'
            create(ns, name)
            before = check_deployment(get_obj(proxy, ns, name), ns, name, 0)
            code, body = patch(proxy, ns, name, 1, before['metadata']['resourceVersion'])
            after = check_uid_continuity(check_deployment(get_obj(proxy, ns, name), ns, name), before)
            rows.append(result_row(name, before, None, code, body, after, 'HTTP 200 and desired_replicas 1'))
            if code != 200 or after['spec']['replicas'] != 1 or after['metadata']['uid'] != before['metadata']['uid']:
                raise RuntimeError('Positive control did not succeed; stop, no inference.')

            # Negative control: same staged drift, intentionally NO precondition.
            name = 'unguarded-after-drift'
            create(ns, name)
            before = check_deployment(get_obj(proxy, ns, name), ns, name, 0)
            drift_code, drift = stage_drift(proxy, ns, name, before)
            code, body = patch(proxy, ns, name, 1)  # Deliberately unguarded.
            after = check_uid_continuity(check_deployment(get_obj(proxy, ns, name), ns, name), before)
            rows.append(result_row(name, before, drift, code, body, after, 'HTTP 200 and desired_replicas 1 (unguarded overwrite)', drift_http_status=drift_code))
            if code != 200 or after['spec']['replicas'] != 1:
                raise RuntimeError('Negative control did not demonstrate unguarded overwrite.')

            # Test: same staged drift, stale precondition MUST reject with HTTP 409.
            name = 'guarded-after-drift'
            create(ns, name)
            before = check_deployment(get_obj(proxy, ns, name), ns, name, 0)
            drift_code, drift = stage_drift(proxy, ns, name, before)
            code, body = patch(proxy, ns, name, 1, before['metadata']['resourceVersion'])
            after = check_uid_continuity(check_deployment(get_obj(proxy, ns, name), ns, name), before)
            rows.append(result_row(name, before, drift, code, body, after, 'HTTP 409 and desired_replicas remain 2', drift_http_status=drift_code))
            if code != 409 or after['spec']['replicas'] != 2:
                raise RuntimeError('Guarded stale write was not rejected as expected.')

            # Follow-up control: wait for a quiet version, recapture and attempt
            # exactly ONE conditional write. No implicit retry after a 409.
            # Deployment status updates can race this test even when replicas
            # remain 2, so the quiet-version check reduces (not eliminates) it.
            new_before, stability = wait_for_quiet_version(
                proxy, ns, name, before['metadata']['uid'])
            code, body = patch(proxy, ns, name, 1, new_before['metadata']['resourceVersion'])
            final = check_uid_continuity(check_deployment(get_obj(proxy, ns, name), ns, name), new_before)
            row = result_row('recapture-fresh-guarded-write', new_before, None, code, body, final,
                             'HTTP 200 and desired_replicas 1')
            row['version_stabilization'] = stability
            rows.append(row)
            if code != 200 or final['spec']['replicas'] != 1:
                raise RuntimeError('Fresh guarded write did not succeed: '
                                   f'HTTP={code}, reason={row["patch_reason"]}, '
                                   f'captured_rv={new_before["metadata"]["resourceVersion"]}, '
                                   f'after_rv={final["metadata"]["resourceVersion"]}; '
                                   'no automatic retry attempted.')
            completed = True
    finally:
        # Unique per-namespace path prevents overwriting a prior run's evidence.
        # Preserve the lab even after failures; never delete any cluster resource implicitly.
        run_dir = output_dir / ns
        run_dir.mkdir(parents=True, exist_ok=False)
        doc = {'schema_version': 2, 'scope': 'Kubernetes API control experiment, not AAP reproduction',
               'epistemic_status': 'local-run-observations-not-independently-reproduced',
               'run_status': 'PASS' if completed else 'INCOMPLETE',
               'namespace_created': namespace_created,
               'run_time_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
               'cluster_scope': safety, 'namespace': ns, 'cases': rows,
               'limits': ['No AAP workflow or approval nodes were executed.',
                          'Desired replicas only; not pod readiness or durability.',
                          'Different Deployments represent independent cases; Kubernetes background controllers can modify resourceVersion.',
                          'Results are locally observed; independent repetition requires another operator.']}
        out = run_dir / 'local_control_receipts.json'
        out.write_text(json.dumps(doc, indent=2) + '\n')
        print(f'RESULT_FILE={out.resolve()}')
        print(f'LAB_NAMESPACE={ns}')
        for row in rows:
            print(f"CASE={row['case']} HTTP={row['patch_http_status']} REPLICAS={row['after_state']['desired_replicas']}")
        print('NOTE=No automatic cleanup. Delete only this labeled namespace after review.')
    if not completed or len(rows) != 4:
        raise RuntimeError('Incomplete experiment. Do not publish or infer success.')
    print('KIND_CONTROL_EXPERIMENT=PASS')


def cleanup(ns):
    preflight()
    if not re.fullmatch(r'summit-drift-public-[0-9]{14}-[a-f0-9]{5}', ns):
        raise RuntimeError('Refusing cleanup: namespace does not match research naming pattern.')
    p = kubectl('get', 'namespace', ns, '-o', 'json')
    obj = json.loads(p.stdout)
    if obj.get('metadata', {}).get('labels', {}).get(LABEL_KEY) != LABEL_VALUE:
        raise RuntimeError('Refusing cleanup: research ownership label absent.')
    kubectl('delete', 'namespace', ns, '--wait=false')
    print(f'DELETED_RESEARCH_NAMESPACE={ns}')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--run', action='store_true', help='Create isolated objects and execute four local control cases')
    g.add_argument('--cleanup', metavar='NAMESPACE', help='Delete a previously created lab namespace after review')
    ap.add_argument('--out', default='results', help='Local output folder for sanitized receipts')
    args = ap.parse_args()
    try:
        if args.cleanup:
            cleanup(args.cleanup)
        elif args.run:
            execute(Path(args.out))
        else:
            print('READ_ONLY_PREFLIGHT=' + json.dumps(preflight(), sort_keys=True))
            print('NO_CHANGES_MADE. Run with --run to execute isolated local cluster controls.')
    except Exception as e:
        print('EXPERIMENT_STOPPED=' + str(e), file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    main()
