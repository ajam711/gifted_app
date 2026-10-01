from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from gifts.models import Connection, Item
from gifts.tests.harness import two_people


class ItemModelTests(TestCase):
    def test_no_status_column(self) -> None:
        names = {field.name for field in Item._meta.get_fields()}
        self.assertNotIn("status", names)
        self.assertNotIn("state", names)


class ConnectionConstraintTests(TestCase):
    def test_pair_must_be_ordered_a_less_than_b(self) -> None:
        ada, bea = two_people()
        higher, lower = (bea, ada) if bea.id > ada.id else (ada, bea)
        with self.assertRaises(IntegrityError):
            Connection.objects.create(
                person_a=higher,
                person_b=lower,
                created_at=timezone.now(),
            )
