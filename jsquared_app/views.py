from __future__ import annotations
from datetime import datetime

from django.db import models

from functools import wraps

from django.contrib import messages
from django.contrib.auth.hashers import check_password 
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.core.management import call_command
from django.db.models import Count, Sum, Max, Q
from openpyxl import Workbook
from io import BytesIO
import tempfile
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User

from .models import (
    FixedMenuItem,
    MeatItem,
    Order,
    OrderItem,
    PriceInquiryRequest,
    Staff,
    VariedMenuItem,
    CookingStyle,
    Supplier,
    SupplierTransaction,
    Discount,
    Account,
    AuditLog,
)

SESSION_STAFF_ID = "staff_id"
SESSION_STAFF_ROLE = "staff_role"


def _ensure_varied_items_synced():
    styles = CookingStyle.objects.select_related("meat_item").exclude(meat_item__isnull=True)
    for style in styles:
        existing = VariedMenuItem.objects.filter(cooking_style=style, is_byom=False).order_by("varied_item_id")
        if existing.exists():
            primary = existing.first()
            updates = []
            if primary.meat_id != style.meat_item_id:
                primary.meat = style.meat_item
                updates.append("meat")
            if float(primary.item_price or 0) != float(style.cooking_charge or 0):
                primary.item_price = float(style.cooking_charge or 0)
                updates.append("item_price")
            if updates:
                primary.save(update_fields=updates)
        else:
            VariedMenuItem.objects.create(
                meat=style.meat_item,
                cooking_style=style,
                item_price=float(style.cooking_charge or 0),
                is_byom=False,
            )


def _current_staff(request) -> Staff | None:
    staff_id = request.session.get(SESSION_STAFF_ID)
    if not staff_id:
        return None
    return Staff.objects.filter(staff_id=staff_id).first()


def staff_login_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.session.get(SESSION_STAFF_ID):
            admin_staff_id = request.session.get('admin_staff_id')
            if admin_staff_id:
                staff = Staff.objects.filter(staff_id=admin_staff_id).first()
                if staff:
                    request.session[SESSION_STAFF_ID] = staff.staff_id
                    request.session[SESSION_STAFF_ROLE] = staff.staff_role
                else:
                    return redirect("login")
            else:
                return redirect("login")
        return view_func(request, *args, **kwargs)

    return wrapper


def require_roles(*roles: str):
    allowed = set(roles)

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.session.get(SESSION_STAFF_ID):
                return redirect("login")

            role = request.session.get(SESSION_STAFF_ROLE)

            # Manager can access everything
            if role == "Manager":
                return view_func(request, *args, **kwargs)

            if role not in allowed:
                return HttpResponseForbidden("Not allowed.")
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def _ensure_default_discounts():
    defaults = [("PWD", 20.0), ("Senior Citizen", 20.0), ("Suki", 0.0)]
    for discount_type, discount_value in defaults:
        discount, created = Discount.objects.get_or_create(
            discount_type=discount_type,
            defaults={"discount_value": discount_value},
        )
        if discount_type == "Suki" and float(discount.discount_value or 0) != 0:
            discount.discount_value = 0.0
            discount.save(update_fields=["discount_value"])

# ============================================================
# LOGIN / LOGOUT
# ============================================================

def login_view(request):
    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""

        # Clear old admin-console session on normal login
        request.session.pop("admin_staff_id", None)

        # Try login using staff_name first
        staff = Staff.objects.filter(staff_name=username).first()

        # If not found, try login using Django username through Account
        if not staff:
            account = Account.objects.select_related("user").filter(user__username=username).first()
            if account:
                staff = Staff.objects.filter(staff_name=account.staff_name).first()

        if not staff:
            messages.error(request, "Invalid username or password.")
            return render(request, "jsquared_app/login.html")

        if password != (staff.staff_password or ""):
            messages.error(request, "Invalid username or password.")
            return render(request, "jsquared_app/login.html")

        request.session["staff_id"] = staff.staff_id
        request.session["staff_role"] = staff.staff_role
        return redirect("home")

    return render(request, "jsquared_app/login.html")


def logout_view(request):
    request.session.flush()
    return redirect("login")

# ============================================================
# ADMIN CONSOLE
# ============================================================

def admin_login_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        admin_staff_id = request.session.get("admin_staff_id")
        if not admin_staff_id:
            return redirect("manager_login")

        staff = Staff.objects.filter(staff_id=admin_staff_id).first()
        if not staff or staff.staff_role != "Manager":
            request.session.pop("admin_staff_id", None)
            messages.error(request, "Manager account required.")
            return redirect("manager_login")

        return view_func(request, *args, **kwargs)
    return wrapper


def manager_login(request):
    if request.method == 'POST':
        # always clear old admin-console session before checking new credentials
        request.session.pop("admin_staff_id", None)

        username = (request.POST.get('username') or '').strip()
        password = request.POST.get('password') or ''

        staff = None

        # Try manager login via staff_name
        possible_staff = Staff.objects.filter(staff_name=username, staff_role="Manager").first()
        if possible_staff and possible_staff.staff_password == password:
            staff = possible_staff

        # If not found, try via Django username linked through Account
        if not staff:
            account = Account.objects.select_related("user").filter(user__username=username).first()
            if account:
                possible_staff = Staff.objects.filter(
                    staff_name=account.staff_name,
                    staff_role="Manager"
                ).first()
                if possible_staff and possible_staff.staff_password == password:
                    staff = possible_staff

        if not staff:
            error = 'Manager account required.'
            return render(request, 'jsquared_app/login.html', {'error': error})

        request.session['admin_staff_id'] = staff.staff_id
        request.session[SESSION_STAFF_ID] = staff.staff_id
        request.session[SESSION_STAFF_ROLE] = staff.staff_role
        request.session.modified = True
        return redirect('admin_console')

    return render(request, 'jsquared_app/admin_console.html')


@admin_login_required
def admin_console(request):
    _ensure_default_discounts()
    stats = {
        "accounts": Staff.objects.count(),
        "suppliers": Supplier.objects.count(),
        "discounts": Discount.objects.count(),
        "audit_logs": AuditLog.objects.count(),
        "completed_orders": Order.objects.filter(order_status__in=["Completed", "Served"]).count(),
    }
    return render(request, "jsquared_app/admin_console.html", {"stats": stats})


@admin_login_required
def account_list(request):
    accounts = Staff.objects.all()
    return render(request, "jsquared_app/account_list.html", {"accounts": accounts})


