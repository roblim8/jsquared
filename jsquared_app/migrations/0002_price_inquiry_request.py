# Generated manually to add PriceInquiryRequest (UC14-UC16).

from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("jsquared_app", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="PriceInquiryRequest",
            fields=[
                ("inquiry_id", models.AutoField(primary_key=True, serialize=False)),
                ("status", models.CharField(choices=[("Queued", "Queued"), ("Pending", "Pending"), ("Completed", "Completed"), ("Cancelled", "Cancelled")], default="Queued", max_length=12)),
                ("requested_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("accepted_at", models.DateTimeField(blank=True, null=True)),
                ("responded_at", models.DateTimeField(blank=True, null=True)),
                ("new_price", models.FloatField(blank=True, null=True)),
                ("notes", models.TextField(blank=True, null=True)),
                ("accepted_by", models.ForeignKey(blank=True, db_column="accepted_by", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="accepted_price_requests", to="jsquared_app.staff")),
                ("meat", models.ForeignKey(db_column="meat_id", on_delete=django.db.models.deletion.PROTECT, to="jsquared_app.meatitem")),
                ("requested_by", models.ForeignKey(db_column="requested_by", on_delete=django.db.models.deletion.PROTECT, related_name="price_requests", to="jsquared_app.staff")),
            ],
            options={
                "db_table": "PRICE_INQUIRY_REQUEST",
                "ordering": ["-requested_at"],
            },
        ),
    ]
