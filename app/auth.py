from fastapi import Header, HTTPException
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import get_settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)


def create_token(user_id: int) -> str:
    settings = get_settings()
    return jwt.encode(
        {"user_id": user_id},
        settings.secret_key,
        algorithm=settings.algorithm,
    )


def get_current_user(authorization: str | None = Header(default=None)) -> int:
    if not authorization:
        raise HTTPException(status_code=401, detail="Token ausente")

    settings = get_settings()

    try:
        token = authorization.replace("Bearer ", "")
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.algorithm],
        )
        return int(payload["user_id"])
    except (JWTError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Token invalido") from exc
