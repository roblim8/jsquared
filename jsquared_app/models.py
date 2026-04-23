from django.db import models
from django.utils import timezone
from django.db.models import Sum
from django.conf import settings


class SeafoodItem(models.Model):
    seafood_id = models.AutoField(primary_key=True)
    seafood_type = models.CharField(max_length=50)
    seafood_unit_price = models.FloatField(default=0)
    weight_range = models.FloatField(default=0)

    class Meta:
        db_table = "SEAFOOD_ITEM"

    def __str__(self):
        return self.seafood_type


class MenuItem(models.Model):
    menu_item_id = models.AutoField(primary_key=True)
    item_name = models.CharField(max_length=50)
    item_category = models.CharField(max_length=50)
    pricing_type = models.CharField(max_length=1)  # "F" or "V"

    class Meta:
        db_table = "MENU_ITEM"

    def __str__(self):
        return self.item_name


class FixedPricing(models.Model):
    menu_item = models.OneToOneField(
        MenuItem,
        on_delete=models.CASCADE,
        primary_key=True,
        db_column="menu_item_id"
    )
    fixed_price = models.FloatField(default=0)

    class Meta:
        db_table = "FIXED_PRICING"


class VariedPricing(models.Model):
    menu_item = models.OneToOneField(
        MenuItem,
        on_delete=models.CASCADE,
        primary_key=True,
        db_column="menu_item_id"
    )
    seafood = models.ForeignKey(
        SeafoodItem,
        on_delete=models.PROTECT,
        db_column="seafood_id"
    )
    menu_price = models.FloatField(default=0)

    class Meta:
        db_table = "VARIED_PRICING"

    def save(self, *args, **kwargs):
        self.menu_price = float(self.seafood.seafood_unit_price)
        super().save(*args, **kwargs)


class Staff(models.Model):
    staff_id = models.AutoField(primary_key=True)
    staff_name = models.CharField(max_length=50)
    staff_role = models.CharField(max_length=20)  
    staff_email = models.CharField(max_length=50)
    staff_password = models.CharField(max_length=25)

    # Link Django user -> STAFF row (so ORDER.staff FK stays correct)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="staff_profile",
    )

    class Meta:
        db_table = "STAFF"

    def __str__(self):
        return f"{self.staff_name} ({self.staff_role})"


class Order(models.Model):
    order_id = models.AutoField(primary_key=True)
    staff = models.ForeignKey(Staff, on_delete=models.PROTECT, db_column="staff_id")
    table_num = models.IntegerField(default=1)
    status = models.CharField(max_length=20, default="Pending")
    order_date = models.DateTimeField(default=timezone.now)
    total_amount = models.FloatField(default=0)
    customer_name = models.CharField(max_length=50, blank=True, null=True)

    class Meta:
        db_table = "ORDER"

    def recompute_total(self):
        total = self.items.aggregate(s=Sum("subtotal"))["s"] or 0
        self.total_amount = float(total)
        self.save(update_fields=["total_amount"])


class OrderItem(models.Model):
    order_item_id = models.AutoField(primary_key=True)
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items",
        db_column="order_id"
    )
    menu_item = models.ForeignKey(
        MenuItem,
        on_delete=models.PROTECT,
        db_column="menu_item_id"
    )
    order_quantity = models.IntegerField(default=1)
    unit_price = models.FloatField(default=0)  # snapshot at time of adding to order
    subtotal = models.FloatField(default=0)

    class Meta:
        db_table = "ORDER_ITEM"

    def save(self, *args, **kwargs):
        self.subtotal = float(self.order_quantity) * float(self.unit_price)
        super().save(*args, **kwargs)
