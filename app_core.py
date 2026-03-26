import math
import os
from collections import defaultdict
from datetime import date, datetime
from functools import wraps
from pathlib import Path

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash

from models import (
    Attendance,
    AttendanceSession,
    AttendanceRequest,
    TeacherLocationHistory,
    Semester,
    Student,
    Subject,
    SystemSetting,
    Teacher,
    TeacherSubjectMap,
    User,
    db,
    init_db,
)

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
INSTANCE_DIR.mkdir(exist_ok=True)

app = Flask(__name__, instance_path=str(INSTANCE_DIR))
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-secret-key")

# Use PostgreSQL in production (Render), SQLite in development
database_url = os.environ.get("DATABASE_URL")
if database_url and database_url.startswith("postgres://"):
    # Fix deprecated postgresql:// scheme
    database_url = database_url.replace("postgres://", "postgresql://", 1)

default_sqlite_path = (INSTANCE_DIR / "laas.db").resolve().as_posix()
app.config["SQLALCHEMY_DATABASE_URI"] = database_url or f"sqlite:///{default_sqlite_path}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

db.init_app(app)
# Ensure schema migrations/seed run for both `python app.py` and `flask run`.
init_db(app)

ALLOWED_SEMESTER_NAMES = [f"Semester {i}" for i in range(1, 7)]
ALLOWED_ROLES = {"admin", "teacher", "student"}

# Attendance geofence/GPS tuning
# - Allowed radius: 100m base rule
# - Accuracy threshold: accept fixes within <= 300m (GPS can vary)
# - Accuracy tolerance: add a modest buffer so valid nearby users are not
#   rejected just because the phone reported a noisy but still acceptable fix.
ATTENDANCE_ALLOWED_RADIUS_M = 100.0
GPS_ACCURACY_ACCEPT_MAX_M = 300.0
ATTENDANCE_MAX_ACCURACY_TOLERANCE_M = 50.0


@app.context_processor
def inject_laas_logo():
    candidates = [
        # "img/bareilly-college-logo.jpg",
        # "img/bareilly-college-logo.jpeg",
        # "img/bareilly-college-logo.png",
        # "img/bareilly-college-logo.webp",
        # "img/bareilly-college-logo.svg",
        "img/laas-logo.jpg",
        "img/laas-logo.jpeg",
        "img/laas-logo.png",
        "img/laas-logo.webp",
    ]
    for rel_path in candidates:
        abs_path = os.path.join(app.static_folder, rel_path.replace("/", os.sep))
        if os.path.exists(abs_path):
            return {"laas_logo": url_for("static", filename=rel_path)}
    return {"laas_logo": ""}


@app.template_filter("fmt_datetime")
def fmt_datetime(value):
    """Format generic dashboard datetimes consistently."""
    if not value:
        return ""
    try:
        return value.strftime("%d/%m/%Y %I:%M %p")
    except Exception:
        return str(value)


@app.template_filter("fmt_date")
def fmt_date(value):
    """Format date-only values for views that should not show time."""
    if not value:
        return ""
    try:
        return value.strftime("%d/%m/%Y")
    except Exception:
        return str(value)


@app.after_request
def add_no_cache_headers(response):
    if response.mimetype in {"text/html", "application/javascript", "text/css"}:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


def login_required(role=None):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if "user_id" not in session:
                flash("Please login first.", "error")
                return redirect(url_for("login"))
            if role:
                current_role = session.get("role")
                if current_role not in ALLOWED_ROLES:
                    session.clear()
                    flash("Invalid account role. Please login again.", "error")
                    return redirect(url_for("login"))
                allowed_roles = role if isinstance(role, (list, tuple, set)) else [role]
                if current_role not in allowed_roles:
                    flash("You are not authorized for this page.", "error")
                    return redirect(url_for("login"))
                if current_role == "student":
                    student = Student.query.filter_by(user_id=session.get("user_id")).first()
                    if not student or not student.verified:
                        session.clear()
                        flash("Your student account is not verified. Please contact the admin.", "error")
                        return redirect(url_for("login"))
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def haversine_meters(lat1, lon1, lat2, lon2):
    r = 6371000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return r * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))


