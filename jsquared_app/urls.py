from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    
    path("", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("home/", views.home, name="home"),

    # Admin console
    path("admin-console/", views.manager_login, name="manager_login"),
    path("admin-console/", views.admin_console, name="admin_console"),
    path("admin-console/accounts/", views.account_list, name="account_list"),
    path("admin-console/accounts/new/", views.account_create, name="account_create"),
    path("admin-console/accounts/<int:account_id>/", views.account_detail, name="account_detail"),
    path("admin-console/sales-report/", views.sales_report, name="sales_report"),

    # Sales report exports
    path("admin-console/sales-report/export/csv/", views.sales_report_export_csv, name="sales_report_export_csv"),
    path("admin-console/sales-report/export/xlsx/", views.sales_report_export_xlsx, name="sales_report_export_xlsx"),
    path("admin-console/sales-report/print/", views.sales_report_print, name="sales_report_print"),

    # Suppliers
    path("admin-console/suppliers/", views.supplier_list, name="supplier_list"),
    path("admin-console/suppliers/new/", views.supplier_create, name="supplier_create"),
    path("admin-console/suppliers/<int:supplier_id>/", views.supplier_detail, name="supplier_detail"),
    path("admin-console/suppliers/<int:supplier_id>/delete/", views.supplier_delete, name="supplier_delete"),
    path("admin-console/suppliers/<int:supplier_id>/transactions/new/", views.supplier_transaction_create, name="supplier_transaction_create"),
    path("admin-console/suppliers/<int:supplier_id>/transactions/<int:transaction_id>/edit/", views.supplier_transaction_update, name="supplier_transaction_update"),
    path("admin-console/suppliers/<int:supplier_id>/transactions/<int:transaction_id>/delete/", views.supplier_transaction_delete, name="supplier_transaction_delete"),
    path( "admin-console/supplier/mark-paid/<int:transaction_id>/", views.supplier_mark_paid,name="supplier_mark_paid"),
    path(
    "admin-console/supplier-transactions/<int:transaction_id>/status/",
    views.supplier_update_transaction_status,
    name="supplier_update_transaction_status",
),

    # Discount management
    path("admin-console/discounts/", views.discount_list, name="discount_list"),
    path("admin-console/discounts/new/", views.discount_create, name="discount_create"),
    path("admin-console/discounts/<int:discount_id>/edit/", views.discount_edit, name="discount_edit"),
    path("admin-console/discounts/<int:discount_id>/delete/", views.discount_delete, name="discount_delete"),

    # Audit log / backup
    path("admin-console/audit-logs/", views.audit_log_list, name="audit_log_list"),
    path("admin-console/backup/", views.backup_restore, name="backup_restore"),
    path("admin-console/backup/download/", views.backup_download, name="backup_download"),

    # Meat prices
    path("meat/", views.meat_price_list, name="meat_price_list"),
    path("meat/new/", views.meat_price_create, name="meat_price_create"),
    path("meat/<int:meat_id>/edit/", views.meat_price_edit, name="meat_price_edit"),
    path("meat/<int:meat_id>/delete/", views.meat_price_delete, name="meat_price_delete"),
    path("meat/<int:meat_id>/", views.meat_detail, name="meat_detail"),

    # Orders
    path("orders/", views.order_list, name="order_list"),
    path("orders/history/", views.order_history, name="order_history"),
    path("orders/new/", views.order_create, name="order_create"),
    path("orders/<int:order_id>/", views.order_detail, name="order_detail"),
    path("orders/<int:order_id>/delete/", views.order_delete, name="order_delete"),
    path("orders/<int:order_id>/items/<int:order_item_id>/delete/", views.order_item_delete,name="order_item_delete",),
    path("orders/<int:order_id>/accept/", views.order_accept, name="order_accept"),
    path("orders/<int:order_id>/cancel/", views.order_cancel, name="order_cancel"),
    path("orders/<int:order_id>/serve/", views.order_serve, name="order_serve"),
    path("orders/<int:order_id>/payment/", views.order_update_payment, name="order_update_payment"),
    path("orders/<int:order_id>/discount/", views.order_update_discount, name="order_update_discount"),
    path("orders/<int:order_id>/checkout/", views.order_checkout, name="order_checkout"),
    path("orders/<int:order_id>/complete/", views.order_complete, name="order_complete"),

    # Price inquiry requests (UC14–UC16)
    path("inquiries/", views.inquiry_list, name="inquiry_list"),
    path("inquiries/new/", views.inquiry_create, name="inquiry_create"),
    path("inquiries/<int:inquiry_id>/accept/", views.inquiry_accept, name="inquiry_accept"),
    path("inquiries/<int:inquiry_id>/update/", views.inquiry_update_price, name="inquiry_update_price"),
    path("inquiries/<int:inquiry_id>/delete/", views.inquiry_delete, name="inquiry_delete"),

    #Cooking Styles
    path("cooking_styles_list/", views.cooking_styles_list, name="cooking_styles_list"),
    path("cookingstyle/<int:meat_id>/", views.meat_category, name="meat_category"),
    path("cookingstyle/<int:meat_id>/new/", views.cooking_style_create, name="cooking_style_create"),
    path("cookingstyle/<int:cooking_style_id>/edit/", views.cooking_style_edit, name="cooking_style_edit"),
    path("cookingstyle/<int:cooking_style_id>/delete/", views.cooking_style_delete, name="cooking_style_delete"),
]
