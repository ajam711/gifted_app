# A person who isn't on the app has no auth user. Existing rows already
# have a user, and this only allows null — it does not rewrite them.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gifts", "0003_item_received"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="person",
            name="user",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="person",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
