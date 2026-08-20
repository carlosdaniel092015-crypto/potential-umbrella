from django.contrib import admin

from apps.shared.models import Configuration


@admin.register(Configuration)
class ConfigurationAdmin(admin.ModelAdmin):
    list_display = ("key", "version", "updated_by", "updated_at")
    search_fields = ("key",)
    readonly_fields = ("version", "updated_by", "updated_at")

    def save_model(self, request, obj, form, change):
        if change:
            obj.version += 1
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
