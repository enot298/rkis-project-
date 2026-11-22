#!/usr/bin/env python3
"""Проверка фикстур без Django и без базы: python tests/fixtures/validate_fixtures.py

Читает 01..04_*.json и проверяет их по правилам схемы v1.1: типы и длины, enum'ы,
уникальность, внешние ключи, CHECK-ограничения, согласованность суммы заказа с
позициями и флага needs_approval (BR-003, BR-007), единственность активного
сеанса (BR-005), пароли тестовых пользователей.

Это проверка самих данных. Реальную загрузку проверяет `python manage.py loaddata`.
"""
import base64
import hashlib
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILES = ["01_accounts.json", "02_customers.json", "03_catalog.json", "04_orders.json"]
TEST_PASSWORD = "Test12345!"

ROLES = {"admin", "manager", "storekeeper", "accountant"}
ORDER_STATUSES = {"new", "in_progress", "done", "cancelled"}
METHODS = {"cash", "card", "transfer"}
PAY_STATUSES = {"pending", "paid", "failed"}
PHONE_RE = re.compile(r"^\+?[0-9]{7,15}$")
USERNAME_RE = re.compile(r"^[\w.@+-]+$")

errors = []


def err(msg):
    errors.append(msg)


def dec(value, digits=10, places=2, label=""):
    d = Decimal(value)
    sign, nums, exp = d.as_tuple()
    if -exp > places or len(nums) > digits:
        err(f"{label}: число {value} не помещается в DECIMAL({digits},{places})")
    return d


def load():
    data = {}
    for name in FILES:
        for row in json.loads((HERE / name).read_text(encoding="utf-8")):
            data.setdefault(row["model"], {})
            if row["pk"] in data[row["model"]]:
                err(f"{row['model']}: повторяется pk={row['pk']}")
            data[row["model"]][row["pk"]] = row["fields"]
    return data


def check_hash(h, label):
    algo, iters, salt, digest = h.split("$")
    dk = hashlib.pbkdf2_hmac("sha256", TEST_PASSWORD.encode(), salt.encode(), int(iters))
    if algo != "pbkdf2_sha256" or base64.b64encode(dk).decode().strip() != digest:
        err(f"{label}: пароль не соответствует тестовому ({TEST_PASSWORD})")


