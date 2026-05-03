from __future__ import annotations
from datetime import datetime, timedelta

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
from collections import defaultdict

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

# Meat item status rules
MEAT_STATUS_AVAILABLE = "Available"
MEAT_STATUS_OUT_OF_STOCK = "Out of Stock"
MEAT_STATUS_DISCONTINUED = "Discontinued"
MEAT_EDIT_STATUSES = {MEAT_STATUS_AVAILABLE, MEAT_STATUS_OUT_OF_STOCK, MEAT_STATUS_DISCONTINUED}
MEAT_MARKET_STATUSES = {MEAT_STATUS_AVAILABLE, MEAT_STATUS_OUT_OF_STOCK}


def _normalize_meat_status(value, default=MEAT_STATUS_AVAILABLE):
    raw = (value or default or MEAT_STATUS_AVAILABLE).strip()
    normalized = raw.lower().replace("_", " ").replace("-", " ")
    if normalized in {"available", "avail"}:
        return MEAT_STATUS_AVAILABLE
    if normalized in {"out of stock", "outofstock", "unavailable", "not available", "inquire"}:
        return MEAT_STATUS_OUT_OF_STOCK
    if normalized in {"discontinued", "archived"}:
        return MEAT_STATUS_DISCONTINUED
    return raw


def _ensure_varied_items_synced():
    styles = CookingStyle.objects.select_related("meat_item").filter(is_active=True).exclude(meat_item__isnull=True)
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
    return Staff.objects.filter(staff_id=staff_id, is_active=True).first()


def _active(qs):
    """Return only active records for models that have is_active."""
    try:
        return qs.filter(is_active=True)
    except Exception:
        return qs


def _archive_instance(obj, fields=None):
    """Soft-delete/archive records instead of hard deleting them."""
    if hasattr(obj, "archive"):
        obj.archive()
        return obj

    update_fields = []
    if hasattr(obj, "is_active"):
        obj.is_active = False
        update_fields.append("is_active")
    if hasattr(obj, "archived_at"):
        obj.archived_at = timezone.now()
        update_fields.append("archived_at")

    if fields:
        for field, value in fields.items():
            if hasattr(obj, field):
                setattr(obj, field, value)
                update_fields.append(field)

    obj.save(update_fields=list(dict.fromkeys(update_fields)) or None)
    return obj


def _as_float(value, field_label, errors, required=True):
    raw = (str(value).strip() if value is not None else "")
    if raw == "":
        if required:
            errors.append(f"{field_label} is required.")
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        errors.append(f"{field_label} must be numeric.")
        return None


def _valid_file(upload, field_label, errors):
    if not upload:
        return True
    allowed_exts = (".jpg", ".jpeg", ".png")
    name = (getattr(upload, "name", "") or "").lower()
    if not name.endswith(allowed_exts):
        errors.append(f"{field_label} must be a JPG, JPEG, or PNG file.")
        return False
    return True


def _message_errors(request, errors):
    for error in errors:
        messages.error(request, error)


def _audit_actor(request):
    """Return the active staff member and a readable doer name for audit logs."""
    staff = _current_staff(request)
    if not staff:
        staff_id = request.session.get(SESSION_STAFF_ID) or request.session.get("admin_staff_id")
        if staff_id:
            staff = Staff.objects.filter(staff_id=staff_id).first()

    if staff:
        doer_name = staff.staff_name or staff.staff_email or f"Staff #{staff.staff_id}"
        if staff.staff_role:
            doer_name = f"{doer_name} ({staff.staff_role})"
        return staff, doer_name

    return None, "Unknown user"


def log_action(request, details, model_name="", object_repr="", action="WRITE"):
    """Create a human-readable audit log entry with the name of the doer."""
    try:
        staff, doer_name = _audit_actor(request)
        AuditLog.objects.create(
            staff=staff,
            username=doer_name,
            action=action,
            path=request.path,
            method=request.method,
            model_name=model_name or "",
            object_repr=str(object_repr or ""),
            details=details,
            created_at=timezone.now(),
        )
    except Exception:
        pass


def _auditlog_has_archive_field():
    """Return True if AuditLog has an is_archived field in the current model/migration."""
    return any(field.name == "is_archived" for field in AuditLog._meta.get_fields())


def archive_old_logs(days=30):
    """Archive audit logs older than the configured number of days without deleting them."""
    if not _auditlog_has_archive_field():
        return 0

    cutoff = timezone.now() - timedelta(days=days)
    return AuditLog.objects.filter(
        created_at__lt=cutoff,
        is_archived=False,
    ).update(is_archived=True)


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
                messages.error(request, "Please log in first.")
                return redirect("login")

            role = request.session.get(SESSION_STAFF_ROLE)

            # Manager can access everything
            if role == "Manager":
                return view_func(request, *args, **kwargs)

            if role not in allowed:
                messages.error(request, "Your account does not have permission to access this feature.")
                return redirect(request.META.get("HTTP_REFERER") or "home")
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


def _validate_varied_item_weight(varied_item, qty, errors):
    """Validate an order weight against the selected meat item's allowed range."""
    try:
        qty = float(qty or 0)
    except (TypeError, ValueError):
        errors.append("Order quantity must be numeric.")
        return

    meat = varied_item.meat

    if qty <= 0:
        errors.append("Order quantity must be greater than zero.")
        return

    if meat.weight_min and qty < float(meat.weight_min):
        errors.append(f"{meat.meat_type} minimum order weight is {meat.weight_min} kg.")

    if meat.weight_max and qty > float(meat.weight_max):
        errors.append(f"{meat.meat_type} maximum order weight is {meat.weight_max} kg.")

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
        staff = Staff.objects.filter(staff_name=username, is_active=True).first()

        # If not found, try login using Django username through Account
        if not staff:
            account = Account.objects.select_related("user").filter(user__username=username, is_active=True, user__is_active=True).first()
            if account:
                staff = Staff.objects.filter(staff_name=account.staff_name, is_active=True).first()

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
        possible_staff = Staff.objects.filter(staff_name=username, staff_role="Manager", is_active=True).first()
        if possible_staff and possible_staff.staff_password == password:
            staff = possible_staff

        # If not found, try via Django username linked through Account
        if not staff:
            account = Account.objects.select_related("user").filter(user__username=username, is_active=True, user__is_active=True).first()
            if account:
                possible_staff = Staff.objects.filter(
                    staff_name=account.staff_name,
                    staff_role="Manager",
                    is_active=True
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
        "accounts": Staff.objects.filter(is_active=True).count(),
        "suppliers": Supplier.objects.filter(is_active=True).count(),
        "discounts": Discount.objects.filter(is_active=True).count(),
        "audit_logs": AuditLog.objects.count(),
        "completed_orders": Order.objects.filter(order_status__in=["Completed", "Served"]).count(),
    }
    return render(request, "jsquared_app/admin_console.html", {"stats": stats})