def get_client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or ""


def get_device_fingerprint():
    user_agent = request.headers.get("User-Agent", "")
    client_ip = get_client_ip()
    return f"{user_agent}|{client_ip}"


def role_dashboard(role):
    if role == "admin":
        return "admin_dashboard"
    if role == "teacher":
        return "teacher_dashboard"
    if role == "student":
        return "student_dashboard"
    return None


def block_registration_for_logged_in_user():
    """Prevent already logged-in users from creating another account."""
    user_id = session.get("user_id")
    if not user_id:
        return None

    user = db.session.get(User, user_id)
    if not user:
        session.clear()
        flash("Session expired. Please login again.", "error")
        return redirect(url_for("login"))

    dashboard = role_dashboard(user.role)
    flash(
        f"You are already registered as {user.role.capitalize()}. Multiple accounts are not allowed.",
        "error",
    )
    if dashboard:
        return redirect(url_for(dashboard))
    return redirect(url_for("login"))


def get_current_semester():
    setting = SystemSetting.query.filter_by(key="current_semester_id").first()
    if not setting:
        return None
    return db.session.get(Semester, int(setting.value))


def normalize_gmail_email(raw_email):
    value = (raw_email or "").strip().lower()
    if not value:
        return None, "Email is required."

    if "@" in value:
        local, domain = value.split("@", 1)
        if not local:
            return None, "Email username is required."
        if domain != "gmail.com":
            return None, "Only @gmail.com email is allowed."
        return f"{local}@gmail.com", None

    return f"{value}@gmail.com", None


def get_allowed_semesters():
    return Semester.query.filter(Semester.name.in_(ALLOWED_SEMESTER_NAMES)).order_by(Semester.id).all()


def is_admin_registration_locked():
    setting = SystemSetting.query.filter_by(key="admin_active_lock").first()
    return bool(setting and setting.value == "1")


def set_admin_registration_lock(is_locked):
    setting = SystemSetting.query.filter_by(key="admin_active_lock").first()
    value = "1" if is_locked else "0"
    if setting:
        setting.value = value
    else:
        setting = SystemSetting(key="admin_active_lock", value=value)
        db.session.add(setting)
    db.session.commit()


def is_student_registration_open():
    setting = SystemSetting.query.filter_by(key="student_registration_open").first()
    return bool(setting and setting.value == "1")


def set_student_registration_open(is_open):
    setting = SystemSetting.query.filter_by(key="student_registration_open").first()
    value = "1" if is_open else "0"
    if setting:
        setting.value = value
    else:
        setting = SystemSetting(key="student_registration_open", value=value)
        db.session.add(setting)
    db.session.commit()


def get_default_attendance_location():
    lat_setting = SystemSetting.query.filter_by(key="attendance_center_lat").first()
    lng_setting = SystemSetting.query.filter_by(key="attendance_center_lng").first()
    if not lat_setting or not lng_setting:
        return None
    try:
        lat = float(lat_setting.value)
        lng = float(lng_setting.value)
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return {"latitude": lat, "longitude": lng}


def set_default_attendance_location(latitude, longitude):
    lat_setting = SystemSetting.query.filter_by(key="attendance_center_lat").first()
    lng_setting = SystemSetting.query.filter_by(key="attendance_center_lng").first()
    lat_value = f"{latitude:.6f}"
    lng_value = f"{longitude:.6f}"
    if lat_setting:
        lat_setting.value = lat_value
    else:
        db.session.add(SystemSetting(key="attendance_center_lat", value=lat_value))
    if lng_setting:
        lng_setting.value = lng_value
    else:
        db.session.add(SystemSetting(key="attendance_center_lng", value=lng_value))
    db.session.commit()

def get_current_academic_session():
    setting = SystemSetting.query.filter_by(key="current_academic_session").first()
    return setting.value if setting else None


