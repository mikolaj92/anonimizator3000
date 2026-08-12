"""Host hooks for my-usermanager install_usermanager_ui (account + admin)."""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from dataclasses import replace
from typing import Final

from app_factory.fastapi import AppFactoryUi
from fastapi import FastAPI, HTTPException, Request, status
from jinja2 import Environment
from my_usermanager.adapters.fastapi_htmx import (
    CapabilityOption,
    CsrfContext,
    ExternalIdentityRow,
    PermissionGrantRow,
    UserManagerUiConfig,
    UserRow,
    install_usermanager_ui,
    row_key_from_user_id,
)
from my_usermanager.adapters.my_auth import MY_AUTH_PROVIDER
from my_usermanager.adapters.my_auth_sqlite import SQLiteAuthDatabase
from my_usermanager.admin import AdminUserGrantSummary, GrantAdminService, UnsafeGrantMutationError
from my_usermanager.manager import UserManager, UserProfileUpdate
from my_usermanager.memory import MemoryRoleStore
from my_usermanager.models import Permission, ValidationError
from my_usermanager.permissions import ADMIN_ROLE_NAME, BUILTIN_ROLES
from my_usermanager.sessions import read_session_principal
from my_usermanager.subjects import AuthenticatedSubject

from anonimizator3000.passkey_setup import AuthDatabaseBinding, session_csrf_token

_SESSION_CSRF_KEY: Final = "csrf_token"


class SessionCsrfProtection:
    """Double-submit CSRF token stored in the signed session cookie."""

    def token(self, request: Request) -> str:
        token = session_csrf_token(request)
        if token is None:
            raise RuntimeError("session middleware is required for CSRF")
        return token

    def validate(self, request: Request, submitted_token: str) -> None:
        expected = request.session.get(_SESSION_CSRF_KEY)
        if (
            not isinstance(expected, str)
            or not expected
            or not isinstance(submitted_token, str)
            or not secrets.compare_digest(expected, submitted_token)
        ):
            raise PermissionError("invalid csrf token")



def summary_to_user_row(summary: AdminUserGrantSummary) -> UserRow:
    """Project an admin grant summary into the adapter's row contract."""
    user = summary.user
    roles = tuple(
        sorted(
            {
                grant.role_name
                for grant in summary.grants
                if grant.role_name is not None and grant.scope.is_global()
            }
        )
    )
    permissions = tuple(
        PermissionGrantRow(
            permission=grant.permission.name,
            label=grant.permission.name,
        )
        for grant in summary.grants
        if grant.permission is not None and grant.scope.is_global()
    )
    is_admin = ADMIN_ROLE_NAME in roles or any(
        permission.name == "admin.access" for permission in summary.projection.permissions
    )
    return UserRow(
        user_id=user.user_id,
        row_key=row_key_from_user_id(user.user_id),
        username=user.username or user.user_id,
        display_name=user.display_name,
        email=user.email,
        disabled=user.disabled,
        is_admin=is_admin,
        roles=roles,
        permissions=permissions,
        external_identities=tuple(
            ExternalIdentityRow(provider=identity.provider, subject=identity.subject)
            for identity in sorted(
                user.external_identities,
                key=lambda item: (item.provider, item.subject),
            )
        ),
    )


class _AuthDb:
    """Resolve the live SQLiteAuthDatabase from app state or constructor."""

    def __init__(
        self,
        database: SQLiteAuthDatabase | AuthDatabaseBinding | None = None,
    ) -> None:
        self._database = database

    def get(self, request: Request | None = None) -> SQLiteAuthDatabase:
        if request is not None:
            binding = getattr(request.app.state, "auth_database_binding", None)
            if isinstance(binding, AuthDatabaseBinding):
                return binding.current
            db = getattr(request.app.state, "auth_database", None)
            if isinstance(db, SQLiteAuthDatabase):
                return db
        if isinstance(self._database, AuthDatabaseBinding):
            return self._database.current
        if isinstance(self._database, SQLiteAuthDatabase):
            return self._database
        raise RuntimeError("auth database is not configured")


