"""Сигналы приложения orders."""
from django.db.models import F
from django.db.models.signals import post_delete, post_save, pre_delete
from django.dispatch import receiver
from django.utils import timezone

from apps.accounts.models import User

from .models import Order, OrderItem


@receiver(pre_delete, sender=User)
def archive_orders_of_deleted_user(sender, instance, **kwargs):
    """BR-006: пользователь удалён -> его заказы не удаляются, а помечаются archived.

    Поле manager после удаления обнулится само (on_delete=SET_NULL).
    """
    Order.objects.filter(manager=instance).update(
        archived=True, updated_at=timezone.now(), version=F("version") + 1
    )


@receiver(post_save, sender=OrderItem)
@receiver(post_delete, sender=OrderItem)
def recalculate_order(sender, instance, **kwargs):
    """Любое изменение позиции пересчитывает сумму заказа и флаг подтверждения (BR-003, BR-007)."""
    if kwargs.get("raw"):  # загрузка фикстур: данные уже согласованы, пересчёт не нужен
        return
    order = Order.objects.filter(pk=instance.order_id).first()
    if order is not None:
        order.recalculate()
