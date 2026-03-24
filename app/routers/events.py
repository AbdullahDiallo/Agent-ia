from fastapi import APIRouter, Request
from ..services.webhook_security import verify_webhook

router = APIRouter(tags=["events"]) 

@router.post("/events/call-status")
async def call_status(request: Request):
    raw_body = await request.body()
    payload = await request.form()
    verify_webhook(
        "twilio_events",
        request=request,
        raw_body=raw_body,
        form_data={str(k): str(v) for k, v in dict(payload).items()},
        url=str(request.url),
    )
    return {"received": True}