@admin_login_required
def account_detail(request, account_id):
    account = get_object_or_404(Staff, staff_id=account_id)
    errors = []

    linked_account = Account.objects.select_related("user").filter(staff_name=account.staff_name).first()
    current_username = linked_account.user.username if linked_account and linked_account.user else account.staff_email

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "delete":
            if linked_account and linked_account.user:
                linked_account.user.delete()
            else:
                try:
                    User.objects.get(username=current_username).delete()
                except User.DoesNotExist:
                    pass

            if linked_account:
                linked_account.delete()

            account.delete()
            messages.success(request, "Account deleted.")
            return redirect("account_list")

        if action == "save":
            new_staff_name = request.POST.get("staff_name", account.staff_name).strip()
            new_role = request.POST.get("role", account.staff_role).strip()
            new_username = request.POST.get("username", current_username).strip()
            new_pw = request.POST.get("new_password", "").strip()
            confirm_pw = request.POST.get("confirm_password", "").strip()

            if not new_staff_name:
                errors.append("Staff name is required")

            if not new_username:
                errors.append("Username is required")

            if new_pw and new_pw != confirm_pw:
                errors.append("Passwords do not match")

            existing_user = User.objects.filter(username=new_username).exclude(username=current_username).first()
            if existing_user:
                errors.append("Username already exists")

            if not errors:
                account.staff_name = new_staff_name
                account.staff_role = new_role
                account.staff_email = new_username

                if new_pw:
                    account.staff_password = new_pw

                account.save()

                if linked_account:
                    linked_account.staff_name = new_staff_name
                    linked_account.role = new_role
                    linked_account.save()

                    if linked_account.user:
                        linked_account.user.username = new_username
                        if new_pw:
                            linked_account.user.set_password(new_pw)
                        linked_account.user.save()
                else:
                    user = User.objects.filter(username=current_username).first()
                    if user:
                        user.username = new_username
                        if new_pw:
                            user.set_password(new_pw)
                        user.save()

                messages.success(request, "Account updated.")
                return redirect("account_detail", account_id=account.staff_id)

    return render(request, "jsquared_app/account_detail.html", {
        "account": account,
        "errors": errors,
        "current_username": current_username,
    })


@admin_login_required
def account_create(request):
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")
        staff_name = request.POST.get("staff_name")
        role = request.POST.get("role")
        confirm_password = request.POST.get("confirm_password")

        if password != confirm_password:
            messages.error(request, "Passwords do not match")
            return redirect("account_create")

        if not staff_name:
            messages.error(request, "Staff name is required")
            return redirect("account_create")

        if User.objects.filter(username=username).exists() or Staff.objects.filter(staff_name=staff_name).exists():
            messages.error(request, "Username already exists")
            return redirect("account_create")

        user = User.objects.create_user(
            username=username,
            password=password
        )

        Account.objects.create(
            user=user,
            staff_name=staff_name,
            role=role
        )

        Staff.objects.create(
            staff_name=staff_name,
            staff_role=role.capitalize(),
            staff_email=username, 
            staff_password=password 
        )

        return redirect("account_list")

    return render(request, "jsquared_app/account_create.html")



@admin_login_required
def sales_report(request):
    start_date = (request.GET.get("start_date") or "").strip()
    end_date = (request.GET.get("end_date") or "").strip()

    orders = Order.objects.filter(order_status__in=["Completed", "Served"]).order_by("-created_at")
    if start_date:
        orders = orders.filter(created_at__date__gte=start_date)
    if end_date:
        orders = orders.filter(created_at__date__lte=end_date)

    return render(request, "jsquared_app/sales_report.html", {
        "orders": orders,
        "start_date": start_date,
        "end_date": end_date,
    })


@admin_login_required
def sales_report_export_csv(request):
    start_date = (request.GET.get("start_date") or "").strip()
    end_date = (request.GET.get("end_date") or "").strip()
    orders = Order.objects.filter(order_status__in=["Completed", "Served"]).order_by("-created_at")
    if start_date:
        orders = orders.filter(created_at__date__gte=start_date)
    if end_date:
        orders = orders.filter(created_at__date__lte=end_date)

    import csv
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="sales_report.csv"'
    writer = csv.writer(response)
    writer.writerow(["Order ID", "Total Sales", "Date Completed"])
    for order in orders:
        writer.writerow([
            f"Order ID #{order.order_id}",
            float(order.total_amount or 0),
            timezone.localtime(order.created_at).strftime('%m/%d/%Y'),
        ])
    return response


