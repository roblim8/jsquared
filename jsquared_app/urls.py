from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    # login/logout
    path("login/", auth_views.LoginView.as_view(template_name="jsquared_app/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),

    # home
    path("", views.home, name="home"),

    # Feature A (Seafood CRUD)
    path("seafood/", views.seafood_price_list, name="seafood_price_list"),
    path("seafood/new/", views.seafood_price_create, name="seafood_price_create"),
    path("seafood/<int:seafood_id>/edit/", views.seafood_price_edit, name="seafood_price_edit"),
    path("seafood/<int:seafood_id>/delete/", views.seafood_price_delete, name="seafood_price_delete"),

    # Feature B (Orders)
    path("orders/", views.order_list, name="order_list"),
    path("orders/new/", views.order_create, name="order_create"),
    path("orders/<int:order_id>/", views.order_detail, name="order_detail"),
    path("orders/<int:order_id>/delete/", views.order_delete, name="order_delete"),
    path("orders/<int:order_id>/items/<int:order_item_id>/delete/", views.order_item_delete, name="order_item_delete"),
]