def main():
    d = load()
    users, sessions = d.get("accounts.user", {}), d.get("accounts.session", {})
    clients, products = d.get("customers.client", {}), d.get("catalog.product", {})
    orders, items, pays = d.get("orders.order", {}), d.get("orders.orderitem", {}), d.get("orders.payment", {})

    # --- пользователи ---
    seen = set()
    for pk, f in users.items():
        lb = f"user {pk}"
        if f["role"] not in ROLES: err(f"{lb}: роль {f['role']}")
        if not (1 <= len(f["username"]) <= 50) or not USERNAME_RE.match(f["username"]): err(f"{lb}: логин")
        if f["username"] in seen: err(f"{lb}: логин не уникален")
        seen.add(f["username"])
        if f["role"] == "admin" and not f["is_superuser"]: err(f"{lb}: администратор должен быть is_superuser")
        check_hash(f["password"], lb)

    # --- сеансы ---
    tokens, active = set(), {}
    for pk, f in sessions.items():
        lb = f"session {pk}"
        if f["user"] not in users: err(f"{lb}: нет пользователя {f['user']}")
        if not (1 <= len(f["token"]) <= 255): err(f"{lb}: длина токена")
        if f["token"] in tokens: err(f"{lb}: токен не уникален")
        tokens.add(f["token"])
        if f["expires_at"] <= f["created_at"]: err(f"{lb}: expires_at должен быть позже created_at")
        if f["is_active"]:
            if f["user"] in active: err(f"{lb}: второй активный сеанс у пользователя {f['user']} (BR-005)")
            active[f["user"]] = pk

    # --- клиенты ---
    emails = set()
    for pk, f in clients.items():
        lb = f"client {pk}"
        if not (1 <= len(f["name"]) <= 255): err(f"{lb}: длина имени {len(f['name'])}")
        if not PHONE_RE.match(f["phone"]) or len(f["phone"]) > 20: err(f"{lb}: телефон {f['phone']}")
        if f["email"] is not None:
            if f["email"] in emails: err(f"{lb}: email не уникален")
            emails.add(f["email"])
            if f["email"] != f["email"].lower() or len(f["email"]) > 255: err(f"{lb}: email")

    # --- товары ---
    barcodes = set()
    for pk, f in products.items():
        lb = f"product {pk}"
        if not (1 <= len(f["name"]) <= 255): err(f"{lb}: длина названия")
        if dec(f["price"], label=lb) < 0: err(f"{lb}: цена < 0")
        if f["stock_quantity"] < 0: err(f"{lb}: остаток < 0")
        if f["barcode"] is not None:
            if f["barcode"] in barcodes: err(f"{lb}: штрихкод не уникален")
            barcodes.add(f["barcode"])

    # --- позиции и суммы заказов ---
    totals, big_discount, pairs = {}, set(), set()
    for pk, f in items.items():
        lb = f"orderitem {pk}"
        if f["order"] not in orders: err(f"{lb}: нет заказа {f['order']}")
        if f["product"] not in products: err(f"{lb}: нет товара {f['product']}")
        if f["quantity"] <= 0: err(f"{lb}: количество <= 0")
        price = dec(f["price_at_moment"], label=lb)
        if price < 0: err(f"{lb}: цена < 0")
        if (f["order"], f["product"]) in pairs: err(f"{lb}: повтор пары (заказ, товар)")
        pairs.add((f["order"], f["product"]))
        totals[f["order"]] = totals.get(f["order"], Decimal("0")) + f["quantity"] * price
        prod = products.get(f["product"])
        if prod and price < Decimal(prod["price"]) * Decimal("0.70"):
            big_discount.add(f["order"])

    # --- заказы ---
    for pk, f in orders.items():
        lb = f"order {pk}"
        if f["client"] not in clients: err(f"{lb}: нет клиента")
        if f["manager"] is not None and f["manager"] not in users: err(f"{lb}: нет менеджера")
        if f["status"] not in ORDER_STATUSES: err(f"{lb}: статус")
        total = dec(f["total_amount"], label=lb)
        if total < 0: err(f"{lb}: сумма < 0")
        if total != totals.get(pk, Decimal("0")):
            err(f"{lb}: total_amount={total}, а сумма позиций={totals.get(pk, Decimal('0'))}")
        expected = total > Decimal("100000") or pk in big_discount
        if expected and not f["needs_approval"]: err(f"{lb}: needs_approval должен быть true (BR-003/BR-007)")
        if not expected and f["needs_approval"]: err(f"{lb}: needs_approval=true без причин")
        if f["manager"] is None and not f["archived"]: err(f"{lb}: заказ без менеджера должен быть в архиве (BR-006)")
        if f["status"] == "cancelled" and not f["cancel_reason"].strip(): err(f"{lb}: нет причины отмены")
        if f["version"] < 0: err(f"{lb}: version < 0")

    # --- платежи ---
    used = set()
    for pk, f in pays.items():
        lb = f"payment {pk}"
        if f["order"] not in orders: err(f"{lb}: нет заказа")
        if f["order"] in used: err(f"{lb}: у заказа уже есть платёж (1:1)")
        used.add(f["order"])
        if dec(f["amount"], label=lb) < 0: err(f"{lb}: сумма < 0")
        if f["method"] not in METHODS: err(f"{lb}: способ")
        if f["status"] not in PAY_STATUSES: err(f"{lb}: статус")
        if f["status"] == "paid" and not f["paid_at"]: err(f"{lb}: оплачен, но нет paid_at")

    counts = {k: len(v) for k, v in d.items()}
    print("Записей:", counts)
    if errors:
        print(f"\nОШИБОК: {len(errors)}")
        for e in errors:
            print(" -", e)
        sys.exit(1)
    print("Все проверки пройдены.")


if __name__ == "__main__":
    main()