@admin_login_required
def sales_report_export_xlsx(request):
    start_date = (request.GET.get("start_date") or "").strip()
    end_date = (request.GET.get("end_date") or "").strip()
    orders = Order.objects.filter(order_status__in=["Completed", "Served"]).order_by("-created_at")
    if start_date:
        orders = orders.filter(created_at__date__gte=start_date)
    if end_date:
        orders = orders.filter(created_at__date__lte=end_date)

    wb = Workbook()
    ws = wb.active
    ws.title = 'Sales Report'
    ws.append(["Order ID", "Total Sales", "Date Completed"])
    for order in orders:
        ws.append([
            f"Order ID #{order.order_id}",
            float(order.total_amount or 0),
            timezone.localtime(order.created_at).strftime('%m/%d/%Y'),
        ])

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    response = HttpResponse(output.read(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="sales_report.xlsx"'
    return response


@admin_login_required
def sales_report_print(request):
    return sales_report(request)

# ============================================================
# HOME
# ============================================================

@staff_login_required
def home(request):
    staff = _current_staff(request)
    role = request.session.get(SESSION_STAFF_ROLE)
    meat_items = MeatItem.objects.all().order_by('-price_updated_at')

    return render(
        request,
        "jsquared_app/home.html",
        {
            "staff": staff,
            "is_manager": role == "Manager",
            "is_cashier": role == "Cashier",
            "is_staff": role == "Staff",
            "meat_items": meat_items,
        },
    )


# ============================================================
# PRICES — MEAT (UC11–UC13)
# ============================================================

@staff_login_required
@require_roles("Staff", "Manager")

def meat_price_list(request):
    meat = MeatItem.objects.order_by("meat_type")
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()
    min_price = (request.GET.get("min_price") or "").strip()
    max_price = (request.GET.get("max_price") or "").strip()

    if q:
        meat = meat.filter(meat_type__icontains=q)
    if status:
        meat = meat.filter(item_status=status)
    if min_price:
        try:
            meat = meat.filter(current_price__gte=float(min_price))
        except ValueError:
            pass
    if max_price:
        try:
            meat = meat.filter(current_price__lte=float(max_price))
        except ValueError:
            pass

    return render(request, "jsquared_app/meat_price_list.html", {
        "meat": meat,
        "q": q,
        "status_filter": status,
        "min_price": min_price,
        "max_price": max_price,
    })


@staff_login_required
@require_roles("Staff", "Manager")
def meat_price_create(request):
    if request.method == "POST":
        MeatItem.objects.create(
            meat_type=(request.POST.get("meat_type") or "").strip(),
            current_price=float(request.POST.get("current_price") or 0),
            weight_min=float(request.POST.get("weight_min") or 0),
            weight_max=float(request.POST.get("weight_max") or 0),
            item_status=request.POST.get("item_status") or "Available",
        )
        return redirect("meat_price_list")

    return render(request, "jsquared_app/meat_price_create.html")


@staff_login_required
@require_roles("Staff", "Manager")
def meat_price_edit(request, meat_id: int):
    m = get_object_or_404(MeatItem, meat_id=meat_id)

    if request.method == "POST":
        m.meat_type = (request.POST.get("meat_type") or m.meat_type).strip()
        m.current_price = float(request.POST.get("current_price") or m.current_price)
        m.weight_min = float(request.POST.get("weight_min") or m.weight_min)
        m.weight_max = float(request.POST.get("weight_max") or m.weight_max)
        m.item_status = request.POST.get("item_status") or m.item_status
        m.save()
        return redirect("meat_price_list")

    return render(request, "jsquared_app/meat_price_edit.html", {"m": m})


@staff_login_required
@require_roles("Staff", "Manager")
def meat_price_delete(request, meat_id: int):
    m = get_object_or_404(MeatItem, meat_id=meat_id)

    if request.method == "POST":
        m.delete()
        return redirect("meat_price_list")

    return render(request, "jsquared_app/meat_price_delete.html", {"m": m})


# ============================================================
# ORDERS (UC1–UC4) 
# ============================================================

@staff_login_required
@require_roles("Staff", "Cashier", "Manager")
def order_list(request):
    role = request.session.get(SESSION_STAFF_ROLE)
    active_statuses = ["Pending", "Preparing"]
    base_qs = Order.objects.filter(order_status__in=active_statuses).order_by("-created_at")

    return render(request, "jsquared_app/order_list.html", {
        "pending_orders": base_qs.filter(order_status="Pending"),
        "preparing_orders": base_qs.filter(order_status="Preparing"),
        "is_manager": role == "Manager",
        "is_cashier": role == "Cashier",
        "is_staff": role == "Staff",
    })


@staff_login_required
@require_roles("Staff", "Cashier", "Manager")
def order_history(request):
    _ensure_default_discounts()
    role = request.session.get(SESSION_STAFF_ROLE)
    history_orders = Order.objects.filter(order_status__in=["Completed", "Cancelled", "Served"]).order_by("-created_at")
    return render(request, "jsquared_app/order_history.html", {
        "history_orders": history_orders,
        "is_manager": role == "Manager",
        "is_cashier": role == "Cashier",
        "is_staff": role == "Staff",
    })


import json

@staff_login_required
@require_roles("Staff", "Manager")
def order_create(request):
    staff = _current_staff(request)

    if request.method == "POST":
        customer_name = request.POST.get("customer_name") or None
        table_num = int(request.POST.get("table_num") or 1)
        items_json = request.POST.get("items_json") or "[]"

        order = Order.objects.create(
            staff=staff,
            table_num=table_num,
            customer_name=customer_name,
            order_status="Pending",
            payment_status="Unpaid",
            payment_method="Cash",
            diner_count=1,
            eligible_count=1,
        )

        try:
            items = json.loads(items_json)
            for item in items:
                if item["type"] == "varied":
                    varied = get_object_or_404(
                        VariedMenuItem,
                        meat_id=item["meatId"],
                        cooking_style_id=item["styleId"],
                    )

                    oi = OrderItem.objects.create(
                        order=order,
                        varied_item=varied,
                        order_quantity=item["weight"],
                    )

                    if item.get("is_byom", False):
                        OrderItem.objects.filter(order_item_id=oi.order_item_id).update(
                            order_unit_price=0.0,
                            cooking_charge=float(varied.item_price),
                            subtotal=float(item["weight"]) * float(varied.item_price),
                        )

                elif item["type"] == "fixed":
                    fixed = get_object_or_404(FixedMenuItem, fixed_item_id=item["fixedId"])
                    OrderItem.objects.create(
                        order=order,
                        fixed_item=fixed,
                        order_quantity=item["qty"],
                    )
        except Exception:
            pass

        order.recompute_total()
        messages.success(request, "Order placed!")
        return redirect("order_list")

    _ensure_varied_items_synced()

    meats = MeatItem.objects.filter(item_status="Available").order_by("meat_type")
    cooking_styles = CookingStyle.objects.all()
    fixed_items = FixedMenuItem.objects.all()

    cooking_styles_json = json.dumps([
        {"id": s.cooking_style_id, "name": s.style_name, "price": s.cooking_charge}
        for s in cooking_styles
    ])

    varied_items = VariedMenuItem.objects.select_related("meat", "cooking_style").all()
    varied_items_json = json.dumps([
        {
            "meat_id": v.meat_id,
            "style_id": v.cooking_style_id,
            "style_name": v.cooking_style.style_name,
            "add_on_price": v.item_price,
            "is_byom": v.is_byom,
        }
        for v in varied_items
    ])

    return render(request, "jsquared_app/order_create.html", {
        "meats": meats,
        "cooking_styles_json": cooking_styles_json,
        "varied_items_json": varied_items_json,
        "alacarte_items": fixed_items.filter(item_category__icontains="ala carte"),
        "drink_items": fixed_items.filter(item_category__icontains="drink"),
        "extra_items": fixed_items.exclude(
            item_category__icontains="ala carte"
        ).exclude(item_category__icontains="drink"),
        "weight_options": range(1, 21),
    })


@staff_login_required
@require_roles("Staff", "Cashier", "Manager")
def order_detail(request, order_id: int):
    order = get_object_or_404(Order, order_id=order_id)

    if order.order_status in ["Served", "Completed"]:
        return redirect("order_checkout", order_id=order.order_id)

    _ensure_varied_items_synced()

    fixed_items = FixedMenuItem.objects.order_by("item_name")
    varied_items = VariedMenuItem.objects.select_related("meat", "cooking_style").order_by(
        "meat__meat_type", "cooking_style__style_name"
    )

    menu_items = (
        [{"key": f"fixed:{f.fixed_item_id}", "label": f"{f.item_name}", "pricing_type": "F"} for f in fixed_items]
        + [{"key": f"varied:{v.varied_item_id}", "label": f"{v.meat.meat_type} — {v.cooking_style.style_name}", "pricing_type": "V"} for v in varied_items]
    )

    if request.method == "POST":
        # Only handle add-item POSTs, not supplier assignment POSTs
        if "menu_item_key" in request.POST:
            if order.order_status != "Pending":
                messages.error(request, "Items can only be added while the order is pending.")
                return redirect("order_detail", order_id=order.order_id)

            key = (request.POST.get("menu_item_key") or "").strip()
            qty = float(request.POST.get("order_quantity") or 1)
            if qty <= 0:
                qty = 1

            if ":" not in key:
                return HttpResponseForbidden("Invalid menu item.")

            kind, raw_id = key.split(":", 1)
            item_id = int(raw_id)

            is_byom = request.POST.get("is_byom") == "1"

            if kind == "fixed":
                fixed_item = get_object_or_404(FixedMenuItem, fixed_item_id=item_id)
                OrderItem.objects.create(order=order, fixed_item=fixed_item, order_quantity=qty)
            elif kind == "varied":
                varied_item = get_object_or_404(VariedMenuItem, varied_item_id=item_id)
                oi = OrderItem.objects.create(order=order, varied_item=varied_item, order_quantity=qty)
                if is_byom:
                    cooking_charge = float(varied_item.cooking_style.cooking_charge)
                    OrderItem.objects.filter(order_item_id=oi.order_item_id).update(
                        order_unit_price=0.0,
                        cooking_charge=cooking_charge,
                        subtotal=float(qty) * cooking_charge,
                    )
            else:
                return HttpResponseForbidden("Invalid menu item type.")

            order.recompute_total()
            messages.success(request, "Item added to order.")
            return redirect("order_detail", order_id=order.order_id)

    order.recompute_total()
    return render(request, "jsquared_app/order_detail.html", {"order": order, "menu_items": menu_items})


@staff_login_required
@require_roles("Staff", "Cashier", "Manager")
def order_accept(request, order_id: int):
    order = get_object_or_404(Order, order_id=order_id)
    if request.method == "POST":
        if order.order_status == "Pending":
            order.order_status = "Preparing"
            order.save(update_fields=["order_status"])
    return redirect("order_list")


@staff_login_required
@require_roles("Staff", "Cashier", "Manager")
def order_cancel(request, order_id: int):
    order = get_object_or_404(Order, order_id=order_id)
    if request.method == "POST":
        order.order_status = "Cancelled"
        order.save(update_fields=["order_status"])
        messages.success(request, f"Order #{order.order_id:03d} cancelled.")
    return redirect("order_history" if order.order_status == "Cancelled" else "order_list")


@staff_login_required
@require_roles("Staff", "Cashier", "Manager")
@require_POST
def order_serve(request, order_id: int):
    order = get_object_or_404(Order, order_id=order_id)

    if order.order_status != "Preparing":
        messages.error(request, "Only preparing orders can be completed.")
        return redirect("order_detail", order_id=order.order_id)

    if not order.items.exists():
        messages.error(request, "Cannot complete an empty order.")
        return redirect("order_detail", order_id=order.order_id)

    if _order_has_missing_required_suppliers(order):
        messages.error(request, "Select a supplier for every meat item before completing the order.")
        return redirect("order_detail", order_id=order.order_id)

    order.recompute_total()
    order.order_status = "Completed"
    order.save(update_fields=["order_status"])
    messages.success(request, f"Order #{order.order_id:03d} completed and moved to history.")
    return redirect("order_list")


@staff_login_required
@require_roles("Cashier", "Manager")
@require_POST
def order_update_payment(request, order_id: int):
    order = get_object_or_404(Order, order_id=order_id)

    if order.order_status == "Cancelled":
        messages.error(request, "You cannot update payment for a cancelled order.")
        return redirect("order_history")

    payment_method = request.POST.get("payment_method") or order.payment_method
    payment_status = request.POST.get("payment_status") or order.payment_status

    valid_methods = {"Cash", "Card", "Online payment"}
    valid_statuses = {"Unpaid", "Paid"}

    if payment_method not in valid_methods or payment_status not in valid_statuses:
        messages.error(request, "Invalid payment details.")
        return redirect("order_detail", order_id=order.order_id)

    order.payment_method = payment_method
    order.payment_status = payment_status
    order.save(update_fields=["payment_method", "payment_status"])
    messages.success(request, f"Payment updated for Order #{order.order_id:03d}.")
    return redirect("order_checkout", order_id=order.order_id)


@staff_login_required
@require_roles("Cashier", "Manager")
def order_update_discount(request, order_id: int):
    order = get_object_or_404(Order, order_id=order_id)

    if request.method == "POST":
        discount_id = (request.POST.get("discount_id") or "").strip()

        order.discount = None
        order.discount_target_amount = None
        order.suki_discount_percent = None

        order.diner_count = max(int(request.POST.get("diner_count") or 1), 1)
        order.pwd_count = max(int(request.POST.get("pwd_count") or 0), 0)
        order.senior_count = max(int(request.POST.get("senior_count") or 0), 0)

        total_special = order.pwd_count + order.senior_count
        if total_special > order.diner_count:
            overflow = total_special - order.diner_count
            if order.senior_count >= overflow:
                order.senior_count -= overflow
            else:
                remaining = overflow - order.senior_count
                order.senior_count = 0
                order.pwd_count = max(order.pwd_count - remaining, 0)

        order.eligible_count = order.pwd_count + order.senior_count

        if discount_id:
            discount = get_object_or_404(Discount, discount_id=int(discount_id))
            order.discount = discount

            if discount.discount_type == "Suki":
                raw_suki = (request.POST.get("suki_discount_percent") or "").strip()
                try:
                    order.suki_discount_percent = float(raw_suki) if raw_suki else None
                except ValueError:
                    order.suki_discount_percent = None

        order.save(
            update_fields=[
                "discount",
                "discount_target_amount",
                "diner_count",
                "pwd_count",
                "senior_count",
                "eligible_count",
                "suki_discount_percent",
            ]
        )
        order.recompute_total()
        messages.success(request, "Discount details updated.")

    return redirect("order_checkout", order_id=order.order_id)


@staff_login_required
@require_roles("Cashier", "Manager")
def order_checkout(request, order_id: int):
    _ensure_default_discounts()
    order = get_object_or_404(Order, order_id=order_id)

    if order.order_status not in ["Completed", "Served"]:
        messages.error(request, "Only completed orders from history can be settled.")
        return redirect("order_history")

    order.recompute_total()
    discount_breakdown = order.compute_discount_breakdown()
    selected_discount_type = order.discount.discount_type if order.discount_id else ""

    return render(
        request,
        "jsquared_app/order_checkout.html",
        {
            "order": order,
            "discounts": Discount.objects.all().order_by("discount_type", "discount_value"),
            "discount_breakdown": discount_breakdown,
            "selected_discount_type": selected_discount_type,
            "is_manager": request.session.get(SESSION_STAFF_ROLE) == "Manager",
            "is_cashier": request.session.get(SESSION_STAFF_ROLE) == "Cashier",
            "is_staff": request.session.get(SESSION_STAFF_ROLE) == "Staff",
        },
    )


@staff_login_required
@require_roles("Cashier", "Manager")
@require_POST
def order_complete(request, order_id: int):
    order = get_object_or_404(Order, order_id=order_id)

    if order.order_status not in ["Preparing", "Served"]:
        messages.error(request, "Only active orders can be completed.")
        return redirect("order_detail", order_id=order.order_id)

    if _order_has_missing_required_suppliers(order):
        messages.error(request, "Select a supplier for every meat item before completing the order.")
        return redirect("order_detail", order_id=order.order_id)

    order.recompute_total()
    order.order_status = "Completed"
    order.save(update_fields=["order_status"])
    messages.success(request, f"Order #{order.order_id:03d} completed and moved to history.")
    return redirect("order_list")


@staff_login_required
@require_roles("Staff")
def order_item_delete(request, order_id: int, order_item_id: int):
    order = get_object_or_404(Order, order_id=order_id)
    item = get_object_or_404(OrderItem, order_item_id=order_item_id, order=order)

    if order.order_status != "Pending":
        messages.error(request, "Items can only be removed while the order is pending.")
        return redirect("order_detail", order_id=order.order_id)

    if request.method == "POST":
        item.delete()
        order.recompute_total()
        messages.success(request, "Order item removed.")
        return redirect("order_detail", order_id=order.order_id)

    return render(request, "jsquared_app/order_item_delete.html", {"order": order, "item": item})


@staff_login_required
@require_roles("Manager")
def order_delete(request, order_id: int):
    order = get_object_or_404(Order, order_id=order_id)

    if request.method == "POST":
        order.delete()
        return redirect("order_list")

    return render(request, "jsquared_app/order_delete.html", {"order": order})


# ============================================================
# INQUIRIES (UC14–UC16)
# ============================================================

@staff_login_required
@require_roles("Staff", "Manager")
def inquiry_list(request):
    staff = _current_staff(request)
    qs = PriceInquiryRequest.objects.select_related("meat", "requested_by", "accepted_by")

    if request.session.get(SESSION_STAFF_ROLE) != "Manager":
        qs = qs.filter(requested_by=staff)

    return render(
        request,
        "jsquared_app/inquiry_list.html",
        {
            "queued": qs.filter(status="Queued"),
            "pending": qs.filter(status="Pending"),
            "completed": qs.filter(status="Completed"),
            "cancelled": qs.filter(status="Cancelled"),
            "completed_count": qs.filter(status="Completed").count(),
            "is_manager": request.session.get(SESSION_STAFF_ROLE) == "Manager",
        },
    )


@staff_login_required
@require_roles("Staff", "Manager")
def inquiry_create(request):
    staff = _current_staff(request)
    meats = MeatItem.objects.order_by("meat_type")

    if request.method == "POST":
        meat_ids = request.POST.getlist("meat_ids") 
        if not meat_ids:
            messages.error(request, "Please select at least one item.")
            return render(request, "jsquared_app/inquiry_create.html", {"meats": meats})

        for meat_id in meat_ids:
            meat = get_object_or_404(MeatItem, meat_id=int(meat_id))
            PriceInquiryRequest.objects.create(
                meat=meat, requested_by=staff, status="Queued"
            )

        messages.success(request, "Price requests created!")
        return redirect("inquiry_list")

    return render(request, "jsquared_app/inquiry_create.html", {"meats": meats})


@staff_login_required
@require_roles("Staff", "Manager")
def inquiry_accept(request, inquiry_id: int):
    staff = _current_staff(request)
    req = get_object_or_404(PriceInquiryRequest, inquiry_id=inquiry_id)

    if request.method == "POST":
        if req.status != "Queued":
            return HttpResponseForbidden("Only queued requests can be accepted.")

        req.status = "Pending"
        req.accepted_by = staff
        req.accepted_at = timezone.now()
        req.save(update_fields=["status", "accepted_by", "accepted_at"])
        return redirect("inquiry_list")

    return render(request, "jsquared_app/inquiry_accept.html", {"req": req})


@staff_login_required
@require_roles("Staff", "Manager")
def inquiry_delete(request, inquiry_id: int):
    staff = _current_staff(request)
    req = get_object_or_404(PriceInquiryRequest, inquiry_id=inquiry_id)

    is_manager = request.session.get(SESSION_STAFF_ROLE) == "Manager"
    is_owner = req.requested_by_id == staff.staff_id

    if not (is_manager or is_owner):
        return HttpResponseForbidden("Not allowed.")

    if request.method == "POST":
        req.status = "Cancelled"
        req.save(update_fields=["status"])
        return redirect("inquiry_list")

    return render(request, "jsquared_app/inquiry_delete.html", {"req": req})


@staff_login_required
@require_roles("Staff", "Manager")
def inquiry_update_price(request, inquiry_id: int):
    req = get_object_or_404(PriceInquiryRequest, inquiry_id=inquiry_id)

    if req.status != "Pending":
        return HttpResponseForbidden("Only pending requests can be updated.")

    if request.method == "POST":
        raw_price = (request.POST.get("new_price") or "").strip()
        notes = (request.POST.get("notes") or "").strip() or None

        if raw_price == "":
            messages.error(request, "Please enter the new price")
            return redirect("inquiry_update_price", inquiry_id=req.inquiry_id)

        new_price = float(raw_price)

        req.new_price = new_price
        req.notes = notes
        req.responded_at = timezone.now()
        req.status = "Completed"
        req.save(update_fields=["new_price", "notes", "responded_at", "status"])

        meat = req.meat
        meat.current_price = new_price
        meat.save()

        messages.success(request, "Price Successfully updated!")
        return redirect("inquiry_list")

    return render(request, "jsquared_app/inquiry_update_price.html", {"req": req})


@staff_login_required
def meat_detail(request, meat_id):
    item = get_object_or_404(MeatItem, meat_id=meat_id)
    
    if request.method == 'POST':
        item.meat_type        = request.POST.get('meat_type', item.meat_type).strip()
        item.meat_description = request.POST.get('meat_description', '').strip() or None
        item.weight_min       = float(request.POST.get('weight_min', item.weight_min))
        item.weight_max       = float(request.POST.get('weight_max', item.weight_max))
        item.save()
        messages.success(request, 'Meat item updated.')
        return redirect('meat_detail', meat_id=item.meat_id)

    is_manager = _current_staff(request).staff_role == 'Manager' if _current_staff(request) else False
    is_staff   = _current_staff(request).staff_role == 'Staff'   if _current_staff(request) else False
    return render(request, 'jsquared_app/meat_detail.html', {
        'item': item,
        'is_manager': is_manager,
        'is_staff': is_staff,
    })


@staff_login_required

def cooking_styles_list(request):
    meat_items = MeatItem.objects.order_by("meat_type")
    q = (request.GET.get("q") or "").strip()
    if q:
        meat_items = meat_items.filter(
            models.Q(meat_type__icontains=q) | models.Q(cooking_styles__style_name__icontains=q)
        ).distinct()
    return render(request, "jsquared_app/cooking_styles_list.html", {"meat_items": meat_items, "q": q})


@staff_login_required
def cooking_style_create(request, meat_id: int):
    meat = get_object_or_404(MeatItem, meat_id=meat_id)

    if request.method == "POST":
        style_name = (request.POST.get("style_name") or "").strip()
        style_description = (request.POST.get("style_description") or "").strip()
        cooking_charge = float(request.POST.get("cooking_charge") or 0)
        c_weight_min = float(request.POST.get("c_weight_min") or 0)
        c_weight_max = float(request.POST.get("c_weight_max") or 0)
        icon = request.FILES.get("icon")

        CookingStyle.objects.create(
            meat_item=meat,
            style_name=style_name,
            style_description=style_description,
            cooking_charge=cooking_charge,
            c_weight_min=c_weight_min,
            c_weight_max=c_weight_max,
            icon=icon,
        )

        return redirect("meat_category", meat_id=meat.meat_id)

    return render(request, "jsquared_app/cooking_style_create.html", {"meat": meat})


@staff_login_required
def cooking_style_edit(request, cooking_style_id: int):
    c = get_object_or_404(CookingStyle, cooking_style_id=cooking_style_id)

    if request.method == "POST":
        c.style_name = (request.POST.get("style_name") or c.style_name).strip()
        c.style_description = (request.POST.get("style_description") or c.style_description).strip()
        c.cooking_charge = float(request.POST.get("cooking_charge") or c.cooking_charge)
        c.c_weight_min = float(request.POST.get("c_weight_min") or c.c_weight_min)
        c.c_weight_max = float(request.POST.get("c_weight_max") or c.c_weight_max)
        c.save()
        return redirect("meat_category", meat_id=c.meat_item_id)

    return render(request, "jsquared_app/cooking_style_edit.html", {"c": c})


@staff_login_required
def cooking_style_delete(request, cooking_style_id: int):
    c = get_object_or_404(CookingStyle, cooking_style_id=cooking_style_id)

    if request.method == "POST":
        c.delete()
        return redirect("cooking_styles_list")

    return render(request, "jsquared_app/cooking_style_delete.html", {"c": c})


@staff_login_required
def meat_category(request, meat_id: int):
    meat = get_object_or_404(MeatItem, meat_id=meat_id)
    styles = CookingStyle.objects.filter(meat_item=meat).order_by("style_name")

    return render(
        request,
        "jsquared_app/meat_category.html",
        {"meat": meat, "styles": styles},
    )

# ============================================================
# SUPPLIERS / TRANSACTIONS
# ============================================================

def _transaction_pending_filter():
    return Q(transactions__payment_status__in=["Pending", "Unpaid"])


def _supplier_pending_filter():
    return Q(payment_status__in=["Pending", "Unpaid"])


def _order_item_is_byom(order_item):
    return bool(order_item and getattr(order_item, "is_effective_byom", False))


def _order_item_requires_supplier(order_item):
    return bool(order_item and order_item.varied_item_id and not _order_item_is_byom(order_item))


def _order_has_missing_required_suppliers(order):
    return any(
        _order_item_requires_supplier(item) and not item.supplier_id
        for item in order.items.select_related("varied_item", "supplier")
    )


def _sync_supplier_transaction(order_item, supplier):
    if not supplier or not order_item:
        return

    meat_obj = order_item.varied_item.meat if order_item.varied_item_id else None
    item_name = None
    unit_price = 0.0
    quantity = float(order_item.order_quantity or 0)

    if order_item.varied_item_id and meat_obj:
        item_name = f"{meat_obj.meat_type} - {order_item.varied_item.cooking_style.style_name}"
        unit_price = float(order_item.order_unit_price or 0)
    elif order_item.fixed_item_id:
        item_name = order_item.fixed_item.item_name
        unit_price = float(order_item.order_unit_price or 0)

    auto_note = f"Auto-created from Order #{order_item.order.order_id}, Item #{order_item.order_item_id}"

    tx = SupplierTransaction.objects.filter(notes=auto_note).first()
    if tx:
        tx.supplier = supplier
        tx.meat = meat_obj
        tx.item_name = item_name
        tx.transaction_date = timezone.localdate()
        tx.unit_price = unit_price
        tx.quantity = quantity
        if tx.payment_status in ["Paid", "Unpaid", "", None]:
            tx.payment_status = "Pending" if tx.payment_status in ["Unpaid", "", None] else "Completed"
        tx.save()
        return tx

    return SupplierTransaction.objects.create(
        supplier=supplier,
        meat=meat_obj,
        item_name=item_name,
        transaction_date=timezone.localdate(),
        unit_price=unit_price,
        quantity=quantity,
        payment_status="Pending",
        notes=auto_note,
    )


def _parse_transaction_form(request, supplier, transaction=None):
    errors = []
    item_input = (request.POST.get("item_name") or "").strip()
    transaction_date = (request.POST.get("transaction_date") or "").strip()
    unit_price_raw = (request.POST.get("unit_price") or "").strip()
    quantity_raw = (request.POST.get("quantity") or "").strip()
    payment_status = (request.POST.get("payment_status") or "Pending").strip()
    notes = (request.POST.get("notes") or "").strip() or None

    meat = MeatItem.objects.filter(meat_type__iexact=item_input).order_by("meat_type").first() if item_input else None
    item_name = meat.meat_type if meat else (item_input or None)

    parsed_transaction_date = None
    if not transaction_date:
        errors.append("Transaction date is required.")
    else:
        try:
            parsed_transaction_date = datetime.strptime(transaction_date, "%Y-%m-%d").date()
        except ValueError:
            errors.append("Transaction date must use YYYY-MM-DD format.")
    try:
        unit_price = float(unit_price_raw)
    except (TypeError, ValueError):
        unit_price = None
    if unit_price is None:
        errors.append("Unit price must be numeric.")
    elif unit_price <= 0:
        errors.append("Unit price must be greater than zero.")

    try:
        quantity = float(quantity_raw)
    except (TypeError, ValueError):
        quantity = None
    if quantity is None:
        errors.append("Quantity must be numeric.")
    elif quantity <= 0:
        errors.append("Quantity must be greater than zero.")

    if payment_status not in [choice[0] for choice in SupplierTransaction.PAYMENT_CHOICES]:
        payment_status = "Pending"

    if not item_name:
        errors.append("Meat item is required.")

    data = {
        "supplier": supplier,
        "meat": meat,
        "item_name": item_name,
        "transaction_date": parsed_transaction_date or timezone.localdate(),
        "unit_price": unit_price or 0,
        "quantity": quantity or 0,
        "payment_status": payment_status,
        "notes": notes,
    }

    if errors:
        return None, errors, data

    tx = transaction or SupplierTransaction(supplier=supplier)
    for key, value in data.items():
        setattr(tx, key, value)
    return tx, [], data


@staff_login_required
@require_roles("Manager")
def supplier_list(request):
    q = (request.GET.get("q") or "").strip()
    suppliers = (
        Supplier.objects.annotate(
            transaction_count=Count("transactions", distinct=True),
            total_transaction_amount=Sum("transactions__transaction_amount"),
            pending_transaction_amount=Sum("transactions__transaction_amount", filter=_transaction_pending_filter()),
            last_transaction_date=Max("transactions__transaction_date"),
        )
        .prefetch_related("transactions__meat")
        .order_by("supplier_name")
    )

    if request.method == "POST":
        supplier_id = (request.POST.get("supplier_id") or "").strip()
        order_item_id = (request.POST.get("order_item_id") or "").strip()
        return_url = (request.POST.get("return_url") or "").strip() or "supplier_list"

        if supplier_id and order_item_id:
            try:
                supplier = Supplier.objects.get(supplier_id=int(supplier_id))
                order_item = OrderItem.objects.select_related(
                    "order", "supplier", "varied_item__meat", "varied_item__cooking_style", "fixed_item"
                ).get(order_item_id=int(order_item_id))

                if not _order_item_requires_supplier(order_item):
                    order_item.supplier = None
                    order_item.save(update_fields=["supplier"])
                    if order_item.fixed_item_id:
                        messages.info(request, "Fixed-price items do not require a supplier.")
                    else:
                        messages.info(request, "BYOM items do not require a supplier.")
                else:
                    order_item.supplier = supplier
                    order_item.save(update_fields=["supplier"])
                    _sync_supplier_transaction(order_item, supplier)
                    messages.success(request, "Supplier assigned and transaction recorded.")
            except (Supplier.DoesNotExist, OrderItem.DoesNotExist, ValueError):
                messages.error(request, "Unable to assign supplier.")

        try:
            return redirect(return_url)
        except Exception:
            return redirect("supplier_list")

    if q:
        suppliers = suppliers.filter(supplier_name__icontains=q)

    suppliers = list(suppliers)
    for supplier in suppliers:
        supplier.recent_transactions = list(supplier.transactions.select_related("meat").order_by("-transaction_date", "-transaction_id")[:3])
        supplier.total_transaction_amount = float(supplier.total_transaction_amount or 0)
        supplier.pending_transaction_amount = float(supplier.pending_transaction_amount or 0)

    overall_pending_payment = float(
        SupplierTransaction.objects.filter(_supplier_pending_filter()).aggregate(total=Sum("transaction_amount"))["total"] or 0
    )

    order_item_id = request.GET.get("order_item_id") or ""

    return render(request, "jsquared_app/supplier_list.html", {
        "suppliers": suppliers,
        "q": q,
        "return_url": request.GET.get("return_url") or "",
        "order_item_id": order_item_id,
        "selection_mode": bool(order_item_id),
        "overall_pending_payment": overall_pending_payment,
    })

@staff_login_required
@require_roles("Manager")
def supplier_create(request):
    if request.method == "POST":
        Supplier.objects.create(
            supplier_name=(request.POST.get("supplier_name") or "").strip(),
            contact_person=(request.POST.get("contact_person") or "").strip() or None,
            phone_number=(request.POST.get("phone_number") or "").strip(),
            supplier_address=(request.POST.get("supplier_address") or "").strip(),
        )
        messages.success(request, "Supplier created.")
        return redirect("supplier_list")
    return render(request, "jsquared_app/supplier_form.html", {"mode": "create"})


@staff_login_required
@require_roles("Manager")
def supplier_detail(request, supplier_id: int):
    supplier = get_object_or_404(
        Supplier.objects.prefetch_related("transactions__meat"),
        supplier_id=supplier_id,
    )
    meats = MeatItem.objects.order_by("meat_type")
    transactions = supplier.transactions.select_related("meat").order_by("-transaction_date", "-transaction_id")

    start_date = (request.GET.get("start_date") or "").strip()
    end_date = (request.GET.get("end_date") or "").strip()
    if start_date:
        transactions = transactions.filter(transaction_date__gte=start_date)
    if end_date:
        transactions = transactions.filter(transaction_date__lte=end_date)

    edit_tx = None
    tx_form = {"payment_status": "Pending", "transaction_date": timezone.localdate().strftime("%Y-%m-%d")}

    edit_tx_id = (request.GET.get("edit_tx") or "").strip()
    if edit_tx_id.isdigit():
        edit_tx = supplier.transactions.filter(transaction_id=int(edit_tx_id)).select_related("meat").first()
        if edit_tx:
            tx_form = {
                "item_name": (edit_tx.meat.meat_type if edit_tx.meat_id else (edit_tx.item_name or "")),
                "transaction_date": edit_tx.transaction_date.strftime("%Y-%m-%d") if edit_tx.transaction_date else "",
                "unit_price": edit_tx.unit_price,
                "quantity": edit_tx.quantity,
                "payment_status": edit_tx.payment_status if edit_tx.payment_status in ["Pending", "Completed"] else ("Pending" if edit_tx.payment_status == "Unpaid" else "Completed"),
                "notes": edit_tx.notes or "",
            }

    if request.method == "POST":
        supplier.supplier_name = (request.POST.get("supplier_name") or supplier.supplier_name).strip()
        supplier.contact_person = (request.POST.get("contact_person") or "").strip() or None
        supplier.phone_number = (request.POST.get("phone_number") or supplier.phone_number).strip()
        supplier.supplier_address = (request.POST.get("supplier_address") or supplier.supplier_address).strip()
        supplier.save()
        messages.success(request, "Supplier updated.")
        return redirect("supplier_detail", supplier_id=supplier.supplier_id)

    total_pending_payment = float(
        supplier.transactions.filter(_supplier_pending_filter()).aggregate(total=Sum("transaction_amount"))["total"] or 0
    )

    return render(
        request,
        "jsquared_app/supplier_detail.html",
        {
            "supplier": supplier,
            "meats": meats,
            "transactions": transactions,
            "total_pending_payment": total_pending_payment,
            "edit_tx": edit_tx,
            "tx_form": tx_form,
            "start_date": start_date,
            "end_date": end_date,
            "transaction_count": transactions.count(),
        },
    )


@staff_login_required
@require_roles("Manager")
def supplier_delete(request, supplier_id: int):
    supplier = get_object_or_404(Supplier, supplier_id=supplier_id)
    if request.method == "POST":
        supplier.delete()
        messages.success(request, "Supplier deleted.")
        return redirect("supplier_list")
    return render(request, "jsquared_app/supplier_delete.html", {"supplier": supplier})


@staff_login_required
@require_roles("Manager")
def supplier_transaction_create(request, supplier_id: int):
    supplier = get_object_or_404(Supplier, supplier_id=supplier_id)
    if request.method == "POST":
        tx, errors, _data = _parse_transaction_form(request, supplier)
        if errors:
            for error in errors:
                messages.error(request, error)
            return redirect(f"{reverse('supplier_detail', args=[supplier.supplier_id])}")
        tx.save()
        messages.success(request, "Supplier transaction recorded.")
        return redirect("supplier_detail", supplier_id=supplier.supplier_id)
    return redirect("supplier_detail", supplier_id=supplier.supplier_id)


@staff_login_required
@require_roles("Manager")
def supplier_transaction_update(request, supplier_id: int, transaction_id: int):
    supplier = get_object_or_404(Supplier, supplier_id=supplier_id)
    transaction = get_object_or_404(SupplierTransaction, transaction_id=transaction_id, supplier=supplier)
    if request.method == "POST":
        tx, errors, _data = _parse_transaction_form(request, supplier, transaction=transaction)
        if errors:
            for error in errors:
                messages.error(request, error)
            return redirect(f"{reverse('supplier_detail', args=[supplier.supplier_id])}?edit_tx={transaction.transaction_id}")
        tx.save()
        messages.success(request, "Supplier transaction updated.")
    return redirect("supplier_detail", supplier_id=supplier.supplier_id)


@staff_login_required
@require_roles("Manager")
def supplier_transaction_delete(request, supplier_id: int, transaction_id: int):
    supplier = get_object_or_404(Supplier, supplier_id=supplier_id)
    transaction = get_object_or_404(SupplierTransaction, transaction_id=transaction_id, supplier=supplier)
    if request.method == "POST":
        transaction.delete()
        messages.success(request, "Supplier transaction deleted.")
    return redirect("supplier_detail", supplier_id=supplier.supplier_id)


# ============================================================
# DISCOUNT MANAGEMENT
# ============================================================

@staff_login_required
@require_roles("Manager")
def discount_list(request):
    _ensure_default_discounts()
    discounts = Discount.objects.all().order_by("discount_type")
    return render(request, "jsquared_app/discount_list.html", {"discounts": discounts})


@staff_login_required
@require_roles("Manager")
def discount_create(request):
    if request.method == "POST":
        Discount.objects.create(
            discount_type=request.POST.get("discount_type") or "Suki",
            discount_value=request.POST.get("discount_value") or 0,
        )
        messages.success(request, "Discount type added.")
        return redirect("discount_list")
    return render(request, "jsquared_app/discount_form.html", {"mode": "create"})


@staff_login_required
@require_roles("Manager")
def discount_edit(request, discount_id: int):
    discount = get_object_or_404(Discount, discount_id=discount_id)
    if request.method == "POST":
        discount.discount_type = request.POST.get("discount_type") or discount.discount_type
        discount.discount_value = request.POST.get("discount_value") or discount.discount_value
        discount.save()
        messages.success(request, "Discount updated.")
        return redirect("discount_list")
    return render(request, "jsquared_app/discount_form.html", {"mode": "edit", "discount": discount})


@staff_login_required
@require_roles("Manager")
def discount_delete(request, discount_id: int):
    discount = get_object_or_404(Discount, discount_id=discount_id)
    if request.method == "POST":
        discount.delete()
        messages.success(request, "Discount deleted.")
        return redirect("discount_list")
    return render(request, "jsquared_app/discount_delete.html", {"discount": discount})


# ============================================================
# AUDIT LOG / BACKUP
# ============================================================

@staff_login_required
@require_roles("Manager")
def audit_log_list(request):
    q = (request.GET.get("q") or "").strip()
    logs = AuditLog.objects.all()
    if q:
        logs = logs.filter(models.Q(username__icontains=q) | models.Q(details__icontains=q))

    simplified_logs = []
    allowed_keywords = [
        "created", "accepted", "cancelled", "served", "completed", "billed out",
        "changed price", "updated payment", "applied", "added supplier", "deleted supplier",
        "recorded transaction", "created inquiry", "accepted inquiry", "updated inquiry",
        "deleted inquiry", "created account", "updated account", "deleted account",
        "added ", "deleted ", "updated ", "restored backup"
    ]

    for log in logs[:300]:
        action_text = (log.details or "").strip()
        if not action_text or action_text.startswith('{') or action_text.startswith('[') or action_text == 'Updated a record':
            continue

        lowered = action_text.lower()
        if not any(keyword in lowered for keyword in allowed_keywords):
            continue

        log.simple_action = action_text
        simplified_logs.append(log)

    return render(request, "jsquared_app/audit_log_list.html", {"logs": simplified_logs, "q": q})


@staff_login_required
@require_roles("Manager")
def backup_restore(request):
    if request.method == "POST" and request.FILES.get("backup_file"):
        upload = request.FILES["backup_file"]
        with tempfile.NamedTemporaryFile(delete=False, suffix='.json') as tmp:
            for chunk in upload.chunks():
                tmp.write(chunk)
            tmp.flush()
            call_command('loaddata', tmp.name)
        messages.success(request, 'Backup restored.')
        return redirect('backup_restore')
    return render(request, 'jsquared_app/backup_restore.html')


@staff_login_required
@require_roles("Manager")
def backup_download(request):
    from io import StringIO

    try:
        staff_id = request.session.get('staff_id') or request.session.get('admin_staff_id')
        staff = Staff.objects.filter(staff_id=staff_id).first() if staff_id else None
        username = staff.staff_email or staff.staff_name if staff else None
        AuditLog.objects.create(
            staff=staff,
            username=username,
            action='WRITE',
            path=request.path,
            method=request.method,
            model_name='backup',
            object_repr='HTTP 200',
            details='Downloaded backup file',
            created_at=timezone.now(),
        )
    except Exception:
        pass

    stream = StringIO()
    call_command(
        'dumpdata',
        '--natural-foreign',
        '--natural-primary',
        '--exclude', 'contenttypes',
        '--indent', '2',
        stdout=stream,
    )
    data = stream.getvalue()
    response = HttpResponse(data, content_type='application/json')
    response['Content-Disposition'] = 'attachment; filename="jsquared_backup.json"'
    return response
