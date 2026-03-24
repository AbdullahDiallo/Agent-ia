from fastapi import APIRouter, Depends
from ..security import require_role

router = APIRouter()

@router.post("/delete", dependencies=[Depends(require_role("admin"))])
def gdpr_delete():
    return {"accepted": True}
