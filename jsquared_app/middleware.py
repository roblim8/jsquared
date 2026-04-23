from __future__ import annotations

import json
import re
from django.utils import timezone
from .models import AuditLog, Staff, MeatItem, Supplier, Order, Discount, PriceInquiryRequest


class AuditLogMiddleware:
    """Simple non-invasive audit logging for write actions."""

    def __init__(self, get_response):
        self.get_response = get_response

    def _clean_value(self, value):
        if value is None:
            return ""
        return str(value).strip()

    def _build_action_text(self, request):
        path = request.path
        post = request.POST

        # Orders
        if path == '/orders/new/':
            customer = self._clean_value(post.get('customer_name')) or 'customer'
            return f'Created order for {customer}'

        m = re.match(r'^/orders/(\d+)/delete/$', path)
        if m:
            return f'Deleted Order #{m.group(1)}'

        m = re.match(r'^/orders/(\d+)/items/(\d+)/delete/$', path)
        if m:
            order_id = m.group(1)
            return f'Removed an item from Order #{order_id}'

        m = re.match(r'^/orders/(\d+)/accept/$', path)
        if m:
            return f'Accepted Order #{m.group(1)}'

        m = re.match(r'^/orders/(\d+)/cancel/$', path)
        if m:
            return f'Cancelled Order #{m.group(1)}'

        m = re.match(r'^/orders/(\d+)/serve/$', path)
        if m:
            return f'Marked Order #{m.group(1)} as served'

        m = re.match(r'^/orders/(\d+)/complete/$', path)
        if m:
            return f'Marked Order #{m.group(1)} as completed'

        m = re.match(r'^/orders/(\d+)/checkout/$', path)
        if m:
            return f'Billed out Order #{m.group(1)}'

        m = re.match(r'^/orders/(\d+)/payment/$', path)
        if m:
            payment_status = self._clean_value(post.get('payment_status'))
            payment_method = self._clean_value(post.get('payment_method'))
            extra = []
            if payment_status:
                extra.append(payment_status)
            if payment_method:
                extra.append(payment_method)
            suffix = f" ({', '.join(extra)})" if extra else ''
            return f'Updated payment for Order #{m.group(1)}{suffix}'

        m = re.match(r'^/orders/(\d+)/discount/$', path)
        if m:
            order_id = m.group(1)
            discount_type = self._clean_value(post.get('discount_type'))
            if discount_type:
                return f'Applied {discount_type} discount to Order #{order_id}'
            return f'Updated discount for Order #{order_id}'

        m = re.match(r'^/orders/(\d+)/$', path)
        if m:
            return f'Updated Order #{m.group(1)}'

        # Meat items / prices
        if path == '/meat/new/':
            meat_type = self._clean_value(post.get('meat_type')) or 'meat item'
            return f'Added {meat_type}'

        m = re.match(r'^/meat/(\d+)/edit/$', path)
        if m:
            meat = MeatItem.objects.filter(pk=m.group(1)).first()
            meat_name = meat.meat_type if meat else 'meat item'
            new_price = self._clean_value(post.get('current_price'))
            if meat and new_price:
                return f'Changed price of {meat_name} from ₱{meat.current_price:g} to ₱{new_price}'
            return f'Updated {meat_name}'

        m = re.match(r'^/meat/(\d+)/delete/$', path)
        if m:
            meat = MeatItem.objects.filter(pk=m.group(1)).first()
            meat_name = meat.meat_type if meat else f'Meat #{m.group(1)}'
            return f'Deleted {meat_name}'

        # Suppliers
        if path == '/admin-console/suppliers/new/':
            supplier_name = self._clean_value(post.get('supplier_name')) or 'supplier'
            return f'Added {supplier_name}'

        m = re.match(r'^/admin-console/suppliers/(\d+)/delete/$', path)
        if m:
            supplier = Supplier.objects.filter(pk=m.group(1)).first()
            supplier_name = supplier.supplier_name if supplier else f'Supplier #{m.group(1)}'
            return f'Deleted {supplier_name}'

        m = re.match(r'^/admin-console/suppliers/(\d+)/transactions/new/$', path)
        if m:
            supplier = Supplier.objects.filter(pk=m.group(1)).first()
            supplier_name = supplier.supplier_name if supplier else f'Supplier #{m.group(1)}'
            item_name = self._clean_value(post.get('item_name'))
            amount = self._clean_value(post.get('transaction_amount'))
            item_part = f' for {item_name}' if item_name else ''
            amount_part = f' (₱{amount})' if amount else ''
            return f'Recorded transaction for {supplier_name}{item_part}{amount_part}'

        m = re.match(r'^/admin-console/suppliers/(\d+)/$', path)
        if m:
            supplier = Supplier.objects.filter(pk=m.group(1)).first()
            supplier_name = supplier.supplier_name if supplier else f'Supplier #{m.group(1)}'
            return f'Updated {supplier_name}'

        # Discounts
        if path == '/admin-console/discounts/new/':
            discount_type = self._clean_value(post.get('discount_type')) or 'discount type'
            discount_value = self._clean_value(post.get('discount_value'))
            value_part = f' ({discount_value}%)' if discount_value else ''
            return f'Added {discount_type}{value_part}'

        m = re.match(r'^/admin-console/discounts/(\d+)/edit/$', path)
        if m:
            discount = Discount.objects.filter(pk=m.group(1)).first()
            discount_name = discount.discount_type if discount else 'discount type'
            new_value = self._clean_value(post.get('discount_value'))
            if discount and new_value:
                return f'Updated {discount_name} discount to {new_value}%'
            return f'Updated {discount_name} discount'

        m = re.match(r'^/admin-console/discounts/(\d+)/delete/$', path)
        if m:
            discount = Discount.objects.filter(pk=m.group(1)).first()
            discount_name = discount.discount_type if discount else f'Discount #{m.group(1)}'
            return f'Deleted {discount_name} discount'

        # Accounts
        if path == '/admin-console/accounts/new/':
            username = self._clean_value(post.get('username')) or 'account'
            return f'Created account for {username}'

        m = re.match(r'^/admin-console/accounts/(\d+)/$', path)
        if m:
            username = self._clean_value(post.get('staff_name')) or self._clean_value(post.get('username'))
            if username:
                return f'Updated account for {username}'
            return f'Updated Account #{m.group(1)}'

        # Inquiries
        if path == '/inquiries/new/':
            try:
                meat = MeatItem.objects.filter(pk=post.get('meat_id')).first()
            except Exception:
                meat = None
            meat_name = meat.meat_type if meat else (self._clean_value(post.get('item_name')) or 'meat request')
            return f'Created inquiry for {meat_name}'

        m = re.match(r'^/inquiries/(\d+)/accept/$', path)
        if m:
            inquiry = PriceInquiryRequest.objects.filter(pk=m.group(1)).first()
            target = inquiry.meat.meat_type if inquiry and inquiry.meat_id else f'Inquiry #{m.group(1)}'
            return f'Accepted inquiry for {target}'

        m = re.match(r'^/inquiries/(\d+)/update/$', path)
        if m:
            inquiry = PriceInquiryRequest.objects.select_related('meat').filter(pk=m.group(1)).first()
            target = inquiry.meat.meat_type if inquiry and inquiry.meat_id else f'Inquiry #{m.group(1)}'
            new_price = self._clean_value(post.get('new_price'))
            notes = self._clean_value(post.get('notes'))
            if inquiry and inquiry.meat_id and new_price:
                old_price = inquiry.meat.current_price
                notes_part = f' - {notes}' if notes else ''
                return f'Changed price of {target} from ₱{old_price:g} to ₱{new_price}{notes_part}'
            status = self._clean_value(post.get('status'))
            remarks = self._clean_value(post.get('remarks')) or notes
            status_part = f' as {status}' if status else ''
            remarks_part = f' - {remarks}' if remarks else ''
            return f'Responded to inquiry for {target}{status_part}{remarks_part}'

        m = re.match(r'^/inquiries/(\d+)/delete/$', path)
        if m:
            inquiry = PriceInquiryRequest.objects.filter(pk=m.group(1)).first()
            target = inquiry.meat.meat_type if inquiry and inquiry.meat_id else f'Inquiry #{m.group(1)}'
            return f'Deleted inquiry for {target}'

        # Backup / restore
        if path == '/admin-console/backup/' and request.FILES.get('backup_file'):
            return f'Restored backup file {request.FILES["backup_file"].name}'

        return None

    def __call__(self, request):
        response = self.get_response(request)

        try:
            if request.method in {"POST", "PUT", "PATCH", "DELETE"} and response.status_code < 500:
                path = request.path
                if path.startswith('/static/') or path.startswith('/media/'):
                    return response

                staff = None
                username = None

                staff_id = request.session.get('staff_id') or request.session.get('admin_staff_id')
                if staff_id:
                    staff = Staff.objects.filter(staff_id=staff_id).first()
                    if staff:
                        username = staff.staff_email or staff.staff_name

                if not username and getattr(request, 'user', None) and request.user.is_authenticated:
                    username = request.user.username

                action_text = self._build_action_text(request)
                if not action_text:
                    return response

                AuditLog.objects.create(
                    staff=staff,
                    username=username,
                    action='WRITE',
                    path=path,
                    method=request.method,
                    model_name=path.strip('/').split('/')[0] or 'root',
                    object_repr=f"HTTP {response.status_code}",
                    details=action_text[:4000],
                    created_at=timezone.now(),
                )
        except Exception:
            pass

        return response
