"""Приложение orders: заказ, позиции заказа и платёж (схема v1.1).

Бизнес-правила на уровне моделей (полная таблица — docs/reviews/traceability-matrix.md):
  BR-001  дата не в прошлом при создании    -> Order.clean()
  BR-002  «Выполнен» -> «Отменён» запрещено -> Order.clean() и Order.save()
  BR-003  сумма > 100 000 ₽                 -> Order.recalculate() -> needs_approval
  BR-004  товара на складе не хватает       -> OrderItem.clean()
  BR-006  пользователь удалён -> archived   -> apps/orders/signals.py + FK SET_NULL
  BR-007  скидка > 30%                      -> Order.recalculate() -> needs_approval
  BR-008  version не совпадает -> 409       -> Order.version + Order.ensure_version()
                                               (ответ 409 формирует API, этап Э7)
"""
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q, Sum, F
from django.utils import timezone

# Пороговые значения бизнес-правил: в одном месте, чтобы не искать по коду
APPROVAL_AMOUNT_THRESHOLD = Decimal("100000")  # BR-003: подтверждение, если сумма БОЛЬШЕ
DISCOUNT_THRESHOLD = Decimal("0.30")  # BR-007: подтверждение, если скидка БОЛЬШЕ 30%


class OrderVersionConflict(Exception):
    """BR-008: версия заказа у клиента устарела. API превращает это в HTTP 409."""


class OrderStatus(models.TextChoices):
    NEW = "new", "Новый"
    IN_PROGRESS = "in_progress", "В работе"
    DONE = "done", "Выполнен"
    CANCELLED = "cancelled", "Отменён"


class PaymentMethod(models.TextChoices):
    CASH = "cash", "Наличные"
    CARD = "card", "Карта"
    TRANSFER = "transfer", "Перевод"


class PaymentStatus(models.TextChoices):
    PENDING = "pending", "Ожидает оплаты"
    PAID = "paid", "Оплачен"
    FAILED = "failed", "Ошибка оплаты"


class Order(models.Model):
    client = models.ForeignKey(
        "customers.Client",
        # Схема 6.1: удаление клиента удаляет его заказы. Риск описан в docs/architecture/models.md
        on_delete=models.CASCADE,
        related_name="orders",
        verbose_name="Клиент",
    )
    # Добавлено в v1.1: связь «менеджер -> заказы» есть в схеме (раздел 4), а столбца не было
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,  # BR-006: заказ не удаляется вместе с пользователем
        null=True,
        blank=True,
        related_name="orders",
        verbose_name="Менеджер",
    )
    date = models.DateField("Дата заказа", default=timezone.localdate)
    status = models.CharField("Статус", max_length=20, choices=OrderStatus.choices, default=OrderStatus.NEW)
    total_amount = models.DecimalField(
        "Сумма, ₽", max_digits=10, decimal_places=2, default=Decimal("0.00"), validators=[MinValueValidator(0)]
    )
    notes = models.TextField("Заметки", blank=True)
    # Добавлено в v1.1: «отмена заказа с указанием причины» (REQ-WEB)
    cancel_reason = models.TextField("Причина отмены", blank=True)
    needs_approval = models.BooleanField("Требует подтверждения", default=False)
    archived = models.BooleanField("В архиве", default=False)
    version = models.IntegerField("Версия (контроль конфликтов)", default=0, editable=False)
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        verbose_name = "Заказ"
        verbose_name_plural = "Заказы"
        ordering = ["-date", "-id"]
        indexes = [
            models.Index(fields=["status"], name="idx_order_status"),
            models.Index(fields=["date"], name="idx_order_date"),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(total_amount__gte=0), name="order_total_gte_0"),
            models.CheckConstraint(
                condition=Q(status__in=[s.value for s in OrderStatus]),
                name="order_status_valid",
            ),
        ]

    def __str__(self):
        return f"Заказ №{self.pk} от {self.date:%d.%m.%Y}" if self.pk and self.date else "Новый заказ"

    # --- проверки бизнес-правил ---

    def _check_status_transition(self):
        """BR-002: выполненный заказ нельзя отменить."""
        if self._state.adding or not self.pk:
            return
        old_status = Order.objects.filter(pk=self.pk).values_list("status", flat=True).first()
        if old_status == OrderStatus.DONE and self.status == OrderStatus.CANCELLED:
            raise ValidationError({"status": "Нельзя отменить выполненный заказ (BR-002)."})

    def clean(self):
        # BR-001: при создании дата не может быть в прошлом
        if self._state.adding and self.date and self.date < timezone.localdate():
            raise ValidationError({"date": "Дата не может быть в прошлом (BR-001)."})
        self._check_status_transition()
        if self.status == OrderStatus.CANCELLED and not self.cancel_reason.strip():
            raise ValidationError({"cancel_reason": "Укажите причину отмены заказа."})

    def save(self, *args, **kwargs):
        self._check_status_transition()  # вторая линия защиты: не только админка
        if not self._state.adding:
            self.version += 1  # BR-008: любое изменение увеличивает версию
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = set(update_fields) | {"version", "updated_at"}
        super().save(*args, **kwargs)

    def ensure_version(self, client_version):
        """BR-008: сравнить версию клиента с серверной. Вызывается из API перед изменением заказа."""
        if client_version != self.version:
            raise OrderVersionConflict(
                f"Версия заказа устарела: у клиента {client_version}, на сервере {self.version}."
            )

    # --- вычисляемые значения (кандидаты ФР, чтобы клиенты не считали сами) ---

    def calculate_total(self):
        line = F("quantity") * F("price_at_moment")
        total = self.items.aggregate(
            s=Sum(line, output_field=models.DecimalField(max_digits=14, decimal_places=2))
        )["s"]
        return total or Decimal("0.00")

    def has_big_discount(self):
        """BR-007: есть позиция со скидкой больше 30% от цены товара."""
        for item in self.items.select_related("product"):
            if item.price_at_moment < item.product.price * (1 - DISCOUNT_THRESHOLD):
                return True
        return False

    @property
    def items_count(self):
        return self.items.count()

    @property
    def is_editable(self):
        """REQ-WEB: редактирование запрещено в статусах «Выполнен» и «Отменён»."""
        return self.status in (OrderStatus.NEW, OrderStatus.IN_PROGRESS)

    def recalculate(self):
        """Пересчитать сумму (кеш) и флаг подтверждения (BR-003, BR-007).

        Флаг только включается, но сам не снимается: снять его (= подтвердить заказ)
        может человек. Запись идёт через update(), чтобы не увеличивать version
        и не вызывать save() повторно.
        """
        total = self.calculate_total()
        needs_approval = self.needs_approval or total > APPROVAL_AMOUNT_THRESHOLD or self.has_big_discount()
        Order.objects.filter(pk=self.pk).update(
            total_amount=total, needs_approval=needs_approval, updated_at=timezone.now()
        )
        self.total_amount = total
        self.needs_approval = needs_approval