@admin_login_required
def account_list(request):
    accounts = Staff.objects.filter(is_active=True)
    return render(request, "jsquared_app/account_list.html", {"accounts": accounts})


@admin_login_required
def account_detail(request, account_id):
    account = get_object_or_404(Staff, staff_id=account_id, is_active=True)
    errors = []

    linked_account = Account.objects.select_related("user").filter(staff_name=account.staff_name, is_active=True).first()
    current_username = linked_account.user.username if linked_account and linked_account.user else account.staff_email

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "delete":
            if linked_account and linked_account.user:
                linked_account.user.is_active = False
                linked_account.user.save(update_fields=["is_active"])

            if linked_account:
                _archive_instance(linked_account)

            _archive_instance(account)
            log_action(request, f"Archived account: {account.staff_name}", "Staff", account.staff_name, action="ARCHIVE")
            messages.success(request, "Account archived. Existing records remain intact.")
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

                log_action(request, f"Updated account: {account.staff_name}", "Staff", account.staff_name, action="UPDATE")
                messages.success(request, "Account updated.")
                return redirect("account_detail", account_id=account.staff_id)

    return render(request, "jsquared_app/account_detail.html", {
        "account": account,
        "errors": errors,
        "current_username": current_username,
    })


@admin_login_required
def account_create(request):
    valid_roles = {"Staff", "Cashier", "Manager"}

    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        email = (request.POST.get("email") or username).strip()
        password = request.POST.get("password") or ""
        staff_name = (request.POST.get("staff_name") or "").strip()
        role = (request.POST.get("role") or "").strip()
        confirm_password = request.POST.get("confirm_password") or ""
        errors = []

        if not staff_name:
            errors.append("Staff name is required.")
        if not username:
            errors.append("Username is required.")
        if not email:
            errors.append("Email is required.")
        if not password:
            errors.append("Password is required.")
        if password != confirm_password:
            errors.append("Passwords do not match.")
        if role not in valid_roles:
            errors.append("Please select a valid role: Staff, Cashier, or Manager.")
        if username and User.objects.filter(username__iexact=username).exists():
            errors.append("Account creation not permitted. Username already exists.")
        if email and User.objects.filter(email__iexact=email).exists():
            errors.append("Account creation not permitted. Email already exists.")
        if staff_name and Staff.objects.filter(staff_name__iexact=staff_name).exists():
            errors.append("Account creation not permitted. Staff name already exists.")

        if errors:
            _message_errors(request, errors)
            return render(request, "jsquared_app/account_create.html", {"form": request.POST})

        user = User.objects.create_user(username=username, email=email, password=password)
        Account.objects.create(user=user, staff_name=staff_name, role=role)
        Staff.objects.create(staff_name=staff_name, staff_role=role, staff_email=email, staff_password=password)

        log_action(request, f"Created account: {staff_name} ({role})", "Staff", staff_name, action="CREATE")
        messages.success(request, "Account created successfully.")
        return redirect("account_list")

    return render(request, "jsquared_app/account_create.html")



@admin_login_required
def sales_report(request):
    start_date = (request.GET.get("start_date") or "").strip()
    end_date = (request.GET.get("end_date") or "").strip()

    orders_qs = Order.objects.filter(order_status__in=["Completed", "Served"]).order_by("-created_at")
    supplier_qs = SupplierTransaction.objects.exclude(payment_status="Cancelled").order_by("-transaction_date", "-transaction_id")

    if start_date:
        orders_qs = orders_qs.filter(created_at__date__gte=start_date)
        supplier_qs = supplier_qs.filter(transaction_date__gte=start_date)
    if end_date:
        orders_qs = orders_qs.filter(created_at__date__lte=end_date)
        supplier_qs = supplier_qs.filter(transaction_date__lte=end_date)

    orders = list(orders_qs)
    supplier_transactions = list(supplier_qs.select_related("supplier", "meat"))

    total_revenue = sum(float(order.total_amount or 0) for order in orders)
    total_expenses = sum(float(tx.transaction_amount or 0) for tx in supplier_transactions)
    net_cash_flow = total_revenue - total_expenses

    transactions = []

    for order in orders:
        transactions.append({
            "type": "sale",
            "label": f"Order #{order.order_id}",
            "description": order.customer_name or "Customer order",
            "date": timezone.localtime(order.created_at),
            "amount": float(order.total_amount or 0),
            "payment_method": order.payment_method or "—", 
        })

    for tx in supplier_transactions:
        item_label = tx.item_name or (tx.meat.meat_type if tx.meat_id else "Purchased item")
        supplier_name = tx.supplier.supplier_name if tx.supplier_id else "Supplier"
        tx_date = tx.transaction_date or timezone.localdate()

        transactions.append({
            "type": "expense",
            "label": f"Supplier Transaction #{tx.transaction_id}",
            "description": f"{supplier_name} - {item_label}",
            "date": timezone.make_aware(datetime.combine(tx.transaction_date, datetime.min.time())),
            "amount": float(tx.transaction_amount or 0),
            "payment_method": tx.payment_status or "—",  # ← shows Paid/Unpaid/etc for supplier rows
        })

    transactions = sorted(transactions, key=lambda x: x["date"], reverse=True)

    payment_breakdown = defaultdict(float)

    for order in orders:
        method = order.payment_method or 'Cash'
        payment_breakdown[method] += float(order.total_amount or 0)

    payment_breakdown = sorted(payment_breakdown.items(), key=lambda x: x[0])

    return render(request, "jsquared_app/sales_report.html", {
        "orders": orders,
        "supplier_transactions": supplier_transactions,
        "transactions": transactions,
        "start_date": start_date,
        "end_date": end_date,
        "total_revenue": total_revenue,
        "total_expenses": total_expenses,
        "net_cash_flow": net_cash_flow,
        "payment_breakdown": payment_breakdown,
    })


