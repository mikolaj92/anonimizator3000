"""Host hooks for my-usermanager install_usermanager_ui (account + admin)."""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Sequence
from dataclasses import replace
from typing import Final

from app_factory.fastapi import AppFactoryUi
from app_factory.platform import IDENTITY_AUTHENTICATED_SHELL
from fastapi import FastAPI, HTTPException, Request, status
from jinja2 import Environment
from my_auth import SQLiteEnrollmentCapabilityStore
from my_usermanager.adapters.fastapi_htmx import (
    CapabilityOption,
    CsrfContext,
    ExternalIdentityRow,
    InvitationResult,
    InvitationRow,
    PermissionGrantRow,
    SessionCsrfProtection,
    UserManagerUiConfig,
    UserRow,
    install_usermanager_ui,
    row_key_from_user_id,
)
from my_usermanager.adapters.my_auth import MY_AUTH_PROVIDER
from my_usermanager.adapters.my_auth_enrollment import build_enrollment_capability_issuer
from my_usermanager.adapters.my_auth_sqlite import SQLiteAuthDatabase
from my_usermanager.adapters.sqlite_invitations import SQLiteInvitationStore
from my_usermanager.admin import AdminUserGrantSummary, GrantAdminService, UnsafeGrantMutationError
from my_usermanager.invitations import InvitationError, InvitationGrant, InvitationService
from my_usermanager.manager import AuthorizationError, UserManager, UserProfileUpdate
from my_usermanager.memory import MemoryAuditStore, MemoryRoleStore
from my_usermanager.models import Permission, User, ValidationError
from my_usermanager.permissions import ADMIN_ROLE_NAME, BUILTIN_ROLES
from my_usermanager.sessions import (
    SessionPrincipal,
    read_session_principal,
    write_session_principal,
)
from my_usermanager.subjects import AuthenticatedSubject

from anonimizator3000.passkey_setup import AuthDatabaseBinding
from anonimizator3000.platform_chrome import PLATFORM_PATHS

_INVITE_TTL_SECONDS: Final = 7 * 24 * 60 * 60

_SESSION_CSRF_KEY: Final = "csrf_token"


def summary_to_user_row(
    summary: AdminUserGrantSummary,
    *,
    invitation: InvitationRow | None = None,
) -> UserRow:
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
        account_status=user.status,
        invitation=invitation,
    )


def _invitation_row(invitation) -> InvitationRow:
    return InvitationRow(
        invitation_id=invitation.invitation_id,
        status=invitation.status,
        expires_at=invitation.expires_at.isoformat(),
    )


