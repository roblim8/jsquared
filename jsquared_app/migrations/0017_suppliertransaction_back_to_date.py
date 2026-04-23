from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("jsquared_app", "0016_suppliertransaction_datetime"),
    ]

    operations = [
        migrations.AlterField(
            model_name="suppliertransaction",
            name="transaction_date",
            field=models.DateField(default=django.utils.timezone.now),
        ),
    ]