@admin_login_required
def sales_report_export_csv(request):
    start_date = (request.GET.get("start_date") or "").strip()
    end_date = (request.GET.get("end_date") or "").strip()

    orders_qs = Order.objects.filter(order_status__in=["Completed", "Served"]).order_by("-created_at")
    supplier_qs = SupplierTransaction.objects.exclude(payment_status="Cancelled").order_by("-transaction_date", "-transaction_id")

    if start_date:
        orders_qs = orders_qs.filter(created_at__date__gte=start_date)
        supplier_qs = supplier_qs.filter(transaction_date__gte=start_date)
    if end_date:
        orders_qs = orders_qs.filter(created_at__date__lte=end_date)
        supplier_qs = supplier_qs.filter(transaction_date__lte=end_date)

    orders = list(orders_qs)
    supplier_transactions = list(supplier_qs.select_related("supplier", "meat"))

    total_revenue = sum(float(order.total_amount or 0) for order in orders)
    total_expenses = sum(float(tx.transaction_amount or 0) for tx in supplier_transactions)
    net_cash_flow = total_revenue - total_expenses

    import csv
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="sales_report.csv"'

    writer = csv.writer(response)
    writer.writerow(["Date", "Type", "Reference", "Description", "Amount"])

    for order in orders:
        writer.writerow([
            timezone.localtime(order.created_at).strftime("%m/%d/%Y"),
            "Sale",
            f"Order #{order.order_id}",
            order.customer_name or "Customer order",
            float(order.total_amount or 0),
        ])

    for tx in supplier_transactions:
        item_label = tx.item_name or (tx.meat.meat_type if tx.meat_id else "Purchased item")
        supplier_name = tx.supplier.supplier_name if tx.supplier_id else "Supplier"
        writer.writerow([
            (tx.transaction_date or timezone.localdate()).strftime("%m/%d/%Y"),
            "Expense",
            f"Supplier Transaction #{tx.transaction_id}",
            f"{supplier_name} - {item_label}",
            -float(tx.transaction_amount or 0),
        ])

    writer.writerow([])
    writer.writerow(["TOTAL SALES", "", "", "", total_revenue])
    writer.writerow(["TOTAL SUPPLIER EXPENSES", "", "", "", total_expenses])
    writer.writerow(["NET CASH FLOW", "", "", "", net_cash_flow])
    return response


