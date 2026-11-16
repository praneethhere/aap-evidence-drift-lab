from __future__ import annotations
import argparse
import json
from dataclasses import asdict
from .simulator import SCENARIOS, STATEGIES, matrix, summary

parser = argparse.ArgumentParser(description='Deterministic evidence-drift lab (not AAP validation)')
parser.add_argument('--json', action='store_true', help='Emit complete JSON result')
parser.add_argument('--scenario', choices=[s.name for s in SCENARIOS])
args = parser.parse_args()
trials = [t for t in matrix() if not args.scenario or t.scenario == args.scenario]
if args.json:
    print(json.dumps({'metadata': {'platform_tested': 'Python toy state machine only', 'AAP_tested': False},
                      'summary': summary(), 'trials': [asdict(t) for t in trials]}, indent=2))
else:
    print('Evidence drift lab -- deterministic MODEL, not AAP measurements\n')
    print(f'{"scenario":29s} {"strategy":21s} {"outcome":48s} {"stale":5s} {"false":5s}')
    for t in trials:
        print(f'{t.scenario:29s} {t.strategy:21s} {t.outcome[:48]:48s} {t.stale_mutations:<5} {t.false_successes:<5}')
    print('\nSummary:')
    for item in summary():
        print(json.dumps(item, sort_keys=True))
