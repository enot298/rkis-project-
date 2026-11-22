"""Приложение catalog: справочник товаров и остатки (таблица product схемы v1.1).

BR-004 (товара не хватает) проверяется в OrderItem.clean() (apps/orders/models.py),
потому что относится к позиции заказа; здесь лежат сами остатки.
"""
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q


class Product(models.Model):
    name = models.CharField("Название", max_length=255)
    price = models.DecimalField("Цена, ₽", max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    stock_quantity = models.IntegerField("Остаток на складе", default=0, validators=[MinValueValidator(0)])
    # Добавлено в схеме v1.1: сканирование штрихкодов в мобильном клиенте (REQ-MOB)
    barcode = models.CharField("Штрихкод", max_length=64, unique=True, null=True, blank=True)
    created_at = models.DateTimeField("Дата создания", auto_now_add=True)
    # Добавлено в v1.1: офлайн-кеш и синхронизация (REQ-DESK, REQ-MOB)
    updated_at = models.DateTimeField("Дата обновления", auto_now=True)

    class Meta:
        verbose_name = "Товар"
        verbose_name_plural = "Товары"
        ordering = ["name"]
        indexes = [models.Index(fields=["name"], name="idx_product_name")]
        constraints = [
            models.CheckConstraint(condition=Q(price__gte=0), name="product_price_gte_0"),
            models.CheckConstraint(condition=Q(stock_quantity__gte=0), name="product_stock_gte_0"),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.barcode is not None:
            self.barcode = self.barcode.strip() or None
        super().save(*args, **kwargs)
