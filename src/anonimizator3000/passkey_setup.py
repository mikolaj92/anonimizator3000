"""Passkey auth wiring (my-auth) on shared SQLiteAuthDatabase."""

from __future__ import annotations

import logging
import os
import secrets
import uuid
from contextlib import suppress
from typing import Any

from app_factory.fastapi import AppFactoryUi
from app_factory.platform import apply_platform_context
from fastapi import FastAPI, HTTPException, Request
from my_auth import PasskeyConfig, PasskeyService, SQLiteChallengeStore, SQLiteCredentialStore
from my_auth.fastapi import PasskeyCookies, PasskeyRouteHooks
from my_auth.fastapi_htmx import PasskeyUi, PasskeyUiConfig, install_passkey_ui
from my_auth.passkeys import PasskeyUser, VerifiedRegistration
from my_usermanager.adapters.my_auth import MY_AUTH_PROVIDER
from my_usermanager.adapters.my_auth_sqlite import SQLiteAuthDatabase
from my_usermanager.models import (
    ExternalIdentity,
    Grant,
    Scope,
    User,
    ValidationError,
    validate_identifier,
)
from my_usermanager.permissions import ADMIN_ROLE_NAME
from my_usermanager.sessions import SessionPrincipal, write_session_principal
from my_usermanager.stores import DuplicateGrantError, DuplicateUserError, UserQuery

from anonimizator3000.config import Settings
from anonimizator3000.platform_chrome import (
    DEFAULT_LOCALE,
    LOCALE_COOKIE_NAME,
    SUPPORTED_LOCALES,
    login_platform_config,
)

logger = logging.getLogger(__name__)

_SESSION_CSRF_KEY = "csrf_token"


class AuthDatabaseBinding:
    """Mutable auth database binding shared by routes across app lifespans."""

    def __init__(self, database: SQLiteAuthDatabase) -> None:
        self.current = database

    def update(self, database: SQLiteAuthDatabase) -> None:
        self.current = database

    @property
    def database(self):
        return self.current.database

    def __getattr__(self, name: str):
        return getattr(self.current, name)


class _OperationStoreProxy:
    """Run each store call on fresh, promptly closed SQLite connections."""

    def __init__(self, database: AuthDatabaseBinding, store_name: str) -> None:
        self._database = database
        self._store_name = store_name

    def __getattr__(self, method_name: str):
        def call(*args, **kwargs):
            stores = self._database.current.stores()
            try:
                store = getattr(stores, self._store_name)
                return getattr(store, method_name)(*args, **kwargs)
            finally:
                stores.close()

        return call


class _SQLiteStoreProxy:
    """Resolve a my-auth SQLite store against the database active this lifespan."""

    def __init__(self, database: AuthDatabaseBinding, store_type: type) -> None:
        self._database = database
        self._store_type = store_type

    def __getattr__(self, method_name: str):
        def call(*args, **kwargs):
            store = self._store_type(self._database.current.database)
            return getattr(store, method_name)(*args, **kwargs)

        return call


def _require_username(raw: str) -> str:
    try:
        return validate_identifier(raw.strip(), field_name="username")
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _passkey_config(settings: Settings) -> PasskeyConfig:
    return PasskeyConfig(
        rp_id=settings.passkey_rp_id,
        rp_name=settings.passkey_rp_name,
        origin=settings.passkey_origin,
    )


def _compute_session(um_user: User, grants: tuple[Grant, ...]) -> dict[str, Any]:
    roles = {grant.role_name for grant in grants if grant.role_name}
    return {
        "id": um_user.user_id,
        "name": um_user.display_name or um_user.username or um_user.user_id,
        "is_admin": ADMIN_ROLE_NAME in roles,
    }


def session_csrf_token(request: Request) -> str | None:
    """Return or mint a double-submit CSRF token in the signed session."""
    if "session" not in request.scope:
        return None
    existing = request.session.get(_SESSION_CSRF_KEY)
    if isinstance(existing, str) and existing:
        return existing
    token = secrets.token_urlsafe(32)
    request.session[_SESSION_CSRF_KEY] = token
    return token


def _complete_registration(
    auth_database: SQLiteAuthDatabase | AuthDatabaseBinding,
    result: VerifiedRegistration,
) -> User:
    """Persist a verified registration; first user becomes admin."""
    database = (
        auth_database.current
        if isinstance(auth_database, AuthDatabaseBinding)
        else auth_database
    )
    username = _require_username(result.user.name)
    user = User(
        user_id=result.user.user_id,
        username=username,
        display_name=result.user.display_name or username,
    )
    identity = ExternalIdentity(provider=MY_AUTH_PROVIDER, subject=user.user_id)
    with database.transaction() as tx:
        first_user = not tx.users.list(limit=1, offset=0, query=UserQuery())
        credential_store = tx.external_store(SQLiteCredentialStore)
        credential_store.save_registration(result)
        try:
            tx.users.create(user)
        except DuplicateUserError:
            if tx.users.get(user.user_id) is None:
                raise
        tx.users.link_external_identity(user_id=user.user_id, identity=identity)
        if first_user:
            with suppress(DuplicateGrantError):
                tx.grants.add_role_grant(user.user_id, ADMIN_ROLE_NAME, Scope.global_())
        completed = tx.users.get(user.user_id)
        if completed is None:
            raise RuntimeError("registration user missing after commit")
        return completed


