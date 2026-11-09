# Additive only. Both columns are nullable, so existing rows are left
# in place. ``received_from`` is not rewritten and is not matched to a
# person: two people can share a name, and older gifts keep the text
# that was stored. ``kept_by`` is filled only for someone with no login
# and exactly one connection, which is their keeper. People who already
# have a login — including the pair who both use the app — stay null.

import django.db.models.deletion
from django.db import migrations, models


def backfill_kept_by(apps, schema_editor):
    """Record the keeper where that person is already unambiguous.

    Does not read or write gift rows.
    """

    Person = apps.get_model("gifts", "Person")
    Connection = apps.get_model("gifts", "Connection")
    for person in Person.objects.filter(user__isnull=True, kept_by__isnull=True):
        links = list(Connection.objects.filter(person_a_id=person.pk))
        links.extend(Connection.objects.filter(person_b_id=person.pk))
        if len(links) != 1:
            continue
        link = links[0]
        other_id = (
            link.person_b_id if link.person_a_id == person.pk else link.person_a_id
        )
        person.kept_by_id = other_id
        person.save(update_fields=["kept_by"])


class Migration(migrations.Migration):

    dependencies = [
        ('gifts', '0005_person_email_blank'),
    ]

    operations = [
        migrations.AddField(
            model_name='item',
            name='received_from_person',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='gifts.person'),
        ),
        migrations.AddField(
            model_name='person',
            name='kept_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='kept_people', to='gifts.person'),
        ),
        migrations.RunPython(backfill_kept_by, migrations.RunPython.noop),
    ]
