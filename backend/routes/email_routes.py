from fastapi import APIRouter, HTTPException

from config import settings
from services.email_service import send_test_email

router = APIRouter()


@router.post("/email/test")
def test_email():
    if not settings.SMTP_HOST:
        raise HTTPException(status_code=400, detail="SMTP not configured")
    try:
        send_test_email()
        return {"success": True, "message": "Test email sent successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to send test email: {str(e)}")
