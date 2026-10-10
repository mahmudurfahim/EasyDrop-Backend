from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from . import views as v
from . import views_app as a

router = DefaultRouter()
router.register("admin/products", v.AdminProducts, "admin-products")
router.register("admin/categories", v.AdminCategories, "admin-categories")
router.register("admin/subcategories", v.AdminSubCategories, "admin-subcategories")
router.register("admin/notices", v.AdminNotices, "admin-notices")
urlpatterns = [
    path("media/product/<int:pk>/", a.media), path("auth/register/", a.register),
    path("products/", a.products), path("products/<int:pk>/", a.product_detail),
    path("favorites/", a.favorites), path("favorites/<int:pk>/", a.favorite_toggle),
    path("cart/", a.cart), path("cart/checkout/", a.cart_checkout), path("cart/<int:pk>/", a.cart_remove),
    path("orders/", a.orders), path("orders/<int:pk>/", a.order_detail), path("orders/<int:pk>/cancel/", a.order_cancel),
    path("accounts/", a.accounts), path("accounts/<int:pk>/", a.account_delete), path("withdraws/", a.withdraw_create),
    path("dashboard/", a.dashboard), path("admin/orders/<int:pk>/note/", a.admin_note),
    path("auth/login/", TokenObtainPairView.as_view()), path("auth/refresh/", TokenRefreshView.as_view()),
    path("auth/verify/", v.verify), path("auth/send-code/", v.send_code), path("auth/reset/", v.reset), path("admin/settings/", v.admin_settings),
    path("me/", v.me), path("categories/", v.categories), path("notices/", v.notices),
    path("admin/products/<int:pk>/images/", v.product_images), path("admin/products/<int:pk>/images/<int:image_id>/", v.product_image_delete), path("wallet/", v.wallet), path("admin/dashboard/", v.dashboard), path("admin/orders/", v.admin_orders), path("admin/orders/<int:pk>/<str:action>/", v.order_action),
    path("admin/resellers/", v.reseller_list), path("admin/resellers/<int:pk>/status/", v.reseller_status),
    path("admin/withdraws/", v.admin_withdraws), path("admin/withdraws/<int:pk>/<str:action>/", v.withdraw_action),
    path("", include(router.urls)),
]
