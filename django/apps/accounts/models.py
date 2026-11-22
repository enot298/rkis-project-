"""Приложение accounts: пользователи и сеансы (таблицы user и session схемы v1.1).

Бизнес-правила:
  * BR-005 — у пользователя не больше одного активного сеанса. Проверяется
    тремя уровнями: частичный UNIQUE-индекс в БД, Session.clean() (понятное
    сообщение в админке) и, позже, API при входе.
  * BR-006 (удаление пользователя -> заказы помечаются archived) реализован
    сигналом в apps/orders/signals.py, потому что затрагивает заказы.
"""
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError
from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


class Role(models.TextChoices):
    ADMIN = "admin", "Администратор"
    MANAGER = "manager", "Менеджер"
    STOREKEEPER = "storekeeper", "Кладовщик"
    ACCOUNTANT = "accountant", "Бухгалтер"


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, username, password, **extra_fields):
        if not username:
            raise ValueError("Необходимо указать логин")
        user = self.model(username=username, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, username, password=None, **extra_fields):
        extra_fields.setdefault("role", Role.MANAGER)
        return self._create_user(username, password, **extra_fields)

    def create_superuser(self, username, password=None, **extra_fields):
        extra_fields["role"] = Role.ADMIN
        extra_fields["is_superuser"] = True
        return self._create_user(username, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """Пользователь системы. Роль определяет доступ в клиентах (веб, десктоп, мобайл).

    В Django-админку входят только администраторы: остальные роли работают
    через API и клиентские приложения.
    """

    username = models.CharField(
        "Логин",
        max_length=50,
        unique=True,
        validators=[UnicodeUsernameValidator()],
        error_messages={"unique": "Пользователь с таким логином уже существует."},
    )
    role = models.CharField("Роль", max_length=20, choices=Role.choices, default=Role.MANAGER)
    is_active = models.BooleanField("Активен", default=True)
    created_at = models.DateTimeField("Дата создания", auto_now_add=True)
    # password и last_login ("Последний вход") приходят из AbstractBaseUser

    objects = UserManager()

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"
        ordering = ["username"]
        indexes = [models.Index(fields=["role"], name="idx_user_role")]
        constraints = [
            models.CheckConstraint(
                condition=Q(role__in=[r.value for r in Role]),
                name="user_role_valid",
            ),
        ]

    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"

    def save(self, *args, **kwargs):
        # Администратор по роли = полный доступ (is_superuser) во всей системе
        if self.role == Role.ADMIN:
            self.is_superuser = True
        super().save(*args, **kwargs)

    @property
    def is_staff(self):
        """Доступ в Django-админку: только активные администраторы."""
        return bool(self.is_active and (self.is_superuser or self.role == Role.ADMIN))

    def get_full_name(self):
        return self.username

    def get_short_name(self):
        return self.username


class Session(models.Model):
    """Сеанс пользователя с JWT-токеном."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,  # схема 6.1: удаляются сеансы пользователя
        related_name="sessions",
        verbose_name="Пользователь",
    )
    token = models.CharField("Токен", max_length=255, unique=True)
    created_at = models.DateTimeField("Дата создания", auto_now_add=True)
    expires_at = models.DateTimeField("Действует до")
    is_active = models.BooleanField("Активен", default=True)

    class Meta:
        verbose_name = "Сеанс входа"
        verbose_name_plural = "Сеансы входа"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "is_active"], name="idx_session_user_active")]
        constraints = [
            # BR-005: не больше одного активного сеанса на пользователя
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(is_active=True),
                name="uniq_active_session_per_user",
            ),
            models.CheckConstraint(
                condition=Q(expires_at__gt=F("created_at")),
                name="session_expires_after_created",
            ),
        ]

    def __str__(self):
        state = "активен" if self.is_active else "завершён"
        return f"Сеанс {self.user} ({state})"

    @property
    def is_expired(self):
        return self.expires_at <= timezone.now()

    def clean(self):
        # BR-005: понятное сообщение вместо ошибки БД
        if self.is_active and self.user_id:
            others = Session.objects.filter(user_id=self.user_id, is_active=True)
            if self.pk:
                others = others.exclude(pk=self.pk)
            if others.exists():
                raise ValidationError(
                    "У пользователя уже есть активный сеанс. Завершите его, прежде чем создавать новый (BR-005)."
                )
