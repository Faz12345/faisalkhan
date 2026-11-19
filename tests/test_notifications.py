import unittest

from app.backend.notifications import (
    E164_PHONE_PATTERN,
    _visit_call_twiml,
    build_visit_call_message,
)


class PatientCallNotificationTests(unittest.TestCase):
    def test_visit_call_includes_appointment_and_clinical_details(self):
        message = build_visit_call_message({
            "patient_name": "Asha",
            "date": "2026-12-01",
            "time_slot": "10:30",
            "reason": "Tooth pain",
            "treatment": "Filling",
        })

        self.assertIn("Asha", message)
        self.assertIn("2026-12-01 at 10:30", message)
        self.assertIn("reason for your visit is Tooth pain", message)
        self.assertIn("Treatment: Filling", message)

    def test_visit_call_explains_treatment_is_not_yet_set(self):
        message = build_visit_call_message({
            "date": "2026-12-01",
            "time_slot": "10:30",
            "reason": "Tooth pain",
        })

        self.assertIn(
            "Your doctor will discuss the appropriate treatment with you during your visit.",
            message,
        )

    def test_twiml_escapes_patient_content(self):
        twiml = _visit_call_twiml("Problem: pain & swelling <today>")

        self.assertIn("pain &amp; swelling &lt;today&gt;", twiml)
        self.assertNotIn("pain & swelling <today>", twiml)

    def test_phone_number_must_be_e164(self):
        self.assertTrue(E164_PHONE_PATTERN.fullmatch("+14155552671"))
        self.assertFalse(E164_PHONE_PATTERN.fullmatch("4155552671"))


if __name__ == "__main__":
    unittest.main()
