from django.contrib import admin
from django.db.models import Count

from apps.orders.models import Order

from .models import Client


class ClientOrderInline(admin.TabularInline):
    """Заказы клиента: компактный просмотр со ссылкой на карточку заказа."""

    model = Order
    extra = 0
    fields = ("date", "status", "total_amount", "archived")
    readonly_fields = fields
    can_delete = False
    show_change_link = True
    verbose_name = "Заказ клиента"
    verbose_name_plural = "Заказы клиента"

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "email", "orders_count", "created_at")
    list_filter = ("created_at",)
    search_fields = ("name", "phone", "email", "address")
    date_hierarchy = "created_at"
    list_per_page = 25
    readonly_fields = ("created_at", "updated_at")
    inlines = [ClientOrderInline]

    fieldsets = (
        ("Контакты", {"fields": ("name", "phone", "email")}),
        ("Адрес", {"fields": ("address",)}),
        ("Служебное", {"fields": ("created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_orders_count=Count("orders"))

    @admin.display(description="Заказов", ordering="_orders_count")
    def orders_count(self, obj):
        return obj._orders_count
