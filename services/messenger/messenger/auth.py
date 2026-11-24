from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, WebSocket, status
from jwt import InvalidTokenError

from .config import get_settings
from .database import pool


@dataclass(frozen=True)
class Identity:
    username: str
    display_name: str
    roles: tuple[str, ...]
    token_version: int
    token_id: str


def decode_token(token: str) -> Identity:
    settings = get_settings()
    if not token or not settings.jwt_public_key:
        raise InvalidTokenError("Token verification is not configured")
    claims = jwt.decode(
        token,
        settings.jwt_public_key,
        algorithms=["EdDSA"],
        audience=settings.jwt_audience,
        issuer=settings.jwt_issuer,
        leeway=settings.jwt_clock_skew_seconds,
        options={"require": ["sub", "aud", "iss", "iat", "exp", "jti", "ver"]},
    )
    username = str(claims["sub"]).strip().lower()
    if not username:
        raise InvalidTokenError("Empty token subject")
    roles = tuple(sorted({str(role) for role in claims.get("roles", ["user"])}))
    return Identity(
        username=username,
        display_name=str(claims.get("name") or username),
        roles=roles,
        token_version=int(claims["ver"]),
        token_id=str(claims["jti"]),
    )


async def identity_is_current(identity: Identity) -> bool:
    async with pool().connection() as connection:
        async with connection.cursor() as cursor:
            await cursor.execute(
                """
                SELECT is_active, messenger_token_version
                FROM public.users
                WHERE username = %s
                """,
                (identity.username,),
            )
            row = await cursor.fetchone()
    return bool(
        row
        and row["is_active"]
        and int(row["messenger_token_version"]) == identity.token_version
    )


async def current_identity(request: Request) -> Identity:
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bearer token required")
    try:
        identity = decode_token(token)
    except InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid access token") from exc
    if not await identity_is_current(identity):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Access token revoked")
    return identity


CurrentIdentity = Annotated[Identity, Depends(current_identity)]


async def websocket_identity(websocket: WebSocket, token: str) -> Identity | None:
    try:
        identity = decode_token(token)
        if not await identity_is_current(identity):
            return None
        return identity
    except (InvalidTokenError, ValueError, TypeError):
        return None
