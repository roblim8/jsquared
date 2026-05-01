from __future__ import annotations

from django.db import models
from django.db.models import Sum
from django.utils import timezone
from django.contrib.auth.models import User
from django.core.validators import FileExtensionValidator


class Account(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    staff_name = models.CharField(max_length=100)

    ROLE_CHOICES = [
        ("Staff", "Staff"),
        ("Cashier", "Cashier"),
        ("Manager", "Manager"),
    ]
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    is_active = models.BooleanField(default=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    def archive(self):
        self.is_active = False
        self.archived_at = timezone.now()
        if self.user_id:
            self.user.is_active = False
            self.user.save(update_fields=["is_active"])
        self.save(update_fields=["is_active", "archived_at"])

    def __str__(self):
        return self.staff_name


class MeatItem(models.Model):
    meat_id = models.AutoField(primary_key=True)
    meat_type = models.CharField(max_length=50)
    meat_description = models.CharField(max_length=200, blank=True, null=True)

    weight_min = models.FloatField(default=0)
    weight_max = models.FloatField(default=0)

    meat_image = models.ImageField(
        upload_to="meat_images/",
        null=True,
        blank=True,
        validators=[FileExtensionValidator(allowed_extensions=["jpg", "jpeg", "png"])],
    )

    item_status = models.CharField(
        max_length=24,
        default="Available",
        choices=[
            ("Available", "Available"),
            ("Out of Stock", "Out of Stock"),
            ("Discontinued", "Discontinued"),
        ],
    )

    current_price = models.FloatField(default=0)
    price_updated_at = models.DateTimeField(default=timezone.now)
    is_active = models.BooleanField(default=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "MEAT_ITEM"

    def save(self, *args, **kwargs):
        self.price_updated_at = timezone.now()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.meat_type

    def archive(self):
        self.is_active = False
        self.archived_at = timezone.now()
        self.item_status = "Discontinued"
        self.save(update_fields=["is_active", "archived_at", "item_status", "price_updated_at"])

    def delete(self, *args, **kwargs):
        self.archive()


class CookingStyle(models.Model):
    cooking_style_id = models.AutoField(primary_key=True)

    meat_item = models.ForeignKey(
        MeatItem,
        on_delete=models.PROTECT,
        related_name="cooking_styles",
        null=True,
        blank=True,
    )

    style_name = models.CharField(max_length=50)
    style_description = models.CharField(max_length=200, blank=True, null=True)

    cooking_charge = models.FloatField(default=0)
    c_weight_min = models.FloatField(default=0)
    c_weight_max = models.FloatField(default=0)
    icon = models.ImageField(
        upload_to="icons/",
        blank=True,
        null=True,
        validators=[FileExtensionValidator(allowed_extensions=["jpg", "jpeg", "png"])],
    )
    is_active = models.BooleanField(default=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "COOKING_STYLE"

    def __str__(self) -> str:
        return self.style_name

    def _sync_varied_menu_item(self):
        if not self.meat_item_id:
            return

        existing = VariedMenuItem.objects.filter(cooking_style=self, is_byom=False).order_by("varied_item_id")
        if existing.exists():
            primary = existing.first()
            updates = []
            if primary.meat_id != self.meat_item_id:
                primary.meat = self.meat_item
                updates.append("meat")
            if float(primary.item_price or 0) != float(self.cooking_charge or 0):
                primary.item_price = float(self.cooking_charge or 0)
                updates.append("item_price")
            if updates:
                primary.save(update_fields=updates)
        else:
            VariedMenuItem.objects.create(
                meat=self.meat_item,
                cooking_style=self,
                item_price=float(self.cooking_charge or 0),
                is_byom=False,
            )

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self._sync_varied_menu_item()

    def archive(self):
        self.is_active = False
        self.archived_at = timezone.now()
        self.save(update_fields=["is_active", "archived_at"])

    def delete(self, *args, **kwargs):
        self.archive()


class Supplier(models.Model):
    supplier_id = models.AutoField(primary_key=True)
    supplier_name = models.CharField(max_length=100)
    contact_person = models.CharField(max_length=100, blank=True, null=True)
    phone_number = models.CharField(max_length=15)
    supplier_address = models.CharField(max_length=200, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "SUPPLIER"

    def __str__(self) -> str:
        return self.supplier_name

    def archive(self):
        self.is_active = False
        self.archived_at = timezone.now()
        self.save(update_fields=["is_active", "archived_at"])

    def delete(self, *args, **kwargs):
        self.archive()


class SupplierTransaction(models.Model):
    PAYMENT_PENDING = "Pending"
    PAYMENT_COMPLETED = "Completed"
    PAYMENT_CHOICES = [
        (PAYMENT_PENDING, PAYMENT_PENDING),
        (PAYMENT_COMPLETED, PAYMENT_COMPLETED),
    ]

    transaction_id = models.AutoField(primary_key=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="transactions")
    meat = models.ForeignKey(MeatItem, on_delete=models.SET_NULL, null=True, blank=True)
    item_name = models.CharField(max_length=100, blank=True, null=True)
    transaction_date = models.DateField(default=timezone.now)
    unit_price = models.FloatField(default=0)
    quantity = models.FloatField(default=0)
    transaction_amount = models.FloatField(default=0)
    payment_status = models.CharField(
        max_length=14,
        default=PAYMENT_PENDING,
        choices=PAYMENT_CHOICES,
    )
    notes = models.TextField(blank=True, null=True)

    class Meta:
        db_table = "SUPPLIER_TRANSACTION"
        ordering = ["-transaction_date", "-transaction_id"]

    def __str__(self) -> str:
        label = self.item_name or (self.meat.meat_type if self.meat_id else "Transaction")
        return f"{self.supplier.supplier_name} - {label}"

    def save(self, *args, **kwargs):
        self.unit_price = float(self.unit_price or 0)
        self.quantity = float(self.quantity or 0)
        self.transaction_amount = round(self.unit_price * self.quantity, 2)
        if self.payment_status == "Unpaid":
            self.payment_status = self.PAYMENT_PENDING
        elif self.payment_status == "Paid":
            self.payment_status = self.PAYMENT_COMPLETED
        super().save(*args, **kwargs)


class PurchaseItem(models.Model):
    purchase_item_id = models.AutoField(primary_key=True)
    transaction = models.ForeignKey(
        SupplierTransaction,
        on_delete=models.CASCADE,
        related_name="purchase_items",
        db_column="transaction_id",
    )
    meat = models.ForeignKey(
        MeatItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column="meat_id",
    )
    quantity = models.FloatField(default=0)
    unit_price = models.FloatField(default=0)

    class Meta:
        db_table = "PURCHASE_ITEM"

    @property
    def line_total(self) -> float:
        return round(float(self.quantity or 0) * float(self.unit_price or 0), 2)

    def __str__(self) -> str:
        item = self.meat.meat_type if self.meat_id else "Purchased item"
        return f"{item} - {self.quantity} kg"


class AuditLog(models.Model):
    audit_log_id = models.AutoField(primary_key=True)
    staff = models.ForeignKey("Staff", on_delete=models.SET_NULL, null=True, blank=True)
    username = models.CharField(max_length=150, blank=True, null=True)
    action = models.CharField(max_length=20)
    path = models.CharField(max_length=255)
    method = models.CharField(max_length=10, default="GET")
    model_name = models.CharField(max_length=100, blank=True, null=True)
    object_repr = models.CharField(max_length=200, blank=True, null=True)
    details = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(default=timezone.now)
    order_type = models.BooleanField(default=False)  # False = dine-in, True = take-out

    class Meta:
        db_table = "AUDIT_LOG"
        ordering = ["-created_at", "-audit_log_id"]

    def __str__(self) -> str:
        who = self.username or (self.staff.staff_name if self.staff_id else "Unknown")
        return f"{self.action} by {who}"


class VariedMenuItem(models.Model):
    varied_item_id = models.AutoField(primary_key=True)

    meat = models.ForeignKey(MeatItem, on_delete=models.PROTECT, db_column="meat_id")
    cooking_style = models.ForeignKey(
        CookingStyle, on_delete=models.PROTECT, db_column="cooking_style_id"
    )
    supplier = models.ForeignKey(
        Supplier, on_delete=models.SET_NULL, null=True, blank=True, db_column="supplier_id"
    )

    # item_price = cooking add-on charge for this meat + style combo
    item_price = models.FloatField(default=0)
    is_byom = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "VARIED_MENU_ITEM"

    def __str__(self) -> str:
        base = f"{self.meat.meat_type} - {self.cooking_style.style_name}"
        return f"{base} (BYO)" if self.is_byom else base


class FixedMenuItem(models.Model):
    fixed_item_id = models.AutoField(primary_key=True)
    item_name = models.CharField(max_length=50)
    item_description = models.CharField(max_length=200, blank=True, null=True)
    item_category = models.CharField(max_length=50)
    fixed_price = models.FloatField(default=0)
    is_active = models.BooleanField(default=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "FIXED_MENU_ITEM"

    def __str__(self) -> str:
        return self.item_name

    def archive(self):
        self.is_active = False
        self.archived_at = timezone.now()
        self.save(update_fields=["is_active", "archived_at"])

    def delete(self, *args, **kwargs):
        self.archive()


class Discount(models.Model):
    discount_id = models.AutoField(primary_key=True)
    discount_type = models.CharField(
        max_length=24,
        choices=[("PWD", "PWD"), ("Senior Citizen", "Senior Citizen"), ("Suki", "Suki")],
    )
    discount_value = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "DISCOUNT"

    def __str__(self) -> str:
        return f"{self.discount_type} ({self.discount_value}%)"

    def archive(self):
        self.is_active = False
        self.archived_at = timezone.now()
        self.save(update_fields=["is_active", "archived_at"])

    def delete(self, *args, **kwargs):
        self.archive()

class Staff(models.Model):
    staff_id = models.AutoField(primary_key=True)
    staff_name = models.CharField(max_length=50)
    staff_role = models.CharField(
        max_length=24,
        choices=[
            ("Staff", "Staff"),
            ("Cashier", "Cashier"),
            ("Manager", "Manager"),
        ],
    )
    staff_email = models.CharField(max_length=50, unique=True)
    staff_address = models.CharField(max_length=200, blank=True, null=True)
    staff_password = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "STAFF"

    def __str__(self) -> str:
        return f"{self.staff_name} ({self.staff_role})"

    def archive(self):
        self.is_active = False
        self.archived_at = timezone.now()
        self.save(update_fields=["is_active", "archived_at"])

    def delete(self, *args, **kwargs):
        self.archive()


class Order(models.Model):
    order_id = models.AutoField(primary_key=True)

    staff = models.ForeignKey(Staff, on_delete=models.PROTECT, db_column="staff_id")
    discount = models.ForeignKey(
        Discount, on_delete=models.SET_NULL, null=True, blank=True, db_column="discount_id"
    )

    table_num = models.IntegerField(default=1)
    order_status = models.CharField(
        max_length=14,
        default="Pending",
        choices=[
            ("Pending", "Pending"),
            ("Preparing", "Preparing"),
            ("Served", "Served"),
            ("Completed", "Completed"),
            ("Cancelled", "Cancelled"),
        ],
    )
    payment_status = models.CharField(
        max_length=10,
        default="Unpaid",
        choices=[("Paid", "Paid"), ("Unpaid", "Unpaid")],
    )
    payment_method = models.CharField(
        max_length=24,
        default="Cash",
        choices=[("Cash", "Cash"), ("Card", "Card"), ("Online payment", "Online payment")],
    )

    created_at = models.DateTimeField(default=timezone.now)
    order_type = models.BooleanField(default=False)  # False = dine-in, True = take-out

    applied_discount = models.IntegerField(blank=True, null=True)
    total_amount = models.FloatField(default=0)
    customer_name = models.CharField(max_length=50, blank=True, null=True)

    diner_count = models.IntegerField(default=1)

    # mixed discount support
    pwd_count = models.IntegerField(default=0)
    senior_count = models.IntegerField(default=0)

    # keep this for compatibility with your current forms/views
    eligible_count = models.IntegerField(default=1)

    # kept for compatibility, but not used in current logic
    discount_target_amount = models.FloatField(blank=True, null=True)

    # custom Suki percentage override
    suki_discount_percent = models.FloatField(blank=True, null=True)

    class Meta:
        db_table = "ORDER"

    def gross_amount(self) -> float:
        return round(float(self.items.aggregate(s=Sum("subtotal"))["s"] or 0), 2)

    def meat_base_total(self) -> float:
        total = 0.0
        for item in self.items.all():
            if item.varied_item_id:
                qty = float(item.order_quantity or 0)
                total += qty * float(item.order_unit_price or 0)
        return round(total, 2)

    def cooking_charge_total(self) -> float:
        total = 0.0
        for item in self.items.all():
            if item.varied_item_id:
                qty = float(item.order_quantity or 0)
                total += qty * float(item.cooking_charge or 0)
        return round(total, 2)

    def fixed_items_total(self) -> float:
        total = 0.0
        for item in self.items.all():
            if item.fixed_item_id:
                total += float(item.subtotal or 0)
        return round(total, 2)

    def discountable_base(self) -> float:
        return round(self.cooking_charge_total() + self.fixed_items_total(), 2)

    def total_special_eligible(self) -> int:
        total = max(int(self.pwd_count or 0), 0) + max(int(self.senior_count or 0), 0)
        if total == 0:
            total = max(int(self.eligible_count or 0), 0)
        return min(total, max(int(self.diner_count or 1), 1))

    def compute_discount_breakdown(self) -> dict:
        gross = self.gross_amount()
        meat_total = self.meat_base_total()
        cooking_total = self.cooking_charge_total()
        fixed_total = self.fixed_items_total()
        discountable = self.discountable_base()

        breakdown = {
            "gross_total": round(gross, 2),
            "meat_charge_total": round(meat_total, 2),
            "cooking_charge_total": round(cooking_total, 2),
            "fixed_items_total": round(fixed_total, 2),
            "discountable_base": round(discountable, 2),
            "eligible_amount": 0.0,
            "vatable_sales": 0.0,
            "vat_exempt_amount": 0.0,
            "discount_20_amount": 0.0,
            "discount_total": 0.0,
            "final_total": round(gross, 2),
            "pwd_count": max(int(self.pwd_count or 0), 0),
            "senior_count": max(int(self.senior_count or 0), 0),
            "eligible_people_total": self.total_special_eligible(),
            "applied_discount_percent": 0.0,
        }

        if not self.discount_id:
            return breakdown

        discount_type = self.discount.discount_type
        discount_value = float(self.discount.discount_value)

        if discount_type in ["PWD", "Senior Citizen"]:
            diners = max(int(self.diner_count or 1), 1)
            eligible_people = self.total_special_eligible()

            eligible = (discountable / diners) * eligible_people if discountable else 0.0
            eligible = round(max(0.0, min(eligible, discountable)), 2)

            vat_exclusive = eligible / 1.12 if eligible else 0.0
            vat_exempt = eligible - vat_exclusive
            discount_20 = vat_exclusive * (discount_value / 100.0)
            discount_total = vat_exempt + discount_20
            final_total = max(gross - discount_total, 0)

            breakdown.update(
                {
                    "eligible_amount": round(eligible, 2),
                    "vatable_sales": round(vat_exclusive, 2),
                    "vat_exempt_amount": round(vat_exempt, 2),
                    "discount_20_amount": round(discount_20, 2),
                    "discount_total": round(discount_total, 2),
                    "final_total": round(final_total, 2),
                    "applied_discount_percent": discount_value,
                }
            )

        elif discount_type == "Suki":
            actual_percent = (
                float(self.suki_discount_percent)
                if self.suki_discount_percent is not None
                else discount_value
            )
            actual_percent = max(actual_percent, 0.0)

            discount_total = discountable * (actual_percent / 100.0)

            breakdown.update(
                {
                    "eligible_amount": round(discountable, 2),
                    "discount_total": round(discount_total, 2),
                    "final_total": round(max(gross - discount_total, 0), 2),
                    "applied_discount_percent": round(actual_percent, 2),
                }
            )

        else:
            discount_total = discountable * (discount_value / 100.0)
            breakdown.update(
                {
                    "eligible_amount": round(discountable, 2),
                    "discount_total": round(discount_total, 2),
                    "final_total": round(max(gross - discount_total, 0), 2),
                    "applied_discount_percent": discount_value,
                }
            )

        return breakdown

    def recompute_total(self) -> None:
        breakdown = self.compute_discount_breakdown()
        self.applied_discount = int(round(breakdown["discount_total"])) if breakdown["discount_total"] else None
        self.total_amount = breakdown["final_total"]
        self.save(update_fields=["applied_discount", "total_amount"])


class OrderItem(models.Model):
    order_item_id = models.AutoField(primary_key=True)

    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="items", db_column="order_id"
    )

    fixed_item = models.ForeignKey(
        FixedMenuItem,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        db_column="fixed_item_id",
    )
    varied_item = models.ForeignKey(
        VariedMenuItem,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        db_column="varied_item_id",
    )

    order_quantity = models.FloatField(default=1)

    order_unit_price = models.FloatField(default=0)
    cooking_charge = models.FloatField(default=0)
    subtotal = models.FloatField(default=0)

    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column="supplier_id",
    )

    class Meta:
        db_table = "ORDER_ITEM"

    def clean(self):
        if bool(self.fixed_item_id) == bool(self.varied_item_id):
            raise ValueError("OrderItem must have exactly one of fixed_item or varied_item.")

    @property
    def is_effective_byom(self):
        return bool(
            self.varied_item_id
            and float(self.order_unit_price or 0) == 0.0
            and float(self.cooking_charge or 0) > 0.0
        )

    def save(self, *args, **kwargs):
        self.clean()
        qty = float(self.order_quantity)

        if self.fixed_item_id:
            self.order_unit_price = float(self.fixed_item.fixed_price)
            self.cooking_charge = 0.0
            self.subtotal = qty * self.order_unit_price
        else:
            base_price = float(self.varied_item.meat.current_price)
            combo_cooking_charge = float(self.varied_item.item_price)

            self.order_unit_price = base_price
            self.cooking_charge = combo_cooking_charge

            if self.varied_item.is_byom:
                self.order_unit_price = 0.0
                self.subtotal = qty * self.cooking_charge
            else:
                self.subtotal = qty * (self.order_unit_price + self.cooking_charge)

        super().save(*args, **kwargs)


class PriceInquiryRequest(models.Model):
    inquiry_id = models.AutoField(primary_key=True)

    meat = models.ForeignKey(MeatItem, on_delete=models.PROTECT, db_column="meat_id")
    requested_by = models.ForeignKey(
        "Staff",
        on_delete=models.PROTECT,
        related_name="price_requests",
        db_column="requested_by",
    )

    STATUS_CHOICES = [
        ("Queued", "Queued"),
        ("Pending", "Pending"),
        ("Completed", "Completed"),
        ("Cancelled", "Cancelled"),
    ]
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="Queued")

    accepted_by = models.ForeignKey(
        "Staff",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="accepted_price_requests",
        db_column="accepted_by",
    )

    requested_at = models.DateTimeField(default=timezone.now)
    accepted_at = models.DateTimeField(null=True, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    new_price = models.FloatField(null=True, blank=True)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        db_table = "PRICE_INQUIRY_REQUEST"
        ordering = ["-requested_at"]

    def __str__(self) -> str:
        return f"#{self.inquiry_id} {self.meat.meat_type} ({self.status})"