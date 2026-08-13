from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env", override=True)

import os
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta, date
from typing import Optional

import bcrypt
import jwt
from bson import ObjectId
from fastapi import FastAPI, APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient

from notifications import (
    notify_both_on_booking,
    get_unread_notifications,
    mark_notifications_read,
)

# ── DB ───────────────────────────────────────────────────────────────────────
client = AsyncIOMotorClient(os.environ["MONGO_URL"])
db = client[os.environ["DB_NAME"]]

# ── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(title="Smile Care Dental Clinic")
api_router = APIRouter(prefix="/api")
app.mount("/api/static", StaticFiles(directory=str(ROOT_DIR / "static")), name="static")

templates = Jinja2Templates(directory=str(ROOT_DIR / "templates"))

JWT_ALGORITHM = "HS256"
COOKIE_NAME = "dental_token"
DR_COOKIE = "dental_dr_token"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("dental")

# ── Clinic time slots ─────────────────────────────────────────────────────────
TIME_SLOTS = [
    "10:00", "10:30", "11:00", "11:30", "12:00", "12:30",
    "13:00", "13:30", "14:00",
    "17:00", "17:30", "18:00", "18:30", "19:00", "19:30",
    "20:00", "20:30", "21:00", "21:30", "22:00", "22:30",
]

# ── Auth helpers ──────────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False

def create_token(user_id: str, role: str) -> str:
    payload = {
        "sub": user_id,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
    }
    return jwt.encode(payload, os.environ["JWT_SECRET"], algorithm=JWT_ALGORITHM)

def set_auth_cookie(response: Response, token: str, is_doctor: bool = False):
    name = DR_COOKIE if is_doctor else COOKIE_NAME
    response.set_cookie(
        key=name, value=token, httponly=True,
        secure=False, samesite="lax", max_age=7 * 24 * 3600, path="/"
    )

def clear_auth_cookies(response: Response):
    response.delete_cookie(key=COOKIE_NAME, path="/")
    response.delete_cookie(key=DR_COOKIE, path="/")

async def _decode_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, os.environ["JWT_SECRET"], algorithms=[JWT_ALGORITHM])
        user = await db.users.find_one({"_id": ObjectId(payload["sub"])})
        if not user:
            return None
        user["id"] = str(user["_id"])
        user.pop("_id", None)
        user.pop("password_hash", None)
        return user
    except Exception:
        return None

async def current_user(request: Request) -> Optional[dict]:
    token = request.cookies.get(COOKIE_NAME) or request.cookies.get(DR_COOKIE)
    if not token:
        return None
    return await _decode_token(token)

async def require_user(request: Request) -> dict:
    user = await current_user(request)
    if not user:
        raise HTTPException(status_code=302, headers={"Location": "/api/login"})
    return user

async def require_doctor(request: Request) -> dict:
    token = request.cookies.get(DR_COOKIE) or request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=302, headers={"Location": "/api/doctor-login"})
    user = await _decode_token(token)
    if not user or user.get("role") != "doctor":
        raise HTTPException(status_code=302, headers={"Location": "/api/doctor-login"})
    return user

async def require_patient(request: Request) -> dict:
    user = await require_user(request)
    if user.get("role") != "patient":
        raise HTTPException(status_code=302, headers={"Location": "/api/doctor"})
    return user

def ctx(request: Request, user: Optional[dict] = None, **extra) -> dict:
    return {
        "request": request,
        "user": user,
        "doctor_name": os.environ.get("DOCTOR_NAME", "Dr. Shehzad Khan"),
        "doctor_phone": os.environ.get("DOCTOR_WHATSAPP", "918767308819"),
        "doctor_email": os.environ.get("DOCTOR_EMAIL", "shehzadkhan0101@gmail.com"),
        **extra,
    }

