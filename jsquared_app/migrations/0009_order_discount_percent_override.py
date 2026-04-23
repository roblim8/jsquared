from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jsquared_app", "0008_order_checkout_discount_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="discount_percent_override",
            field=models.FloatField(blank=True, null=True),
        ),
    ]