def build_passkey_components(
    auth_database: SQLiteAuthDatabase,
    settings: Settings,
    database_binding: AuthDatabaseBinding | None = None,
) -> tuple[PasskeyService, PasskeyRouteHooks, AuthDatabaseBinding]:
    """Build shared service/hooks for packaged passkey HTML routes."""
    binding = database_binding or AuthDatabaseBinding(auth_database)
    user_store = _OperationStoreProxy(binding, "users")
    grant_store = _OperationStoreProxy(binding, "grants")
    credential_store = _SQLiteStoreProxy(binding, SQLiteCredentialStore)
    challenge_store = _SQLiteStoreProxy(binding, SQLiteChallengeStore)
    service = PasskeyService(
        config=_passkey_config(settings),
        challenges=challenge_store,
        credentials=credential_store,
    )

    def prepare_registration(_request: Request, username: str) -> PasskeyUser:
        handle = _require_username(username)
        if user_store.get_by_username(handle) is not None:
            raise HTTPException(status_code=400, detail="username is already taken")
        return PasskeyUser(
            user_id=uuid.uuid4().hex,
            user_handle=uuid.uuid4().bytes,
            name=handle,
            display_name=handle,
        )

    def complete_registration(
        _request: Request, result: VerifiedRegistration
    ) -> PasskeyUser | None:
        user = _complete_registration(binding, result)
        return credential_store.get_user(user.user_id)

    def get_auth_user(user_id: str) -> PasskeyUser | None:
        identity = ExternalIdentity(provider=MY_AUTH_PROVIDER, subject=user_id)
        user = user_store.resolve_external_identity(identity)
        if user is None or user.disabled or user.user_id != user_id:
            return None
        return credential_store.get_user(user_id)

    def principal_for(user: User) -> SessionPrincipal:
        grants = grant_store.list_grants_for_user(user.user_id)
        return SessionPrincipal(
            user_id=user.user_id,
            username=user.username,
            display_name=user.display_name,
            roles=frozenset(g.role_name for g in grants if g.role_name),
            permissions=frozenset(g.permission for g in grants if g.permission),
        )

    def login(_response, request: Request, auth_user: PasskeyUser) -> None:
        user = user_store.get(auth_user.user_id)
        if user is None:
            return
        write_session_principal(request.session, principal_for(user))
        grants = grant_store.list_grants_for_user(user.user_id)
        request.session["user"] = _compute_session(user, grants)
        _ = session_csrf_token(request)

    def logout(_response, request: Request) -> None:
        request.session.clear()

    def get_session_user(request: Request) -> PasskeyUser | None:
        if "session" not in request.scope:
            return None
        session_user = request.session.get("user")
        user_id = session_user.get("id") if isinstance(session_user, dict) else None
        return get_auth_user(user_id) if isinstance(user_id, str) else None

    def registration_allowed(request: Request) -> bool:
        if "session" not in request.scope:
            return True
        session_user = request.session.get("user")
        user_id = session_user.get("id") if isinstance(session_user, dict) else None
        return not isinstance(user_id, str) or get_auth_user(user_id) is not None

    def render_login(request: Request):
        del request
        raise RuntimeError("interactive rendering is owned by my-auth.fastapi_htmx")

    def render_register(request: Request, *, bootstrap: bool):
        del request, bootstrap
        raise RuntimeError("interactive rendering is owned by my-auth.fastapi_htmx")

    hooks = PasskeyRouteHooks(
        get_session_user=get_session_user,
        prepare_registration=prepare_registration,
        complete_registration=complete_registration,
        get_auth_user=get_auth_user,
        login=login,
        logout=logout,
        registration_allowed=registration_allowed,
        render_login=render_login,
        render_register=render_register,
    )
    return service, hooks, binding


def install_passkey_routes(
    app: FastAPI,
    *,
    platform: AppFactoryUi,
    auth_database: SQLiteAuthDatabase,
    settings: Settings,
) -> tuple[PasskeyUi, AuthDatabaseBinding]:
    """Install packaged passkey pages while retaining host-owned hooks."""
    service, hooks, binding = build_passkey_components(auth_database, settings)
    passkey_ui = install_passkey_ui(
        app,
        platform=platform,
        service=service,
        hooks=hooks,
        config=PasskeyUiConfig(
            cookies=PasskeyCookies(
                secure=settings.session_cookie_secure,
                samesite="lax",
            ),
            csrf_token=session_csrf_token,
            login_success_url="/",
            register_success_url="/",
            locale_cookie_name=LOCALE_COOKIE_NAME,
            locale_query_param="lang",
            supported_locales=SUPPORTED_LOCALES,
            default_locale=DEFAULT_LOCALE,
        ),
    )
    if getattr(passkey_ui, "environment", None) is not None:
        apply_platform_context(passkey_ui.environment, login_platform_config())
    app.state.auth_database_binding = binding
    return passkey_ui, binding


def bootstrap_admin(auth_database: SQLiteAuthDatabase) -> None:
    """Grant ADMIN_ROLE_NAME to BOOTSTRAP_ADMIN_ID when configured (idempotent)."""
    admin_id = os.getenv("BOOTSTRAP_ADMIN_ID") or os.getenv("ANON_BOOTSTRAP_ADMIN_ID")
    if not admin_id:
        return
    try:
        with auth_database.transaction() as tx:
            tx.grants.add_role_grant(admin_id, ADMIN_ROLE_NAME, Scope.global_())
        logger.info("Bootstrapped admin role for user %s", admin_id)
    except DuplicateGrantError:
        logger.debug("Admin role already granted to %s", admin_id)
    except Exception:
        logger.exception("Failed to bootstrap admin for %s", admin_id)
