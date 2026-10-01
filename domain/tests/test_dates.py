"""ImportantDate: month+day, not a stale calendar date.

The Home badge is "next date for the *other* person". These tests pin
that, plus recurring vs one-off.
"""

from datetime import date

from domain.errors import Invalid
from domain.tests.harness import DomainTestCase, two_people


class ImportantDateTests(DomainTestCase):
    def test_next_partner_date_recurring_birthday(self) -> None:
        """V1.md example: 3 Sep looking at a 15 Sep birthday → 12 days."""

        store, ada, bea = two_people()
        store.add_important_date(bea.id, "Birthday", month=9, day=15)
        upcoming = store.next_partner_important_date(ada.id, on=date(2026, 9, 3))
        self.assertIsNotNone(upcoming)
        assert upcoming is not None  # narrows the type for the checker
        self.assertEqual(upcoming.label, "Birthday")
        self.assertEqual(upcoming.on, date(2026, 9, 15))
        self.assertEqual(upcoming.days, 12)

    def test_recurring_rolls_to_next_year_after_the_day(self) -> None:
        """A birthday that already happened this year comes back next year."""

        store, ada, bea = two_people()
        store.add_important_date(bea.id, "Birthday", month=9, day=1)
        upcoming = store.next_partner_important_date(ada.id, on=date(2026, 9, 3))
        self.assertIsNotNone(upcoming)
        assert upcoming is not None
        self.assertEqual(upcoming.on, date(2027, 9, 1))

    def test_one_off_past_date_is_not_upcoming(self) -> None:
        """``year`` set means it does not recur. Past one-offs disappear."""

        store, ada, bea = two_people()
        store.add_important_date(bea.id, "Housewarming", month=1, day=1, year=2026)
        upcoming = store.next_partner_important_date(ada.id, on=date(2026, 9, 3))
        self.assertIsNone(upcoming)

    def test_does_not_use_viewer_dates(self) -> None:
        """Ada's home badge is Bea's next date, never Ada's own."""

        store, ada, bea = two_people()
        store.add_important_date(ada.id, "Ada birthday", month=9, day=10)
        store.add_important_date(bea.id, "Bea birthday", month=10, day=1)
        upcoming = store.next_partner_important_date(ada.id, on=date(2026, 9, 3))
        self.assertIsNotNone(upcoming)
        assert upcoming is not None
        self.assertEqual(upcoming.label, "Bea birthday")

    def test_rejects_bad_month(self) -> None:
        store, _ada, bea = two_people()
        with self.assertRaises(Invalid):
            store.add_important_date(bea.id, "Birthday", month=0, day=1)
