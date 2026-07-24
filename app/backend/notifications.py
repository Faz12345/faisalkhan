"""
Notification system for Smile Care Clinic.
Supports: In-App Alert, Email (doctor + patient), WhatsApp (doctor + patient), Telegram
"""

import os
import json
import logging
import asyncio
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import Optional
from pathlib import Path

logger = logging.getLogger("dental.notifications")

# ---------------------------------------------------------------------------
# In-App Alert (via MongoDB)
# ---------------------------------------------------------------------------
async def store_inapp_notification(db, notification: dict):
    """Store a notification document in MongoDB."""
    await db.notifications.insert_one({
        **notification,
        "read": False,
        "created_at": datetime.utcnow().isoformat(),
    })


async def get_unread_notifications(db) -> list:
    """Retrieve unread notifications for the dashboard."""
    notifs = await db.notifications.find({"read": False}).sort("created_at", -1).to_list(50)
    for n in notifs:
        n["id"] = str(n["_id"])
        n.pop("_id", None)
    return notifs


async def mark_notifications_read(db, notif_ids: list):
    """Mark given notification IDs as read."""
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


# ---------------------------------------------------------------------------
# Email Notification (Doctor)
# ---------------------------------------------------------------------------
async def send_email(to_email: str, subject: str, body: str):
    """Low-level email sender via SMTP."""
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
            logger.error("Failed to send email to %s: %s", to_email, e)

    await loop.run_in_executor(None, _send)


# ---------------------------------------------------------------------------
# WhatsApp Notification (via CallMeBot / WhatsApp API)
# ---------------------------------------------------------------------------
async def send_whatsapp(phone: str, message: str):
    """
    Send WhatsApp message using CallMeBot API.
    Requires WHATSAPP_API_KEY env var (free from callmebot.com).
    Format: phone must include country code, e.g. 919876543210
    """
    api_key = os.environ.get("WHATSAPP_API_KEY", "")
    if not api_key or not phone:
        logger.warning("WhatsApp not configured or no phone — skipping")
        return

    import aiohttp
    # CallMeBot WhatsApp API endpoint
    url = f"https://api.callmebot.com/whatsapp.php"
    params = {
        "phone": phone,
        "text": message,
        "apikey": api_key,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    logger.info("WhatsApp sent to %s", phone)
                else:
                    logger.error("WhatsApp API error for %s: %s", phone, await resp.text())
    except Exception as e:
        logger.error("WhatsApp send failed for %s: %s", phone, e)


# ---------------------------------------------------------------------------
# Notify Doctor on Booking (Email + WhatsApp + In-App)
# ---------------------------------------------------------------------------
async def notify_doctor_on_booking(db, appointment: dict):
    """Notify the doctor via all channels."""
    doctor_email = os.environ.get("DOCTOR_EMAIL", "")
    doctor_phone = os.environ.get("DOCTOR_PHONE", "")
    doctor_name = os.environ.get("DOCTOR_NAME", "Doctor")
    base_url = os.environ.get("BASE_URL", "http://localhost:8000")

    # 1. In-App notification
    notif = {
        "type": "new_booking",
        "title": "New Appointment",
        "message": f"{appointment['patient_name']} booked {appointment['date']} at {appointment['time_slot']}",
        "appointment_date": appointment["date"],
        "appointment_time": appointment["time_slot"],
        "patient_name": appointment["patient_name"],
        "patient_email": appointment.get("patient_email", ""),
    }
    await store_inapp_notification(db, notif)

    # 2. Email to Doctor
    subject = f"New Appointment - {appointment['patient_name']} on {appointment['date']}"
    body = f"""Hi Doctor,

A new appointment has been booked!

Patient: {appointment['patient_name']}
Email: {appointment['patient_email']}
Phone: {appointment.get('patient_phone', 'N/A')}
Date: {appointment['date']}
Time: {appointment['time_slot']}
Reason: {appointment.get('reason', 'N/A')}

View on dashboard: {base_url}/api/doctor
"""
    if doctor_email:
        asyncio.ensure_future(send_email(doctor_email, subject, body))

    # 3. WhatsApp to Doctor
    wa_msg = (
        f"*New Appointment!*%0A"
        f"Patient: {appointment['patient_name']}%0A"
        f"Date: {appointment['date']} at {appointment['time_slot']}%0A"
        f"Reason: {appointment.get('reason', 'N/A')}%0A"
        f"Phone: {appointment.get('patient_phone', 'N/A')}"
    )
    if doctor_phone:
        asyncio.ensure_future(send_whatsapp(doctor_phone, wa_msg))


# ---------------------------------------------------------------------------
# Notify Patient on Booking (Email + WhatsApp)
# ---------------------------------------------------------------------------
async def notify_patient_on_booking(appointment: dict):
    """Notify the patient about their confirmed booking."""
    patient_email = appointment.get("patient_email", "")
    patient_phone = appointment.get("patient_phone", "")
    doctor_name = os.environ.get("DOCTOR_NAME", "Dr. Shahzad Khan")
    clinic_address = os.environ.get("CLINIC_ADDRESS", "42 Maple Street, Downtown")
    base_url = os.environ.get("BASE_URL", "http://localhost:8000")

    # 1. Email to Patient
    subject = f"Appointment Confirmed - Smile Care Clinic"
    body = f"""Hi {appointment['patient_name']},

Your appointment at Smile Care Clinic has been confirmed!

Doctor: {doctor_name}
Date: {appointment['date']}
Time: {appointment['time_slot']}
Reason: {appointment.get('reason', 'N/A')}

Clinic Address: {clinic_address}

You can manage your appointments here:
{base_url}/api/my-appointments

Thank you for choosing Smile Care!
"""
    if patient_email:
        asyncio.ensure_future(send_email(patient_email, subject, body))

    # 2. WhatsApp to Patient
    wa_msg = (
        f"*Appointment Confirmed!*%0A%0A"
        f"Hi {appointment['patient_name']},%0A%0A"
        f"Your visit at Smile Care Clinic:%0A"
        f"📅 {appointment['date']} at {appointment['time_slot']}%0A"
        f"👨‍⚕️ {doctor_name}%0A"
        f"📍 {clinic_address}%0A%0A"
        f"Manage: {base_url}/api/my-appointments"
    )
    if patient_phone:
        asyncio.ensure_future(send_whatsapp(patient_phone, wa_msg))


# ---------------------------------------------------------------------------
# Master notify function (called when a booking is made)
# ---------------------------------------------------------------------------
async def notify_both_on_booking(db, appointment: dict):
    """Notify both doctor and patient about a new booking."""
    # Fire doctor notifications
    asyncio.ensure_future(notify_doctor_on_booking(db, appointment))
    # Fire patient notifications
    asyncio.ensure_future(notify_patient_on_booking(appointment))
