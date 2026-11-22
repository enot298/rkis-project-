"""Приложение customers: клиенты (таблица client схемы v1.1)."""
from django.core.validators import RegexValidator
from django.db import models

phone_validator = RegexValidator(
    regex=r"^\+?[0-9]{7,15}$",
    message="Введите телефон цифрами, например +79001112233 (от 7 до 15 цифр).",
)


class Client(models.Model):
    name = models.CharField("Имя / название", max_length=255)
    phone = models.CharField("Телефон", max_length=20, validators=[phone_validator])
    # Пустой email хранится как NULL, иначе UNIQUE ломался бы на нескольких клиентах без email
    email = models.EmailField("Email", max_length=255, unique=True, null=True, blank=True)
    address = models.TextField("Адрес", blank=True)
    created_at = models.DateTimeField("Дата создания", auto_now_add=True)
    updated_at = models.DateTimeField("Дата обновления", auto_now=True)

    class Meta:
        verbose_name = "Клиент"
        verbose_name_plural = "Клиенты"
        ordering = ["name"]
        # idx_client_email из схемы не нужен: UNIQUE уже создаёт индекс по email
        indexes = [models.Index(fields=["name"], name="idx_client_name")]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if self.email is not None:
            self.email = self.email.strip().lower() or None
        super().save(*args, **kwargs)
