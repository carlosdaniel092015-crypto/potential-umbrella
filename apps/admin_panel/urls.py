from django.contrib import admin
from django.urls import path

urlpatterns = [
    path("admin/", admin.site.urls),
]

admin.site.site_header = "Plataforma IA WhatsApp — Sued Fargesa"
admin.site.site_title = "Panel IA WhatsApp"
