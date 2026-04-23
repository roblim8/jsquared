from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jsquared_app", "0006_meatitem_meat_description"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="payment_status",
            field=models.CharField(choices=[("Unpaid", "Unpaid"), ("Paid", "Paid")], default="Unpaid", max_length=10),
        ),
    ]
