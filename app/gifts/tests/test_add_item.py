from django.test import Client, TestCase

from gifts.models import Item
from gifts.tests.harness import PASSWORD, two_people


class AddItemHTTPTests(TestCase):
    def setUp(self) -> None:
        self.ada, self.bea = two_people()

    def _login(self, username: str) -> None:
        ok = self.client.login(username=username, password=PASSWORD)
        self.assertTrue(ok)

    def test_login_required(self) -> None:
        response = self.client.get("/my-list/add/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_add_form_has_csrf_and_no_list_picker(self) -> None:
        self._login("ada")
        response = self.client.get("/my-list/add/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertContains(response, "Add to your list")
        self.assertNotContains(response, "<select")
        self.assertNotContains(response, "whose list")
        self.assertNotContains(response, "Private note")

    def test_their_list_add_has_private_note_and_named_owner(self) -> None:
        self._login("bea")
        response = self.client.get("/their-list/add/")
        self.assertIn("Add to Ada", response.content.decode())
        self.assertContains(response, "Private note")
        self.assertNotContains(response, "<select")

    def test_own_add_persists_liked_and_redirects(self) -> None:
        self._login("ada")
        response = self.client.post("/my-list/add/", {"name": "Kettle"}, follow=True)
        self.assertEqual(response.redirect_chain, [("/my-list/", 302)])
        item = Item.objects.get(name="Kettle")
        self.assertEqual(item.owner_id, self.ada.id)
        self.assertEqual(item.added_by_id, self.ada.id)
        self.assertEqual(item.reaction, "liked")
        self.assertIsNotNone(item.reacted_at)
        self.assertEqual(item.reacted_at, item.created_at)
        self.assertContains(response, "Kettle")
        self.assertContains(response, "Saved Kettle.")

    def test_partner_add_is_pending(self) -> None:
        self._login("bea")
        self.client.post("/their-list/add/", {"name": "Suggestion"})
        item = Item.objects.get(name="Suggestion")
        self.assertEqual(item.owner_id, self.ada.id)
        self.assertEqual(item.added_by_id, self.bea.id)
        self.assertIsNone(item.reaction)
        self.assertIsNone(item.reacted_at)

    def test_name_required(self) -> None:
        self._login("ada")
        response = self.client.post("/my-list/add/", {"name": "   "})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Name is required")
        self.assertEqual(Item.objects.count(), 0)

    def test_optional_fields_roundtrip_on_shopper_list(self) -> None:
        self._login("bea")
        self.client.post(
            "/their-list/add/",
            {
                "name": "Coat",
                "url": "https://example.com/coat",
                "price_text": "around $120",
                "tag": "Birthday",
                "giver_note": "navy, not black",
            },
        )
        response = self.client.get("/their-list/")
        self.assertContains(response, "Coat")
        self.assertContains(response, "around $120")
        self.assertContains(response, "Birthday")
        self.assertContains(response, "navy, not black")
        self.assertContains(response, "https://example.com/coat")

    def test_giver_note_absent_from_owner_list_html(self) -> None:
        self._login("bea")
        self.client.post(
            "/their-list/add/",
            {
                "name": "Coat",
                "giver_note": "navy not black hide in hall closet",
            },
        )
        self.client.logout()
        self._login("ada")
        response = self.client.get("/my-list/")
        self.assertContains(response, "Coat")
        self.assertNotContains(response, "navy not black")
        self.assertNotContains(response, "giver_note")
        self.assertNotContains(response, "claimed_by")
        self.assertNotContains(response, "given_at")
        self.assertNotContains(response, "Private note")

    def test_own_list_ignores_posted_giver_note(self) -> None:
        self._login("ada")
        self.client.post(
            "/my-list/add/",
            {"name": "Mug", "giver_note": "should not persist"},
        )
        item = Item.objects.get(name="Mug")
        self.assertIsNone(item.giver_note)

    def test_add_form_csrf_enforced(self) -> None:
        client = Client(enforce_csrf_checks=True)
        client.login(username="ada", password=PASSWORD)
        response = client.post("/my-list/add/", {"name": "Kettle"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Item.objects.count(), 0)