# ── Lifespan / startup ────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.users.create_index("email", unique=True)
    await db.appointments.create_index([("date", 1), ("time_slot", 1)])
    await db.appointments.create_index("patient_id")
    await db.treatments.create_index("patient_id")
    await db.notifications.create_index("read")
    await db.notifications.create_index("created_at")

    doctor_email = os.environ["DOCTOR_EMAIL"].lower()
    existing = await db.users.find_one({"email": doctor_email})
    if existing is None:
        await db.users.insert_one({
            "email": doctor_email,
            "password_hash": hash_password(os.environ["DOCTOR_PASSWORD"]),
            "name": os.environ["DOCTOR_NAME"],
            "role": "doctor",
            "phone": os.environ.get("DOCTOR_PHONE", ""),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.info("Seeded doctor account: %s", doctor_email)
    else:
        if not verify_password(os.environ["DOCTOR_PASSWORD"], existing["password_hash"]):
            await db.users.update_one(
                {"email": doctor_email},
                {"$set": {"password_hash": hash_password(os.environ["DOCTOR_PASSWORD"])}},
            )
    yield
    client.close()

app.router.lifespan_context = lifespan

# ── Public pages ──────────────────────────────────────────────────────────────
@api_router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "home.html", ctx(request, await current_user(request)))

@api_router.get("/about", response_class=HTMLResponse)
async def about(request: Request):
    return templates.TemplateResponse(request, "about.html", ctx(request, await current_user(request)))

# ── Patient auth ──────────────────────────────────────────────────────────────
@api_router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = await current_user(request)
    if user:
        target = "/api/doctor" if user["role"] == "doctor" else "/api/my-appointments"
        return RedirectResponse(url=target, status_code=303)
    return templates.TemplateResponse(request, "login.html", ctx(request, None, error=None))

@api_router.post("/login")
async def login_submit(request: Request, email: str = Form(...), password: str = Form(...)):
    email = email.strip().lower()
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(password, user["password_hash"]):
        return templates.TemplateResponse(request, "login.html", ctx(request, None, error="Invalid email or password."), status_code=400)
    if user["role"] == "doctor":
        return templates.TemplateResponse(
        request, "login.html",
        ctx(request, None, error="Doctors must use the Doctor Login page."),
            status_code=400,
        )
    token = create_token(str(user["_id"]), user["role"])
    resp = RedirectResponse(url="/api/my-appointments", status_code=303)
    set_auth_cookie(resp, token, is_doctor=False)
    return resp

@api_router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    if await current_user(request):
        return RedirectResponse(url="/api/my-appointments", status_code=303)
    return templates.TemplateResponse(request, "register.html", ctx(request, None, error=None))

@api_router.post("/register")
async def register_submit(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(""),
    password: str = Form(...),
):
    email = email.strip().lower()
    name = name.strip()
    if len(password) < 6:
        return templates.TemplateResponse(
        request, "register.html",
        ctx(request, None, error="Password must be at least 6 characters."),
            status_code=400,
        )
    if await db.users.find_one({"email": email}):
        return templates.TemplateResponse(
        request, "register.html",
        ctx(request, None, error="An account with this email already exists."),
            status_code=400,
        )
    result = await db.users.insert_one({
        "email": email, "name": name, "phone": phone.strip(),
        "password_hash": hash_password(password), "role": "patient",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    token = create_token(str(result.inserted_id), "patient")
    resp = RedirectResponse(url="/api/my-appointments", status_code=303)
    set_auth_cookie(resp, token)
    return resp

@api_router.post("/logout")
async def logout():
    resp = RedirectResponse(url="/api/", status_code=303)
    clear_auth_cookies(resp)
    return resp

# ── Doctor auth (separate login) ──────────────────────────────────────────────
@api_router.get("/doctor-login", response_class=HTMLResponse)
async def doctor_login_page(request: Request):
    token = request.cookies.get(DR_COOKIE)
    if token:
        user = await _decode_token(token)
        if user and user.get("role") == "doctor":
            return RedirectResponse(url="/api/doctor", status_code=303)
    return templates.TemplateResponse(request, "doctor_login.html", ctx(request, None, error=None))

@api_router.post("/doctor-login")
async def doctor_login_submit(
    request: Request, email: str = Form(...), password: str = Form(...)
):
    email = email.strip().lower()
    user = await db.users.find_one({"email": email})
    if not user or user.get("role") != "doctor" or not verify_password(password, user["password_hash"]):
        return templates.TemplateResponse(
        request, "doctor_login.html",
        ctx(request, None, error="Invalid credentials. Doctor access only."),
            status_code=400,
        )
    token = create_token(str(user["_id"]), "doctor")
    resp = RedirectResponse(url="/api/doctor", status_code=303)
    set_auth_cookie(resp, token, is_doctor=True)
    return resp

@api_router.post("/doctor-logout")
async def doctor_logout():
    resp = RedirectResponse(url="/api/doctor-login", status_code=303)
    clear_auth_cookies(resp)
    return resp

# ── Password reset ────────────────────────────────────────────────────────────
@api_router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    return templates.TemplateResponse(request, "forgot_password.html", ctx(request, None, error=None, success=None))

@api_router.post("/forgot-password")
async def forgot_password_submit(request: Request, email: str = Form(...)):
    email = email.strip().lower()
    user = await db.users.find_one({"email": email})
    if user:
        token = create_token(str(user["_id"]), user["role"])
        reset_url = f"{os.environ.get('BASE_URL','http://localhost:8000')}/api/reset-password?token={token}"
        try:
            from notifications import send_password_reset_email
            await send_password_reset_email(email, reset_url)
        except Exception as e:
            logger.error("Reset email error: %s", e)
    return templates.TemplateResponse(
        request, "forgot_password.html",
        ctx(request, None, error=None, success="If that email exists, a reset link was sent."),
    )

@api_router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request, token: str = ""):
    return templates.TemplateResponse(request, "reset_password.html", ctx(request, None, token=token, error=None))

@api_router.post("/reset-password")
async def reset_password_submit(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
):
    if password != confirm_password:
        return templates.TemplateResponse(
        request, "reset_password.html",
        ctx(request, None, token=token, error="Passwords do not match."),
            status_code=400,
        )
    if len(password) < 6:
        return templates.TemplateResponse(
        request, "reset_password.html",
        ctx(request, None, token=token, error="Password must be at least 6 characters."),
            status_code=400,
        )
    try:
        payload = jwt.decode(token, os.environ["JWT_SECRET"], algorithms=[JWT_ALGORITHM])
        user_id = payload["sub"]
    except Exception:
        return templates.TemplateResponse(
        request, "reset_password.html",
        ctx(request, None, token=token, error="Invalid or expired reset link."),
            status_code=400,
        )
    await db.users.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"password_hash": hash_password(password)}},
    )
    return RedirectResponse(url="/api/login", status_code=303)

