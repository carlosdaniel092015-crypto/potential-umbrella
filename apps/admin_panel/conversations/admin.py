from django.contrib import admin

from apps.shared.models import Contact, Conversation, Message, Order, TraceEvent


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    can_delete = False
    readonly_fields = (
        "direction",
        "message_type",
        "content",
        "status",
        "agent",
        "external_id",
        "created_at",
    )
    fields = readonly_fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ("phone", "name", "created_at")
    search_fields = ("phone", "name")


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("contact", "channel", "status", "agent", "last_inbound_at", "updated_at")
    list_filter = ("channel", "status")
    inlines = [MessageInline]
    # El contador de posiciones se reparte bajo bloqueo en el registro de
    # mensajes. Editable desde aquí, un guardado escribiría el valor leído en el
    # GET y podría repetir una posición ya usada, con lo que dos mensajes
    # dejarían de ser anteriores el uno al otro.
    readonly_fields = ("next_seq", "last_inbound_at")


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("conversation", "direction", "message_type", "status", "agent", "created_at")
    list_filter = ("direction", "message_type", "status")
    search_fields = ("content", "external_id")
    readonly_fields = [f.name for f in Message._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("conversation", "status", "odoo_order_id", "created_at")
    list_filter = ("status",)


@admin.register(TraceEvent)
class TraceEventAdmin(admin.ModelAdmin):
    list_display = ("conversation", "trace_id", "event_type", "created_at")
    search_fields = ("trace_id",)
    readonly_fields = [f.name for f in TraceEvent._meta.fields]

    def has_add_permission(self, request):
        return False
