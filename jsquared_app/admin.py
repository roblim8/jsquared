from django.contrib import admin

from .models import (
    CookingStyle,
    Discount,
    FixedMenuItem,
    MeatItem,
    Order,
    OrderItem,
    Staff,
    Supplier,
    VariedMenuItem,
    PriceInquiryRequest,
)


@admin.register(MeatItem)
class MeatItemAdmin(admin.ModelAdmin):
    list_display = ("meat_id", "meat_type", "current_price", "item_status", "price_updated_at")
    search_fields = ("meat_type",)


@admin.register(CookingStyle)
class CookingStyleAdmin(admin.ModelAdmin):
    list_display = ("cooking_style_id", "style_name", "meat_item", "cooking_charge", "c_weight_min", "c_weight_max")
    list_filter = ("meat_item",)
    search_fields = ("style_name", "meat_item__meat_type")


@admin.register(FixedMenuItem)
class FixedMenuItemAdmin(admin.ModelAdmin):
    list_display = ("fixed_item_id", "item_name", "item_category", "fixed_price")
    search_fields = ("item_name", "item_category")


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("supplier_id", "supplier_name", "phone_number")
    search_fields = ("supplier_name",)


@admin.register(VariedMenuItem)
class VariedMenuItemAdmin(admin.ModelAdmin):
    list_display = ("varied_item_id", "meat", "cooking_style", "cooking_add_on_charge", "is_byom")
    list_filter = ("is_byom", "cooking_style")
    search_fields = ("meat__meat_type", "cooking_style__style_name")

    @admin.display(description="Cooking Add-On Charge")
    def cooking_add_on_charge(self, obj):
        return obj.item_price


@admin.register(Discount)
class DiscountAdmin(admin.ModelAdmin):
    list_display = ("discount_id", "discount_type", "discount_value")


@admin.register(Staff)
class StaffAdmin(admin.ModelAdmin):
    list_display = ("staff_id", "staff_name", "staff_role", "staff_email")
    list_filter = ("staff_role",)
    search_fields = ("staff_name", "staff_email")


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("order_id", "table_num", "order_status", "total_amount", "created_at")
    list_filter = ("order_status", "payment_method")
    inlines = [OrderItemInline]


@admin.register(PriceInquiryRequest)
class PriceInquiryRequestAdmin(admin.ModelAdmin):
    list_display = ("inquiry_id", "meat", "status", "requested_by", "accepted_by", "requested_at")
    list_filter = ("status",)
    search_fields = ("inquiry_id", "meat__meat_type", "requested_by__staff_name")
