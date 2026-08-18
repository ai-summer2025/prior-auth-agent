#!/usr/bin/env python3
"""
Comprehensive test suite for prior_auth_agent.py
All tests must pass with exit code 0.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure the module is importable from the same directory
sys.path.insert(0, str(Path(__file__).parent))

from prior_auth_agent import (
    AuthStatus,
    AuthRequest,
    ClinicalNote,
    CriteriaEvaluator,
    AppealDrafter,
    PriorAuthAgent,
    StateStore,
    TRANSITIONS,
    make_sample_note,
    validate_transition,
)


class TestAuthStatusTransitions(unittest.TestCase):
    def test_valid_transitions(self):
        validate_transition(AuthStatus.PENDING, AuthStatus.REVIEWING)
        validate_transition(AuthStatus.REVIEWING, AuthStatus.APPROVED)
        validate_transition(AuthStatus.REVIEWING, AuthStatus.DENIED)
        validate_transition(AuthStatus.DENIED, AuthStatus.APPEALING)
        validate_transition(AuthStatus.APPEALING, AuthStatus.RESOLVED)

    def test_invalid_transition_raises(self):
        with self.assertRaises(ValueError):
            validate_transition(AuthStatus.PENDING, AuthStatus.APPROVED)
        with self.assertRaises(ValueError):
            validate_transition(AuthStatus.APPROVED, AuthStatus.DENIED)
        with self.assertRaises(ValueError):
            validate_transition(AuthStatus.RESOLVED, AuthStatus.PENDING)

    def test_all_statuses_in_transitions(self):
        for status in AuthStatus:
            self.assertIn(status, TRANSITIONS, f"{status} missing from TRANSITIONS")


class TestClinicalNoteValidation(unittest.TestCase):
    def _base_note(self, **overrides):
        defaults = dict(
            patient_id="PT-TEST",
            patient_age=40,
            diagnosis_codes=["M54.5"],
            procedure_code="72148",
            procedure_name="MRI",
            clinical_narrative="Patient has back pain that has persisted for several weeks despite treatment.",
            ordering_provider="Dr. Test",
            prior_treatments=["PT"],
            urgency="routine",
        )
        defaults.update(overrides)
        return ClinicalNote(**defaults)

    def test_valid_note_has_no_errors(self):
        note = self._base_note()
        self.assertEqual(note.validate(), [])

    def test_invalid_age_flagged(self):
        note = self._base_note(patient_age=-1)
        errors = note.validate()
        self.assertTrue(any("age" in e.lower() for e in errors))

    def test_missing_diagnosis_codes(self):
        note = self._base_note(diagnosis_codes=[])
        errors = note.validate()
        self.assertTrue(any("diagnosis" in e.lower() for e in errors))

    def test_bad_procedure_code(self):
        note = self._base_note(procedure_code="ABC")
        errors = note.validate()
        self.assertTrue(any("procedure" in e.lower() or "format" in e.lower() for e in errors))

    def test_empty_narrative(self):
        note = self._base_note(clinical_narrative="   ")
        errors = note.validate()
        self.assertTrue(any("narrative" in e.lower() for e in errors))

    def test_invalid_urgency(self):
        note = self._base_note(urgency="whenever")
        errors = note.validate()
        self.assertTrue(any("urgency" in e.lower() for e in errors))

    def test_valid_urgency_values(self):
        for urgency in ("routine", "urgent", "emergent"):
            note = self._base_note(urgency=urgency)
            self.assertEqual(note.validate(), [], f"Expected no errors for urgency={urgency}")


class TestCriteriaEvaluator(unittest.TestCase):
    def setUp(self):
        self.evaluator = CriteriaEvaluator()

    def test_mri_approved_scenario(self):
        note = make_sample_note("mri_approved")
        result = self.evaluator.evaluate("REQ-001", note)
        self.assertEqual(result.recommended_status, AuthStatus.APPROVED)
        self.assertTrue(result.all_required_met)

    def test_mri_denied_scenario(self):
        note = make_sample_note("mri_denied")
        result = self.evaluator.evaluate("REQ-002", note)
        self.assertEqual(result.recommended_status, AuthStatus.DENIED)
        self.assertFalse(result.all_required_met)
        self.assertGreater(len(result.failed_required), 0)

    def test_knee_approved_scenario(self):
        note = make_sample_note("knee_approved")
        result = self.evaluator.evaluate("REQ-003", note)
        self.assertEqual(result.recommended_status, AuthStatus.APPROVED)

    def test_cgm_approved_scenario(self):
        note = make_sample_note("cgm_approved")
        result = self.evaluator.evaluate("REQ-004", note)
        self.assertEqual(result.recommended_status, AuthStatus.APPROVED)

    def test_unknown_cpt_uses_default_policy(self):
        note = ClinicalNote(
            patient_id="PT-X",
            patient_age=50,
            diagnosis_codes=["Z99.89"],
            procedure_code="99999",
            procedure_name="Unknown Procedure",
            clinical_narrative=(
                "Patient has a well-documented medical condition that has been evaluated comprehensively by the treating provider. "
                "Conservative management has been attempted over an extended period without adequate response to treatment. "
                "The requested intervention is medically necessary based on clinical findings documented in the record. "
                "The treating provider has reviewed all available diagnostic information and determined this procedure "
                "is the most appropriate next step in the patient care plan given the clinical circumstances."
            ),
            ordering_provider="Dr. Default",
            prior_treatments=[],
            urgency="routine",
        )
        result = self.evaluator.evaluate("REQ-005", note)
        # Should use default policy — both diagnosis and narrative present
        self.assertEqual(result.recommended_status, AuthStatus.APPROVED)

    def test_result_has_required_fields(self):
        note = make_sample_note("mri_approved")
        result = self.evaluator.evaluate("REQ-006", note)
        self.assertEqual(result.request_id, "REQ-006")
        self.assertEqual(result.procedure_code, "72148")
        self.assertIsInstance(result.criteria_results, list)
        self.assertGreater(len(result.criteria_results), 0)
        self.assertIsInstance(result.summary, str)
        self.assertIsInstance(result.evaluated_at, str)

    def test_check_conservative_treatment(self):
        note = ClinicalNote(
            patient_id="PT-C",
            patient_age=45,
            diagnosis_codes=["M54.5"],
            procedure_code="72148",
            procedure_name="MRI",
            clinical_narrative="Physical therapy for 8 weeks was completed.",
            ordering_provider="Dr. A",
            prior_treatments=["PT x8 weeks"],
            urgency="routine",
        )
        met, evidence = self.evaluator.check_conservative_treatment(note)
        self.assertTrue(met)

    def test_check_conservative_treatment_fails_when_absent(self):
        note = ClinicalNote(
            patient_id="PT-D",
            patient_age=30,
            diagnosis_codes=["M54.5"],
            procedure_code="72148",
            procedure_name="MRI",
            clinical_narrative="Patient has back pain.",
            ordering_provider="Dr. B",
            prior_treatments=[],
            urgency="routine",
        )
        met, evidence = self.evaluator.check_conservative_treatment(note)
        self.assertFalse(met)

    def test_diabetes_diagnosis_check(self):
        note = ClinicalNote(
            patient_id="PT-E",
            patient_age=50,
            diagnosis_codes=["E11.9"],
            procedure_code="95251",
            procedure_name="CGM",
            clinical_narrative="Insulin dependent.",
            ordering_provider="Dr. C",
            prior_treatments=["Insulin"],
            urgency="routine",
        )
        met, _ = self.evaluator.check_diabetes_diagnosis(note)
        self.assertTrue(met)

    def test_no_diabetes_diagnosis_fails(self):
        note = ClinicalNote(
            patient_id="PT-F",
            patient_age=50,
            diagnosis_codes=["J45.20"],
            procedure_code="95251",
            procedure_name="CGM",
            clinical_narrative="Insulin dependent.",
            ordering_provider="Dr. D",
            prior_treatments=[],
            urgency="routine",
        )
        met, _ = self.evaluator.check_diabetes_diagnosis(note)
        self.assertFalse(met)


class TestAppealDrafter(unittest.TestCase):
    def setUp(self):
        self.drafter = AppealDrafter()
        self.evaluator = CriteriaEvaluator()

    def test_appeal_draft_produced_for_denied(self):
        note = make_sample_note("mri_denied")
        ev = self.evaluator.evaluate("APPEAL-001", note)
        self.assertEqual(ev.recommended_status, AuthStatus.DENIED)
        letter = self.drafter.draft("APPEAL-001", note, ev)
        self.assertIn("APPEAL", letter.upper())
        self.assertIn("APPEAL-001", letter)
        self.assertIn(note.patient_id, letter)
        self.assertIn(note.procedure_code, letter)

    def test_appeal_raises_for_approved(self):
        note = make_sample_note("mri_approved")
        ev = self.evaluator.evaluate("APPEAL-002", note)
        self.assertEqual(ev.recommended_status, AuthStatus.APPROVED)
        with self.assertRaises(ValueError):
            self.drafter.draft("APPEAL-002", note, ev)

    def test_appeal_contains_ordering_provider(self):
        note = make_sample_note("mri_denied")
        ev = self.evaluator.evaluate("APPEAL-003", note)
        letter = self.drafter.draft("APPEAL-003", note, ev)
        self.assertIn(note.ordering_provider, letter)

    def test_appeal_contains_diagnosis_codes(self):
        note = make_sample_note("mri_denied")
        ev = self.evaluator.evaluate("APPEAL-004", note)
        letter = self.drafter.draft("APPEAL-004", note, ev)
        for code in note.diagnosis_codes:
            self.assertIn(code, letter)


class TestStateStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.store_path = Path(self.tmp.name)
        # Start fresh
        self.store_path.unlink(missing_ok=True)

    def tearDown(self):
        self.store_path.unlink(missing_ok=True)

    def _make_agent(self):
        return PriorAuthAgent(
            store=StateStore(self.store_path),
            verbose=False,
        )

    def test_put_and_get(self):
        agent = self._make_agent()
        note = make_sample_note("mri_approved")
        req = agent.submit(note)
        retrieved = agent.store.get(req.request_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.request_id, req.request_id)

    def test_persistence_across_store_instances(self):
        agent1 = self._make_agent()
        note = make_sample_note("cgm_approved")
        req = agent1.submit(note)
        rid = req.request_id

        # New store instance reading same file
        store2 = StateStore(self.store_path)
        retrieved = store2.get(rid)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.request_id, rid)

    def test_by_status_filter(self):
        agent = self._make_agent()
        note1 = make_sample_note("mri_approved")
        note2 = make_sample_note("mri_denied")
        r1 = agent.submit(note1)
        r2 = agent.submit(note2)

        pending = agent.store.by_status(AuthStatus.PENDING)
        self.assertEqual(len(pending), 2)

    def test_all_returns_list(self):
        agent = self._make_agent()
        self.assertIsInstance(agent.store.all(), list)


class TestPriorAuthAgent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.store_path = Path(self.tmp.name)
        self.store_path.unlink(missing_ok=True)
        self.agent = PriorAuthAgent(
            store=StateStore(self.store_path),
            verbose=False,
        )

    def tearDown(self):
        self.store_path.unlink(missing_ok=True)

    def test_submit_creates_pending_request(self):
        note = make_sample_note("mri_approved")
        req = self.agent.submit(note)
        self.assertEqual(req.status, AuthStatus.PENDING)
        self.assertIsNotNone(req.request_id)
        self.assertGreater(len(req.history), 0)

    def test_submit_invalid_note_raises(self):
        note = ClinicalNote(
            patient_id="", patient_age=-5,
            diagnosis_codes=[], procedure_code="BAD",
            procedure_name="Test", clinical_narrative="",
            ordering_provider="", prior_treatments=[], urgency="invalid",
        )
        with self.assertRaises(ValueError):
            self.agent.submit(note)

    def test_full_workflow_approved(self):
        note = make_sample_note("mri_approved")
        req = self.agent.run_full_workflow(note)
        self.assertEqual(req.status, AuthStatus.APPROVED)
        self.assertIsNotNone(req.evaluation)
        self.assertIsNone(req.appeal_letter)

    def test_full_workflow_denied_generates_appeal(self):
        note = make_sample_note("mri_denied")
        req = self.agent.run_full_workflow(note)
        self.assertEqual(req.status, AuthStatus.APPEALING)
        self.assertIsNotNone(req.appeal_letter)
        self.assertIsNotNone(req.appeal_submitted_at)

    def test_run_evaluation_transitions_correctly(self):
        note = make_sample_note("knee_approved")
        req = self.agent.submit(note)
        self.assertEqual(req.status, AuthStatus.PENDING)
        req = self.agent.run_evaluation(req.request_id)
        self.assertIn(req.status, [AuthStatus.APPROVED, AuthStatus.DENIED])

    def test_resolve_request(self):
        note = make_sample_note("mri_approved")
        req = self.agent.run_full_workflow(note)
        req = self.agent.resolve(req.request_id, "Auth approved after review")
        self.assertEqual(req.status, AuthStatus.RESOLVED)
        self.assertEqual(req.resolution_note, "Auth approved after review")

    def test_get_nonexistent_request_raises(self):
        with self.assertRaises(KeyError):
            self.agent.run_evaluation("DOES-NOT-EXIST")

    def test_status_report_contains_key_fields(self):
        note = make_sample_note("cgm_approved")
        req = self.agent.run_full_workflow(note)
        report = self.agent.get_status_report(req.request_id)
        self.assertIn(req.request_id, report)
        self.assertIn(note.patient_id, report)

    def test_dashboard_returns_string(self):
        note = make_sample_note("mri_approved")
        self.agent.run_full_workflow(note)
        dash = self.agent.dashboard()
        self.assertIsInstance(dash, str)
        self.assertIn("APPROVED", dash)

    def test_dashboard_empty_store(self):
        dash = self.agent.dashboard()
        self.assertIn("No prior", dash)

    def test_history_grows_with_transitions(self):
        note = make_sample_note("mri_denied")
        req = self.agent.submit(note)
        self.assertEqual(len(req.history), 1)
        req = self.agent.run_evaluation(req.request_id)
        self.assertEqual(len(req.history), 3)  # submit + reviewing + denied
        req = self.agent.submit_appeal(req.request_id)
        self.assertEqual(len(req.history), 4)

    def test_multiple_requests_tracked_independently(self):
        r1 = self.agent.run_full_workflow(make_sample_note("mri_approved"))
        r2 = self.agent.run_full_workflow(make_sample_note("mri_denied"))
        self.assertNotEqual(r1.request_id, r2.request_id)
        self.assertEqual(r1.status, AuthStatus.APPROVED)
        self.assertEqual(r2.status, AuthStatus.APPEALING)


class TestSampleNotes(unittest.TestCase):
    def test_all_scenarios_loadable(self):
        for scenario in ["mri_approved", "mri_denied", "knee_approved", "cgm_approved"]:
            note = make_sample_note(scenario)
            self.assertIsInstance(note, ClinicalNote)
            self.assertEqual(note.validate(), [], f"Validation failed for {scenario}")

    def test_unknown_scenario_raises(self):
        with self.assertRaises(ValueError):
            make_sample_note("nonexistent_scenario")


class TestAuthRequestSerialization(unittest.TestCase):
    def test_round_trip_serialization(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        tmp.close()
        path = Path(tmp.name)
        path.unlink(missing_ok=True)

        agent = PriorAuthAgent(store=StateStore(path), verbose=False)
        note = make_sample_note("mri_denied")
        req = agent.run_full_workflow(note)
        rid = req.request_id

        # Reload from disk
        store2 = StateStore(path)
        req2 = store2.get(rid)

        self.assertIsNotNone(req2)
        self.assertEqual(req2.request_id, rid)
        self.assertEqual(req2.status, AuthStatus.APPEALING)
        self.assertIsNotNone(req2.appeal_letter)
        self.assertIsNotNone(req2.evaluation)

        path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
