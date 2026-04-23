from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from .models import (
    CookingStyle,
    MeatItem,
    Order,
    OrderItem,
    Staff,
    Supplier,
    SupplierTransaction,
    VariedMenuItem,
    FixedMenuItem,
)
from .views import SESSION_STAFF_ID, SESSION_STAFF_ROLE


class SupplierModuleTests(TestCase):
    def setUp(self):
        self.manager = Staff.objects.create(
            staff_name="Manager User",
            staff_role="Manager",
            staff_email="manager@example.com",
            staff_address="Quezon City",
            staff_password="test123",
        )

        session = self.client.session
        session[SESSION_STAFF_ID] = self.manager.staff_id
        session[SESSION_STAFF_ROLE] = self.manager.staff_role
        session.save()

        self.supplier = Supplier.objects.create(
            supplier_name="Dela Cruz Meat Supply",
            contact_person="Juan Dela Cruz",
            phone_number="09171234567",
            supplier_address="Marikina Public Market",
        )

        self.meat = MeatItem.objects.create(
            meat_type="Pork Belly",
            meat_description="Fresh pork belly",
            weight_min=1,
            weight_max=10,
            current_price=330,
        )
        self.style = CookingStyle.objects.create(
            meat_item=self.meat,
            style_name="Inihaw",
            style_description="Grilled",
            cooking_charge=70,
            c_weight_min=1,
            c_weight_max=10,
        )
        self.varied_item = VariedMenuItem.objects.get(
            meat=self.meat,
            cooking_style=self.style,
            is_byom=False,
        )
        self.order = Order.objects.create(
            staff=self.manager,
            customer_name="Cass",
            table_num=3,
        )
        self.order_item = OrderItem.objects.create(
            order=self.order,
            varied_item=self.varied_item,
            order_quantity=2,
        )

    def test_assign_supplier_from_order_creates_transaction(self):
        response = self.client.post(
            reverse("supplier_list"),
            {
                "supplier_id": self.supplier.supplier_id,
                "order_item_id": self.order_item.order_item_id,
                "return_url": reverse("order_detail", args=[self.order.order_id]),
                "action": "assign_supplier",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.order_item.refresh_from_db()
        self.assertEqual(self.order_item.supplier_id, self.supplier.supplier_id)

        tx = SupplierTransaction.objects.get(
            notes=f"Auto-created from Order #{self.order.order_id}, Item #{self.order_item.order_item_id}"
        )
        self.assertEqual(tx.supplier_id, self.supplier.supplier_id)
        self.assertEqual(tx.meat_id, self.meat.meat_id)
        self.assertEqual(tx.payment_status, "Pending")
        self.assertAlmostEqual(tx.unit_price, 330.0)
        self.assertAlmostEqual(tx.quantity, 2.0)
        self.assertAlmostEqual(tx.transaction_amount, 660.0)

    def test_supplier_list_shows_transaction_summary_and_pending_total(self):
        SupplierTransaction.objects.create(
            supplier=self.supplier,
            meat=self.meat,
            item_name="Pork Belly - Inihaw",
            transaction_date=timezone.localdate(),
            unit_price=250,
            quantity=6,
            payment_status="Pending",
            notes="Manual entry",
        )

        response = self.client.get(reverse("supplier_list"))

        self.assertEqual(response.status_code, 200)
        supplier = next(s for s in response.context["suppliers"] if s.supplier_id == self.supplier.supplier_id)
        self.assertEqual(supplier.transaction_count, 1)
        self.assertEqual(float(supplier.total_transaction_amount), 1500.0)
        self.assertEqual(float(supplier.pending_transaction_amount), 1500.0)
        self.assertContains(response, "Total Pending Payment")
        self.assertContains(response, "Pending: ₱ 1500.00")

    def test_manual_supplier_transaction_is_visible_on_detail_page(self):
        response = self.client.post(
            reverse("supplier_transaction_create", args=[self.supplier.supplier_id]),
            {
                "meat_id": self.meat.meat_id,
                "transaction_date": timezone.localdate().strftime("%Y-%m-%d"),
                "unit_price": "330",
                "quantity": "12",
                "payment_status": "Completed",
                "notes": "Purchase for wet market run",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            SupplierTransaction.objects.filter(
                supplier=self.supplier,
                transaction_amount=3960,
                notes="Purchase for wet market run",
            ).exists()
        )
        self.assertContains(response, "Transactions")
        self.assertContains(response, "Purchase for wet market run")
        self.assertContains(response, "Completed")


    def test_manual_supplier_transaction_allows_custom_item_name_when_not_in_dropdown(self):
        response = self.client.post(
            reverse("supplier_transaction_create", args=[self.supplier.supplier_id]),
            {
                "meat_id": "",
                "item_name": "Imported Salmon Belly",
                "transaction_date": timezone.localdate().strftime("%Y-%m-%d"),
                "unit_price": "450",
                "quantity": "3",
                "payment_status": "Pending",
                "notes": "Custom market purchase",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            SupplierTransaction.objects.filter(
                supplier=self.supplier,
                meat__isnull=True,
                item_name="Imported Salmon Belly",
                transaction_amount=1350,
            ).exists()
        )
        self.assertContains(response, "Imported Salmon Belly")

    def test_edit_supplier_transaction_updates_system_generated_or_manual_details(self):
        transaction = SupplierTransaction.objects.create(
            supplier=self.supplier,
            meat=self.meat,
            item_name="Pork Belly",
            transaction_date=timezone.localdate(),
            unit_price=330,
            quantity=10,
            payment_status="Pending",
            notes="Auto-created from Order #1, Item #1",
        )

        response = self.client.post(
            reverse("supplier_transaction_update", args=[self.supplier.supplier_id, transaction.transaction_id]),
            {
                "meat_id": self.meat.meat_id,
                "transaction_date": timezone.localdate().strftime("%Y-%m-%d"),
                "unit_price": "340",
                "quantity": "11",
                "payment_status": "Completed",
                "notes": transaction.notes,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        transaction.refresh_from_db()
        self.assertAlmostEqual(transaction.unit_price, 340.0)
        self.assertAlmostEqual(transaction.quantity, 11.0)
        self.assertAlmostEqual(transaction.transaction_amount, 3740.0)
        self.assertEqual(transaction.payment_status, "Completed")


    def test_cannot_complete_order_without_supplier_for_meat_items(self):
        self.order.order_status = "Preparing"
        self.order.save(update_fields=["order_status"])

        response = self.client.post(
            reverse("order_serve", args=[self.order.order_id]),
            follow=True,
        )

        self.order.refresh_from_db()
        self.assertEqual(self.order.order_status, "Preparing")
        self.assertContains(response, "Select a supplier for every meat item before completing the order.")

    def test_can_complete_byom_order_without_supplier(self):
        byom_item = VariedMenuItem.objects.get(
            meat=self.meat,
            cooking_style=self.style,
            is_byom=True,
        )
        self.order_item.delete()
        OrderItem.objects.create(
            order=self.order,
            varied_item=byom_item,
            order_quantity=2,
        )
        self.order.order_status = "Preparing"
        self.order.save(update_fields=["order_status"])

        response = self.client.post(
            reverse("order_serve", args=[self.order.order_id]),
            follow=True,
        )

        self.order.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.order.order_status, "Completed")

    def test_order_detail_shows_none_and_disables_supplier_for_byom_item(self):
        byom_item = VariedMenuItem.objects.get(
            meat=self.meat,
            cooking_style=self.style,
            is_byom=True,
        )
        self.order_item.delete()
        OrderItem.objects.create(
            order=self.order,
            varied_item=byom_item,
            order_quantity=1,
        )

        response = self.client.get(reverse("order_detail", args=[self.order.order_id]))

        self.assertContains(response, 'data-is-byom="1"')
        self.assertContains(response, 'None')

    def test_order_detail_shows_none_and_disables_supplier_for_fixed_item(self):
        fixed_item = FixedMenuItem.objects.create(
            item_name="Rice",
            item_description="Plain rice",
            item_category="Sides",
            fixed_price=35,
        )
        self.order_item.delete()
        OrderItem.objects.create(
            order=self.order,
            fixed_item=fixed_item,
            order_quantity=1,
            order_unit_price=35,
            subtotal=35,
        )

        response = self.client.get(reverse("order_detail", args=[self.order.order_id]))

        self.assertContains(response, 'data-requires-supplier="0"')
        self.assertContains(response, 'None')
        self.assertNotContains(response, '>Select<')

    def test_supplier_detail_can_filter_transactions_by_date_range(self):
        today = timezone.localdate()
        older = today - timedelta(days=1)

        SupplierTransaction.objects.create(
            supplier=self.supplier,
            meat=self.meat,
            item_name="Older Purchase",
            transaction_date=older,
            unit_price=100,
            quantity=1,
            payment_status="Pending",
            notes="Older",
        )
        SupplierTransaction.objects.create(
            supplier=self.supplier,
            meat=self.meat,
            item_name="Today Purchase",
            transaction_date=today,
            unit_price=200,
            quantity=2,
            payment_status="Completed",
            notes="Today",
        )

        response = self.client.get(
            reverse("supplier_detail", args=[self.supplier.supplier_id]),
            {"start_date": str(today), "end_date": str(today)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Today")
        self.assertNotContains(response, "Older")
