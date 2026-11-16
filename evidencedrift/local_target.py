"""Local, file-backed demonstration of evidence-bound conditional actions.

This is a SINGLE-HOST teaching artifact, not a distributed lock, not an AAP
implementation, and not a production enforcement boundary.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from dataclasses import asdict
import fcntl
import json
from pathlib import Path
from .simulator import Evidence

DEFAULT_TARGET = 'service:demo'

@contextmanager
def transaction(path: Path):
    """Lock one toy state file for one local process family (POSIX only)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix + '.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not path.exists():
            raise FileNotFoundError('Run `init` first')
        state = json.loads(path.read_text())
        try:
            yield state
        finally:
            path.write_text(json.dumps(state, indent=2, sort_keys=True) + '\n')
            fcntl.flock(lock, fcntl.LOCK_UN)

def init(path: Path) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError('Use a new path or explicitly delete old demo state')
    state = {'target': DEFAULT_TARGET, 'status':'degraded', 'version': 1,
             'restart_count': 0, 'completed_events': []}
    with path.open('x') as stream:
        json.dump(state, stream, indent=2, sort_keys=True)
    return state

def observe(path: Path, event_id: str) -> dict:
    with transaction(path) as state:
        e = Evidence(event_id, state['target'], state['status'], state['version'], 'healthy')
    return {**asdict(e), 'sha256': e.digest}

def inject(path: Path, status: str) -> dict:
    with transaction(path) as state:
        state['status'] = status
        state['version'] += 1
        return {'disposition': 'external_mutation', 'version': state['version'], 'status':status}

def apply(path: Path, evidence_path: Path, postaction_failure: bool = False) -> tuple[int, dict]:
    saved = json.loads(evidence_path.read_text())
    e = Evidence(**{k: saved[k] for k in ['event_id','target','observed_status','observed_version','desired_status']})
    if saved.get('sha256') != e.digest:
        return 2, {'disposition': 'blocked', 'reason': 'invalid_evidence_digest'}
    with transaction(path) as state:
        if e.event_id in state['completed_events']:
            return 0, {'disposition': 'deduplicated', 'restart_count':state['restart_count']}
        if (state['target'] != e.target or state['version'] != e.observed_version or
            state['status'] != e.observed_status):
            return 2, {'disposition': 'blocked', 'reason': 'evidence_no_longer_matches',
                       'observed_version': e.observed_version, 'actual_version':state['version']}
        # Local lock covers check and update. This does NOT establish atomicity
        # for real cloud APIs or distributed state.
        state['status'] = e.desired_status
        state['version'] += 1
        state['restart_count'] += 1
        if postaction_failure:
            state['status'] = 'degraded'
            state['version'] += 1
        if state['status'] != e.desired_status:
            return 3, {'disposition': 'postcondition_failed', 'status':state['status'],
                       'version':state['version']}
        state['completed_events'].append(e.event_id)
        return 0, {'disposition': 'verified_success', 'restart_count':state['restart_count'],
                   'version':state['version']}

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('init','observe','inject','apply','state'):
        p = sub.add_parser(name)
        p.add_argument('--path', type=Path, required=True)
        if name == 'observe':
            p.add_argument('--event-id', default='event-0001')
        if name == 'inject':
            p.add_argument('--status', choices=['degraded','healthy'], required=True)
        if name == 'apply':
            p.add_argument('--evidence', type=Path, required=True)
            p.add_argument('--postaction-failure', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command == 'init':
            output = init(args.path)
        elif args.command == 'observe':
            output = observe(args.path, args.event_id)
        elif args.command == 'inject':
            output = inject(args.path, args.status)
        elif args.command == 'apply':
            code, output = apply(args.path, args.evidence, args.postaction_failure)
            print(json.dumps(output, indent=2, sort_keys=True))
            return code
        else:
            output = json.loads(args.path.read_text())
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0
    except (FileNotFoundError, FileExistsError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({'error': str(exc)}))
        return 4

if __name__ == '__main__':
    raise SystemExit(main())
