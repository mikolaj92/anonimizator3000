"""Product shell chrome: paths, menu, and per-request platform context."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from app_factory.platform import (
    MenuGroup,
    MenuItem,
    PlatformConfig,
    PlatformLocale,
    PlatformPaths,
    PlatformUser,
    apply_platform_context,
    build_platform_context,
)
from jinja2 import Environment

APP_NAME: Final = "Dokumenty"
DEFAULT_LOCALE: Final = "pl"
LOCALE_COOKIE_NAME: Final = "anon_lang"
SUPPORTED_LOCALES: Final[tuple[str, ...]] = ("pl", "en", "de")

_PATHS: Final = PlatformPaths(
    login="/login",
    logout="/logout",
    register="/register",
    account="/account",
    admin_users="/admin/users",
)

_LOCALES: Final = (
    PlatformLocale(code="pl", label="PL"),
    PlatformLocale(code="en", label="EN"),
    PlatformLocale(code="de", label="DE"),
)


def build_menu(*, is_admin: bool = False) -> tuple[MenuGroup, ...]:
    """Sidebar navigation for the anonymization portal."""
    product = MenuGroup(
        label="Produkt",
        items=(
            MenuItem(label="Anonimizacja", href="/", icon="file"),
        ),
    )
    if not is_admin:
        return (product,)
    admin = MenuGroup(
        label="Administracja",
        items=(
            MenuItem(label="Użytkownicy", href=_PATHS.admin_users, icon="users"),
        ),
    )
    return (product, admin)


def platform_config(
    *,
    user: Mapping[str, Any] | None = None,
    show_register: bool = True,
) -> PlatformConfig:
    """Build chrome config; admin menu only when the session user is admin."""
    is_admin = bool(user and user.get("is_admin"))
    return PlatformConfig(
        app_name=APP_NAME,
        brand_href="/",
        brand_htmx=False,
        navigation_label="Nawigacja",
        menu=build_menu(is_admin=is_admin),
        paths=_PATHS,
        enable_admin_users=True,
        show_register=show_register,
        locales=_LOCALES,
        default_locale=DEFAULT_LOCALE,
        htmx_nav=False,
    )


# Install-time globals (guest menu).
PLATFORM_CONFIG: Final = platform_config(user=None)


def install_platform_chrome(environments: list[Environment]) -> PlatformConfig:
    """Bind static platform globals into host Jinja environments."""
    for environment in environments:
        apply_platform_context(environment, PLATFORM_CONFIG)
    return PLATFORM_CONFIG


def platform_request_context(
    *,
    user: Mapping[str, Any] | None,
    current_path: str = "",
    locale: str | None = None,
) -> dict[str, Any]:
    """Map the flat session user dict into product shell context."""
    platform_user: PlatformUser | None = None
    if user and user.get("id"):
        platform_user = PlatformUser(
            display_name=str(user.get("name") or user.get("id")),
            is_admin=bool(user.get("is_admin")),
            user_id=str(user.get("id")),
        )
    return build_platform_context(
        platform_config(user=user),
        user=platform_user,
        current_path=current_path,
        locales=_LOCALES,
        locale=locale or DEFAULT_LOCALE,
    )


def login_platform_config() -> PlatformConfig:
    """Chrome for packaged my-auth login/register pages."""
    return PlatformConfig(
        app_name=APP_NAME,
        brand_href="/",
        brand_htmx=False,
        paths=_PATHS,
        enable_admin_users=False,
        show_register=True,
        locales=(
            PlatformLocale(code="pl", label="PL", href="/login?lang=pl"),
            PlatformLocale(code="en", label="EN", href="/login?lang=en"),
            PlatformLocale(code="de", label="DE", href="/login?lang=de"),
        ),
        default_locale=DEFAULT_LOCALE,
        htmx_nav=False,
    )
