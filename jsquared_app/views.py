from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponseForbidden

from .models import (
    SeafoodItem, VariedPricing,
    MenuItem, FixedPricing,
    Order, OrderItem,
    Staff
)


# ---------- RBAC using Django Groups ----------

def _in_any_group(user, group_names: set[str]) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=list(group_names)).exists()


def require_groups(*group_names: str):
    allowed = set(group_names)
    return user_passes_test(lambda u: _in_any_group(u, allowed))


def _staff_for_user(user):
    return Staff.objects.filter(user=user).first()


@login_required
def home(request):
    return render(request, "jsquared_app/home.html")


# ============================================================
# Feature A: Manage Seafood Prices (CRUD)
# Allowed: Market Staff, Manager
# ============================================================

@login_required
@require_groups("Staff", "Manager")
def seafood_price_list(request):
    seafood = SeafoodItem.objects.order_by("seafood_type")
    return render(request, "jsquared_app/seafood_price_list.html", {"seafood": seafood})


@login_required
@require_groups("Staff", "Manager")
def seafood_price_create(request):
    if request.method == "POST":
        seafood_type = request.POST.get("seafood_type") or ""
        seafood_unit_price = float(request.POST.get("seafood_unit_price") or 0)
        weight_range = float(request.POST.get("weight_range") or 0)

        SeafoodItem.objects.create(
            seafood_type=seafood_type,
            seafood_unit_price=seafood_unit_price,
            weight_range=weight_range,
        )
        return redirect("seafood_price_list")

    return render(request, "jsquared_app/seafood_price_create.html")


@login_required
@require_groups("Staff", "Manager")
def seafood_price_edit(request, seafood_id: int):
    s = get_object_or_404(SeafoodItem, seafood_id=seafood_id)

    if request.method == "POST":
        s.seafood_type = request.POST.get("seafood_type") or s.seafood_type
        s.seafood_unit_price = float(request.POST.get("seafood_unit_price") or 0)
        s.weight_range = float(request.POST.get("weight_range") or 0)
        s.save()

        # Update any VariedPricing rows tied to this seafood
        for vp in VariedPricing.objects.filter(seafood=s):
            vp.save()

        return redirect("seafood_price_list")

    return render(request, "jsquared_app/seafood_price_edit.html", {"s": s})


@login_required
@require_groups("Staff", "Manager")
def seafood_price_delete(request, seafood_id: int):
    s = get_object_or_404(SeafoodItem, seafood_id=seafood_id)

    if request.method == "POST":
        s.delete()
        return redirect("seafood_price_list")

    return render(request, "jsquared_app/seafood_price_delete.html", {"s": s})


# ============================================================
# Feature B: Orders (CRUD-ish)
# Allowed: Waiter, Manager
# ============================================================

def _latest_unit_price(menu_item: MenuItem) -> float:
    if menu_item.pricing_type == "F":
        fp = FixedPricing.objects.get(menu_item=menu_item)
        return float(fp.fixed_price)

    vp = VariedPricing.objects.get(menu_item=menu_item)
    return float(vp.menu_price)


@login_required
@require_groups("Staff", "Manager")
def order_list(request):
    staff = _staff_for_user(request.user)
    if not staff:
        return HttpResponseForbidden("No STAFF record linked to this user.")

    orders = Order.objects.order_by("-order_date")[:50]
    return render(request, "jsquared_app/order_list.html", {"orders": orders})


@login_required
@require_groups("Staff", "Manager")
def order_create(request):
    staff = _staff_for_user(request.user)
    if not staff:
        return HttpResponseForbidden("No STAFF record linked to this user.")

    if request.method == "POST":
        table_num = int(request.POST.get("table_num") or 1)
        customer_name = request.POST.get("customer_name") or None

        order = Order.objects.create(
            staff=staff,
            table_num=table_num,
            customer_name=customer_name,
            status="Pending",
        )
        return redirect("order_detail", order_id=order.order_id)

    return render(request, "jsquared_app/order_create.html")


@login_required
@require_groups("Staff", "Manager")
def order_detail(request, order_id: int):
    staff = _staff_for_user(request.user)
    if not staff:
        return HttpResponseForbidden("No STAFF record linked to this user.")

    order = get_object_or_404(Order, order_id=order_id)
    menu_items = MenuItem.objects.order_by("item_name")

    # Add item to order
    if request.method == "POST":
        menu_item_id = int(request.POST.get("menu_item_id"))
        qty = int(request.POST.get("order_quantity") or 1)

        menu_item = get_object_or_404(MenuItem, menu_item_id=menu_item_id)
        unit_price = _latest_unit_price(menu_item)  # snapshot now

        OrderItem.objects.create(
            order=order,
            menu_item=menu_item,
            order_quantity=qty,
            unit_price=unit_price,
        )

        order.recompute_total()
        return redirect("order_detail", order_id=order.order_id)

    order.recompute_total()
    return render(request, "jsquared_app/order_detail.html", {"order": order, "menu_items": menu_items})


@login_required
@require_groups("Staff", "Manager")
def order_item_delete(request, order_id: int, order_item_id: int):
    staff = _staff_for_user(request.user)
    if not staff:
        return HttpResponseForbidden("No STAFF record linked to this user.")

    order = get_object_or_404(Order, order_id=order_id)
    item = get_object_or_404(OrderItem, order_item_id=order_item_id, order=order)

    if request.method == "POST":
        item.delete()
        order.recompute_total()
        return redirect("order_detail", order_id=order.order_id)

    return render(request, "jsquared_app/order_item_delete.html", {"order": order, "item": item})


@login_required
@require_groups("Staffpython ", "Manager")
def order_delete(request, order_id: int):
    staff = _staff_for_user(request.user)
    if not staff:
        return HttpResponseForbidden("No STAFF record linked to this user.")

    order = get_object_or_404(Order, order_id=order_id)

    if request.method == "POST":
        order.delete()
        return redirect("order_list")

    return render(request, "jsquared_app/order_delete.html", {"order": order})
