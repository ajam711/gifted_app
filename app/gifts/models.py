"""The four domain tables, persisted.

No ``List`` table: a list is ``Item.owner_id == viewer``.
No ``Claim`` table, no ``Reaction`` table, no stored ``status``.
Auth lives on ``django.contrib.auth.User``; ``Person`` has no password.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q


class Person(models.Model):
    """Someone who can own a list. ``user`` null means no login.

    A login can be linked later by setting ``user``. That is an update
    of this nullable column, not a data migration.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="person",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=100)
    email = models.EmailField(blank=True)

    def __str__(self) -> str:
        return self.name


class Connection(models.Model):
    """One row per pair. Every partner lookup goes through here."""

    person_a = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="+"
    )
    person_b = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="+"
    )
    created_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(person_a_id__lt=F("person_b_id")),
                name="connection_person_a_lt_b",
            ),
            models.UniqueConstraint(
                fields=["person_a", "person_b"],
                name="connection_unique_pair",
            ),
        ]

    def clean(self):
        super().clean()
        self._one_keeper_for_someone_without_a_login()

    def save(self, *args, **kwargs):
        self._one_keeper_for_someone_without_a_login()
        super().save(*args, **kwargs)

    def _one_keeper_for_someone_without_a_login(self) -> None:
        """A person with no login has one connection: their keeper."""

        if self.person_a_id is None or self.person_b_id is None:
            return
        for person in Person.objects.filter(
            id__in=[self.person_a_id, self.person_b_id], user__isnull=True
        ):
            existing = Connection.objects.filter(
                Q(person_a_id=person.id) | Q(person_b_id=person.id)
            )
            if self.pk:
                existing = existing.exclude(pk=self.pk)
            if existing.exists():
                raise ValidationError(
                    "Someone who isn't on the app can have only one connection."
                )

    def __str__(self) -> str:
        return f"{self.person_a} — {self.person_b}"


class ImportantDate(models.Model):
    """Recurring or one-off date. Home shows the partner's next one."""

    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="important_dates"
    )
    label = models.CharField(max_length=100)
    month = models.PositiveSmallIntegerField()
    day = models.PositiveSmallIntegerField()
    year = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(month__gte=1) & Q(month__lte=12),
                name="importantdate_month_range",
            ),
            models.CheckConstraint(
                condition=Q(day__gte=1) & Q(day__lte=31),
                name="importantdate_day_range",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.label} ({self.person})"


class Item(models.Model):
    """One wish-list row. Claim/given columns are stored, never shown to the owner."""

    owner = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="owned_items"
    )
    added_by = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="added_items"
    )
    name = models.CharField(max_length=200)
    url = models.CharField(max_length=500, null=True, blank=True)
    price_text = models.CharField(max_length=100, null=True, blank=True)
    tag = models.CharField(max_length=100, null=True, blank=True)
    giver_note = models.TextField(null=True, blank=True)
    reaction = models.CharField(
        max_length=8,
        null=True,
        blank=True,
        choices=[("liked", "liked"), ("disliked", "disliked")],
    )
    reacted_at = models.DateTimeField(null=True, blank=True)
    claimed_by = models.ForeignKey(
        Person,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    claimed_at = models.DateTimeField(null=True, blank=True)
    given_at = models.DateTimeField(null=True, blank=True)
    claim_released_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    received_from = models.CharField(max_length=100, null=True, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(reaction__isnull=True)
                | Q(reaction__in=["liked", "disliked"]),
                name="item_reaction_valid",
            ),
        ]

    def __str__(self) -> str:
        return self.name
