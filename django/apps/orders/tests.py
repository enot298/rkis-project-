"""Тесты бизнес-правил на уровне моделей. Запуск: python manage.py test apps"""
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Role, Session, User
from apps.catalog.models import Product
from apps.customers.models import Client

from .models import Order, OrderItem, OrderStatus, OrderVersionConflict, Payment


def make_order(client, **kwargs):
    kwargs.setdefault("date", timezone.localdate())
    return Order.objects.create(client=client, **kwargs)


class BaseCase(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(name="ООО Тест", phone="+79001112233")
        self.product = Product.objects.create(name="Товар", price=Decimal("1000.00"), stock_quantity=50)


class OrderRulesTests(BaseCase):
    def test_br001_past_date_rejected_on_create(self):
        order = Order(client=self.client_obj, date=timezone.localdate() - timedelta(days=1))
        with self.assertRaises(ValidationError):
            order.full_clean()

    def test_br001_today_is_allowed(self):
        Order(client=self.client_obj, date=timezone.localdate()).full_clean()

    def test_br001_old_order_can_be_edited(self):
        order = make_order(self.client_obj, date=timezone.localdate() - timedelta(days=5))
        order.notes = "правка"
        order.full_clean()  # проверка даты только при создании

    def test_br002_done_to_cancelled_rejected(self):
        order = make_order(self.client_obj, status=OrderStatus.DONE)
        order.status = OrderStatus.CANCELLED
        order.cancel_reason = "ошибка"
        with self.assertRaises(ValidationError):
            order.save()

    def test_br002_other_transitions_allowed(self):
        order = make_order(self.client_obj, status=OrderStatus.NEW)
        order.status = OrderStatus.IN_PROGRESS
        order.save()
        order.status = OrderStatus.DONE
        order.save()

    def test_cancel_requires_reason_in_clean(self):
        order = make_order(self.client_obj)
        order.status = OrderStatus.CANCELLED
        with self.assertRaises(ValidationError):
            order.full_clean()

    def test_br003_boundary_100000_does_not_require_approval(self):
        product = Product.objects.create(name="Дорогой", price=Decimal("100000.00"), stock_quantity=1)
        order = make_order(self.client_obj)
        OrderItem.objects.create(order=order, product=product, quantity=1, price_at_moment=Decimal("100000.00"))
        order.refresh_from_db()
        self.assertEqual(order.total_amount, Decimal("100000.00"))
        self.assertFalse(order.needs_approval)

    def test_br003_over_100000_requires_approval(self):
        product = Product.objects.create(name="Дорогой", price=Decimal("100000.01"), stock_quantity=1)
        order = make_order(self.client_obj)
        OrderItem.objects.create(order=order, product=product, quantity=1, price_at_moment=Decimal("100000.01"))
        order.refresh_from_db()
        self.assertTrue(order.needs_approval)

    def test_br007_discount_over_30_percent_requires_approval(self):
        order = make_order(self.client_obj)
        OrderItem.objects.create(order=order, product=self.product, quantity=1, price_at_moment=Decimal("690.00"))
        order.refresh_from_db()
        self.assertTrue(order.needs_approval)

    def test_br007_discount_exactly_30_percent_is_ok(self):
        order = make_order(self.client_obj)
        OrderItem.objects.create(order=order, product=self.product, quantity=1, price_at_moment=Decimal("700.00"))
        order.refresh_from_db()
        self.assertFalse(order.needs_approval)

    def test_total_is_recalculated_on_item_change_and_delete(self):
        order = make_order(self.client_obj)
        item = OrderItem.objects.create(order=order, product=self.product, quantity=3, price_at_moment=Decimal("1000.00"))
        order.refresh_from_db()
        self.assertEqual(order.total_amount, Decimal("3000.00"))
        item.quantity = 5
        item.save()
        order.refresh_from_db()
        self.assertEqual(order.total_amount, Decimal("5000.00"))
        item.delete()
        order.refresh_from_db()
        self.assertEqual(order.total_amount, Decimal("0.00"))

    def test_br008_version_increments_and_conflict_detected(self):
        order = make_order(self.client_obj)
        self.assertEqual(order.version, 0)
        order.notes = "1"
        order.save()
        self.assertEqual(order.version, 1)
        order.ensure_version(1)
        with self.assertRaises(OrderVersionConflict):
            order.ensure_version(0)

    def test_deleting_client_cascades_to_orders(self):
        order = make_order(self.client_obj)
        self.client_obj.delete()
        self.assertFalse(Order.objects.filter(pk=order.pk).exists())


class OrderItemRulesTests(BaseCase):
    def test_br004_not_enough_stock_rejected(self):
        order = make_order(self.client_obj)
        item = OrderItem(order=order, product=self.product, quantity=51, price_at_moment=Decimal("1000.00"))
        with self.assertRaises(ValidationError):
            item.full_clean()

    def test_br004_exact_stock_allowed(self):
        order = make_order(self.client_obj)
        OrderItem(order=order, product=self.product, quantity=50, price_at_moment=Decimal("1000.00")).full_clean()

    def test_unique_product_per_order(self):
        order = make_order(self.client_obj)
        OrderItem.objects.create(order=order, product=self.product, quantity=1, price_at_moment=Decimal("1000.00"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            OrderItem.objects.create(order=order, product=self.product, quantity=2, price_at_moment=Decimal("1000.00"))

    def test_zero_quantity_rejected_by_db(self):
        order = make_order(self.client_obj)
        with self.assertRaises(IntegrityError), transaction.atomic():
            OrderItem.objects.create(order=order, product=self.product, quantity=0, price_at_moment=Decimal("1.00"))

    def test_product_in_order_cannot_be_deleted(self):
        order = make_order(self.client_obj)
        OrderItem.objects.create(order=order, product=self.product, quantity=1, price_at_moment=Decimal("1000.00"))
        with self.assertRaises(ProtectedError):
            self.product.delete()


class PaymentTests(BaseCase):
    def test_one_payment_per_order(self):
        order = make_order(self.client_obj)
        Payment.objects.create(order=order, amount=Decimal("10.00"), method="cash")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Payment.objects.create(order=order, amount=Decimal("5.00"), method="card")

    def test_paid_payment_gets_paid_at(self):
        order = make_order(self.client_obj)
        payment = Payment.objects.create(order=order, amount=Decimal("10.00"), method="cash", status="paid")
        self.assertIsNotNone(payment.paid_at)

    def test_negative_amount_rejected_by_db(self):
        order = make_order(self.client_obj)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Payment.objects.create(order=order, amount=Decimal("-1.00"), method="cash")


class UserAndSessionTests(BaseCase):
    def test_br006_deleting_user_archives_orders(self):
        manager = User.objects.create_user("mgr", "Test12345!", role=Role.MANAGER)
        order = make_order(self.client_obj, manager=manager)
        manager.delete()
        order.refresh_from_db()
        self.assertTrue(order.archived)
        self.assertIsNone(order.manager)

    def test_br005_only_one_active_session(self):
        user = User.objects.create_user("u1", "Test12345!")
        now = timezone.now()
        Session.objects.create(user=user, token="t1", expires_at=now + timedelta(hours=1))
        second = Session(user=user, token="t2", created_at=now, expires_at=now + timedelta(hours=1))
        with self.assertRaises(ValidationError):
            second.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            second.save()

    def test_inactive_sessions_can_accumulate(self):
        user = User.objects.create_user("u2", "Test12345!")
        now = timezone.now()
        for i in range(3):
            Session.objects.create(user=user, token=f"old{i}", is_active=False, expires_at=now + timedelta(hours=1))

    def test_admin_role_is_staff_and_superuser(self):
        admin = User.objects.create_user("adm", "Test12345!", role=Role.ADMIN)
        self.assertTrue(admin.is_superuser)
        self.assertTrue(admin.is_staff)

    def test_manager_cannot_enter_django_admin(self):
        manager = User.objects.create_user("m", "Test12345!", role=Role.MANAGER)
        self.assertFalse(manager.is_staff)

    def test_create_superuser_sets_admin_role(self):
        user = User.objects.create_superuser("root", "Test12345!")
        self.assertEqual(user.role, Role.ADMIN)
        self.assertTrue(user.is_superuser)


class ClientTests(TestCase):
    def test_empty_email_is_stored_as_null_and_not_unique_conflict(self):
        a = Client.objects.create(name="A", phone="+79000000001", email="")
        b = Client.objects.create(name="B", phone="+79000000002", email="")
        self.assertIsNone(a.email)
        self.assertIsNone(b.email)

    def test_email_is_lowercased_and_unique(self):
        Client.objects.create(name="A", phone="+79000000001", email="A@Test.RU")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Client.objects.create(name="B", phone="+79000000002", email="a@test.ru")

    def test_phone_validation(self):
        with self.assertRaises(ValidationError):
            Client(name="X", phone="abc").full_clean()
