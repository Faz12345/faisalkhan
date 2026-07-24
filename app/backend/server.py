from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env", override=True)

import os
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta, date, time
from typing import Optional

import bcrypt
import jwt
from bson import ObjectId
from fastapi import FastAPI, APIRouter, Request, Form, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from motor.motor_asyncio import AsyncIOMotorClient
from starlette.middleware.cors import CORSMiddleware

# ------------------------------------------------------------------ setup
mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

app = FastAPI(title="Smile Care Clinic")
api_router = APIRouter(prefix="/api")

# Templates & static (mounted at /api paths since ingress routes /api to backend)
templates = Jinja2Templates(directory=str(ROOT_DIR / "templates"))
app.mount("/api/static", StaticFiles(directory=str(ROOT_DIR / "static")), name="static")

JWT_ALGORITHM = "HS256"
COOKIE_NAME = "dental_token"

TIME_SLOTS = [
    "10:30", "11:00", "11:30", "12:00", "12:30", "13:00", "13:30", "14:00", "14:30",
    "18:00", "18:30", "19:00", "19:30", "20:00", "20:30", "21:00", "21:30", "22:00", "22:30",
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("dental")

# ------------------------------------------------------------------ helpers
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False

def create_token(user_id: str, role: str) -> str:
    payload = {
        "sub": user_id,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
    }
    return jwt.encode(payload, os.environ["JWT_SECRET"], algorithm=JWT_ALGORITHM)

def set_auth_cookie(response: Response, token: str):
    response.set_cookie(
        key=COOKIE_NAME, value=token, httponly=True, secure=False,
        samesite="lax", max_age=7 * 24 * 3600, path="/",
    )

def clear_auth_cookie(response: Response):
    response.delete_cookie(key=COOKIE_NAME, path="/")

async def current_user(request: Request) -> Optional[dict]:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
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

async def require_user(request: Request) -> dict:
    user = await current_user(request)
    if not user:
        raise HTTPException(status_code=302, headers={"Location": "/api/login"})
    return user

async def require_doctor(request: Request) -> dict:
    user = await require_user(request)
    if user.get("role") != "doctor":
        raise HTTPException(status_code=302, headers={"Location": "/api/"})
    return user

async def require_patient(request: Request) -> dict:
    user = await require_user(request)
    if user.get("role") != "patient":
        raise HTTPException(status_code=302, headers={"Location": "/api/doctor"})
    return user

def ctx(request: Request, user: Optional[dict] = None, **extra) -> dict:
    return {"request": request, "user": user, "doctor_name": os.environ.get("DOCTOR_NAME", "Dr. Shahzad Khan"), **extra}

# ------------------------------------------------------------------ startup
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- startup ---
    await db.users.create_index("email", unique=True)
    await db.appointments.create_index([("date", 1), ("time_slot", 1)])
    await db.appointments.create_index("patient_id")
    await db.treatments.create_index("patient_id")

    # Seed doctor account
    doctor_email = os.environ["DOCTOR_EMAIL"].lower()
    existing = await db.users.find_one({"email": doctor_email})
    if existing is None:
        await db.users.insert_one({
            "email": doctor_email,
            "password_hash": hash_password(os.environ["DOCTOR_PASSWORD"]),
            "name": os.environ["DOCTOR_NAME"],
            "role": "doctor",
            "phone": "",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        logger.info("Seeded doctor account: %s", doctor_email)
    elif not verify_password(os.environ["DOCTOR_PASSWORD"], existing["password_hash"]):
        await db.users.update_one(
            {"email": doctor_email},
            {"$set": {"password_hash": hash_password(os.environ["DOCTOR_PASSWORD"])}},
        )

    yield

    # --- shutdown ---
    client.close()

app.router.lifespan_context = lifespan

# ------------------------------------------------------------------ public pages
@api_router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user = await current_user(request)
    return templates.TemplateResponse(request, "home.html", ctx(request, user))

@api_router.get("/about", response_class=HTMLResponse)
async def about(request: Request):
    user = await current_user(request)
    return templates.TemplateResponse(request, "about.html", ctx(request, user))

# ------------------------------------------------------------------ auth
@api_router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = await current_user(request)
    if user:
        return RedirectResponse(url="/api/doctor" if user["role"] == "doctor" else "/api/my-appointments", status_code=303)
    return templates.TemplateResponse(request, "login.html", ctx(request, None, error=None))

@api_router.post("/login")
async def login_submit(request: Request, email: str = Form(...), password: str = Form(...)):
    email = email.strip().lower()
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(password, user["password_hash"]):
        return templates.TemplateResponse(
            request,
            "login.html",
            ctx(request, None, error="Invalid email or password."),
            status_code=400,
        )
    token = create_token(str(user["_id"]), user["role"])
    target = "/api/doctor" if user["role"] == "doctor" else "/api/my-appointments"
    resp = RedirectResponse(url=target, status_code=303)
    set_auth_cookie(resp, token)
    return resp

@api_router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    user = await current_user(request)
    if user:
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
            request,
            "register.html",
            ctx(request, None, error="Password must be at least 6 characters."),
            status_code=400,
        )
    existing = await db.users.find_one({"email": email})
    if existing:
        return templates.TemplateResponse(
            request,
            "register.html",
            ctx(request, None, error="An account with this email already exists."),
            status_code=400,
        )
    doc = {
        "email": email,
        "name": name,
        "phone": phone.strip(),
        "password_hash": hash_password(password),
        "role": "patient",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    result = await db.users.insert_one(doc)
    token = create_token(str(result.inserted_id), "patient")
    resp = RedirectResponse(url="/api/my-appointments", status_code=303)
    set_auth_cookie(resp, token)
    return resp

@api_router.post("/logout")
async def logout():
    resp = RedirectResponse(url="/api/", status_code=303)
    clear_auth_cookie(resp)
    return resp

# ------------------------------------------------------------------ patient
@api_router.get("/book", response_class=HTMLResponse)
async def book_page(request: Request, date_str: Optional[str] = None):
    user = await require_patient(request)
    # Compute available slots for a chosen date (default: tomorrow)
    selected_date = date_str or (date.today() + timedelta(days=1)).isoformat()
    booked = await db.appointments.find(
        {"date": selected_date, "status": {"$ne": "cancelled"}}
    ).to_list(200)
    taken = {a["time_slot"] for a in booked}
    slots = [{"time": s, "available": s not in taken} for s in TIME_SLOTS]
    min_date = date.today().isoformat()
    return templates.TemplateResponse(
        request,
        "book.html",
        ctx(request, user, slots=slots, selected_date=selected_date, min_date=min_date, error=None),
    )

@api_router.post("/book")
async def book_submit(
    request: Request,
    date_str: str = Form(..., alias="date"),
    time_slot: str = Form(...),
    reason: str = Form(...),
):
    user = await require_patient(request)
    # Validate date not in past
    try:
        req_date = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date")
    if req_date < date.today():
        return RedirectResponse(url=f"/api/book?date_str={date_str}", status_code=303)
    if time_slot not in TIME_SLOTS:
        raise HTTPException(status_code=400, detail="Invalid time slot")
    # Ensure slot is still free
    conflict = await db.appointments.find_one({
        "date": date_str, "time_slot": time_slot, "status": {"$ne": "cancelled"},
    })
    if conflict:
        return RedirectResponse(url=f"/api/book?date_str={date_str}", status_code=303)
    await db.appointments.insert_one({
        "patient_id": user["id"],
        "patient_name": user["name"],
        "patient_email": user["email"],
        "patient_phone": user.get("phone", ""),
        "date": date_str,
        "time_slot": time_slot,
        "reason": reason.strip(),
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return RedirectResponse(url="/api/my-appointments", status_code=303)

@api_router.get("/my-appointments", response_class=HTMLResponse)
async def my_appointments(request: Request):
    user = await require_patient(request)
    appts = await db.appointments.find({"patient_id": user["id"]}).sort("date", -1).to_list(200)
    for a in appts:
        a["id"] = str(a["_id"])
        a.pop("_id", None)
    # Also load own treatment history
    treatments = await db.treatments.find({"patient_id": user["id"]}).sort("date", -1).to_list(200)
    for t in treatments:
        t["id"] = str(t["_id"])
        t.pop("_id", None)
    return templates.TemplateResponse(
        request, "my_appointments.html", ctx(request, user, appointments=appts, treatments=treatments),
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

# ------------------------------------------------------------------ doctor
@api_router.get("/doctor", response_class=HTMLResponse)
async def doctor_dashboard(request: Request, filter: str = "upcoming"):
    doctor = await require_doctor(request)
    today = date.today().isoformat()
    if filter == "today":
        query = {"date": today, "status": {"$ne": "cancelled"}}
    elif filter == "past":
        query = {"date": {"$lt": today}}
    elif filter == "all":
        query = {}
    else:  # upcoming
        query = {"date": {"$gte": today}, "status": {"$ne": "cancelled"}}
    appts = await db.appointments.find(query).sort([("date", 1), ("time_slot", 1)]).to_list(500)
    for a in appts:
        a["id"] = str(a["_id"])
        a.pop("_id", None)
    # Stats
    total_patients = await db.users.count_documents({"role": "patient"})
    total_today = await db.appointments.count_documents({"date": today, "status": {"$ne": "cancelled"}})
    total_pending = await db.appointments.count_documents({"status": "pending"})
    total_completed = await db.appointments.count_documents({"status": "completed"})
    return templates.TemplateResponse(
        request,
        "doctor_dashboard.html",
        ctx(
            request, doctor,
            appointments=appts, active_filter=filter,
            stats={"patients": total_patients, "today": total_today,
                   "pending": total_pending, "completed": total_completed},
        ),
    )

@api_router.post("/doctor/appointment/{appt_id}/status")
async def update_appt_status(request: Request, appt_id: str, new_status: str = Form(...)):
    await require_doctor(request)
    if new_status not in {"pending", "confirmed", "completed", "cancelled"}:
        raise HTTPException(status_code=400, detail="Invalid status")
    try:
        oid = ObjectId(appt_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Not found")
    await db.appointments.update_one({"_id": oid}, {"$set": {"status": new_status}})
    referer = request.headers.get("referer", "/api/doctor")
    return RedirectResponse(url=referer, status_code=303)

@api_router.get("/doctor/patients", response_class=HTMLResponse)
async def doctor_patients(request: Request, q: str = ""):
    doctor = await require_doctor(request)
    query = {"role": "patient"}
    if q:
        query["$or"] = [
            {"name": {"$regex": q, "$options": "i"}},
            {"email": {"$regex": q, "$options": "i"}},
            {"phone": {"$regex": q, "$options": "i"}},
        ]
    patients = await db.users.find(query).sort("name", 1).to_list(500)
    result = []
    for p in patients:
        pid = str(p["_id"])
        visits = await db.appointments.count_documents({"patient_id": pid, "status": "completed"})
        upcoming = await db.appointments.count_documents({
            "patient_id": pid, "date": {"$gte": date.today().isoformat()},
            "status": {"$in": ["pending", "confirmed"]},
        })
        result.append({
            "id": pid, "name": p["name"], "email": p["email"],
            "phone": p.get("phone", ""), "visits": visits, "upcoming": upcoming,
        })
    return templates.TemplateResponse(
        request, "doctor_patients.html", ctx(request, doctor, patients=result, q=q),
    )

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
    appts = await db.appointments.find({"patient_id": patient_id}).sort([("date", -1)]).to_list(200)
    for a in appts:
        a["id"] = str(a["_id"])
        a.pop("_id", None)
    treatments = await db.treatments.find({"patient_id": patient_id}).sort("date", -1).to_list(200)
    for t in treatments:
        t["id"] = str(t["_id"])
        t.pop("_id", None)
    return templates.TemplateResponse(
        request,
        "patient_detail.html",
        ctx(request, doctor, patient=patient, appointments=appts, treatments=treatments),
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
    doctor = await require_doctor(request)
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
        "doctor_name": doctor["name"],
        "diagnosis": diagnosis.strip(),
        "treatment": treatment.strip(),
        "notes": notes.strip(),
        "cost": cost_val,
        "date": date.today().isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return RedirectResponse(url=f"/api/doctor/patient/{patient_id}", status_code=303)

# ------------------------------------------------------------------ health
@api_router.get("/health")
async def health():
    return {"status": "ok"}

# ------------------------------------------------------------------ mount
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Redirect the root URL to /api/ so bare domain works
@app.get("/")
async def _root():
    return RedirectResponse(url="/api/", status_code=302)

# Custom 302 exception handler for auth redirects raised inside dependencies
from fastapi.exceptions import HTTPException as _HTTPException
from starlette.responses import RedirectResponse as _RR
@app.exception_handler(_HTTPException)
async def _redirect_handler(request: Request, exc: _HTTPException):
    if exc.status_code == 302 and exc.headers and "Location" in exc.headers:
        return _RR(url=exc.headers["Location"], status_code=303)
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
