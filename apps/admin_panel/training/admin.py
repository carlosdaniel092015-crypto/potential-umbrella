from django.contrib import admin

from apps.shared.models import TrainingExample


@admin.register(TrainingExample)
class TrainingExampleAdmin(admin.ModelAdmin):
    list_display = ("message", "created_by", "created_at")
    readonly_fields = ("message", "created_by", "created_at")
