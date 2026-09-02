"""Product shell chrome: paths, menu, and per-request platform context."""

from __future__ import annotations

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
from my_auth.fastapi import PasskeyPaths
from my_usermanager.permissions import ADMIN_ROLE_NAME
from my_usermanager.sessions import SessionPrincipal

APP_NAME: Final = "Dokumenty"
DEFAULT_LOCALE: Final = "pl"
LOCALE_COOKIE_NAME: Final = "anon_lang"
SUPPORTED_LOCALES: Final[tuple[str, ...]] = ("pl", "en", "de")

PASSKEY_PATHS: Final = PasskeyPaths()
PLATFORM_PATHS: Final = PlatformPaths(
    login=PASSKEY_PATHS.login_page,
    logout=PASSKEY_PATHS.logout,
    register=PASSKEY_PATHS.register_page,
    activation=PASSKEY_PATHS.activation_page,
    recovery=PASSKEY_PATHS.recovery_page,
    account="/account",
    credentials=PASSKEY_PATHS.credentials_page,
    admin_users="/admin/users",
    # Packaged invite form lives on the users page; POST remains /admin/users/invite.
    invite="/admin/users",
)

_LOCALE_LABELS: Final = (("pl", "PL"), ("en", "EN"), ("de", "DE"))
_LOCALES: Final = tuple(
    PlatformLocale(code=code, label=label) for code, label in _LOCALE_LABELS
)


def build_menu(*, is_admin: bool = False) -> tuple[MenuGroup, ...]:
    """Sidebar product navigation; identity slots come from PlatformConfig flags."""
    del is_admin
    return (
        MenuGroup(
            label="Produkt",
            items=(
                MenuItem(label="Anonimizacja", href="/", icon="file"),
            ),
        ),
    )


def platform_user_from_principal(principal: SessionPrincipal | None) -> PlatformUser | None:
    """Project the typed session principal into product chrome."""
    if principal is None:
        return None
    return PlatformUser(
        display_name=principal.display_name or principal.username or principal.user_id,
        is_admin=ADMIN_ROLE_NAME in principal.roles,
        user_id=principal.user_id,
    )


def platform_config(
    *,
    user: PlatformUser | None = None,
    show_register: bool = True,
) -> PlatformConfig:
    """Build chrome config from the typed principal projection."""
    is_admin = bool(user and user.is_admin)
    return PlatformConfig(
        app_name=APP_NAME,
        brand_href="/",
        brand_htmx=False,
        navigation_label="Nawigacja",
        menu=build_menu(is_admin=is_admin),
        paths=PLATFORM_PATHS,
        enable_account=True,
        enable_credentials=True,
        enable_admin_users=True,
        enable_invite=True,
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
    user: PlatformUser | None,
    current_path: str = "",
    locale: str | None = None,
) -> dict[str, Any]:
    """Build product shell context from the typed principal projection."""
    resolved_locale = locale if locale in SUPPORTED_LOCALES else DEFAULT_LOCALE
    locale_path = current_path or "/"
    locales = tuple(
        PlatformLocale(
            code=code,
            label=label,
            href=f"{locale_path}?lang={code}",
        )
        for code, label in _LOCALE_LABELS
    )
    return {
        **build_platform_context(
            platform_config(user=user),
            user=user,
            current_path=current_path,
            locales=locales,
            locale=resolved_locale,
        ),
        "lang": resolved_locale,
    }


def login_platform_config() -> PlatformConfig:
    """Chrome for packaged my-auth login/register pages."""
    return PlatformConfig(
        app_name=APP_NAME,
        brand_href="/",
        brand_htmx=False,
        paths=PLATFORM_PATHS,
        enable_account=False,
        enable_credentials=False,
        enable_admin_users=False,
        enable_invite=False,
        show_register=True,
        locales=(
            PlatformLocale(code="pl", label="PL", href="/login?lang=pl"),
            PlatformLocale(code="en", label="EN", href="/login?lang=en"),
            PlatformLocale(code="de", label="DE", href="/login?lang=de"),
        ),
        default_locale=DEFAULT_LOCALE,
        htmx_nav=False,
    )
