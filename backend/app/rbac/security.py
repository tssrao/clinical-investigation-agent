"""JWT issuing/verification (design doc 2.2: "JWT -> role resolution -> row-
level policy injection"). This is the *first* link in that chain - decoding a
token into a role + patient scope the rest of the RBAC layer (policy.py) then
enforces at the database level. This module makes no authorization decisions
itself, just identity/claims extraction.
"""

from datetime import datetime, timedelta, timezone
from typing import Literal

from jose import JWTError, jwt

from app.core.config import settings

Role = Literal["doctor", "insurance_adjuster"]
VALID_ROLES = ("doctor", "insurance_adjuster")


class InvalidTokenError(Exception):
    pass


def create_access_token(role: Role, patient_scope: list[str], expires_minutes: int = 60) -> str:
    if role not in VALID_ROLES:
        raise ValueError(f"unknown role: {role!r}, must be one of {VALID_ROLES}")
    now = datetime.now(timezone.utc)
    claims = {
        "role": role,
        "patient_scope": patient_scope,
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
    }
    return jwt.encode(claims, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError as e:
        raise InvalidTokenError(str(e)) from e

    if payload.get("role") not in VALID_ROLES:
        raise InvalidTokenError(f"unknown or missing role in token: {payload.get('role')!r}")
    if not isinstance(payload.get("patient_scope"), list):
        raise InvalidTokenError("token missing patient_scope claim")

    return payload
