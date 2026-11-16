"""Toy state-machine experiment: NOT an Ansible Automation Platform emulator.

Models four conceptual guard placements and deliberately introduces state changes
at stages where evidence, approval, and execution can be separated in time.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
from typing import Literal

Strategy = Literal['approval_only', 'job_time_policy', 'preaction_check', 'bound_and_verified']
STATEGIES: tuple[Strategy, ...] = (
    'approval_only', 'job_time_policy', 'preaction_check', 'bound_and_verified'
)

@dataclass(frozen=True)
class Evidence:
    event_id: str
    target: str
    observed_status: str
    observed_version: int
    desired_status: str

    @property
    def digest(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(',', ':'))
        return sha256(payload.encode()).hexdigest()

@dataclass
class World:
    target: str = 'service:demo'
    status: str = 'degraded'
    version: int = 1
    mutations: int = 0
    completed_events: set[str] = field(default_factory=set)
    history: list[str] = field(default_factory=list)

    def external_change(self, new_status: str, label: str) -> None:
        self.status = new_status
        self.version += 1
        self.history.append(f'external:{label}:version={self.version}:status={self.status}')

    def restart(self, evidence: Evidence) -> None:
        """Intentionally non-idempotent mutation for illustrative baselines."""
        self.status = evidence.desired_status
        self.version += 1
        self.mutations += 1
        self.history.append(f'mutation:version={self.version}:status={self.status}')

@dataclass(frozen=True)
class Scenario:
    name: str
    injection_stage: str | None = None
    changed_status: str = 'healthy'
    repeats: int = 1
    explanation: str = ''

SCENARIOS = (
    Scenario('stable', explanation='No intervening changes'),
    Scenario('change_during_approval', 'before_job', 'healthy', explanation='Externally repaired before job policy evaluation'),
    Scenario('change_after_job_policy', 'after_job_policy', 'healthy', explanation='Changed after admission, before near-action checks'),
    Scenario('change_after_preaction_check', 'after_preaction', 'healthy', explanation='Changed after non-atomic pre-action check'),
    Scenario('postaction_regression', 'after_action', 'degraded', explanation='Regression after mutation and before job response'),
    Scenario('same_status_new_version', 'before_job', 'degraded', explanation='Version changed, state label unchanged'),
    Scenario('duplicate_event', repeats=2, explanation='Same approved event arrives twice'),
)

@dataclass
class Attempt:
    disposition: str
    mutated: bool
    stale_mutation: bool
    false_success: bool
    completed_correctly: bool

@dataclass
class Trial:
    scenario: str
    strategy: Strategy
    outcome: str
    mutations: int
    stale_mutations: int
    false_successes: int
    final_status: str
    final_version: int
    history: list[str]


def run_once(strategy: Strategy, world: World, evidence: Evidence, scenario: Scenario) -> Attempt:
    if strategy not in STATEGIES:
        raise ValueError(f'Unrecognized strategy: {strategy}')

    def inject(stage: str) -> None:
        if scenario.injection_stage == stage:
            world.external_change(scenario.changed_status, stage)

    def matches_initial_evidence() -> bool:
        return (world.target == evidence.target and
                world.status == evidence.observed_status and
                world.version == evidence.observed_version)

    inject('before_job')
    if strategy == 'bound_and_verified' and evidence.event_id in world.completed_events:
        world.history.append('blocked:duplicate_event')
        return Attempt('deduplicated', False, False, False, True)

    # The policy baseline is intentionally stronger than default AAP policy:
    # it is assumed to see the live target version, not just job metadata.
    if strategy == 'job_time_policy' and not matches_initial_evidence():
        world.history.append('blocked:job_time_policy')
        return Attempt('blocked_at_job', False, False, False, False)
    inject('after_job_policy')

    if strategy == 'preaction_check' and not matches_initial_evidence():
        world.history.append('blocked:preaction')
        return Attempt('blocked_at_preaction', False, False, False, False)
    inject('after_preaction')

    if strategy == 'bound_and_verified':
        # This is an abstract atomic target-side conditional mutation: it is
        # NOT something Ansible or OPA automatically guarantees.
        if not matches_initial_evidence():
            world.history.append('blocked:atomic_version_check')
            return Attempt('blocked_at_mutation', False, False, False, False)

    stale = not matches_initial_evidence()
    world.restart(evidence)
    inject('after_action')

    if strategy == 'bound_and_verified':
        if world.status != evidence.desired_status:
            world.history.append('detected:postcondition_failure')
            return Attempt('postcondition_failed', True, stale, False, False)
        world.completed_events.add(evidence.event_id)
        world.history.append('verified:expected_postcondition')
        return Attempt('verified_success', True, stale, False, True)

    false_success = stale or world.status != evidence.desired_status
    world.history.append('reported:job_success_without_final_state_proof')
    return Attempt('reported_success', True, stale, false_success, not false_success)


def run_trial(strategy: Strategy, scenario: Scenario) -> Trial:
    world = World()
    evidence = Evidence('event-0001', world.target, world.status, world.version, 'healthy')
    world.history.append('approved:evidence_sha256=' + evidence.digest[:16])
    attempts = [run_once(strategy, world, evidence, scenario) for _ in range(scenario.repeats)]
    return Trial(
        scenario.name, strategy, ','.join(a.disposition for a in attempts),
        world.mutations, sum(a.stale_mutation for a in attempts),
        sum(a.false_success for a in attempts), world.status, world.version,
        world.history.copy(),
    )


def matrix() -> list[Trial]:
    return [run_trial(strategy, scenario) for scenario in SCENARIOS for strategy in STATEGIES]


def summary() -> list[dict]:
    trials = matrix()
    return [{
        'strategy': strategy,
        'scenarios': len(SCENARIOS),
        'mutations': sum(t.mutations for t in trials if t.strategy == strategy),
        'stale_mutations': sum(t.stale_mutations for t in trials if t.strategy == strategy),
        'false_successes': sum(t.false_successes for t in trials if t.strategy == strategy),
        'postcondition_failures_detected': sum('postcondition_failed' in t.outcome for t in trials if t.strategy == strategy),
        'duplicate_mutations': next(t.mutations - 1 for t in trials if t.strategy == strategy and t.scenario == 'duplicate_event'),
    } for strategy in STATEGIES]
