"""
Notification system — Smile Care Dental Clinic.
Channels: In-App (PostgreSQL), Email (Resend HTTPS API), WhatsApp (CallMeBot).

Email previously used raw SMTP (smtplib), but Render's free tier blocks
outbound traffic on SMTP ports (25, 465, 587) as of Sept 2025. Resend's
API is called over HTTPS (port 443), which is never blocked, and tends
to land in the inbox more reliably than SMTP from a shared cloud IP.
"""

import os
import asyncio
import logging
from datetime import datetime
from typing import Optional

import aiohttp

logger = logging.getLogger("dental.notifications")


# ── In-App (PostgreSQL) ───────────────────────────────────────────────────────
async def store_inapp_notification(db, notification: dict):
    await db.notifications.insert_one({
        **notification,
        "read": False,
        "created_at": datetime.utcnow().isoformat(),
    })


async def get_unread_notifications(db) -> list:
    notifs = await db.notifications.find({"read": False}).sort("created_at", -1).to_list(50)
    for n in notifs:
        n["id"] = str(n["_id"])
        n.pop("_id", None)
    return notifs


async def mark_notifications_read(db, notif_ids: list):
    if notif_ids:
        await db.notifications.update_many(
            {"_id": {"$in": notif_ids}},
            {"$set": {"read": True}},
        )


