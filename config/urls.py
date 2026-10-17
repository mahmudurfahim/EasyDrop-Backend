from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def health(request):
    # Lightweight uptime check (UptimeRobot): no auth, no DB, no throttling.
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),

    path("api/health/", health),
    path("api/", include("shop.urls")),
]