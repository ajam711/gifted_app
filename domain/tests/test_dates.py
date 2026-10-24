"""ImportantDate: month+day, not a stale calendar date.

The Home badge is "next date for the *other* person". These tests pin
that, plus recurring vs one-off.
"""

from datetime import date

from domain.errors import Invalid, NotFound
from domain.store import InMemoryStore
from domain.tests.harness import NOW, DomainTestCase, two_people


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

    def test_soonest_date_wins(self) -> None:
        """"Next" is the earliest occurrence, not the first row stored."""

        store, ada, bea = two_people()
        store.add_important_date(bea.id, "Anniversary", month=12, day=1)
        store.add_important_date(bea.id, "Birthday", month=9, day=15)
        upcoming = store.next_partner_important_date(ada.id, on=date(2026, 9, 3))
        self.assertIsNotNone(upcoming)
        assert upcoming is not None
        self.assertEqual(upcoming.label, "Birthday")
        self.assertEqual(upcoming.days, 12)

    def test_same_day_keeps_the_earlier_record(self) -> None:
        """Two occurrences on one day: the one recorded first keeps the label."""

        store, ada, bea = two_people()
        store.add_important_date(bea.id, "Birthday", month=9, day=15)
        store.add_important_date(bea.id, "Anniversary", month=9, day=15)
        upcoming = store.next_partner_important_date(ada.id, on=date(2026, 9, 3))
        self.assertIsNotNone(upcoming)
        assert upcoming is not None
        self.assertEqual(upcoming.label, "Birthday")

    def test_today_is_zero_days(self) -> None:
        """A date that falls on ``on`` is upcoming, in 0 days, not next year."""

        store, ada, bea = two_people()
        store.add_important_date(bea.id, "Birthday", month=9, day=3)
        upcoming = store.next_partner_important_date(ada.id, on=date(2026, 9, 3))
        self.assertIsNotNone(upcoming)
        assert upcoming is not None
        self.assertEqual(upcoming.on, date(2026, 9, 3))
        self.assertEqual(upcoming.days, 0)

    def test_one_off_future_is_upcoming(self) -> None:
        store, ada, bea = two_people()
        store.add_important_date(bea.id, "Housewarming", month=10, day=1, year=2026)
        upcoming = store.next_partner_important_date(ada.id, on=date(2026, 9, 3))
        self.assertIsNotNone(upcoming)
        assert upcoming is not None
        self.assertEqual(upcoming.on, date(2026, 10, 1))
        self.assertEqual(upcoming.days, 28)

    def test_past_one_off_does_not_hide_a_later_birthday(self) -> None:
        store, ada, bea = two_people()
        store.add_important_date(bea.id, "Housewarming", month=1, day=1, year=2026)
        store.add_important_date(bea.id, "Birthday", month=9, day=15)
        upcoming = store.next_partner_important_date(ada.id, on=date(2026, 9, 3))
        self.assertIsNotNone(upcoming)
        assert upcoming is not None
        self.assertEqual(upcoming.label, "Birthday")

    def test_third_person_date_cannot_steal_the_badge(self) -> None:
        """Lookup of the other person goes through Connection, not Person rows."""

        store, ada, bea = two_people()
        cal = store.add_person("Cal", "cal@example.com")
        store.add_important_date(cal.id, "Cal day", month=9, day=4)
        store.add_important_date(bea.id, "Birthday", month=9, day=15)
        upcoming = store.next_partner_important_date(ada.id, on=date(2026, 9, 3))
        self.assertIsNotNone(upcoming)
        assert upcoming is not None
        self.assertEqual(upcoming.label, "Birthday")
        self.assertEqual(upcoming.days, 12)

    def test_home_badges_without_a_partner_have_no_date(self) -> None:
        store = InMemoryStore(now=lambda: NOW)
        ada = store.add_person("Ada", "ada@example.com")
        badges = store.home_badges(ada.id, date(2026, 9, 3))
        self.assertEqual(badges.needs_you, 0)
        self.assertIsNone(badges.upcoming)

    def test_next_date_requires_a_partner(self) -> None:
        store = InMemoryStore(now=lambda: NOW)
        ada = store.add_person("Ada", "ada@example.com")
        with self.assertRaises(NotFound):
            store.next_partner_important_date(ada.id, on=date(2026, 9, 3))