class OrderItem(models.Model):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,  # схема 6.1: удаляются позиции заказа
        related_name="items",
        verbose_name="Заказ",
    )
    product = models.ForeignKey(
        "catalog.Product",
        # Схема 6.1: нельзя удалить товар, если он в заказах (в таблице 3.5 стоял CASCADE: описка)
        on_delete=models.PROTECT,
        related_name="order_items",
        verbose_name="Товар",
    )
    quantity = models.IntegerField("Количество", validators=[MinValueValidator(1)])
    price_at_moment = models.DecimalField(
        "Цена на момент заказа, ₽", max_digits=10, decimal_places=2, validators=[MinValueValidator(0)]
    )

    class Meta:
        verbose_name = "Позиция заказа"
        verbose_name_plural = "Позиции заказа"
        ordering = ["order_id", "id"]
        constraints = [
            models.UniqueConstraint(fields=["order", "product"], name="uniq_order_item_order_product"),
            models.CheckConstraint(condition=Q(quantity__gt=0), name="order_item_quantity_gt_0"),
            models.CheckConstraint(condition=Q(price_at_moment__gte=0), name="order_item_price_gte_0"),
        ]

    def __str__(self):
        return f"{self.product} × {self.quantity}"

    @property
    def line_total(self):
        if self.quantity is None or self.price_at_moment is None:
            return Decimal("0.00")
        return self.quantity * self.price_at_moment

    def clean(self):
        # BR-004: нельзя заказать больше, чем есть на складе (для существующей позиции — только прирост)
        if self.product_id and self.quantity:
            previous = 0
            if self.pk:
                previous = OrderItem.objects.filter(pk=self.pk).values_list("quantity", flat=True).first() or 0
            increase = self.quantity - previous
            available = self.product.stock_quantity
            if increase > available:
                raise ValidationError(
                    {"quantity": f"Недостаточно товара: на складе {available}, нужно ещё {increase} (BR-004)."}
                )


class Payment(models.Model):
    order = models.OneToOneField(
        Order,
        on_delete=models.CASCADE,
        related_name="payment",
        verbose_name="Заказ",
    )
    amount = models.DecimalField("Сумма, ₽", max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    method = models.CharField("Способ оплаты", max_length=20, choices=PaymentMethod.choices)
    status = models.CharField(
        "Статус платежа", max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING
    )
    paid_at = models.DateTimeField("Дата оплаты", null=True, blank=True)

    class Meta:
        verbose_name = "Платёж"
        verbose_name_plural = "Платежи"
        ordering = ["-id"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gte=0), name="payment_amount_gte_0"),
            models.CheckConstraint(
                condition=Q(method__in=[m.value for m in PaymentMethod]), name="payment_method_valid"
            ),
            models.CheckConstraint(
                condition=Q(status__in=[s.value for s in PaymentStatus]), name="payment_status_valid"
            ),
        ]

    def __str__(self):
        return f"Платёж по заказу №{self.order_id}"

    def save(self, *args, **kwargs):
        # Оплаченный платёж всегда имеет дату оплаты
        if self.status == PaymentStatus.PAID and self.paid_at is None:
            self.paid_at = timezone.now()
        super().save(*args, **kwargs)
