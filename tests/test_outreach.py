import unittest

from scout.models import Finding
from scout.outreach import suggest_outreach_points


class SuggestOutreachPointsTests(unittest.TestCase):
    def test_includes_the_real_evidence_and_source_not_generic_advice(self):
        finding = Finding(
            kind="case_study_role_signal", evidence="Our full-stack team built a customer portal.",
            source_url="https://acme.test/case-studies", confidence="medium",
            suggestion="Use this work example to ask how the capability is delivered.",
        )

        points = suggest_outreach_points(finding)

        self.assertIn("Our full-stack team built a customer portal.", points)
        self.assertIn("https://acme.test/case-studies", points)

    def test_two_companies_same_kind_produce_different_output(self):
        # This is the actual point of the feature: personalized raw material
        # per company, not a category-level writing tip repeated everywhere.
        finding_a = Finding(kind="advertised_role_signal", evidence="Web Developer role", source_url="https://a.test/careers", confidence="high", suggestion="x")
        finding_b = Finding(kind="advertised_role_signal", evidence="Data Analyst role", source_url="https://b.test/careers", confidence="high", suggestion="x")

        self.assertNotEqual(suggest_outreach_points(finding_a), suggest_outreach_points(finding_b))

    def test_does_not_read_as_a_send_ready_message(self):
        # Not a drafted message: no greeting, no closing question composed
        # as if ready to paste into an email client.
        finding = Finding(
            kind="seo_metadata_gap", evidence="Homepage <title> reads \"Acme (Copy)\"",
            source_url="https://acme.test", confidence="high",
            suggestion="Mention this small, easily fixed detail as a low-pressure opener.",
        )

        points = suggest_outreach_points(finding)

        self.assertNotIn("Hi -", points)
        self.assertNotIn("would you be open", points.lower())

    def test_team_contact_signal_has_no_outreach_angle(self):
        finding = Finding(
            kind="team_contact_signal",
            evidence='"CTO" found on this page, near the text: "Meet Jane Doe, our CTO"',
            source_url="https://acme.test/about",
            confidence="medium",
            suggestion="Possible contact: Jane Doe (CTO) - verify before reaching out.",
        )

        self.assertIsNone(suggest_outreach_points(finding))

    def test_potential_role_related_need_uses_its_own_already_personalized_suggestion(self):
        # finding.suggestion here is hidden_need_finding()'s own contact/MSP-
        # aware text (see test_research.py) - already specific to this
        # company, so the framing tip is added on top, not the raw evidence
        # (which is meta-commentary, not real site content - see the
        # confirmed bug this project fixed by not quoting it).
        finding = Finding(
            kind="potential_role_related_need",
            evidence="The company site contains public business information consistent with the education sector; no matching Technology and digital delivery role was found in scanned pages.",
            source_url="https://acme.test",
            confidence="low",
            suggestion="A likely technical contact was already found on this site - consider directing the question to them specifically.",
        )

        points = suggest_outreach_points(finding)

        self.assertIn("A likely technical contact was already found", points)
        self.assertIn("tentative", points)
        self.assertNotIn(finding.evidence, points)

    def test_unknown_finding_kind_returns_none_not_a_crash(self):
        finding = Finding(kind="some_future_kind", evidence="x", source_url="https://acme.test", confidence="low", suggestion="x")

        self.assertIsNone(suggest_outreach_points(finding))


if __name__ == "__main__":
    unittest.main()
