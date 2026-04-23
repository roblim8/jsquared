from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jsquared_app", "0007_order_payment_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="diner_count",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="order",
            name="eligible_count",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="order",
            name="discount_target_amount",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="order",
            name="applied_discount",
            field=models.FloatField(blank=True, null=True),
        ),
    ]