# ── Patient booking ───────────────────────────────────────────────────────────
@api_router.get("/book", response_class=HTMLResponse)
async def book_page(request: Request, date_str: Optional[str] = None):
    user = await require_patient(request)
    selected_date = date_str or (date.today() + timedelta(days=1)).isoformat()
    booked = await db.appointments.find(
        {"date": selected_date, "status": {"$ne": "cancelled"}}
    ).to_list(200)
    taken = {a["time_slot"] for a in booked}
    slots = [{"time": s, "available": s not in taken} for s in TIME_SLOTS]
    return templates.TemplateResponse(
        request, "book.html",
        ctx(request, user, slots=slots, selected_date=selected_date,
            min_date=date.today().isoformat()),
    )

@api_router.post("/book")
async def book_submit(
    request: Request,
    date_str: str = Form(..., alias="date"),
    time_slot: str = Form(...),
    reason: str = Form(...),
):
    user = await require_patient(request)
    try:
        req_date = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date")
    if req_date < date.today():
        return RedirectResponse(url=f"/api/book?date_str={date_str}", status_code=303)
    if time_slot not in TIME_SLOTS:
        return RedirectResponse(url=f"/api/book?date_str={date_str}", status_code=303)
    if await db.appointments.find_one(
        {"date": date_str, "time_slot": time_slot, "status": {"$ne": "cancelled"}}
    ):
        return RedirectResponse(url=f"/api/book?date_str={date_str}&conflict=1", status_code=303)
    appt = {
        "patient_id": user["id"],
        "patient_name": user["name"],
        "patient_email": user["email"],
        "patient_phone": user.get("phone", ""),
        "date": date_str,
        "time_slot": time_slot,
        "reason": reason.strip(),
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    result = await db.appointments.insert_one(appt)
    appt["_id"] = result.inserted_id
    try:
        await notify_both_on_booking(db, appt)
    except Exception as e:
        logger.error("Notification failed: %s", e)
    return RedirectResponse(url="/api/my-appointments?booked=1", status_code=303)

@api_router.get("/my-appointments", response_class=HTMLResponse)
async def my_appointments(request: Request):
    user = await require_patient(request)
    booked = request.query_params.get("booked") == "1"
    appts = await db.appointments.find({"patient_id": user["id"]}).sort("date", -1).to_list(200)
    for a in appts:
        a["id"] = str(a["_id"])
        a.pop("_id", None)
    treatments = await db.treatments.find({"patient_id": user["id"]}).sort("date", -1).to_list(200)
    for t in treatments:
        t["id"] = str(t["_id"])
        t.pop("_id", None)
    return templates.TemplateResponse(
        request, "my_appointments.html",
        ctx(request, user, appointments=appts, treatments=treatments, booked=booked),
    )

@api_router.post("/appointment/{appt_id}/cancel")
async def cancel_appointment(request: Request, appt_id: str):
    user = await require_patient(request)
    try:
        oid = ObjectId(appt_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Not found")
    await db.appointments.update_one(
        {"_id": oid, "patient_id": user["id"], "status": {"$in": ["pending", "confirmed"]}},
        {"$set": {"status": "cancelled"}},
    )
    return RedirectResponse(url="/api/my-appointments", status_code=303)

# ── Doctor dashboard ──────────────────────────────────────────────────────────
@api_router.get("/doctor", response_class=HTMLResponse)
async def doctor_dashboard(request: Request, filter: str = "upcoming"):
    doctor = await require_doctor(request)
    ts = date.today().isoformat()
    if filter == "today":
        q = {"date": ts, "status": {"$ne": "cancelled"}}
    elif filter == "past":
        q = {"date": {"$lt": ts}}
    elif filter == "all":
        q = {}
    else:
        q = {"date": {"$gte": ts}, "status": {"$ne": "cancelled"}}

    appts = await db.appointments.find(q).sort([("date", 1), ("time_slot", 1)]).to_list(500)
    for a in appts:
        a["id"] = str(a["_id"])
        a.pop("_id", None)

    stats = {
        "patients": await db.users.count_documents({"role": "patient"}),
        "today": await db.appointments.count_documents({"date": ts, "status": {"$ne": "cancelled"}}),
        "pending": await db.appointments.count_documents({"status": "pending"}),
        "completed": await db.appointments.count_documents({"status": "completed"}),
    }
    unread = []
    try:
        unread = await get_unread_notifications(db)
    except Exception as e:
        logger.error("Notifications load error: %s", e)

    today_display = datetime.now().strftime("%A, %B %d")
    return templates.TemplateResponse(
        request, "doctor_dashboard.html",
        ctx(request, doctor, appointments=appts, active_filter=filter,
            stats=stats, today_display=today_display, notifications=unread),
    )

@api_router.post("/doctor/notifications/read-all")
async def mark_all_notifications_read(request: Request):
    await require_doctor(request)
    try:
        unread = await get_unread_notifications(db)
        ids = [n["id"] for n in unread]
        await mark_notifications_read(db, ids)
    except Exception as e:
        logger.error("Mark read error: %s", e)
    return RedirectResponse(url="/api/doctor", status_code=303)

@api_router.post("/doctor/appointment/{appt_id}/status")
async def update_appointment_status(
    request: Request, appt_id: str, new_status: str = Form(...)
):
    await require_doctor(request)
    valid = {"pending", "confirmed", "completed", "cancelled"}
    if new_status not in valid:
        raise HTTPException(status_code=400, detail="Invalid status")
    try:
        oid = ObjectId(appt_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Not found")
    appt = await db.appointments.find_one({"_id": oid})
    if not appt:
        raise HTTPException(status_code=404, detail="Not found")
    await db.appointments.update_one({"_id": oid}, {"$set": {"status": new_status}})
    patient_id = appt.get("patient_id", "")
    referer = request.headers.get("referer", "")
    if f"/patient/{patient_id}" in referer:
        return RedirectResponse(url=f"/api/doctor/patient/{patient_id}", status_code=303)
    return RedirectResponse(url="/api/doctor", status_code=303)

# ── Doctor: book a patient ────────────────────────────────────────────────────
@api_router.get("/doctor/book", response_class=HTMLResponse)
async def doctor_book_page(request: Request, date_str: Optional[str] = None, patient_id: str = ""):
    doctor = await require_doctor(request)
    selected_date = date_str or (date.today() + timedelta(days=1)).isoformat()

    patients_list = []
    async for p in db.users.find({"role": "patient"}).sort("name", 1):
        patients_list.append({"id": str(p["_id"]), "name": p["name"], "email": p["email"]})

    booked = await db.appointments.find(
        {"date": selected_date, "status": {"$ne": "cancelled"}}
    ).to_list(200)
    taken = {a["time_slot"] for a in booked}
    slots = [{"time": s, "available": s not in taken} for s in TIME_SLOTS]
    available_count = sum(1 for s in slots if s["available"])

    selected_patient = None
    if patient_id:
        try:
            p = await db.users.find_one({"_id": ObjectId(patient_id), "role": "patient"})
            if p:
                selected_patient = {"id": str(p["_id"]), "name": p["name"], "email": p["email"]}
        except Exception:
            pass

    try:
        dt_parsed = datetime.strptime(selected_date, "%Y-%m-%d")
        date_label = dt_parsed.strftime("%A, %B %d, %Y")
    except ValueError:
        date_label = selected_date

    return templates.TemplateResponse(
        request, "doctor_book.html",
        ctx(request, doctor, patients=patients_list, slots=slots,
            selected_date=selected_date, min_date=date.today().isoformat(),
            available_count=available_count, total_count=len(slots),
            date_label=date_label, selected_patient=selected_patient),
    )

@api_router.post("/doctor/book")
async def doctor_book_submit(
    request: Request,
    date_str: str = Form(..., alias="date"),
    time_slot: str = Form(...),
    patient_id: str = Form(...),
    reason: str = Form(...),
):
    doctor = await require_doctor(request)
    try:
        req_date = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date")
    if req_date < date.today():
        raise HTTPException(status_code=400, detail="Cannot book in the past")
    if time_slot not in TIME_SLOTS:
        raise HTTPException(status_code=400, detail="Invalid time slot")
    try:
        patient_oid = ObjectId(patient_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Patient not found")
    patient = await db.users.find_one({"_id": patient_oid, "role": "patient"})
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    if await db.appointments.find_one(
        {"date": date_str, "time_slot": time_slot, "status": {"$ne": "cancelled"}}
    ):
        return RedirectResponse(url=f"/api/doctor/book?date_str={date_str}&conflict=1", status_code=303)
    appt = {
        "patient_id": patient_id,
        "patient_name": patient["name"],
        "patient_email": patient["email"],
        "patient_phone": patient.get("phone", ""),
        "date": date_str,
        "time_slot": time_slot,
        "reason": reason.strip(),
        "status": "confirmed",
        "booked_by": "doctor",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    result = await db.appointments.insert_one(appt)
    appt["_id"] = result.inserted_id
    try:
        await notify_both_on_booking(db, appt)
    except Exception as e:
        logger.error("Notification failed: %s", e)
    return RedirectResponse(url="/api/doctor", status_code=303)

# ── Doctor: patient list ──────────────────────────────────────────────────────
@api_router.get("/doctor/patients", response_class=HTMLResponse)
async def doctor_patients(request: Request, q: str = ""):
    doctor = await require_doctor(request)
    query: dict = {"role": "patient"}
    if q:
        query["$or"] = [
            {"name": {"$regex": q, "$options": "i"}},
            {"email": {"$regex": q, "$options": "i"}},
            {"phone": {"$regex": q, "$options": "i"}},
        ]
    patients_raw = await db.users.find(query).sort("name", 1).to_list(500)
    result = []
    today_iso = date.today().isoformat()
    for p in patients_raw:
        pid = str(p["_id"])
        visits = await db.appointments.count_documents({"patient_id": pid, "status": "completed"})
        upcoming = await db.appointments.count_documents({
            "patient_id": pid,
            "date": {"$gte": today_iso},
            "status": {"$in": ["pending", "confirmed"]},
        })
        txns = await db.treatments.find({"patient_id": pid}).to_list(500)
        total_spent = sum(t.get("cost", 0) or 0 for t in txns)
        last_appt = await db.appointments.find_one(
            {"patient_id": pid, "status": "completed"},
            sort=[("date", -1)],
        )
        last_visit = last_appt["date"] if last_appt else None
        result.append({
            "id": pid,
            "name": p["name"],
            "email": p["email"],
            "phone": p.get("phone", ""),
            "visits": visits,
            "upcoming": upcoming,
            "total_spent": total_spent,
            "last_visit": last_visit,
        })
    return templates.TemplateResponse(request, "doctor_patients.html", ctx(request, doctor, patients=result, q=q))

# ── Doctor: patient detail ────────────────────────────────────────────────────
@api_router.get("/doctor/patient/{patient_id}", response_class=HTMLResponse)
async def doctor_patient_detail(request: Request, patient_id: str):
    doctor = await require_doctor(request)
    try:
        oid = ObjectId(patient_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Not found")
    patient = await db.users.find_one({"_id": oid, "role": "patient"})
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    patient["id"] = str(patient["_id"])
    patient.pop("_id", None)
    patient.pop("password_hash", None)

    appts = await db.appointments.find({"patient_id": patient_id}).sort("date", -1).to_list(200)
    for a in appts:
        a["id"] = str(a["_id"])
        a.pop("_id", None)

    treatments = await db.treatments.find({"patient_id": patient_id}).sort("date", -1).to_list(200)
    for t in treatments:
        t["id"] = str(t["_id"])
        t.pop("_id", None)

    total_spent = sum(t.get("cost", 0) or 0 for t in treatments)

    return templates.TemplateResponse(
        request, "patient_detail.html",
        ctx(request, doctor, patient=patient, appointments=appts,
            treatments=treatments, total_spent=total_spent),
    )

@api_router.post("/doctor/patient/{patient_id}/notes")
async def add_treatment_note(
    request: Request,
    patient_id: str,
    diagnosis: str = Form(...),
    treatment: str = Form(...),
    notes: str = Form(""),
    cost: str = Form("0"),
):
    await require_doctor(request)
    try:
        oid = ObjectId(patient_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Not found")
    patient = await db.users.find_one({"_id": oid, "role": "patient"})
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    try:
        cost_val = float(cost) if cost else 0.0
    except ValueError:
        cost_val = 0.0
    await db.treatments.insert_one({
        "patient_id": patient_id,
        "patient_name": patient["name"],
        "doctor_name": os.environ.get("DOCTOR_NAME", "Dr. Shehzad Khan"),
        "diagnosis": diagnosis.strip(),
        "treatment": treatment.strip(),
        "notes": notes.strip(),
        "cost": cost_val,
        "date": date.today().isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return RedirectResponse(url=f"/api/doctor/patient/{patient_id}", status_code=303)

@api_router.post("/doctor/patient/{patient_id}/delete-appointment/{appt_id}")
async def doctor_delete_appointment(request: Request, patient_id: str, appt_id: str):
    await require_doctor(request)
    try:
        oid = ObjectId(appt_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Not found")
    await db.appointments.delete_one({"_id": oid})
    return RedirectResponse(url=f"/api/doctor/patient/{patient_id}", status_code=303)

# ── Health check ──────────────────────────────────────────────────────────────
@api_router.get("/health")
async def health():
    return {"status": "ok", "clinic": "Smile Care Dental"}

# ── Root redirect ─────────────────────────────────────────────────────────────
@app.get("/")
async def root_redirect():
    return RedirectResponse(url="/api/", status_code=302)

# ── Router + middleware ───────────────────────────────────────────────────────
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Exception handler: redirect 302/303 HTTPExceptions ───────────────────────
from fastapi.exceptions import HTTPException as _HTTPException
from fastapi.responses import JSONResponse

@app.exception_handler(_HTTPException)
async def http_exception_handler(request: Request, exc: _HTTPException):
    if exc.status_code in (302, 303) and exc.headers and "Location" in exc.headers:
        return RedirectResponse(url=exc.headers["Location"], status_code=303)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