# ── Email (Resend API) ──────────────────────────────────────────────────────────
async def send_email(to_email: str, subject: str, body: str):
    api_key = os.environ.get("RESEND_API_KEY", "")
    # Until your domain is verified on Resend, this must stay
    # "onboarding@resend.dev" and can only deliver to the address you
    # signed up with. After verifying your own domain, set
    # RESEND_FROM_EMAIL to something like "no-reply@yourdomain.com".
    from_email = os.environ.get("RESEND_FROM_EMAIL", "onboarding@resend.dev")

    if not api_key:
        logger.warning("RESEND_API_KEY not configured — skipping email to %s", to_email)
        return

    payload = {
        "from": from_email,
        "to": [to_email],
        "subject": subject,
        "text": body,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.resend.com/emails",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status in (200, 201):
                    logger.info("Email sent to %s", to_email)
                else:
                    err_text = await resp.text()
                    if resp.status == 403 and "testing emails" in err_text:
                        logger.error(
                            "Resend sandbox only allows emails to the account owner. "
                            "Verify a sending domain and set RESEND_FROM_EMAIL to that "
                            "domain before emailing %s: %s",
                            to_email,
                            err_text,
                        )
                    else:
                        logger.error("Email send failed to %s: %s %s", to_email, resp.status, err_text)
    except Exception as e:
        logger.error("Email send failed to %s: %s", to_email, e)


# ── WhatsApp (CallMeBot) ──────────────────────────────────────────────────────
async def send_whatsapp(phone: str, message: str):
    api_key = os.environ.get("WHATSAPP_API_KEY", "")
    if not api_key or not phone:
        logger.warning("WhatsApp not configured — skipping")
        return
    try:
        import aiohttp
        url = "https://api.callmebot.com/whatsapp.php"
        params = {"phone": phone, "text": message, "apikey": api_key}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params,
                                   timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    logger.info("WhatsApp sent to %s", phone)
                else:
                    logger.error("WhatsApp error %s: %s", phone, await resp.text())
    except Exception as e:
        logger.error("WhatsApp failed for %s: %s", phone, e)


# ── Doctor notification ───────────────────────────────────────────────────────
async def notify_doctor_on_booking(db, appointment: dict):
    doctor_email = os.environ.get("DOCTOR_EMAIL", "shehzadkhan0101@gmail.com")
    doctor_phone = os.environ.get("DOCTOR_WHATSAPP", "918767308819")
    doctor_name = os.environ.get("DOCTOR_NAME", "Dr. Shehzad Khan")
    base_url = os.environ.get("BASE_URL", "http://localhost:8000").strip()

    # 1. In-App
    notif = {
        "type": "new_booking",
        "title": "New Appointment Booked",
        "message": (
            f"{appointment['patient_name']} booked "
            f"{appointment['date']} at {appointment['time_slot']}"
        ),
        "appointment_date": appointment["date"],
        "appointment_time": appointment["time_slot"],
        "patient_name": appointment["patient_name"],
        "patient_email": appointment.get("patient_email", ""),
        "patient_phone": appointment.get("patient_phone", ""),
    }
    await store_inapp_notification(db, notif)

    # 2. Email
    subject = f"New Appointment — {appointment['patient_name']} on {appointment['date']}"
    body = (
        f"Hi {doctor_name},\n\n"
        f"A new appointment has been booked!\n\n"
        f"Patient : {appointment['patient_name']}\n"
        f"Email   : {appointment.get('patient_email', 'N/A')}\n"
        f"Phone   : {appointment.get('patient_phone', 'N/A')}\n"
        f"Date    : {appointment['date']}\n"
        f"Time    : {appointment['time_slot']}\n"
        f"Reason  : {appointment.get('reason', 'N/A')}\n\n"
        f"Dashboard: {base_url}/api/doctor\n"
    )
    if doctor_email:
        asyncio.ensure_future(send_email(doctor_email, subject, body))

    # 3. WhatsApp
    wa_msg = (
        f"*New Appointment!*\n"
        f"Patient: {appointment['patient_name']}\n"
        f"Date: {appointment['date']} at {appointment['time_slot']}\n"
        f"Reason: {appointment.get('reason', 'N/A')}\n"
        f"Phone: {appointment.get('patient_phone', 'N/A')}"
    )
    if doctor_phone:
        asyncio.ensure_future(send_whatsapp(doctor_phone, wa_msg))


# ── Patient notification ──────────────────────────────────────────────────────
async def notify_patient_on_booking(appointment: dict):
    patient_email = appointment.get("patient_email", "")
    patient_phone = appointment.get("patient_phone", "")
    doctor_name = os.environ.get("DOCTOR_NAME", "Dr. Shehzad Khan")
    clinic_addr = os.environ.get("CLINIC_ADDRESS", "Dental Clinic, Main Road")
    base_url = os.environ.get("BASE_URL", "http://localhost:8000").strip()

    subject = "Appointment Confirmed — Smile Care Dental Clinic"
    body = (
        f"Hi {appointment['patient_name']},\n\n"
        f"Your appointment at Smile Care Dental Clinic is confirmed!\n\n"
        f"Doctor  : {doctor_name} (BDS)\n"
        f"Date    : {appointment['date']}\n"
        f"Time    : {appointment['time_slot']}\n"
        f"Reason  : {appointment.get('reason', 'N/A')}\n\n"
        f"Address : {clinic_addr}\n\n"
        f"Manage appointments: {base_url}/api/my-appointments\n\n"
        f"Thank you for choosing Smile Care!\n"
    )
    if patient_email:
        asyncio.ensure_future(send_email(patient_email, subject, body))

    wa_msg = (
        f"*Appointment Confirmed!*\n\n"
        f"Hi {appointment['patient_name']},\n\n"
        f"Smile Care Dental Clinic:\n"
        f"Date: {appointment['date']} at {appointment['time_slot']}\n"
        f"Doctor: {doctor_name} (BDS)\n"
        f"Address: {clinic_addr}\n\n"
        f"Manage: {base_url}/api/my-appointments"
    )
    if patient_phone:
        asyncio.ensure_future(send_whatsapp(patient_phone, wa_msg))


# ── Master function ───────────────────────────────────────────────────────────
async def notify_both_on_booking(db, appointment: dict):
    asyncio.ensure_future(notify_doctor_on_booking(db, appointment))
    asyncio.ensure_future(notify_patient_on_booking(appointment))


# ── Password reset ────────────────────────────────────────────────────────────
async def send_password_reset_email(to_email: str, reset_url: str):
    subject = "Reset your password — Smile Care Dental"
    body = (
        f"Hi,\n\n"
        f"Click the link below to reset your password (expires in 7 days):\n\n"
        f"{reset_url}\n\n"
        f"If you did not request this, ignore this email.\n\n"
        f"— Smile Care Dental Clinic\n"
    )
    await send_email(to_email, subject, body)
