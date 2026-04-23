from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("jsquared_app", "0015_suppliertransaction_quantity_unitprice_status"),
    ]

    operations = [
        migrations.AlterField(
            model_name="suppliertransaction",
            name="transaction_date",
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
    ]
