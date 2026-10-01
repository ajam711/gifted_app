"""Connection is the only way to find "the other person".

If these fail, some later screen will hard-code "there are two users"
and break the moment a third ``Person`` row exists.
"""

from domain.errors import Forbidden, Invalid, NotFound
from domain.tests.harness import DomainTestCase, two_people


class ConnectionTests(DomainTestCase):
    def test_canonical_order_independent_of_call_order(self) -> None:
        """Ada is 1, Bea is 2, so the stored pair is always (1, 2)."""

        store, ada, bea = two_people()
        conn = store._connections[(ada.id, bea.id)]
        self.assertEqual(conn.person_a_id, ada.id)
        self.assertEqual(conn.person_b_id, bea.id)
        self.assertLess(conn.person_a_id, conn.person_b_id)

    def test_connect_swapped_ids_still_orders_a_less_than_b(self) -> None:
        """``connect(higher, lower)`` still stores ``a < b``.

        Extra people here are *not* a groups feature. They are a way to
        call ``connect`` with reversed ids without colliding with Ada/Bea.
        """

        store, ada, bea = two_people()
        cal = store.add_person("Cal", "cal@example.com")
        dee = store.add_person("Dee", "dee@example.com")
        conn = store.connect(dee.id, cal.id)
        self.assertLess(conn.person_a_id, conn.person_b_id)
        self.assertEqual(conn.person_a_id, cal.id)
        self.assertEqual(conn.person_b_id, dee.id)

    def test_pair_is_unique(self) -> None:
        """Connecting Bea-Ada after Ada-Bea is the same pair → Invalid."""

        store, ada, bea = two_people()
        with self.assertRaises(Invalid):
            store.connect(bea.id, ada.id)

    def test_partner_lookup_uses_connection_not_person_count(self) -> None:
        """A third Person must not change Ada's partner, and has none."""

        store, ada, bea = two_people()
        cal = store.add_person("Cal", "cal@example.com")
        self.assertEqual(len(store._people), 3)
        self.assertEqual(store.partner_id(ada.id), bea.id)
        self.assertEqual(store.partner_id(bea.id), ada.id)
        with self.assertRaises(NotFound):
            store.partner_id(cal.id)

    def test_unconnected_person_cannot_add_to_someone_elses_list(self) -> None:
        """No connection → cannot write to that list. Forbids a silent third shopper."""

        store, ada, _bea = two_people()
        cal = store.add_person("Cal", "cal@example.com")
        with self.assertRaises(Forbidden):
            store.add_item(cal.id, ada.id, "Sneaky gift")

    def test_cannot_connect_person_to_self(self) -> None:
        store, ada, _bea = two_people()
        with self.assertRaises(Invalid):
            store.connect(ada.id, ada.id)
