# packages/backend/app/api/v1/endpoints/auth.py
from fastapi import APIRouter, HTTPException, Depends, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from datetime import timedelta
from typing import Optional
import uuid

from app.core.security import (
    verify_password, get_password_hash, create_access_token,
    ACCESS_TOKEN_EXPIRE_MINUTES
)

router = APIRouter()

# In-memory user store (replace with database later)
users_db = {}
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")

@router.post("/register")
async def register(user_data: dict):
    """Register a new user"""
    username = user_data.get("username")
    if username in users_db:
        raise HTTPException(status_code=400, detail="Username already exists")
    
    user_id = str(uuid.uuid4())
    user = {
        "id": user_id,
        "username": username,
        "email": user_data.get("email"),
        "full_name": user_data.get("full_name"),
        "hashed_password": get_password_hash(user_data.get("password")),
        "role": user_data.get("role", "user"),
        "team": user_data.get("team"),
        "is_active": True
    }
    users_db[username] = user
    return {"message": "User created successfully", "user_id": user_id}

@router.post("/token")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    """Login and get access token"""
    user = users_db.get(form_data.username)
    if not user or not verify_password(form_data.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user["username"], "role": user["role"], "team": user["team"] or ""},
        expires_delta=access_token_expires
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": user["id"],
            "username": user["username"],
            "email": user["email"],
            "full_name": user["full_name"],
            "role": user["role"],
            "team": user["team"]
        }
    }

@router.get("/me")
async def get_current_user(token: str = Depends(oauth2_scheme)):
    """Get current user info"""
    from jose import JWTError, jwt
    from app.core.security import SECRET_KEY, ALGORITHM
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    user = users_db.get(username)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    
    return {
        "id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "full_name": user["full_name"],
        "role": user["role"],
        "team": user["team"]
    }

@router.post("/logout")
async def logout():
    """Logout (client-side token removal)"""
    return {"message": "Logged out successfully"}