def _invitation_connection(database: SQLiteAuthDatabase) -> sqlite3.Connection:
    if isinstance(database.database, sqlite3.Connection):
        return database.database
    connection = sqlite3.connect(
        database.database, timeout=30, check_same_thread=False
    )
    _ = connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _invitation_service(
    database: SQLiteAuthDatabase,
    stores,
    invitations: SQLiteInvitationStore,
) -> InvitationService:
    return InvitationService(
        manager=UserManager(
            users=stores.users,
            roles=MemoryRoleStore(),
            grants=stores.grants,
        ),
        users=stores.users,
        identities=stores.users,
        invitations=invitations,
        enrollment=build_enrollment_capability_issuer(
            SQLiteEnrollmentCapabilityStore(database.database)
        ),
        audit=MemoryAuditStore(),
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
        database = self._auth.get(request)
        stores = database.stores()
        connection = _invitation_connection(database)
        try:
            invitations = SQLiteInvitationStore(connection)
            service = GrantAdminService(
                users=stores.users,
                roles=MemoryRoleStore(),
                grants=stores.grants,
            )
            summaries = service.list_users(limit=500, offset=0)
            rows = []
            for summary in summaries:
                pending = invitations.get_pending_for_user(summary.user.user_id)
                rows.append(
                    summary_to_user_row(
                        summary,
                        invitation=None if pending is None else _invitation_row(pending),
                    )
                )
            return tuple(rows)
        finally:
            stores.close()
            if connection is not database.database:
                connection.close()

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
        token = SessionCsrfProtection(session_key=_SESSION_CSRF_KEY).token(request)
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

    def invite_user(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
        username: str,
        email: str,
        role: str,
    ) -> InvitationResult:
        database = self._auth.get(request)
        stores = database.stores()
        connection = _invitation_connection(database)
        try:
            invited = User(
                user_id=uuid.uuid4().hex,
                username=username,
                email=email or None,
                display_name=username,
                status="pending",
            )
            service = _invitation_service(
                database, stores, SQLiteInvitationStore(connection)
            )
            issued = service.invite(
                actor_id=current_user.user_id,
                user=invited,
                grants=(InvitationGrant(role_name=role),),
                ttl_seconds=_INVITE_TTL_SECONDS,
            )
            return InvitationResult(
                activation_url=issued.activation_url(PLATFORM_PATHS.activation)
            )
        except InvitationError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="invitation is unavailable",
            ) from exc
        except (AuthorizationError, ValidationError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        finally:
            stores.close()
            if connection is not database.database:
                connection.close()

    def reissue_invitation(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
        invitation_id: str,
    ) -> InvitationResult:
        database = self._auth.get(request)
        stores = database.stores()
        connection = _invitation_connection(database)
        try:
            service = _invitation_service(
                database, stores, SQLiteInvitationStore(connection)
            )
            issued = service.reissue(
                actor_id=current_user.user_id,
                invitation_id=invitation_id,
                ttl_seconds=_INVITE_TTL_SECONDS,
            )
            return InvitationResult(
                activation_url=issued.activation_url(PLATFORM_PATHS.activation)
            )
        except InvitationError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="invitation is unavailable",
            ) from exc
        except (AuthorizationError, ValidationError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        finally:
            stores.close()
            if connection is not database.database:
                connection.close()

    def revoke_invitation(
        self,
        request: Request,
        current_user: AuthenticatedSubject,
        invitation_id: str,
    ) -> UserRow:
        database = self._auth.get(request)
        stores = database.stores()
        connection = _invitation_connection(database)
        try:
            invitations = SQLiteInvitationStore(connection)
            service = _invitation_service(database, stores, invitations)
            revoked = service.revoke(
                actor_id=current_user.user_id,
                invitation_id=invitation_id,
            )
            user = stores.users.get(revoked.user_id)
            if user is None:
                raise HTTPException(status_code=404, detail="user not found")
            grants = GrantAdminService(
                users=stores.users,
                roles=MemoryRoleStore(),
                grants=stores.grants,
            )
            return summary_to_user_row(
                grants.summary_for_user(user),
                invitation=_invitation_row(revoked),
            )
        except InvitationError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="invitation is unavailable",
            ) from exc
        except (AuthorizationError, ValidationError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        finally:
            stores.close()
            if connection is not database.database:
                connection.close()

    def page_context(self, request: Request) -> dict[str, object]:
        """Product shell chrome for package account/admin pages."""
        from anonimizator3000.platform_chrome import (
            DEFAULT_LOCALE,
            LOCALE_COOKIE_NAME,
            platform_request_context,
        )

        session_user = None
        if "session" in request.scope:
            from anonimizator3000.platform_chrome import platform_user_from_principal

            session_user = platform_user_from_principal(
                read_session_principal(request.session)
            )
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
                principal = read_session_principal(request.session)
                if principal is not None:
                    write_session_principal(
                        request.session,
                        SessionPrincipal(
                            user_id=principal.user_id,
                            username=stored.username,
                            display_name=stored.display_name,
                            roles=principal.roles,
                            permissions=principal.permissions,
                        ),
                    )
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
            account_path=PLATFORM_PATHS.account,
            profile_path="/account/profile",
            users_path=PLATFORM_PATHS.admin_users,
            invite_path="/admin/users/invite",
            login_url=PLATFORM_PATHS.login,
            logout_path=PLATFORM_PATHS.logout,
            account_enabled=True,
            admin_enabled=True,
            csrf_protection=SessionCsrfProtection(session_key=_SESSION_CSRF_KEY),
            base_template=IDENTITY_AUTHENTICATED_SHELL,
        ),
    )
