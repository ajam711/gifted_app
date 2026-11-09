from django.template import Context, Template
from django.test import SimpleTestCase, TestCase

from gifts.models import Item
from gifts.money import format_dollars
from gifts.tests.harness import PASSWORD, two_people


class FormatDollarsTests(SimpleTestCase):
    def test_plain_amounts_gain_a_dollar_sign_and_cents(self) -> None:
        self.assertEqual(format_dollars("24.99"), "$24.99")
        self.assertEqual(format_dollars("24"), "$24.00")
        self.assertEqual(format_dollars("24.9"), "$24.90")
        self.assertEqual(format_dollars(".5"), "$0.50")
        self.assertEqual(format_dollars("0"), "$0.00")

    def test_thousands_and_existing_dollar_signs_are_normalized(self) -> None:
        self.assertEqual(format_dollars("1200"), "$1,200.00")
        self.assertEqual(format_dollars("1,200.5"), "$1,200.50")
        self.assertEqual(format_dollars("$24.99"), "$24.99")
        self.assertEqual(format_dollars("$ 24.9"), "$24.90")
        self.assertEqual(format_dollars("24.99$"), "$24.99")

    def test_rounds_half_up_to_cents(self) -> None:
        self.assertEqual(format_dollars("1.005"), "$1.01")
        self.assertEqual(format_dollars("1.004"), "$1.00")
        self.assertEqual(format_dollars("24.995"), "$25.00")

    def test_negative_amount(self) -> None:
        self.assertEqual(format_dollars("-3.5"), "-$3.50")
        self.assertEqual(format_dollars("$-3.5"), "-$3.50")
        self.assertEqual(format_dollars("-0.001"), "$0.00")

    def test_blank_is_empty(self) -> None:
        self.assertEqual(format_dollars(None), "")
        self.assertEqual(format_dollars(""), "")
        self.assertEqual(format_dollars("   "), "")

    def test_free_text_keeps_a_dollar_sign_it_already_has(self) -> None:
        self.assertEqual(format_dollars("around $120"), "around $120")
        self.assertEqual(format_dollars("  around $120  "), "around $120")

    def test_free_text_without_a_dollar_sign_gains_one(self) -> None:
        self.assertEqual(format_dollars("around 120"), "around $120")
        self.assertEqual(format_dollars("40 to 60"), "$40 to 60")
        self.assertEqual(format_dollars("see note"), "$see note")

    def test_template_filter_formats_and_stays_escaped(self) -> None:
        html = Template("{% load money %}{{ price|dollars }}").render(
            Context({"price": "24.99"})
        )
        self.assertEqual(html, "$24.99")
        escaped = Template("{% load money %}{{ price|dollars }}").render(
            Context({"price": "<b>24</b>"})
        )
        self.assertEqual(escaped, "&lt;b&gt;$24&lt;/b&gt;")
        self.assertNotIn("<b>", escaped)


class PriceDisplayTests(TestCase):
    def setUp(self) -> None:
        self.ada, self.bea = two_people()

    def _login(self, username: str) -> None:
        ok = self.client.login(username=username, password=PASSWORD)
        self.assertTrue(ok)

    def test_add_form_shows_price_as_dollars(self) -> None:
        self._login("ada")
        response = self.client.get("/my-list/add/")
        self.assertContains(response, 'name="price_text"')
        self.assertContains(response, 'placeholder="24.99"')
        self.assertContains(response, 'class="money-sign"')
        self.assertContains(response, ">$</span>")
        self.assertContains(response, "US dollars")

    def test_plain_amount_displays_on_both_lists_and_is_stored_as_typed(self) -> None:
        self._login("ada")
        self.client.post(
            "/my-list/add/", {"name": "Mug", "price_text": "24.99"}
        )
        item = Item.objects.get(name="Mug")
        self.assertEqual(item.price_text, "24.99")

        owner = self.client.get("/my-list/")
        self.assertContains(owner, '<p class="meta">$24.99</p>')
        self.assertNotContains(owner, "<p class=\"meta\">24.99</p>")

        self.client.logout()
        self._login("bea")
        shopper = self.client.get("/their-list/")
        self.assertContains(shopper, '<p class="meta">$24.99</p>')
        self.assertNotContains(shopper, "<p class=\"meta\">24.99</p>")

    def test_grouped_amount_displays_for_the_shopper(self) -> None:
        self._login("bea")
        self.client.post(
            "/their-list/add/", {"name": "Coat", "price_text": "2499.5"}
        )
        self.assertEqual(Item.objects.get(name="Coat").price_text, "2499.5")
        response = self.client.get("/their-list/")
        self.assertContains(response, '<p class="meta">$2,499.50</p>')

    def test_free_text_price_is_unchanged_on_the_owner_list(self) -> None:
        self._login("bea")
        self.client.post(
            "/their-list/add/",
            {"name": "Coat", "price_text": "around $120"},
        )
        self.assertEqual(Item.objects.get(name="Coat").price_text, "around $120")
        self.client.logout()
        self._login("ada")
        response = self.client.get("/my-list/")
        self.assertContains(response, '<p class="meta">around $120</p>')
        self.assertNotContains(response, "$120.00")

    def test_price_html_is_escaped(self) -> None:
        self._login("ada")
        self.client.post(
            "/my-list/add/", {"name": "Mug", "price_text": "<b>24</b>"}
        )
        response = self.client.get("/my-list/")
        self.assertContains(response, "&lt;b&gt;$24&lt;/b&gt;")
        self.assertNotContains(response, "<b>24</b>")
        self.assertNotContains(response, "<b>$24</b>")
