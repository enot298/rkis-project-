from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db.models import Count

from .models import Order, OrderItem, OrderStatus, Payment, PaymentStatus


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 1
    fields = ("product", "quantity", "price_at_moment", "line_total")
    readonly_fields = ("line_total",)
    autocomplete_fields = ("product",)

    @admin.display(description="Сумма по позиции, ₽")
    def line_total(self, obj):
        return obj.line_total if obj.pk else "—"


class PaymentInline(admin.StackedInline):
    model = Payment
    extra = 0
    max_num = 1
    fields = ("amount", "method", "status", "paid_at")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "id", "client", "date", "status", "total_amount", "needs_approval", "archived", "manager", "positions",
    )
    list_display_links = ("id", "client")
    list_filter = ("status", "needs_approval", "archived", "date", "manager")
    search_fields = ("client__name", "client__phone", "notes", "manager__username")
    date_hierarchy = "date"
    list_select_related = ("client", "manager")
    list_per_page = 25
    autocomplete_fields = ("client", "manager")
    readonly_fields = ("total_amount", "version", "created_at", "updated_at")
    inlines = [OrderItemInline, PaymentInline]
    actions = ["set_in_progress", "set_done", "set_cancelled", "archive_orders", "unarchive_orders", "approve_orders"]

    fieldsets = (
        ("Основное", {"fields": ("client", "manager", "date", "status")}),
        ("Сумма и подтверждение", {"fields": ("total_amount", "needs_approval")}),
        ("Заметки", {"fields": ("notes", "cancel_reason")}),
        ("Служебное", {"fields": ("archived", "version", "created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_positions=Count("items"))

    @admin.display(description="Позиций", ordering="_positions")
    def positions(self, obj):
        return obj._positions

    # --- массовые действия ---

    def _change_status(self, request, queryset, status):
        done = 0
        for order in queryset:
            order.status = status
            if status == OrderStatus.CANCELLED and not order.cancel_reason.strip():
                order.cancel_reason = "Отменён массовым действием в админке"
            try:
                order.save()
                done += 1
            except ValidationError as exc:
                messages.error(request, f"{order}: {'; '.join(exc.messages)}")
        if done:
            self.message_user(request, f"Статус изменён у заказов: {done}.", messages.SUCCESS)

    @admin.action(description="Перевести в статус «В работе»")
    def set_in_progress(self, request, queryset):
        self._change_status(request, queryset, OrderStatus.IN_PROGRESS)

    @admin.action(description="Перевести в статус «Выполнен»")
    def set_done(self, request, queryset):
        self._change_status(request, queryset, OrderStatus.DONE)

    @admin.action(description="Отменить выбранные заказы")
    def set_cancelled(self, request, queryset):
        self._change_status(request, queryset, OrderStatus.CANCELLED)

    @admin.action(description="Отправить в архив")
    def archive_orders(self, request, queryset):
        updated = 0
        for order in queryset.filter(archived=False):
            order.archived = True
            order.save()
            updated += 1
        self.message_user(request, f"Заказов в архиве: {updated}.", messages.SUCCESS)

    @admin.action(description="Вернуть из архива")
    def unarchive_orders(self, request, queryset):
        updated = 0
        for order in queryset.filter(archived=True):
            order.archived = False
            order.save()
            updated += 1
        self.message_user(request, f"Возвращено из архива: {updated}.", messages.SUCCESS)

    @admin.action(description="Подтвердить заказы (снять «требует подтверждения»)")
    def approve_orders(self, request, queryset):
        updated = 0
        for order in queryset.filter(needs_approval=True):
            order.needs_approval = False
            order.save()
            updated += 1
        self.message_user(request, f"Подтверждено заказов: {updated}.", messages.SUCCESS)


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ("order", "product", "quantity", "price_at_moment", "line_total")
    search_fields = ("product__name", "order__client__name")
    list_filter = ("order__status",)
    list_select_related = ("order", "product")
    list_per_page = 25
    autocomplete_fields = ("order", "product")
    readonly_fields = ("line_total",)

    @admin.display(description="Сумма по позиции, ₽")
    def line_total(self, obj):
        return obj.line_total if obj.pk else "—"


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("order", "amount", "method", "status", "paid_at")
    list_filter = ("status", "method", "paid_at")
    search_fields = ("order__client__name",)
    date_hierarchy = "paid_at"
    list_select_related = ("order",)
    list_per_page = 25
    autocomplete_fields = ("order",)
