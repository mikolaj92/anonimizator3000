"""Passkey auth wiring (my-auth) on shared SQLiteAuthDatabase."""

from __future__ import annotations

import logging
import os
import secrets
import sqlite3
import uuid
from contextlib import suppress
from typing import Any, Literal

from app_factory.fastapi import AppFactoryUi
from app_factory.platform import apply_platform_context
from fastapi import FastAPI, HTTPException, Request
from my_auth import (
    EnrollmentCapabilityNotFound,
    PasskeyConfig,
    PasskeyService,
    SQLiteChallengeStore,
    SQLiteCredentialStore,
    SQLiteEnrollmentCapabilityStore,
    registration_context_from_capability,
)
from my_auth.fastapi import PasskeyCookies, PasskeyRouteHooks
from my_auth.fastapi_htmx import PasskeyUi, PasskeyUiConfig, install_passkey_ui
from my_auth.passkeys import PasskeyUser, RegistrationContext, VerifiedRegistration
from my_usermanager.adapters.my_auth import MY_AUTH_PROVIDER
from my_usermanager.adapters.my_auth_sqlite import SQLiteAuthDatabase
from my_usermanager.adapters.sqlite_invitations import SQLiteInvitationStore
from my_usermanager.invitations import InvitationActivation, InvitationError, InvitationService
from my_usermanager.manager import UserManager
from my_usermanager.memory import MemoryAuditStore, MemoryRoleStore
from my_usermanager.models import (
    ExternalIdentity,
    Grant,
    Scope,
    User,
    ValidationError,
    validate_identifier,
)
from my_usermanager.permissions import ADMIN_ROLE_NAME
from my_usermanager.sessions import (
    SessionPrincipal,
    read_session_principal,
    write_session_principal,
)
from my_usermanager.stores import DuplicateGrantError, DuplicateUserError, UserQuery

from anonimizator3000.config import Settings
from anonimizator3000.platform_chrome import (
    DEFAULT_LOCALE,
    LOCALE_COOKIE_NAME,
    PASSKEY_PATHS,
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


def _resolved_database(
    auth_database: SQLiteAuthDatabase | AuthDatabaseBinding,
) -> SQLiteAuthDatabase:
    if isinstance(auth_database, AuthDatabaseBinding):
        return auth_database.current
    return auth_database


def _passkey_user_from_account(user: User) -> PasskeyUser:
    return PasskeyUser(
        user_id=user.user_id,
        user_handle=uuid.uuid4().bytes,
        name=user.username,
        display_name=user.display_name or user.username,
    )


def _activate_invited_user(
    database: SQLiteAuthDatabase, result: VerifiedRegistration
) -> User:
    """Link the verified passkey to the pending invited account."""
    identity = ExternalIdentity(provider=MY_AUTH_PROVIDER, subject=result.user.user_id)
    conn = sqlite3.connect(database.database, timeout=30, check_same_thread=False)
    try:
        invitations = SQLiteInvitationStore(conn)
        pending = invitations.get_pending_for_user(result.user.user_id)
        if pending is None:
            raise InvitationError
        stores = database.stores()
        try:
            service = InvitationService(
                manager=UserManager(
                    users=stores.users,
                    roles=MemoryRoleStore(),
                    grants=stores.grants,
                ),
                users=stores.users,
                identities=stores.users,
                invitations=invitations,
                enrollment=_enrollment_issuer(database),
                audit=MemoryAuditStore(),
            )
            return service.activate(
                InvitationActivation(
                    invitation_id=pending.invitation_id,
                    capability_id=pending.capability_id,
                    identity=identity,
                )
            )
        finally:
            stores.close()
    finally:
        conn.close()


def _enrollment_issuer(database: SQLiteAuthDatabase):
    from my_usermanager.adapters.my_auth_enrollment import (
        build_enrollment_capability_issuer,
    )

    return build_enrollment_capability_issuer(
        SQLiteEnrollmentCapabilityStore(database.database)
    )


def _complete_registration(
    auth_database: SQLiteAuthDatabase | AuthDatabaseBinding,
    result: VerifiedRegistration,
) -> User:
    """Persist a verified registration; first user becomes admin."""
    database = _resolved_database(auth_database)
    kind = result.context.kind if result.context is not None else "bootstrap"
    if kind == "invitation":
        with database.transaction() as tx:
            tx.external_store(SQLiteCredentialStore).save_registration(result)
        return _activate_invited_user(database, result)
    if kind == "recovery":
        with database.transaction() as tx:
            tx.external_store(SQLiteCredentialStore).save_registration(result)
            completed = tx.users.get(result.user.user_id)
            if completed is None or not completed.is_active:
                raise RuntimeError("recovery user missing after commit")
            return completed
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
        request: Request, result: VerifiedRegistration
    ) -> PasskeyUser | None:
        user = _complete_registration(binding, result)
        if result.context is not None and result.context.kind in {
            "invitation",
            "recovery",
        }:
            flow_id = request.cookies.get(PasskeyCookies().registration_challenge)
            if flow_id:
                try:
                    SQLiteEnrollmentCapabilityStore(binding.current.database).consume(
                        flow_id=flow_id
                    )
                except Exception:
                    logger.exception("failed to consume enrollment capability")
        return credential_store.get_user(user.user_id)

    def get_auth_user(user_id: str) -> PasskeyUser | None:
        identity = ExternalIdentity(provider=MY_AUTH_PROVIDER, subject=user_id)
        user = user_store.resolve_external_identity(identity)
        if user is None or not user.is_active or user.user_id != user_id:
            return None
        return credential_store.get_user(user_id)

    def prepare_capability_registration_context(
        _request: Request,
        flow_id: str,
        kind: Literal["invitation", "recovery"],
        capability: str,
    ) -> RegistrationContext:
        store = SQLiteEnrollmentCapabilityStore(binding.current.database)
        purpose = "invitation" if kind == "invitation" else "account_recovery"
        record = store.claim(
            token=capability, flow_id=flow_id, expected_purpose=purpose
        )
        if kind == "invitation":
            invited = user_store.get(record.subject)
            if invited is None or invited.status != "pending":
                raise EnrollmentCapabilityNotFound(
                    "enrollment capability is unavailable"
                )
            passkey_user = _passkey_user_from_account(invited)
        else:
            passkey_user = credential_store.get_user(record.subject)
            recovered = user_store.get(record.subject)
            if (
                passkey_user is None
                or recovered is None
                or not recovered.is_active
            ):
                raise EnrollmentCapabilityNotFound(
                    "enrollment capability is unavailable"
                )
        return registration_context_from_capability(
            kind=kind,
            user=passkey_user,
            capability_id=record.capability_id,
            capability_subject=record.subject,
            capability_purpose=record.purpose,
        )

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
        principal = read_session_principal(request.session)
        return get_auth_user(principal.user_id) if principal is not None else None

    def registration_allowed(request: Request) -> bool:
        if "session" not in request.scope:
            return True
        principal = read_session_principal(request.session)
        return principal is None or get_auth_user(principal.user_id) is not None

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
        prepare_capability_registration_context=prepare_capability_registration_context,
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
            paths=PASSKEY_PATHS,
            cookies=PasskeyCookies(
                secure=settings.session_cookie_secure,
                samesite="lax",
            ),
            csrf_token=session_csrf_token,
            login_success_url="/",
            register_success_url="/",
            activation_success_url="/account",
            recovery_success_url="/login",
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
