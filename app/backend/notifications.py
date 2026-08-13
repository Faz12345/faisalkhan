"""
Notification system — Smile Care Dental Clinic.
Channels: In-App (MongoDB), Email (SMTP), WhatsApp (CallMeBot).
"""

import os
import asyncio
import logging
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

logger = logging.getLogger("dental.notifications")


# ── In-App (MongoDB) ──────────────────────────────────────────────────────────
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
    from bson import ObjectId
    oids = []
    for nid in notif_ids:
        try:
            oids.append(ObjectId(nid))
        except Exception:
            pass
    if oids:
        await db.notifications.update_many(
            {"_id": {"$in": oids}},
            {"$set": {"read": True}},
        )


# ── Email (SMTP) ──────────────────────────────────────────────────────────────
async def send_email(to_email: str, subject: str, body: str):
    smtp_host = os.environ.get("SMTP_HOST", "")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")

    if not smtp_host or not smtp_user:
        logger.warning("SMTP not configured — skipping email to %s", to_email)
        return

    msg = MIMEMultipart()
    msg["From"] = smtp_user
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    loop = asyncio.get_event_loop()

    def _send():
        try:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)
            logger.info("Email sent to %s", to_email)
        except Exception as e:
            logger.error("Email send failed to %s: %s", to_email, e)

    await loop.run_in_executor(None, _send)


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
    base_url = os.environ.get("BASE_URL", "http://localhost:8000")

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
    base_url = os.environ.get("BASE_URL", "http://localhost:8000")

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
