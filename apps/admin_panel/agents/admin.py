from django.contrib import admin

from apps.shared.models import Agent, AgentPromptHistory, RoutingRule


class AgentPromptHistoryInline(admin.TabularInline):
    model = AgentPromptHistory
    extra = 0
    can_delete = False
    readonly_fields = ("previous_prompt", "new_prompt", "changed_by", "changed_at")

    def has_add_permission(self, request, obj=None):  # historial append-only
        return False


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    list_display = ("name", "llm_model", "temperature", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name", "description")
    inlines = [AgentPromptHistoryInline]

    def save_model(self, request, obj, form, change):
        # Auditoría de cambios de prompt (§5.1 Repudiation): cada edición del
        # system_prompt queda registrada con autor, valor anterior y nuevo.
        if change and "system_prompt" in form.changed_data:
            previous = Agent.objects.get(pk=obj.pk).system_prompt
            super().save_model(request, obj, form, change)
            AgentPromptHistory.objects.create(
                agent=obj,
                previous_prompt=previous,
                new_prompt=obj.system_prompt,
                changed_by=request.user,
            )
            return
        super().save_model(request, obj, form, change)


@admin.register(RoutingRule)
class RoutingRuleAdmin(admin.ModelAdmin):
    list_display = ("intent", "agent", "min_confidence", "is_active", "updated_at")
    list_filter = ("is_active", "agent")
    list_editable = ("agent", "min_confidence", "is_active")
    ordering = ("intent",)


@admin.register(AgentPromptHistory)
class AgentPromptHistoryAdmin(admin.ModelAdmin):
    list_display = ("agent", "changed_by", "changed_at")
    readonly_fields = ("agent", "previous_prompt", "new_prompt", "changed_by", "changed_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
