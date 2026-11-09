# Email is optional for someone who isn't on the app. ``blank`` is form
# validation only; existing addresses are left as they are.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gifts', '0004_person_user_nullable'),
    ]

    operations = [
        migrations.AlterField(
            model_name='person',
            name='email',
            field=models.EmailField(blank=True, max_length=254),
        ),
    ]
