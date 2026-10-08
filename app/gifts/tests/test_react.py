from django.test import Client, TestCase

from gifts.models import Item
from gifts.tests.harness import PASSWORD, two_people


class ReactHTTPTests(TestCase):
    def setUp(self) -> None:
        self.ada, self.bea = two_people()

    def _login(self, username: str) -> None:
        ok = self.client.login(username=username, password=PASSWORD)
        self.assertTrue(ok)

    def test_login_required(self) -> None:
        response = self.client.post("/my-list/1/react/", {"reaction": "liked"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_my_list_groups_pending_and_liked(self) -> None:
        self._login("bea")
        self.client.post("/their-list/add/", {"name": "Suggestion"})
        self.client.logout()
        self._login("ada")
        self.client.post("/my-list/add/", {"name": "Kettle"})
        response = self.client.get("/my-list/")
        self.assertContains(response, "Needs a reaction")
        self.assertContains(response, "Suggestion")
        self.assertContains(response, "Liked")
        self.assertContains(response, "Kettle")
        self.assertContains(response, 'name="reaction" value="liked"')
        self.assertContains(response, 'name="reaction" value="disliked"')
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertNotContains(response, "giver_note")
        self.assertNotContains(response, "claimed_by")
        self.assertNotContains(response, "given_at")
        self.assertNotContains(response, "Private note")

    def test_owner_like_moves_item_out_of_pending(self) -> None:
        self._login("bea")
        self.client.post("/their-list/add/", {"name": "Suggestion"})
        item = Item.objects.get(name="Suggestion")
        self.client.logout()
        self._login("ada")
        response = self.client.post(
            f"/my-list/{item.id}/react/",
            {"reaction": "liked"},
            follow=True,
        )
        self.assertEqual(response.redirect_chain, [("/my-list/", 302)])
        item.refresh_from_db()
        self.assertEqual(item.reaction, "liked")
        self.assertIsNotNone(item.reacted_at)
        self.assertContains(response, "Saved Suggestion.")
        self.assertContains(response, "Liked")
        self.assertNotContains(response, "Needs a reaction")
        self.assertNotContains(response, 'name="reaction" value="liked"')
        self.assertContains(response, 'name="reaction" value="disliked"')

    def test_owner_dislike_groups_separately(self) -> None:
        self._login("bea")
        self.client.post("/their-list/add/", {"name": "Puzzle"})
        item = Item.objects.get(name="Puzzle")
        self.client.logout()
        self._login("ada")
        response = self.client.post(
            f"/my-list/{item.id}/react/",
            {"reaction": "disliked"},
            follow=True,
        )
        item.refresh_from_db()
        self.assertEqual(item.reaction, "disliked")
        self.assertContains(response, "Disliked")
        self.assertContains(response, "Puzzle")
        self.assertNotContains(response, "Needs a reaction")

    def test_partner_cannot_react(self) -> None:
        self._login("bea")
        self.client.post("/their-list/add/", {"name": "Toaster"})
        item = Item.objects.get(name="Toaster")
        response = self.client.post(
            f"/my-list/{item.id}/react/",
            {"reaction": "liked"},
        )
        self.assertEqual(response.status_code, 403)
        item.refresh_from_db()
        self.assertIsNone(item.reaction)

    def test_dislike_on_claimed_item_saves_normally(self) -> None:
        """The owner sees a normal save; the claim is released behind the scenes."""

        self._login("ada")
        self.client.post("/my-list/add/", {"name": "Watch"})
        item = Item.objects.get(name="Watch")
        item.claimed_by = self.bea
        item.save(update_fields=["claimed_by"])
        response = self.client.post(
            f"/my-list/{item.id}/react/",
            {"reaction": "disliked"},
            follow=True,
        )
        self.assertEqual(response.redirect_chain, [("/my-list/", 302)])
        self.assertContains(response, "Saved Watch.")
        body = response.content.decode().lower()
        self.assertNotIn("claim", body)
        self.assertNotIn("released", body)
        item.refresh_from_db()
        self.assertEqual(item.reaction, "disliked")
        self.assertIsNone(item.claimed_by_id)

    def test_missing_item_is_404(self) -> None:
        self._login("ada")
        response = self.client.post("/my-list/9999/react/", {"reaction": "liked"})
        self.assertEqual(response.status_code, 404)

    def test_get_is_not_allowed(self) -> None:
        self._login("ada")
        self.client.post("/my-list/add/", {"name": "Kettle"})
        item = Item.objects.get(name="Kettle")
        response = self.client.get(f"/my-list/{item.id}/react/")
        self.assertEqual(response.status_code, 405)

    def test_react_csrf_enforced(self) -> None:
        from django.utils import timezone

        item = Item.objects.create(
            owner=self.ada,
            added_by=self.ada,
            name="Kettle",
            reaction="liked",
            reacted_at=timezone.now(),
            created_at=timezone.now(),
        )
        client = Client(enforce_csrf_checks=True)
        client.login(username="ada", password=PASSWORD)
        response = client.post(
            f"/my-list/{item.id}/react/",
            {"reaction": "disliked"},
        )
        self.assertEqual(response.status_code, 403)
        item.refresh_from_db()
        self.assertEqual(item.reaction, "liked")
