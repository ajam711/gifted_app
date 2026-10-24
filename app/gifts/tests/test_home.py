"""Home badges: "N need you" and the partner's next date.

The owner's Home HTML is part of the secrecy check. A claim must not
change the badge text or the response size.
"""

import json
import re
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone as dj_timezone

from gifts.models import Connection, ImportantDate, Item, Person
from gifts.store import DjangoStore
from gifts.tests.harness import PASSWORD, two_people

NOW = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)
ON = NOW.date()
_CSRF = re.compile(rb'name="csrfmiddlewaretoken" value="[^"]+"')


def _dump(badges) -> str:
    return json.dumps(
        asdict(badges),
        default=lambda value: value.isoformat(),
        sort_keys=True,
    )


class DjangoStoreBadgeTests(TestCase):
    def setUp(self) -> None:
        self.ada, self.bea = two_people()
        self.store = DjangoStore(now=lambda: NOW)

    def test_needs_you_and_partner_birthday(self) -> None:
        """V1.md example, through the Django store: 3 Sep → 15 Sep is 12 days."""

        self.store.add_item(self.bea.id, self.ada.id, "Suggestion")
        self.store.add_item(self.ada.id, self.bea.id, "On Bea's list")
        liked = self.store.add_item(self.ada.id, self.ada.id, "Liked")
        ImportantDate.objects.create(
            person=self.bea, label="Birthday", month=9, day=15
        )
        ImportantDate.objects.create(
            person=self.ada, label="Ada day", month=9, day=4
        )
        badges = self.store.home_badges(self.ada.id, ON)
        self.assertEqual(badges.needs_you, 1)
        self.assertIsNotNone(badges.upcoming)
        assert badges.upcoming is not None
        self.assertEqual(badges.upcoming.label, "Birthday")
        self.assertEqual(badges.upcoming.on, ON.replace(day=15))
        self.assertEqual(badges.upcoming.days, 12)
        self.assertFalse(hasattr(badges, "claimed_by_id"))

        before = _dump(badges)
        self.store.claim(self.bea.id, liked.id)
        after = _dump(self.store.home_badges(self.ada.id, ON))
        self.assertEqual(before, after)
        self.assertEqual(len(before.encode()), len(after.encode()))

    def test_same_day_keeps_the_earlier_row(self) -> None:
        ImportantDate.objects.create(
            person=self.bea, label="Birthday", month=9, day=15
        )
        ImportantDate.objects.create(
            person=self.bea, label="Anniversary", month=9, day=15
        )
        upcoming = self.store.next_partner_important_date(self.ada.id, ON)
        self.assertIsNotNone(upcoming)
        assert upcoming is not None
        self.assertEqual(upcoming.label, "Birthday")

    def test_third_person_date_is_ignored(self) -> None:
        cal_user = User.objects.create_user("cal", "cal@example.com", PASSWORD)
        cal = Person.objects.create(
            user=cal_user, name="Cal", email="cal@example.com"
        )
        ImportantDate.objects.create(person=cal, label="Cal day", month=9, day=4)
        ImportantDate.objects.create(
            person=self.bea, label="Birthday", month=9, day=15
        )
        upcoming = self.store.next_partner_important_date(self.ada.id, ON)
        self.assertIsNotNone(upcoming)
        assert upcoming is not None
        self.assertEqual(upcoming.label, "Birthday")


