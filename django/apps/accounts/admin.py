from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm

from .models import Session, User


class UserCreationFormWithRole(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "role")


class UserChangeFormWithRole(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = "__all__"


class SessionInline(admin.TabularInline):
    """Сеансы пользователя: только просмотр (токены в карточке пользователя не показываем)."""

    model = Session
    extra = 0
    fields = ("created_at", "expires_at", "is_active")
    readonly_fields = fields
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = UserChangeFormWithRole
    add_form = UserCreationFormWithRole

    list_display = ("username", "role", "is_active", "last_login", "created_at")
    list_filter = ("role", "is_active")
    search_fields = ("username",)
    ordering = ("username",)
    date_hierarchy = "created_at"
    list_per_page = 25
    filter_horizontal = ()
    readonly_fields = ("is_superuser", "last_login", "created_at")
    inlines = [SessionInline]
    actions = ["deactivate_users", "activate_users"]

    fieldsets = (
        ("Учётная запись", {"fields": ("username", "password")}),
        ("Роль и доступ", {"fields": ("role", "is_active", "is_superuser")}),
        ("Служебное", {"fields": ("last_login", "created_at")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("username", "role", "password1", "password2")}),
    )

    @admin.action(description="Заблокировать выбранных пользователей")
    def deactivate_users(self, request, queryset):
        # Свою учётную запись заблокировать нельзя, иначе администратор потеряет доступ
        updated = queryset.exclude(pk=request.user.pk).update(is_active=False)
        self.message_user(request, f"Заблокировано пользователей: {updated}.", messages.SUCCESS)

    @admin.action(description="Разблокировать выбранных пользователей")
    def activate_users(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f"Разблокировано пользователей: {updated}.", messages.SUCCESS)


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ("user", "created_at", "expires_at", "is_active", "expired")
    list_filter = ("is_active", "created_at")
    search_fields = ("user__username",)
    date_hierarchy = "created_at"
    list_select_related = ("user",)
    list_per_page = 25
    readonly_fields = ("created_at",)
    autocomplete_fields = ("user",)
    actions = ["close_sessions"]

    fieldsets = (
        ("Сеанс", {"fields": ("user", "token", "is_active")}),
        ("Сроки", {"fields": ("created_at", "expires_at")}),
    )

    @admin.display(description="Истёк", boolean=True)
    def expired(self, obj):
        return obj.is_expired

    @admin.action(description="Завершить выбранные сеансы")
    def close_sessions(self, request, queryset):
        updated = queryset.filter(is_active=True).update(is_active=False)
        self.message_user(request, f"Завершено сеансов: {updated}.", messages.SUCCESS)