class AnonUserManagerHooks:
    """Host-owned policy and persistence for package account + admin users UI."""

    def __init__(
        self,
        database: SQLiteAuthDatabase | AuthDatabaseBinding | None = None,
    ) -> None:
        self._auth = _AuthDb(database)

    def get_current_user(self, request: Request) -> AuthenticatedSubject | None:
        if "session" not in request.scope:
            return None
        principal = read_session_principal(request.session)
        if principal is None:
            return None
        return AuthenticatedSubject(
            provider=MY_AUTH_PROVIDER,
            subject=principal.user_id,
            user_id=principal.user_id,
            username=principal.username,
            display_name=principal.display_name,
        )

    def require_admin(
        self, request: Request, current_user: AuthenticatedSubject
    ) -> None:
        if "session" not in request.scope:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="admin.access required",
            )
        principal = read_session_principal(request.session)
        if principal is not None:
            is_admin = ADMIN_ROLE_NAME in principal.roles or Permission(
                "admin.access"
            ) in principal.permissions
            if is_admin and principal.user_id == current_user.user_id:
                return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin.access required",
        )

    def list_users(
        self, request: Request, current_user: AuthenticatedSubject
    ) -> Sequence[UserRow]:
        del current_user
        stores = self._auth.get(request).stores()
        try:
            service = GrantAdminService(
                users=stores.users,
                roles=MemoryRoleStore(),
                grants=stores.grants,
            )
            summaries = service.list_users(limit=500, offset=0)
            return tuple(summary_to_user_row(s) for s in summaries)
        finally:
            stores.close()

    def role_options(
        self, request: Request, current_user: AuthenticatedSubject
    ) -> Sequence[str]:
        del request, current_user
        return tuple(sorted(BUILTIN_ROLES.keys()))

    def capability_options(
        self, request: Request, current_user: AuthenticatedSubject
    ) -> Sequence[CapabilityOption]:
        del request, current_user
        return ()

    def set_user_disabled(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
        user_id: str,
        disabled: bool,
    ) -> UserRow:
        del current_user
        stores = self._auth.get(request).stores()
        try:
            user = stores.users.get(user_id)
            if user is None:
                raise HTTPException(status_code=404, detail="user not found")
            updated = replace(user, disabled=disabled)
            stores.users.update(updated)
            service = GrantAdminService(
                users=stores.users,
                roles=MemoryRoleStore(),
                grants=stores.grants,
            )
            return summary_to_user_row(service.summary_for_user(updated))
        finally:
            stores.close()

    def grant_role(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
        user_id: str,
        role_name: str,
    ) -> UserRow:
        return self._mutate_role(
            request,
            current_user,
            user_id=user_id,
            role_name=role_name,
            action="grant",
        )

    def revoke_role(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
        user_id: str,
        role_name: str,
    ) -> UserRow:
        return self._mutate_role(
            request,
            current_user,
            user_id=user_id,
            role_name=role_name,
            action="revoke",
        )

    def grant_permission(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
        user_id: str,
        permission: PermissionGrantRow,
    ) -> UserRow:
        return self._mutate_permission(
            request,
            current_user,
            user_id=user_id,
            permission=permission,
            action="grant",
        )

    def revoke_permission(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
        user_id: str,
        permission: PermissionGrantRow,
    ) -> UserRow:
        return self._mutate_permission(
            request,
            current_user,
            user_id=user_id,
            permission=permission,
            action="revoke",
        )

    def csrf_context(self, request: Request) -> CsrfContext:
        token = SessionCsrfProtection().token(request)
        return CsrfContext(
            hidden_inputs=(("csrf", token),),
            headers={"X-CSRF-Token": token},
        )

    def after_user_disabled_changed(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
        row: UserRow,
    ) -> None:
        del request, current_user, row

    def render_passkey_panel(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
    ):
        del request, current_user
        return None

    def page_context(self, request: Request) -> dict[str, object]:
        """Product shell chrome for package account/admin pages."""
        from anonimizator3000.platform_chrome import (
            DEFAULT_LOCALE,
            LOCALE_COOKIE_NAME,
            platform_request_context,
        )

        session_user = None
        if "session" in request.scope:
            raw = request.session.get("user")
            if isinstance(raw, dict) and raw.get("id"):
                session_user = raw
        locale = request.query_params.get("lang") or request.cookies.get(
            LOCALE_COOKIE_NAME
        )
        context = platform_request_context(
            user=session_user,
            current_path=request.url.path,
            locale=locale or DEFAULT_LOCALE,
        )
        context["user"] = session_user
        context["page_title"] = (
            "Użytkownicy"
            if request.url.path.startswith("/admin/users")
            else "Konto"
        )
        return context

    def update_own_profile(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
        update: object,
    ) -> AuthenticatedSubject:
        if not isinstance(update, UserProfileUpdate):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid profile update",
            )
        stores = self._auth.get(request).stores()
        try:
            manager = UserManager(
                users=stores.users,
                roles=MemoryRoleStore(),
                grants=stores.grants,
            )
            try:
                stored = manager.update_own_profile(
                    actor_id=current_user.user_id,
                    update=update,
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(exc),
                ) from exc
            if "session" in request.scope:
                session_user = request.session.get("user")
                if isinstance(session_user, dict):
                    session_user["name"] = (
                        stored.display_name or stored.username or stored.user_id
                    )
                    request.session["user"] = session_user
            return AuthenticatedSubject(
                provider=MY_AUTH_PROVIDER,
                subject=stored.user_id,
                user_id=stored.user_id,
                username=stored.username,
                first_name=stored.first_name,
                last_name=stored.last_name,
                display_name=stored.display_name,
                email=stored.email,
                birth_date=stored.birth_date,
                gender=stored.gender,
            )
        finally:
            stores.close()

    def _mutate_role(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
        *,
        user_id: str,
        role_name: str,
        action: str,
    ) -> UserRow:
        stores = self._auth.get(request).stores()
        try:
            service = GrantAdminService(
                users=stores.users,
                roles=MemoryRoleStore(),
                grants=stores.grants,
            )
            try:
                if action == "grant":
                    service.grant_role(
                        actor_id=current_user.user_id,
                        target_user_id=user_id,
                        role_name=role_name,
                    )
                else:
                    service.revoke_role(
                        actor_id=current_user.user_id,
                        target_user_id=user_id,
                        role_name=role_name,
                    )
            except (ValueError, ValidationError, UnsafeGrantMutationError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(exc),
                ) from exc
            user = stores.users.get(user_id)
            if user is None:
                raise HTTPException(status_code=404, detail="user not found")
            return summary_to_user_row(service.summary_for_user(user))
        finally:
            stores.close()

    def _mutate_permission(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
        *,
        user_id: str,
        permission: PermissionGrantRow,
        action: str,
    ) -> UserRow:
        stores = self._auth.get(request).stores()
        try:
            service = GrantAdminService(
                users=stores.users,
                roles=MemoryRoleStore(),
                grants=stores.grants,
            )
            perm = Permission(name=permission.permission)
            try:
                if action == "grant":
                    service.grant_permission(
                        actor_id=current_user.user_id,
                        target_user_id=user_id,
                        permission=perm,
                    )
                else:
                    service.revoke_permission(
                        actor_id=current_user.user_id,
                        target_user_id=user_id,
                        permission=perm,
                    )
            except (ValueError, ValidationError, UnsafeGrantMutationError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(exc),
                ) from exc
            user = stores.users.get(user_id)
            if user is None:
                raise HTTPException(status_code=404, detail="user not found")
            return summary_to_user_row(service.summary_for_user(user))
        finally:
            stores.close()


def install_anon_usermanager_ui(
    app: FastAPI,
    *,
    platform: AppFactoryUi,
    environment: Environment,
    database: SQLiteAuthDatabase | AuthDatabaseBinding | None = None,
) -> None:
    """Mount package-owned account/profile and admin users surfaces."""
    hooks = AnonUserManagerHooks(database)
    install_usermanager_ui(
        app,
        platform=platform,
        hooks=hooks,
        environment=environment,
        config=UserManagerUiConfig(
            account_path="/account",
            profile_path="/account/profile",
            users_path="/admin/users",
            login_url="/login",
            logout_path="/logout",
            account_enabled=True,
            admin_enabled=True,
            csrf_protection=SessionCsrfProtection(),
            base_template="base.html",
        ),
    )