class HomeBadgeHTTPTests(TestCase):
    def setUp(self) -> None:
        self.ada, self.bea = two_people()
        self.owner = Client()
        self.owner.login(username="ada", password=PASSWORD)
        self.shopper = Client()
        self.shopper.login(username="bea", password=PASSWORD)

    def _date(self, person, label: str, days_away: int, *, year: bool) -> None:
        target = dj_timezone.localdate() + timedelta(days=days_away)
        ImportantDate.objects.create(
            person=person,
            label=label,
            month=target.month,
            day=target.day,
            year=target.year if year else None,
        )

    def _doors(self, content: bytes) -> tuple[bytes, bytes]:
        content = _CSRF.sub(b"CSRF", content)
        start = content.index(b'<nav class="doors">')
        end = content.index(b"</nav>") + len(b"</nav>")
        nav = content[start:end]
        my_door, their_door = nav.split(b"</a>")[:2]
        return my_door, their_door

    def test_home_without_a_partner_still_loads(self) -> None:
        Connection.objects.all().delete()
        response = self.owner.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your list")
        self.assertNotContains(response, "Their list")
        self.assertNotContains(response, " days")

    def test_login_required(self) -> None:
        response = Client().get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_needs_you_is_pending_on_my_list_only(self) -> None:
        self.shopper.post("/their-list/add/", {"name": "For Ada"})
        self.shopper.post("/their-list/add/", {"name": "Also for Ada"})
        self.owner.post("/their-list/add/", {"name": "For Bea"})
        response = self.owner.get("/")
        my_door, their_door = self._doors(response.content)
        self.assertIn(b"2 need you", my_door)
        self.assertNotIn(b"need you", their_door)
        shopper_home = self.shopper.get("/")
        shopper_my, _shopper_their = self._doors(shopper_home.content)
        self.assertIn(b"1 need you", shopper_my)

    def test_needs_you_hidden_when_nothing_is_pending(self) -> None:
        self.owner.post("/my-list/add/", {"name": "Kettle"})
        response = self.owner.get("/")
        self.assertNotContains(response, "need you")

    def test_liking_clears_the_needs_you_badge(self) -> None:
        self.shopper.post("/their-list/add/", {"name": "Suggestion"})
        self.assertContains(self.owner.get("/"), "1 need you")
        item = Item.objects.get(name="Suggestion")
        self.owner.post(f"/my-list/{item.id}/react/", {"reaction": "liked"})
        self.assertNotContains(self.owner.get("/"), "need you")

    def test_partner_date_badge_uses_their_next_date(self) -> None:
        self._date(self.ada, "Ada day", 1, year=True)
        self._date(self.bea, "Birthday", 12, year=True)
        response = self.owner.get("/")
        my_door, their_door = self._doors(response.content)
        self.assertIn(b"Birthday in 12 days", their_door)
        self.assertNotIn(b"Ada day", my_door)
        self.assertNotIn(b"in ", my_door)
        bea_home = self.shopper.get("/")
        _bea_my, bea_their = self._doors(bea_home.content)
        self.assertIn(b"Ada day in 1 days", bea_their)
        self.assertNotIn(b"Birthday", bea_their)

    def test_no_date_means_no_date_badge(self) -> None:
        response = self.owner.get("/")
        self.assertNotContains(response, " days")

    def test_past_one_off_is_not_shown(self) -> None:
        ImportantDate.objects.create(
            person=self.bea, label="Housewarming", month=1, day=1, year=2000
        )
        response = self.owner.get("/")
        self.assertNotContains(response, "Housewarming")
        self.assertNotContains(response, " days")

    def test_claim_does_not_change_home_badges(self) -> None:
        """Rendered badges, and the whole Home response, ignore a claim."""

        self.shopper.post("/their-list/add/", {"name": "Suggestion"})
        liked = Item.objects.create(
            owner=self.ada,
            added_by=self.ada,
            name="Liked",
            reaction="liked",
            reacted_at=NOW,
            created_at=NOW,
        )
        self._date(self.bea, "Birthday", 12, year=True)
        before = self.owner.get("/")
        self.assertEqual(before.status_code, 200)
        before_body = _CSRF.sub(b"CSRF", before.content)
        self.shopper.post(f"/their-list/{liked.id}/claim/")
        after = self.owner.get("/")
        after_body = _CSRF.sub(b"CSRF", after.content)
        self.assertEqual(before_body, after_body)
        self.assertEqual(len(before_body), len(after_body))
        my_door, their_door = self._doors(after_body)
        self.assertIn(b"1 need you", my_door)
        self.assertIn(b"Birthday in 12 days", their_door)
        self.assertNotIn(b"claim", my_door.lower())
        self.assertNotIn(b"claim", their_door.lower())
        liked.refresh_from_db()
        self.assertEqual(liked.claimed_by_id, self.bea.id)
