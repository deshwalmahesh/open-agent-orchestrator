"""fastapi-users JWT auth wiring."""
from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Annotated

import structlog
from fastapi import Depends, Request
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin, schemas
from fastapi_users.authentication import AuthenticationBackend, BearerTransport, JWTStrategy
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_async_session
from app.db.models import UserDB

log = structlog.get_logger()


class UserRead(schemas.BaseUser[uuid.UUID]):
    name: str = ""
    slack_user_id: str | None = None


class UserCreate(schemas.BaseUserCreate):
    name: str = ""


class UserUpdate(schemas.BaseUserUpdate):
    name: str | None = None
    slack_user_id: str | None = None


async def get_user_db(
    session: Annotated[AsyncSession, Depends(get_async_session)],
) -> AsyncGenerator[SQLAlchemyUserDatabase, None]:
    yield SQLAlchemyUserDatabase(session, UserDB)


class UserManager(UUIDIDMixin, BaseUserManager[UserDB, uuid.UUID]):
    @property
    def reset_password_token_secret(self) -> str:
        return get_settings().jwt_secret

    @property
    def verification_token_secret(self) -> str:
        return get_settings().jwt_secret

    async def on_after_register(self, user: UserDB, request: Request | None = None) -> None:
        log.info("user.registered", user_id=str(user.id), email=user.email)


async def get_user_manager(
    user_db: Annotated[SQLAlchemyUserDatabase, Depends(get_user_db)],
) -> AsyncGenerator[UserManager, None]:
    yield UserManager(user_db)


bearer_transport = BearerTransport(tokenUrl="auth/jwt/login")


def get_jwt_strategy() -> JWTStrategy:
    s = get_settings()
    return JWTStrategy(secret=s.jwt_secret, lifetime_seconds=s.jwt_lifetime_seconds)


auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)

fastapi_users = FastAPIUsers[UserDB, uuid.UUID](get_user_manager, [auth_backend])

# Use as a FastAPI dependency to require an authenticated user in any route.
current_active_user = fastapi_users.current_user(active=True)
