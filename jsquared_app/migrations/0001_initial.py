# Generated manually to align with Deliverable 1 schema.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CookingStyle",
            fields=[
                ("cooking_style_id", models.AutoField(primary_key=True, serialize=False)),
                ("style_name", models.CharField(max_length=50)),
                ("style_description", models.CharField(blank=True, max_length=200, null=True)),
                ("cooking_charge", models.FloatField(default=0)),
                ("c_weight_min", models.FloatField(default=0)),
                ("c_weight_max", models.FloatField(default=0)),
            ],
            options={"db_table": "COOKING_STYLE"},
        ),
        migrations.CreateModel(
            name="Discount",
            fields=[
                ("discount_id", models.AutoField(primary_key=True, serialize=False)),
                (
                    "discount_type",
                    models.CharField(
                        choices=[
                            ("PWD", "PWD"),
                            ("Senior Citizen", "Senior Citizen"),
                            ("Suki", "Suki"),
                        ],
                        max_length=24,
                    ),
                ),
                ("discount_value", models.DecimalField(decimal_places=2, default=0, max_digits=6)),
            ],
            options={"db_table": "DISCOUNT"},
        ),
        migrations.CreateModel(
            name="FixedMenuItem",
            fields=[
                ("fixed_item_id", models.AutoField(primary_key=True, serialize=False)),
                ("item_name", models.CharField(max_length=50)),
                ("item_description", models.CharField(blank=True, max_length=200, null=True)),
                ("item_category", models.CharField(max_length=50)),
                ("fixed_price", models.FloatField(default=0)),
            ],
            options={"db_table": "FIXED_MENU_ITEM"},
        ),
        migrations.CreateModel(
            name="MeatItem",
            fields=[
                ("meat_id", models.AutoField(primary_key=True, serialize=False)),
                ("meat_type", models.CharField(max_length=50)),
                ("weight_min", models.FloatField(default=0)),
                ("weight_max", models.FloatField(default=0)),
                (
                    "meat_image",
                    models.ImageField(blank=True, null=True, upload_to="meat_images/"),
                ),
                (
                    "item_status",
                    models.CharField(
                        choices=[
                            ("Available", "Available"),
                            ("Out of Stock", "Out of Stock"),
                            ("Discontinued", "Discontinued"),
                        ],
                        default="Available",
                        max_length=24,
                    ),
                ),
                ("current_price", models.FloatField(default=0)),
                (
                    "price_updated_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
            ],
            options={"db_table": "MEAT_ITEM"},
        ),
        migrations.CreateModel(
            name="Supplier",
            fields=[
                ("supplier_id", models.AutoField(primary_key=True, serialize=False)),
                ("supplier_name", models.CharField(max_length=100)),
                ("contact_person", models.CharField(blank=True, max_length=100, null=True)),
                ("phone_number", models.CharField(max_length=15)),
                ("supplier_address", models.CharField(max_length=200)),
            ],
            options={"db_table": "SUPPLIER"},
        ),
        migrations.CreateModel(
            name="Staff",
            fields=[
                ("staff_id", models.AutoField(primary_key=True, serialize=False)),
                ("staff_name", models.CharField(max_length=50)),
                (
                    "staff_role",
                    models.CharField(
                        choices=[
                            ("Waiter", "Waiter"),
                            ("Cashier", "Cashier"),
                            ("Manager", "Manager"),
                            ("Kitchen Staff", "Kitchen Staff"),
                            ("Market Staff", "Market Staff"),
                        ],
                        max_length=24,
                    ),
                ),
                ("staff_email", models.CharField(max_length=50)),
                ("staff_address", models.CharField(blank=True, max_length=200, null=True)),
                ("staff_password", models.CharField(max_length=25)),
                (
                    "user",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="staff_profile",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"db_table": "STAFF"},
        ),
        migrations.CreateModel(
            name="VariedMenuItem",
            fields=[
                ("varied_item_id", models.AutoField(primary_key=True, serialize=False)),
                ("item_price", models.FloatField(default=0)),
                ("is_byom", models.BooleanField(default=False)),
                (
                    "cooking_style",
                    models.ForeignKey(
                        db_column="cooking_style_id",
                        on_delete=django.db.models.deletion.PROTECT,
                        to="jsquared_app.cookingstyle",
                    ),
                ),
                (
                    "meat",
                    models.ForeignKey(
                        db_column="meat_id",
                        on_delete=django.db.models.deletion.PROTECT,
                        to="jsquared_app.meatitem",
                    ),
                ),
                (
                    "supplier",
                    models.ForeignKey(
                        blank=True,
                        db_column="supplier_id",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="jsquared_app.supplier",
                    ),
                ),
            ],
            options={"db_table": "VARIED_MENU_ITEM"},
        ),
        migrations.CreateModel(
            name="Order",
            fields=[
                ("order_id", models.AutoField(primary_key=True, serialize=False)),
                ("table_num", models.IntegerField(default=1)),
                (
                    "order_status",
                    models.CharField(
                        choices=[
                            ("Pending", "Pending"),
                            ("Preparing", "Preparing"),
                            ("Served", "Served"),
                            ("Completed", "Completed"),
                            ("Cancelled", "Cancelled"),
                        ],
                        default="Pending",
                        max_length=14,
                    ),
                ),
                (
                    "payment_method",
                    models.CharField(
                        choices=[
                            ("Cash", "Cash"),
                            ("Card", "Card"),
                            ("Online payment", "Online payment"),
                        ],
                        default="Cash",
                        max_length=24,
                    ),
                ),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("applied_discount", models.IntegerField(blank=True, null=True)),
                ("total_amount", models.FloatField(default=0)),
                ("customer_name", models.CharField(blank=True, max_length=50, null=True)),
                (
                    "discount",
                    models.ForeignKey(
                        blank=True,
                        db_column="discount_id",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="jsquared_app.discount",
                    ),
                ),
                (
                    "staff",
                    models.ForeignKey(
                        db_column="staff_id",
                        on_delete=django.db.models.deletion.PROTECT,
                        to="jsquared_app.staff",
                    ),
                ),
            ],
            options={"db_table": "ORDER"},
        ),
        migrations.CreateModel(
            name="OrderItem",
            fields=[
                ("order_item_id", models.AutoField(primary_key=True, serialize=False)),
                ("order_quantity", models.IntegerField(default=1)),
                ("order_unit_price", models.FloatField(default=0)),
                ("cooking_charge", models.FloatField(default=0)),
                ("subtotal", models.FloatField(default=0)),
                (
                    "fixed_item",
                    models.ForeignKey(
                        blank=True,
                        db_column="fixed_item_id",
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="jsquared_app.fixedmenuitem",
                    ),
                ),
                (
                    "order",
                    models.ForeignKey(
                        db_column="order_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="items",
                        to="jsquared_app.order",
                    ),
                ),
                (
                    "varied_item",
                    models.ForeignKey(
                        blank=True,
                        db_column="varied_item_id",
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        to="jsquared_app.variedmenuitem",
                    ),
                ),
            ],
            options={"db_table": "ORDER_ITEM"},
        ),
    ]
