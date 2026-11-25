from fastapi import APIRouter, Depends
from app.core.auth import get_current_user
from app.api.v1.didit import router as didit_router

router = APIRouter()
router.include_router(didit_router)

@router.get("/me", tags=["identity"])
async def who_am_i(user: dict = Depends(get_current_user)):
    app_metadata = user.get("app_metadata") or {}
    return {"id": user["id"], "email": user.get("email"), "role": app_metadata.get("role")}
