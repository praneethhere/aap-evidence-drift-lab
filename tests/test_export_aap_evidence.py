import unittest
from scripts.export_aap_evidence import allowlisted_dict, validate_pair, relevant_events

class EvidenceTests(unittest.TestCase):
    def test_only_allowlisted_scalars(self):
        result = allowlisted_dict({"http_status": "409", "Authorization": "SECRET",
                                   "disposition": "stale_precondition", "replicas": 4,
                                   "obj": {"key": "bad"}},
                                  {"http_status", "disposition", "replicas", "obj"})
        self.assertEqual(result, {"http_status": "409", "disposition": "stale_precondition", "replicas": 4})

    def test_nonsecret_debug_event_selected(self):
        class Fake:
            def call(self, method, path):
                return {"next": None, "results": [
                    {"event": "runner_on_ok", "counter": 10, "event_data": {
                        "task": "Report nonsecret rejection evidence",
                        "res": {"msg": {"http_status": "409", "disposition": "stale_precondition",
                                        "replicas": "4", "token": "SECRET"}}}},
                    {"event": "runner_on_ok", "event_data": {
                        "task": "Capture scale subresource", "res": {"msg": "SECRET"}}},
                ]}
        out = relevant_events(Fake(), 69)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["nonsecret_debug"],
                         {"http_status": "409", "disposition": "stale_precondition", "replicas": "4"})

    def make_pair(self):
        from datetime import datetime, timedelta, timezone
        def case(kind, expected, initial_version):
            base = datetime(2026, 11, 16, 21, 0, tzinfo=timezone.utc)
            keys = ("capture", "approve_capture", "precheck", "experimental_hold", "action", "verify")
            steps = {}
            for i, key in enumerate(keys):
                item = {"status": "successful", "id": i + 1,
                        "started": (base + timedelta(seconds=2*i+1)).isoformat(),
                        "finished": (base + timedelta(seconds=2*i+2)).isoformat(),
                        "artifacts": {}, "relevant_events": []}
                if key in ("approve_capture", "experimental_hold"):
                    item["timed_out"] = False
                steps[key] = item
            steps["capture"]["artifacts"] = {"edk_approved_replicas": 2,
                "edk_approved_version": initial_version, "edk_approved_uid": "same-uid"}
            steps["action"]["artifacts"] = {"edk_action_kind": kind,
                "edk_expected_replicas_after": expected}
            steps["verify"]["relevant_events"] = [{"event": "runner_on_ok",
                "task": "Report the observation with scope limitation",
                "nonsecret_debug": {"path": kind, "approved_version": initial_version,
                    "final_version": str(int(initial_version) + 100),
                    "final_replicas": str(expected),
                    "result": "final_state_matches_expected_value_at_observation_instant",
                    "limitation": "does_not_establish_stable_readiness_or_future_correctness"}}]
            return {"status": "successful", "started": base.isoformat(),
                    "finished": (base + timedelta(seconds=20)).isoformat(),
                    "steps": steps}
        u, g = case("unchecked", 3, "2605"), case("guarded", 4, "46988")
        g["steps"]["action"]["relevant_events"] = [{
            "event": "runner_on_ok", "task": "Report nonsecret rejection evidence",
            "nonsecret_debug": {"http_status": 409,
                "disposition": "stale_precondition", "replicas": 4},
        }]
        return u, g

    def test_pair_validation(self):
        u, g = self.make_pair()
        check = validate_pair(u, g)
        self.assertTrue(check["guarded_409_debug_directly_observed"])
        self.assertTrue(check["verifier_observations_verified"])
        self.assertTrue(check["step_chronology_verified"])
        g["steps"]["action"]["relevant_events"] = []
        self.assertFalse(validate_pair(u, g)["guarded_409_debug_directly_observed"])

    def test_rejects_missing_final_verifier_event(self):
        u, g = self.make_pair()
        g["steps"]["verify"]["relevant_events"] = []
        with self.assertRaisesRegex(ValueError, "final verifier observation"):
            validate_pair(u, g)

    def test_rejects_tampered_final_observation(self):
        u, g = self.make_pair()
        u["steps"]["verify"]["relevant_events"][0]["nonsecret_debug"]["final_replicas"] = "4"
        with self.assertRaisesRegex(ValueError, "final replica observation disagrees"):
            validate_pair(u, g)

    def test_rejects_broken_step_order(self):
        u, g = self.make_pair()
        u["steps"]["action"]["started"] = "2026-11-16T21:00:00+00:00"
        with self.assertRaisesRegex(ValueError, "timestamps out of order"):
            validate_pair(u, g)

    def test_rejects_timed_out_approval(self):
        u, g = self.make_pair()
        g["steps"]["experimental_hold"]["timed_out"] = True
        with self.assertRaisesRegex(ValueError, "timed out"):
            validate_pair(u, g)

if __name__ == "__main__":
    unittest.main()
