"""Authentication endpoints (PRODUCT.md §47-48).

POST /auth/register · POST /auth/login · GET /auth/me
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.core.rate_limiter import auth_limit
from app.core.security import (
    CurrentUser,
    DBSession,
    create_access_token,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.schemas.inputs import LoginIn, RegisterIn
from app.schemas.outputs import user_out

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", status_code=201, dependencies=[Depends(auth_limit)])
def register(body: RegisterIn, db: DBSession):
    existing = db.query(User).filter(User.email == body.email).first()
    if existing:
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    user = User(
        email=body.email,
        name=body.name.strip(),
        phone=body.phone,
        password_hash=hash_password(body.password),
        role="USER",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_access_token(user.id, user.role)
    return {"access_token": token, "token_type": "bearer", "user": user_out(user)}


@router.post("/login", dependencies=[Depends(auth_limit)])
def login(body: LoginIn, db: DBSession):
    user = db.query(User).filter(User.email == body.email.strip().lower()).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = create_access_token(user.id, user.role)
    return {"access_token": token, "token_type": "bearer", "user": user_out(user)}


@router.get("/me")
def me(user: CurrentUser):
    return user_out(user)
