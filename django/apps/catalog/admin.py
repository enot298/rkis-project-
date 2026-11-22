from django.contrib import admin

from .models import Product

LOW_STOCK_LIMIT = 10


class StockFilter(admin.SimpleListFilter):
    title = "Остаток на складе"
    parameter_name = "stock"

    def lookups(self, request, model_admin):
        return (
            ("none", "Нет в наличии"),
            ("low", f"Мало (1–{LOW_STOCK_LIMIT})"),
            ("ok", f"Достаточно (больше {LOW_STOCK_LIMIT})"),
        )

    def queryset(self, request, queryset):
        if self.value() == "none":
            return queryset.filter(stock_quantity=0)
        if self.value() == "low":
            return queryset.filter(stock_quantity__gte=1, stock_quantity__lte=LOW_STOCK_LIMIT)
        if self.value() == "ok":
            return queryset.filter(stock_quantity__gt=LOW_STOCK_LIMIT)
        return queryset


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "barcode", "price", "stock_quantity", "created_at")
    list_filter = (StockFilter, "created_at")
    search_fields = ("name", "barcode")
    date_hierarchy = "created_at"
    list_per_page = 25
    readonly_fields = ("created_at", "updated_at")

    fieldsets = (
        ("Товар", {"fields": ("name", "barcode")}),
        ("Цена и склад", {"fields": ("price", "stock_quantity")}),
        ("Служебное", {"fields": ("created_at", "updated_at")}),
    )
