"""Authentication helpers for WebUI user JWTs."""

from dataclasses import dataclass

import jwt
from fastapi import Request
from jwt import ExpiredSignatureError, InvalidTokenError

from .config import JWT_AUDIENCE, JWT_ISSUER, JWT_LEEWAY_SECONDS, JWT_SECRET


REQUIRED_CLAIMS = ("workid", "cnname", "depart", "username", "role")


class JwtAuthenticationError(Exception):
    """Raised when a WebUI JWT cannot be trusted."""


@dataclass(frozen=True)
class CurrentUser:
    workid: str
    cnname: str
    depart: str
    username: str
    role: str


def validate_jwt_configuration() -> None:
    if len(JWT_SECRET.encode("utf-8")) < 32:
        raise RuntimeError("JWT_SECRET must contain at least 32 UTF-8 bytes")
    if not JWT_ISSUER:
        raise RuntimeError("JWT_ISSUER is required")
    if not JWT_AUDIENCE:
        raise RuntimeError("JWT_AUDIENCE is required")


def authenticate_authorization_header(authorization: str | None) -> CurrentUser:
    if not authorization:
        raise JwtAuthenticationError("Authorization header is required")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise JwtAuthenticationError("Authorization must use a Bearer token")

    try:
        claims = jwt.decode(
            token.strip(),
            JWT_SECRET,
            algorithms=["HS256"],
            issuer=JWT_ISSUER,
            audience=JWT_AUDIENCE,
            options={"require": list(REQUIRED_CLAIMS)},
            leeway=JWT_LEEWAY_SECONDS,
        )
    except ExpiredSignatureError as exc:
        raise JwtAuthenticationError("JWT has expired") from exc
    except InvalidTokenError as exc:
        raise JwtAuthenticationError("JWT validation failed") from exc

    values = {claim: str(claims.get(claim, "")).strip() for claim in REQUIRED_CLAIMS}
    if any(not value for value in values.values()):
        raise JwtAuthenticationError("JWT is missing required user claims")

    return CurrentUser(**values)


def get_current_user(request: Request) -> CurrentUser:
    user = getattr(request.state, "user", None)
    if not isinstance(user, CurrentUser):
        raise JwtAuthenticationError("Request has not been authenticated")
    return user