@admin_login_required
def sales_report_export_xlsx(request):
    start_date = (request.GET.get("start_date") or "").strip()
    end_date = (request.GET.get("end_date") or "").strip()

    orders_qs = Order.objects.filter(order_status__in=["Completed", "Served"]).order_by("-created_at")
    supplier_qs = SupplierTransaction.objects.exclude(payment_status="Cancelled").order_by("-transaction_date", "-transaction_id")

    if start_date:
        orders_qs = orders_qs.filter(created_at__date__gte=start_date)
        supplier_qs = supplier_qs.filter(transaction_date__gte=start_date)
    if end_date:
        orders_qs = orders_qs.filter(created_at__date__lte=end_date)
        supplier_qs = supplier_qs.filter(transaction_date__lte=end_date)

    orders = list(orders_qs)
    supplier_transactions = list(supplier_qs.select_related("supplier", "meat"))

    total_revenue = sum(float(order.total_amount or 0) for order in orders)
    total_expenses = sum(float(tx.transaction_amount or 0) for tx in supplier_transactions)
    net_cash_flow = total_revenue - total_expenses

    wb = Workbook()
    ws = wb.active
    ws.title = "Sales Report"
    ws.append(["Date", "Type", "Reference", "Description", "Amount"])

    for order in orders:
        ws.append([
            timezone.localtime(order.created_at).strftime("%m/%d/%Y"),
            "Sale",
            f"Order #{order.order_id}",
            order.customer_name or "Customer order",
            float(order.total_amount or 0),
        ])

    for tx in supplier_transactions:
        item_label = tx.item_name or (tx.meat.meat_type if tx.meat_id else "Purchased item")
        supplier_name = tx.supplier.supplier_name if tx.supplier_id else "Supplier"
        ws.append([
            (tx.transaction_date or timezone.localdate()).strftime("%m/%d/%Y"),
            "Expense",
            f"Supplier Transaction #{tx.transaction_id}",
            f"{supplier_name} - {item_label}",
            -float(tx.transaction_amount or 0),
        ])

    ws.append([])
    ws.append(["TOTAL SALES", "", "", "", total_revenue])
    ws.append(["TOTAL SUPPLIER EXPENSES", "", "", "", total_expenses])
    ws.append(["NET CASH FLOW", "", "", "", net_cash_flow])

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    response = HttpResponse(
        output.read(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = 'attachment; filename="sales_report.xlsx"'
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
    meat_items = MeatItem.objects.filter(is_active=True).order_by('-price_updated_at')

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
    meat = MeatItem.objects.filter(is_active=True).order_by("meat_type")
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
    """Create a meat item. New meat items always start as Available."""
    if request.method == "POST":
        errors = []
        meat_type = (request.POST.get("meat_type") or "").strip()
        meat_description = (request.POST.get("meat_description") or "").strip() or None
        current_price = _as_float(request.POST.get("current_price"), "Current price", errors)
        weight_min = _as_float(request.POST.get("weight_min"), "Minimum weight", errors)
        weight_max = _as_float(request.POST.get("weight_max"), "Maximum weight", errors)
        item_status = MEAT_STATUS_AVAILABLE
        meat_image = request.FILES.get("meat_image")

        if not meat_type:
            errors.append("Meat type is required.")
        elif len(meat_type) > 100:
            errors.append("Meat type exceeds the maximum allowable number of characters.")
        elif MeatItem.objects.filter(meat_type__iexact=meat_type, is_active=True).exists():
            errors.append("Add not permitted. Meat item already exists. Please input a unique meat type.")

        if meat_description and len(meat_description) > 200:
            errors.append("Description exceeds the maximum allowable number of characters (200). Please input a shorter description.")
        if current_price is not None and current_price <= 0:
            errors.append("Current price must be greater than zero.")
        if weight_min is not None and weight_min <= 0:
            errors.append("Minimum weight must be greater than zero.")
        if weight_max is not None and weight_max <= 0:
            errors.append("Maximum weight must be greater than zero.")
        if weight_min is not None and weight_max is not None and weight_min > weight_max:
            errors.append("Add not permitted. Minimum weight cannot be greater than maximum weight.")
        _valid_file(meat_image, "Meat image", errors)

        if errors:
            _message_errors(request, errors)
            return render(request, "jsquared_app/meat_price_create.html", {"form": request.POST})

        meat_item = MeatItem.objects.create(
            meat_type=meat_type,
            meat_description=meat_description,
            current_price=current_price,
            weight_min=weight_min,
            weight_max=weight_max,
            item_status=item_status,
            meat_image=meat_image,
        )
        log_action(request, f"Created meat item: {meat_item.meat_type}", "MeatItem", meat_item.meat_type, action="CREATE")
        messages.success(request, "Meat item successfully added. New meat items are automatically marked as Available.")
        return redirect("home")

    return render(request, "jsquared_app/meat_price_create.html")


@staff_login_required
@require_roles("Staff", "Manager")
def meat_price_edit(request, meat_id: int):
    m = get_object_or_404(MeatItem, meat_id=meat_id, is_active=True)

    if request.method == "POST":
        errors = []
        meat_type = (request.POST.get("meat_type") or m.meat_type).strip()
        meat_description = (request.POST.get("meat_description") or "").strip() or None
        current_price = _as_float(request.POST.get("current_price"), "Current price", errors)
        weight_min = _as_float(request.POST.get("weight_min"), "Minimum weight", errors)
        weight_max = _as_float(request.POST.get("weight_max"), "Maximum weight", errors)
        new_status = _normalize_meat_status(request.POST.get("item_status"), m.item_status)
        meat_image = request.FILES.get("meat_image")

        if not meat_type:
            errors.append("Meat type is required.")
        elif len(meat_type) > 100:
            errors.append("Meat type exceeds the maximum allowable number of characters.")
        elif MeatItem.objects.filter(meat_type__iexact=meat_type, is_active=True).exclude(meat_id=m.meat_id).exists():
            errors.append("Update not permitted. Meat item already exists. Please input a unique meat type.")

        if meat_description and len(meat_description) > 200:
            errors.append("Description exceeds the maximum allowable number of characters (200). Please input a shorter description.")
        if current_price is not None and current_price <= 0:
            errors.append("Current price must be greater than zero.")
        if weight_min is not None and weight_min <= 0:
            errors.append("Minimum weight must be greater than zero.")
        if weight_max is not None and weight_max <= 0:
            errors.append("Maximum weight must be greater than zero.")
        if weight_min is not None and weight_max is not None and weight_min > weight_max:
            errors.append("Update not permitted. Minimum weight cannot be greater than maximum weight.")
        if new_status not in MEAT_EDIT_STATUSES:
            errors.append("Invalid meat status. Please choose Available, Out of Stock, or Discontinued.")
        _valid_file(meat_image, "Meat image", errors)

        if errors:
            _message_errors(request, errors)
            return render(request, "jsquared_app/meat_price_edit.html", {"m": m, "form": request.POST})

        m.meat_type = meat_type
        m.meat_description = meat_description
        m.current_price = current_price
        m.weight_min = weight_min
        m.weight_max = weight_max
        m.item_status = new_status

        if new_status == MEAT_STATUS_DISCONTINUED:
            m.is_active = False
            if hasattr(m, "archived_at"):
                m.archived_at = timezone.now()
        else:
            m.is_active = True
            if hasattr(m, "archived_at"):
                m.archived_at = None

        if meat_image:
            m.meat_image = meat_image

        m.save()
        log_action(request, f"Updated meat item: {m.meat_type}; status set to {new_status}", "MeatItem", m.meat_type, action="UPDATE")

        if new_status == MEAT_STATUS_DISCONTINUED:
            messages.success(request, "Meat item marked as Discontinued and archived. Existing records remain intact.")
        else:
            messages.success(request, f"Meat item updated. Status is now {new_status}.")
        return redirect("home")

    return render(request, "jsquared_app/meat_price_edit.html", {"m": m})


@staff_login_required
@require_roles("Staff", "Manager")
def meat_price_delete(request, meat_id: int):
    m = get_object_or_404(MeatItem, meat_id=meat_id, is_active=True)

    if request.method == "POST":
        _archive_instance(m, {"item_status": "Discontinued"})
        log_action(request, f"Archived meat item: {m.meat_type}", "MeatItem", m.meat_type, action="ARCHIVE")
        messages.success(request, "Meat item archived. Existing records remain intact.")
        return redirect("home")

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

    _ensure_varied_items_synced()

    meats = MeatItem.objects.filter(is_active=True, item_status="Available").order_by("meat_type")
    cooking_styles = CookingStyle.objects.filter(is_active=True)
    fixed_items = FixedMenuItem.objects.filter(is_active=True)

    cooking_styles_json = json.dumps([
        {"id": s.cooking_style_id, "name": s.style_name, "price": s.cooking_charge}
        for s in cooking_styles
    ])

    varied_items = VariedMenuItem.objects.select_related("meat", "cooking_style").filter(
        is_active=True, meat__is_active=True, cooking_style__is_active=True
    )
    varied_items_json = json.dumps([
        {
            "meat_id": v.meat_id,
            "style_id": v.cooking_style_id,
            "style_name": v.cooking_style.style_name,
            "add_on_price": v.item_price,
            "is_byom": v.is_byom,
            "weight_min": v.meat.weight_min,
            "weight_max": v.meat.weight_max,
        }
        for v in varied_items
    ])

    context = {
        "meats": meats,
        "cooking_styles_json": cooking_styles_json,
        "varied_items_json": varied_items_json,
        "alacarte_items": fixed_items.filter(item_category__icontains="ala carte"),
        "drink_items": fixed_items.filter(item_category__icontains="drink"),
        "extra_items": fixed_items.exclude(
            item_category__icontains="ala carte"
        ).exclude(item_category__icontains="drink"),
        "weight_options": range(1, 21),
    }

    if request.method == "POST":
        customer_name = request.POST.get("customer_name") or None
        table_num = int(request.POST.get("table_num") or 1)
        items_json = request.POST.get("items_json") or "[]"
        errors = []

        try:
            items = json.loads(items_json)
        except json.JSONDecodeError:
            items = []
            errors.append("Invalid order item data.")

        if not items:
            errors.append("Cannot create an empty order.")

        prepared_items = []

        for item in items:
            try:
                item_type = item.get("type")

                if item_type == "varied":
                    varied = get_object_or_404(
                        VariedMenuItem.objects.select_related("meat", "cooking_style"),
                        meat_id=item.get("meatId"),
                        cooking_style_id=item.get("styleId"),
                        is_active=True,
                        meat__is_active=True,
                        cooking_style__is_active=True,
                    )

                    qty = float(item.get("weight") or 0)
                    _validate_varied_item_weight(varied, qty, errors)
                    prepared_items.append({
                        "type": "varied",
                        "varied": varied,
                        "qty": qty,
                        "is_byom": bool(item.get("is_byom", False)),
                    })

                elif item_type == "fixed":
                    fixed = get_object_or_404(FixedMenuItem, fixed_item_id=item.get("fixedId"), is_active=True)
                    qty = float(item.get("qty") or 1)
                    if qty <= 0:
                        errors.append(f"{fixed.item_name} quantity must be greater than zero.")
                    prepared_items.append({"type": "fixed", "fixed": fixed, "qty": qty})

                else:
                    errors.append("Invalid menu item type.")

            except (TypeError, ValueError):
                errors.append("Order quantity must be numeric.")

        if errors:
            _message_errors(request, errors)
            return render(request, "jsquared_app/order_create.html", context)

        order = Order.objects.create(
            staff=staff,
            table_num=table_num,
            customer_name=customer_name,
            order_status="Pending",
            payment_status="Unpaid",
            payment_method="Cash",
            diner_count=1,
            eligible_count=1,
            order_type=request.POST.get("order_type") == "1",
        )

        for prepared in prepared_items:
            if prepared["type"] == "varied":
                oi = OrderItem.objects.create(
                    order=order,
                    varied_item=prepared["varied"],
                    order_quantity=prepared["qty"],
                )

                if prepared["is_byom"]:
                    cooking_charge = float(prepared["varied"].item_price or 0)
                    OrderItem.objects.filter(order_item_id=oi.order_item_id).update(
                        order_unit_price=0.0,
                        cooking_charge=cooking_charge,
                        subtotal=float(prepared["qty"]) * cooking_charge,
                    )

            elif prepared["type"] == "fixed":
                OrderItem.objects.create(
                    order=order,
                    fixed_item=prepared["fixed"],
                    order_quantity=prepared["qty"],
                )

        order.recompute_total()
        log_action(request, f"Created order #{order.order_id:03d}", "Order", f"Order #{order.order_id:03d}", action="CREATE")
        messages.success(request, "Order placed!")
        return redirect("order_list")

    return render(request, "jsquared_app/order_create.html", context)

@staff_login_required
@require_roles("Staff", "Cashier", "Manager")
def order_detail(request, order_id: int):
    order = get_object_or_404(Order, order_id=order_id)

    if order.order_status in ["Served", "Completed"]:
        return redirect("order_checkout", order_id=order.order_id)

    _ensure_varied_items_synced()

    fixed_items = FixedMenuItem.objects.filter(is_active=True).order_by("item_name")
    varied_items = VariedMenuItem.objects.select_related("meat", "cooking_style").filter(
        is_active=True, meat__is_active=True, cooking_style__is_active=True
    ).order_by("meat__meat_type", "cooking_style__style_name")

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
            try:
                qty = float(request.POST.get("order_quantity") or 1)
            except (TypeError, ValueError):
                messages.error(request, "Order quantity must be numeric.")
                return redirect("order_detail", order_id=order.order_id)

            if qty <= 0:
                messages.error(request, "Order quantity must be greater than zero.")
                return redirect("order_detail", order_id=order.order_id)

            if ":" not in key:
                return HttpResponseForbidden("Invalid menu item.")

            kind, raw_id = key.split(":", 1)
            item_id = int(raw_id)

            is_byom = request.POST.get("is_byom") == "1"

            if kind == "fixed":
                fixed_item = get_object_or_404(FixedMenuItem, fixed_item_id=item_id, is_active=True)
                OrderItem.objects.create(order=order, fixed_item=fixed_item, order_quantity=qty)

            elif kind == "varied":
                varied_item = get_object_or_404(
                    VariedMenuItem.objects.select_related("meat", "cooking_style"),
                    varied_item_id=item_id,
                    is_active=True,
                    meat__is_active=True,
                    cooking_style__is_active=True,
                )

                errors = []
                _validate_varied_item_weight(varied_item, qty, errors)
                if errors:
                    _message_errors(request, errors)
                    return redirect("order_detail", order_id=order.order_id)

                oi = OrderItem.objects.create(order=order, varied_item=varied_item, order_quantity=qty)
                if is_byom:
                    cooking_charge = float(varied_item.item_price or varied_item.cooking_style.cooking_charge or 0)
                    OrderItem.objects.filter(order_item_id=oi.order_item_id).update(
                        order_unit_price=0.0,
                        cooking_charge=cooking_charge,
                        subtotal=float(qty) * cooking_charge,
                    )
            else:
                return HttpResponseForbidden("Invalid menu item type.")

            order.recompute_total()
            log_action(request, f"Added item to order #{order.order_id:03d}", "OrderItem", f"Order #{order.order_id:03d}", action="UPDATE")
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
            log_action(request, f"Accepted order #{order.order_id:03d}; status changed to Preparing", "Order", f"Order #{order.order_id:03d}", action="UPDATE")
    return redirect("order_list")


@staff_login_required
@require_roles("Staff", "Cashier", "Manager")
def order_cancel(request, order_id: int):
    order = get_object_or_404(Order, order_id=order_id)
    if request.method == "POST":
        order.order_status = "Cancelled"
        order.save(update_fields=["order_status"])
        log_action(request, f"Cancelled order #{order.order_id:03d}", "Order", f"Order #{order.order_id:03d}", action="UPDATE")
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
    log_action(request, f"Completed order #{order.order_id:03d}", "Order", f"Order #{order.order_id:03d}", action="UPDATE")
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
    log_action(request, f"Updated payment for order #{order.order_id:03d}: {payment_status} via {payment_method}", "Order", f"Order #{order.order_id:03d}", action="UPDATE")
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
            discount = get_object_or_404(Discount, discount_id=int(discount_id), is_active=True)
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
        log_action(request, f"Updated discount details for order #{order.order_id:03d}", "Order", f"Order #{order.order_id:03d}", action="UPDATE")
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
            "discounts": Discount.objects.filter(is_active=True).order_by("discount_type", "discount_value"),
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
    log_action(request, f"Completed order #{order.order_id:03d}", "Order", f"Order #{order.order_id:03d}", action="UPDATE")
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
        log_action(request, f"Removed item from pending order #{order.order_id:03d}", "OrderItem", f"Item #{order_item_id}", action="DELETE")
        messages.success(request, "Order item removed.")
        return redirect("order_detail", order_id=order.order_id)

    return render(request, "jsquared_app/order_item_delete.html", {"order": order, "item": item})


@staff_login_required
@require_roles("Staff", "Manager")
def order_delete(request, order_id: int):
    order = get_object_or_404(Order, order_id=order_id)

    if request.method == "POST":
        order.order_status = "Cancelled"
        order.save(update_fields=["order_status"])
        log_action(request, f"Cancelled order #{order.order_id:03d}. Existing records remain intact.", "Order", f"Order #{order.order_id:03d}", action="UPDATE")
        messages.success(request, f"Order #{order.order_id:03d} cancelled. Existing records remain intact.")
        return redirect("order_history")

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
    meats = MeatItem.objects.filter(is_active=True).order_by("meat_type")

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

        log_action(request, f"Created {len(meat_ids)} price inquiry request(s)", "PriceInquiryRequest", "Multiple inquiries", action="CREATE")
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
        log_action(request, f"Accepted price inquiry #{req.inquiry_id}", "PriceInquiryRequest", f"Inquiry #{req.inquiry_id}", action="UPDATE")
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
        log_action(request, f"Cancelled price inquiry #{req.inquiry_id}", "PriceInquiryRequest", f"Inquiry #{req.inquiry_id}", action="UPDATE")
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
        new_status = _normalize_meat_status(request.POST.get("item_status"), MEAT_STATUS_AVAILABLE)
        errors = []

        if new_status not in MEAT_MARKET_STATUSES:
            errors.append("Invalid market availability status. Please choose Available or Out of Stock.")

        new_price = None
        if raw_price:
            new_price = _as_float(raw_price, "New price", errors)
            if new_price is not None and new_price <= 0:
                errors.append("New price must be greater than zero.")
        elif new_status == MEAT_STATUS_AVAILABLE:
            errors.append("Please enter the new price when the meat item is Available.")

        if errors:
            _message_errors(request, errors)
            return redirect("inquiry_update_price", inquiry_id=req.inquiry_id)

        req.notes = notes
        req.responded_at = timezone.now()
        req.status = "Completed"
        if new_price is not None:
            req.new_price = new_price
        req.save(update_fields=["new_price", "notes", "responded_at", "status"])

        meat = req.meat
        if new_price is not None:
            meat.current_price = new_price
        meat.item_status = new_status
        meat.is_active = True
        if hasattr(meat, "archived_at"):
            meat.archived_at = None
            meat.save(update_fields=["current_price", "item_status", "is_active", "archived_at", "price_updated_at"])
        else:
            meat.save(update_fields=["current_price", "item_status", "is_active", "price_updated_at"])

        if new_price is not None:
            log_action(request, f"Updated price inquiry #{req.inquiry_id}; changed price of {meat.meat_type} to ₱{new_price} and status to {new_status}", "PriceInquiryRequest", f"Inquiry #{req.inquiry_id}", action="UPDATE")
        else:
            log_action(request, f"Updated price inquiry #{req.inquiry_id}; marked {meat.meat_type} as {new_status}", "PriceInquiryRequest", f"Inquiry #{req.inquiry_id}", action="UPDATE")

        messages.success(request, f"Price inquiry completed. Meat status is now {new_status}.")
        return redirect("inquiry_list")

    return render(request, "jsquared_app/inquiry_update_price.html", {"req": req})


@staff_login_required
@require_roles("Staff", "Manager")
def meat_detail(request, meat_id):
    item = get_object_or_404(MeatItem, meat_id=meat_id, is_active=True)

    if request.method == "POST":
        errors = []

        meat_type = (request.POST.get("meat_type") or item.meat_type).strip()
        meat_description = (request.POST.get("meat_description") or "").strip() or None
        weight_min = _as_float(request.POST.get("weight_min"), "Minimum weight", errors)
        weight_max = _as_float(request.POST.get("weight_max"), "Maximum weight", errors)
        item_status = (request.POST.get("item_status") or item.item_status).strip()

        valid_statuses = {"Available", "Out of Stock", "Discontinued"}

        if not meat_type:
            errors.append("Meat type is required.")

        if weight_min is not None and weight_min <= 0:
            errors.append("Minimum weight must be greater than zero.")

        if weight_max is not None and weight_max <= 0:
            errors.append("Maximum weight must be greater than zero.")

        if weight_min is not None and weight_max is not None and weight_min > weight_max:
            errors.append("Minimum weight cannot be greater than maximum weight.")

        if item_status not in valid_statuses:
            errors.append("Invalid meat status.")

        if request.FILES.get("meat_image"):
            _valid_file(request.FILES.get("meat_image"), "Meat image", errors)

        if errors:
            _message_errors(request, errors)
            return redirect("meat_detail", meat_id=item.meat_id)

        item.meat_type = meat_type
        item.meat_description = meat_description
        item.weight_min = weight_min
        item.weight_max = weight_max
        item.item_status = item_status

        if request.FILES.get("meat_image"):
            item.meat_image = request.FILES.get("meat_image")

        if item_status == "Discontinued":
            item.is_active = False
            item.archived_at = timezone.now()
            item.save()
            log_action(request, f"Archived meat item: {item.meat_type}", "MeatItem", item.meat_type, action="ARCHIVE")
            messages.success(request, "Meat item discontinued and archived.")
            return redirect("home")

        item.is_active = True
        item.archived_at = None
        item.save()

        log_action(request, f"Updated meat item: {item.meat_type}", "MeatItem", item.meat_type, action="UPDATE")
        messages.success(request, "Meat item updated.")
        return redirect("meat_detail", meat_id=item.meat_id)

    is_manager = _current_staff(request).staff_role == "Manager" if _current_staff(request) else False
    is_staff = _current_staff(request).staff_role == "Staff" if _current_staff(request) else False

    return render(request, "jsquared_app/meat_detail.html", {
        "item": item,
        "is_manager": is_manager,
        "is_staff": is_staff,
    })


@staff_login_required
@require_roles("Staff", "Manager")
def cooking_styles_list(request):
    meat_items = MeatItem.objects.filter(is_active=True).order_by("meat_type")
    q = (request.GET.get("q") or "").strip()
    if q:
        meat_items = meat_items.filter(
            models.Q(meat_type__icontains=q) | models.Q(cooking_styles__style_name__icontains=q, cooking_styles__is_active=True)
        ).distinct()
    return render(request, "jsquared_app/cooking_styles_list.html", {"meat_items": meat_items, "q": q})
@staff_login_required
@require_roles("Staff", "Manager")
def cooking_style_create(request, meat_id: int):
    meat = get_object_or_404(MeatItem, meat_id=meat_id, is_active=True)

    if request.method == "POST":
        errors = []
        style_name = (request.POST.get("style_name") or "").strip()
        style_description = (request.POST.get("style_description") or "").strip()
        cooking_charge = _as_float(request.POST.get("cooking_charge"), "Cooking charge per kg", errors)
        c_weight_min = 0
        c_weight_max = 0
        icon = request.FILES.get("icon")

        if not style_name:
            errors.append("Cooking style name is required.")
        elif len(style_name) > 50:
            errors.append("Add not permitted. Cooking style name exceeds the maximum allowable number of characters. Please input a shorter name.")
        elif CookingStyle.objects.filter(meat_item=meat, style_name__iexact=style_name, is_active=True).exists():
            errors.append("Add not permitted. Cooking style name already exists for this meat. Please input a unique cooking style name.")
        if style_description and len(style_description) > 200:
            errors.append("Add not permitted. Description exceeds the maximum allowable number of characters (200). Please input a shorter description.")
        if cooking_charge is not None and cooking_charge <= 0:
            errors.append("Cooking charge per kg must be greater than zero.")
        _valid_file(icon, "Cooking style icon", errors)

        if errors:
            _message_errors(request, errors)
            return render(request, "jsquared_app/cooking_style_create.html", {"meat": meat, "form": request.POST})

        style = CookingStyle.objects.create(
            meat_item=meat,
            style_name=style_name,
            style_description=style_description,
            cooking_charge=cooking_charge,
            c_weight_min=c_weight_min,
            c_weight_max=c_weight_max,
            icon=icon,
        )
        log_action(request, f"Created cooking style: {style.style_name} for {meat.meat_type}", "CookingStyle", style.style_name, action="CREATE")
        messages.success(request, "Cooking style successfully added. Cooking charge is computed per kg.")
        return redirect("meat_category", meat_id=meat.meat_id)

    return render(request, "jsquared_app/cooking_style_create.html", {"meat": meat})

@staff_login_required
@require_roles("Staff", "Manager")
def cooking_style_edit(request, cooking_style_id: int):
    c = get_object_or_404(CookingStyle, cooking_style_id=cooking_style_id, is_active=True)

    if request.method == "POST":
        errors = []
        style_name = (request.POST.get("style_name") or c.style_name).strip()
        style_description = (request.POST.get("style_description") or "").strip()
        cooking_charge = _as_float(request.POST.get("cooking_charge"), "Cooking charge per kg", errors)
        icon = request.FILES.get("icon")

        if not style_name:
            errors.append("Cooking style name is required.")
        elif len(style_name) > 50:
            errors.append("Cooking style name exceeds the maximum allowable number of characters. Please input a shorter name.")
        elif CookingStyle.objects.filter(
            meat_item=c.meat_item,
            style_name__iexact=style_name,
            is_active=True,
        ).exclude(cooking_style_id=c.cooking_style_id).exists():
            errors.append("Update not permitted. Cooking style name already exists for this meat. Please input a unique cooking style name.")

        if style_description and len(style_description) > 200:
            errors.append("Description exceeds the maximum allowable number of characters (200). Please input a shorter description.")
        if cooking_charge is not None and cooking_charge <= 0:
            errors.append("Cooking charge per kg must be greater than zero.")
        _valid_file(icon, "Cooking style icon", errors)

        if errors:
            _message_errors(request, errors)
            return render(request, "jsquared_app/cooking_style_edit.html", {"c": c, "form": request.POST})

        c.style_name = style_name
        c.style_description = style_description
        c.cooking_charge = cooking_charge
        c.c_weight_min = 0
        c.c_weight_max = 0
        if icon:
            c.icon = icon
        c.save()
        log_action(request, f"Updated cooking style: {c.style_name}", "CookingStyle", c.style_name, action="UPDATE")
        messages.success(request, "Cooking style updated. Cooking charge is computed per kg.")
        return redirect("meat_category", meat_id=c.meat_item_id)

    return render(request, "jsquared_app/cooking_style_edit.html", {"c": c})

@staff_login_required
@require_roles("Manager")
def cooking_style_delete(request, cooking_style_id: int):
    c = get_object_or_404(CookingStyle, cooking_style_id=cooking_style_id, is_active=True)

    if request.method == "POST":
        _archive_instance(c)
        log_action(request, f"Archived cooking style: {c.style_name}", "CookingStyle", c.style_name, action="ARCHIVE")
        messages.success(request, "Cooking style archived. Existing records remain intact.")
        return redirect("cooking_styles_list")

    return render(request, "jsquared_app/cooking_style_delete.html", {"c": c})


@staff_login_required
@require_roles("Staff", "Manager")
def meat_category(request, meat_id: int):
    meat = get_object_or_404(MeatItem, meat_id=meat_id, is_active=True)
    styles = CookingStyle.objects.filter(meat_item=meat, is_active=True).order_by("style_name")

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




def _supplier_expense_q_for_orders(order_ids):
    """Build a query for auto-created supplier transactions tied to the selected completed/served orders."""
    query = Q()
    for order_id in order_ids:
        query |= Q(notes__icontains=f"Auto-created from Order #{order_id},")
        query |= Q(notes__icontains=f"Auto-created from Order #{order_id} ")
        query |= Q(notes__icontains=f"Auto-created from Order #{order_id}")
    return query


def _apply_supplier_expenses_to_orders(orders):
    """Attach supplier_expense and net_sales to each order for reporting display/export."""
    orders = list(orders)
    for order in orders:
        expense_query = _supplier_expense_q_for_orders([order.order_id])
        supplier_expense = 0
        if expense_query:
            supplier_expense = SupplierTransaction.objects.filter(expense_query).exclude(
                payment_status="Cancelled"
            ).aggregate(total=Sum("transaction_amount"))["total"] or 0
        order.supplier_expense = float(supplier_expense or 0)
        order.net_sales = float(order.total_amount or 0) - order.supplier_expense
    return orders


def _report_totals_for_orders(orders):
    """Return revenue, supplier expenses, and net sales for completed/served orders only."""
    orders = list(orders)
    order_ids = [order.order_id for order in orders]
    total_revenue = sum(float(order.total_amount or 0) for order in orders)

    total_expenses = 0
    if order_ids:
        expense_query = _supplier_expense_q_for_orders(order_ids)
        total_expenses = SupplierTransaction.objects.filter(expense_query).exclude(
            payment_status="Cancelled"
        ).aggregate(total=Sum("transaction_amount"))["total"] or 0

    total_expenses = float(total_expenses or 0)
    net_profit = total_revenue - total_expenses
    return total_revenue, total_expenses, net_profit

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

    auto_note = f"From Order #{order_item.order.order_id}"

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
        .filter(is_active=True)
        .order_by("supplier_name")
    )

    if request.method == "POST":
        supplier_id = (request.POST.get("supplier_id") or "").strip()
        order_item_id = (request.POST.get("order_item_id") or "").strip()
        return_url = (request.POST.get("return_url") or "").strip() or "supplier_list"

        if supplier_id and order_item_id:
            try:
                supplier = Supplier.objects.get(supplier_id=int(supplier_id), is_active=True)
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
                    log_action(request, f"Assigned supplier {supplier.supplier_name} to order item #{order_item.order_item_id}", "OrderItem", f"Item #{order_item.order_item_id}", action="UPDATE")
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
        errors = []
        supplier_name = (request.POST.get("supplier_name") or "").strip()
        contact_person = (request.POST.get("contact_person") or "").strip() or None
        phone_number = (request.POST.get("phone_number") or "").strip()
        supplier_address = (request.POST.get("supplier_address") or "").strip()

        if not supplier_name:
            errors.append("Supplier name is required.")
        elif Supplier.objects.filter(supplier_name__iexact=supplier_name, is_active=True).exists():
            errors.append("Supplier creation not permitted. Supplier already exists. Please input a unique supplier name.")
        if not phone_number:
            errors.append("Phone number is required.")
        elif not phone_number.isdigit() or len(phone_number) != 11:
            errors.append("Phone number must contain exactly 11 digits.")
        elif Supplier.objects.filter(phone_number=phone_number, is_active=True).exists():
            errors.append("Supplier creation not permitted. Phone number already exists. Please input a unique phone number.")
        if not supplier_address:
            errors.append("Supplier address/store location is required.")

        if errors:
            _message_errors(request, errors)
            return render(request, "jsquared_app/supplier_form.html", {"mode": "create", "form": request.POST, "return_url": request.GET.get("return_url") or "", "order_item_id": request.GET.get("order_item_id") or ""})

        supplier = Supplier.objects.create(supplier_name=supplier_name, contact_person=contact_person, phone_number=phone_number, supplier_address=supplier_address)
        log_action(request, f"Created supplier: {supplier.supplier_name}", "Supplier", supplier.supplier_name, action="CREATE")
        messages.success(request, "Supplier successfully added.")

        return_url = request.GET.get("return_url")
        order_item_id = request.GET.get("order_item_id")
        if return_url:
            return redirect(f"{reverse('supplier_list')}?return_url={return_url}&order_item_id={order_item_id}")
        return redirect("supplier_list")

    return render(request, "jsquared_app/supplier_form.html", {"mode": "create", "return_url": request.GET.get("return_url") or "", "order_item_id": request.GET.get("order_item_id") or ""})


@staff_login_required
@require_roles("Manager")
def supplier_detail(request, supplier_id: int):
    supplier = get_object_or_404(
        Supplier.objects.prefetch_related("transactions__meat"),
        supplier_id=supplier_id,
        is_active=True,
    )
    meats = MeatItem.objects.filter(is_active=True).order_by("meat_type")
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
        log_action(request, f"Updated supplier: {supplier.supplier_name}", "Supplier", supplier.supplier_name, action="UPDATE")
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
    supplier = get_object_or_404(Supplier, supplier_id=supplier_id, is_active=True)
    if request.method == "POST":
        _archive_instance(supplier)
        log_action(request, f"Archived supplier: {supplier.supplier_name}", "Supplier", supplier.supplier_name, action="ARCHIVE")
        messages.success(request, "Supplier archived. Existing records remain intact.")
        return redirect("supplier_list")
    return render(request, "jsquared_app/supplier_delete.html", {"supplier": supplier})


@staff_login_required
@require_roles("Manager")
def supplier_transaction_create(request, supplier_id: int):
    messages.error(request, "Manual supplier transactions are disabled. Supplier expenses are automatically recorded when a supplier is assigned to an order item.")
    return redirect("supplier_detail", supplier_id=supplier_id)


@staff_login_required
@require_roles("Manager")
def supplier_transaction_update(request, supplier_id: int, transaction_id: int):
    messages.error(request, "Manual supplier transaction editing is disabled to preserve sales report integrity.")
    return redirect("supplier_detail", supplier_id=supplier_id)


@staff_login_required
@require_roles("Manager")
def supplier_transaction_delete(request, supplier_id: int, transaction_id: int):
    messages.error(request, "Manual supplier transaction cancellation is disabled to preserve sales report integrity.")
    return redirect("supplier_detail", supplier_id=supplier_id)


# ============================================================
# DISCOUNT MANAGEMENT
# ============================================================

@staff_login_required
@require_roles("Manager")
def discount_list(request):
    _ensure_default_discounts()
    discounts = Discount.objects.filter(is_active=True).order_by("discount_type")
    return render(request, "jsquared_app/discount_list.html", {"discounts": discounts})


@staff_login_required
@require_roles("Manager")
def discount_create(request):
    if request.method == "POST":
        discount = Discount.objects.create(
            discount_type=request.POST.get("discount_type") or "Suki",
            discount_value=request.POST.get("discount_value") or 0,
        )
        log_action(request, f"Created discount type: {discount.discount_type}", "Discount", discount.discount_type, action="CREATE")
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
        log_action(request, f"Updated discount type: {discount.discount_type}", "Discount", discount.discount_type, action="UPDATE")
        messages.success(request, "Discount updated.")
        return redirect("discount_list")
    return render(request, "jsquared_app/discount_form.html", {"mode": "edit", "discount": discount})


@staff_login_required
@require_roles("Manager")
def discount_delete(request, discount_id: int):
    discount = get_object_or_404(Discount, discount_id=discount_id, is_active=True)
    if request.method == "POST":
        _archive_instance(discount)
        log_action(request, f"Archived discount type: {discount.discount_type}", "Discount", discount.discount_type, action="ARCHIVE")
        messages.success(request, "Discount archived. Existing records remain intact.")
        return redirect("discount_list")
    return render(request, "jsquared_app/discount_delete.html", {"discount": discount})


# ============================================================
# AUDIT LOG / BACKUP
# ============================================================

@staff_login_required
@require_roles("Manager")
def audit_log_list(request):
    archive_old_logs(30)

    q = (request.GET.get("q") or "").strip()

    if _auditlog_has_archive_field():
        logs = AuditLog.objects.filter(is_archived=False).order_by("-created_at")
    else:
        logs = AuditLog.objects.all().order_by("-created_at")

    if q:
        logs = logs.filter(models.Q(username__icontains=q) | models.Q(details__icontains=q))

    simplified_logs = []
    allowed_keywords = [
        "created", "accepted", "cancelled", "served", "completed", "billed out",
        "changed price", "updated payment", "applied", "assigned",
        "recorded transaction", "created inquiry", "accepted inquiry", "updated inquiry",
        "added ", "deleted ", "updated ", "archived", "downloaded backup", "restored backup"
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
        log_action(request, "Restored backup file", "backup", upload.name, action="WRITE")
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
        username = (f"{staff.staff_name} ({staff.staff_role})" if staff else "Unknown user")
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
