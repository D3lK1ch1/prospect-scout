import unittest

from scout.models import CompanyResult, Finding
from scout.ranking import rank_companies, rank_reason


def company(name, status="needs_review", location_verified=False, location_checked=True, findings=None) -> CompanyResult:
    return CompanyResult(domain=f"https://{name}", name=name, status=status, location_verified=location_verified, location_checked=location_checked, findings=findings or [])


def finding(confidence: str, observed_at: str | None = None) -> Finding:
    return Finding(kind="advertised_role_signal", evidence="ev", source_url="https://x.test", confidence=confidence, suggestion="sugg", observed_at=observed_at)


class RankCompaniesTests(unittest.TestCase):
    def test_eligible_ranks_above_needs_review_regardless_of_input_order(self):
        weak = company("weak", status="needs_review")
        strong = company("strong", status="eligible", location_verified=True, findings=[finding("high")])

        ranked = rank_companies([weak, strong])

        self.assertEqual([c.name for c in ranked], ["strong", "weak"])

    def test_location_verified_breaks_ties_within_same_status(self):
        unverified = company("unverified", status="needs_review", location_verified=False)
        verified = company("verified", status="needs_review", location_verified=True)

        ranked = rank_companies([unverified, verified])

        self.assertEqual([c.name for c in ranked], ["verified", "unverified"])

    def test_more_and_higher_confidence_findings_rank_higher(self):
        fewer = company("fewer", status="eligible", location_verified=True, findings=[finding("low")])
        more = company("more", status="eligible", location_verified=True, findings=[finding("high"), finding("medium")])

        ranked = rank_companies([fewer, more])

        self.assertEqual([c.name for c in ranked], ["more", "fewer"])

    def test_ties_preserve_original_order(self):
        first = company("first")
        second = company("second")

        ranked = rank_companies([first, second])

        self.assertEqual([c.name for c in ranked], ["first", "second"])

    def test_more_recent_dated_evidence_ranks_higher_when_otherwise_tied(self):
        older = company("older", status="eligible", location_verified=True, findings=[finding("high", observed_at="2025-01-01")])
        newer = company("newer", status="eligible", location_verified=True, findings=[finding("high", observed_at="2026-08-01")])

        ranked = rank_companies([older, newer])

        self.assertEqual([c.name for c in ranked], ["newer", "older"])

    def test_no_dated_evidence_does_not_crash_and_ranks_below_a_dated_one(self):
        undated = company("undated", status="eligible", location_verified=True, findings=[finding("high")])
        dated = company("dated", status="eligible", location_verified=True, findings=[finding("high", observed_at="2026-08-01")])

        ranked = rank_companies([undated, dated])

        self.assertEqual([c.name for c in ranked], ["dated", "undated"])

    def test_unparseable_observed_at_does_not_crash_and_falls_back_to_no_date(self):
        garbage = company("garbage", status="eligible", location_verified=True, findings=[finding("high", observed_at="not-a-date")])
        dated = company("dated", status="eligible", location_verified=True, findings=[finding("high", observed_at="2026-08-01")])

        ranked = rank_companies([garbage, dated])

        self.assertEqual([c.name for c in ranked], ["dated", "garbage"])


class RankReasonTests(unittest.TestCase):
    def test_mentions_location_verification(self):
        self.assertIn("location verified", rank_reason(company("acme", location_verified=True)))
        self.assertIn("location not verified", rank_reason(company("acme", location_verified=False)))

    def test_mentions_high_confidence_findings_matching_requested_interest(self):
        reason = rank_reason(company("acme", findings=[finding("high"), finding("low")]))

        self.assertIn("1 high-confidence finding", reason)
        self.assertIn("1 other finding", reason)

    def test_no_findings_is_stated_plainly(self):
        self.assertIn("no findings", rank_reason(company("acme")))

    def test_skipped_location_check_says_so_instead_of_claiming_failure(self):
        reason = rank_reason(company("acme", location_checked=False))

        self.assertIn("location check skipped", reason)
        self.assertNotIn("location not verified", reason)

    def test_mentions_most_recent_dated_evidence_when_present(self):
        reason = rank_reason(company("acme", findings=[finding("high", observed_at="2025-01-01"), finding("high", observed_at="2026-08-01")]))

        self.assertIn("most recent dated evidence: 2026-08-01", reason)

    def test_omits_recency_mention_when_no_dated_evidence(self):
        reason = rank_reason(company("acme", findings=[finding("high")]))

        self.assertNotIn("most recent dated evidence", reason)


class RankKeyLocationSkipTests(unittest.TestCase):
    def test_skipped_check_does_not_rank_below_a_genuine_location_failure(self):
        failed_check = company("failed", status="eligible", location_verified=False, location_checked=True)
        skipped_check = company("skipped", status="eligible", location_verified=False, location_checked=False)

        ranked = rank_companies([failed_check, skipped_check])

        self.assertEqual([c.name for c in ranked], ["skipped", "failed"])


if __name__ == "__main__":
    unittest.main()
