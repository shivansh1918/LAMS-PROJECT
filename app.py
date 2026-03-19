import math
import os
from collections import defaultdict
from datetime import date, datetime
from functools import wraps

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


app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-secret-key")

# Use PostgreSQL in production (Render), SQLite in development
database_url = os.environ.get("DATABASE_URL")
if database_url and database_url.startswith("postgres://"):
    # Fix deprecated postgresql:// scheme
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url or "sqlite:///laas.db"
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


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/home")
def home():
    return redirect(url_for("index"))


@app.route("/register/student", methods=["GET", "POST"])
def register_student():
    blocked = block_registration_for_logged_in_user()
    if blocked:
        return blocked

    semesters = get_allowed_semesters()
    registration_open = is_student_registration_open()
    if not registration_open:
        flash("Student registration is currently closed. Please contact the admin.", "error")
        return render_template("register_student.html", semesters=semesters)

    current_session = get_current_academic_session()
    if not current_session:
        flash(
            "Registration is not available. Please wait for admin to start a new session",
            "error",
        )
        return render_template("register_student.html", semesters=semesters)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email_raw = request.form.get("email", "").strip() or request.form.get("email_username", "").strip()
        password = request.form.get("password", "")
        roll_no = request.form.get("roll_no", "").strip().upper()
        semester_raw = request.form.get("semester_id", "").strip()
        try:
            semester_id = int(semester_raw) if semester_raw else None
        except (TypeError, ValueError):
            semester_id = None

        email, email_error = normalize_gmail_email(email_raw)
        if email_error:
            flash(email_error, "error")
            return render_template("register_student.html", semesters=semesters)

        missing = []
        if not name:
            missing.append("Name")
        if not email:
            missing.append("Email")
        if not password:
            missing.append("Password")
        if not roll_no:
            missing.append("Roll Number")
        if not semester_id:
            missing.append("Semester")
        if missing:
            flash(f"Missing required fields: {', '.join(missing)}.", "error")
            return render_template("register_student.html", semesters=semesters)

        semester = db.session.get(Semester, semester_id)
        if not semester:
            flash("Invalid semester selected.", "error")
            return render_template("register_student.html", semesters=semesters)
        if semester.name not in ALLOWED_SEMESTER_NAMES:
            flash("Only fixed semesters are allowed.", "error")
            return render_template("register_student.html", semesters=semesters)

        existing_user = User.query.filter_by(email=email).first()
        existing_roll = Student.query.filter_by(roll_no=roll_no).first()

        # Existing student account: keep same email and update semester.
        if existing_user:
            if existing_user.role != "student":
                flash("This email is already used by another role.", "error")
                return render_template("register_student.html", semesters=semesters)

            existing_student = Student.query.filter_by(user_id=existing_user.id).first()
            if not existing_student:
                flash("Student profile missing for this email.", "error")
                return render_template("register_student.html", semesters=semesters)
            if existing_student.roll_no != roll_no:
                flash("Roll number does not match this existing student account.", "error")
                return render_template("register_student.html", semesters=semesters)

            existing_user.name = name
            existing_user.password = generate_password_hash(password)
            existing_user.email_verified = True
            existing_student.semester_id = semester_id
            db.session.commit()
            flash("Registration updated successfully. Semester changed.", "success")
        else:
            if existing_roll:
                flash("Roll number already registered with another email.", "error")
                return render_template("register_student.html", semesters=semesters)

            user = User(
                role="student",
                name=name,
                email=email,
                password=generate_password_hash(password),
                email_verified=True,
            )
            db.session.add(user)
            db.session.flush()
            db.session.add(
                Student(
                    user_id=user.id,
                    roll_no=roll_no,
                    mobile_no=None,
                    semester_id=semester_id,
                    verified=False,
                )
            )
            db.session.commit()
            flash("Student registration successful. Please wait for admin verification.", "success")

        # Auto-fill next login form.
        session["prefill_login_email"] = email
        session["prefill_login_password"] = password
        session["prefill_login_role"] = "student"
        return redirect(url_for("login"))

    return render_template("register_student.html", semesters=semesters)


@app.route("/register/admin", methods=["GET", "POST"])
def register_admin():
    blocked = block_registration_for_logged_in_user()
    if blocked:
        return blocked

    if request.method == "POST":
        admin_exists = User.query.filter_by(role="admin").first() is not None
        if admin_exists:
            flash(
                "Admin already registered. Multiple administrator accounts are not permitted.",
                "error",
            )
            return redirect(url_for("login"))

        name = request.form.get("name", "").strip()
        email_raw = request.form.get("email", "").strip() or request.form.get("email_username", "").strip()
        password = request.form.get("password", "")
        department = request.form.get("department", "").strip()

        email, email_error = normalize_gmail_email(email_raw)
        if email_error:
            flash(email_error, "error")
            return render_template("register_admin.html")

        if is_admin_registration_locked():
            flash(
                "Admin already registered. Multiple administrator accounts are not permitted.",
                "error",
            )
            return redirect(url_for("login"))

        if not all([name, email, password, department]):
            flash("All fields are required.", "error")
            return render_template("register_admin.html")

        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            if existing_user.role == "admin":
                flash(
                    "Admin already registered. Multiple administrator accounts are not permitted.",
                    "error",
                )
            else:
                flash("Email is already registered.", "error")
            return render_template("register_admin.html")

        user = User(
            role="admin",
            name=name,
            email=email,
            password=generate_password_hash(password),
            email_verified=True,
            department=department,
        )
        db.session.add(user)
        db.session.commit()

        set_admin_registration_lock(True)
        # Redirect to common login with auto-filled credentials.
        session["prefill_login_email"] = email
        session["prefill_login_password"] = password
        session["prefill_login_role"] = "admin"
        flash("Admin account created successfully. Please login.", "success")
        return redirect(url_for("login"))

    return render_template("register_admin.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "").strip().lower()

        if not all([email, password, role]):
            flash("Email, password and role are required.", "error")
            return render_template(
                "login.html",
                prefill_email=email,
                prefill_password=password,
                prefill_role=role,
            )
        if role not in ALLOWED_ROLES:
            flash("Invalid role selection.", "error")
            return render_template(
                "login.html",
                prefill_email=email,
                prefill_password=password,
                prefill_role="",
            )

        user = User.query.filter_by(email=email, role=role).first()
        if not user or not check_password_hash(user.password, password):
            flash("Invalid credentials.", "error")
            return render_template(
                "login.html",
                prefill_email=email,
                prefill_password=password,
                prefill_role=role,
            )
        if user.role not in ALLOWED_ROLES:
            flash("This account role is no longer supported.", "error")
            return render_template(
                "login.html",
                prefill_email=email,
                prefill_password="",
                prefill_role="",
            )

        if role == "student":
            student = Student.query.filter_by(user_id=user.id).first()
            if not student or not student.verified:
                flash("Your student account is not verified. Please contact the admin.", "error")
                return render_template(
                    "login.html",
                    prefill_email=email,
                    prefill_password=password,
                    prefill_role=role,
                )
        session.clear()
        # Track last successful login for dashboard visibility.
        user.last_login_at = datetime.now()
        db.session.commit()
        session["user_id"] = user.id
        session["role"] = user.role
        session["name"] = user.name
        if user.role == "admin":
            set_admin_registration_lock(True)
        dashboard = role_dashboard(user.role)
        if not dashboard:
            session.clear()
            flash("Unsupported account role.", "error")
            return redirect(url_for("login"))
        return redirect(url_for(dashboard))

    return render_template(
        "login.html",
        prefill_email=session.pop("prefill_login_email", ""),
        prefill_password=session.pop("prefill_login_password", ""),
        prefill_role=session.pop("prefill_login_role", ""),
    )


@app.route("/logout")
def logout():
    current_role = session.get("role")
    if current_role == "admin":
        set_admin_registration_lock(False)
    session.clear()
    flash("Logged out successfully.", "success")
    return redirect(url_for("login"))


@app.route("/student/dashboard")
@login_required(role="student")
def student_dashboard():
    student = Student.query.filter_by(user_id=session["user_id"]).first_or_404()
    user = db.session.get(User, session["user_id"])
    semester = db.session.get(Semester, student.semester_id)
    semesters = get_allowed_semesters()

    selected_semester_id = request.args.get("semester_id", type=int) or student.semester_id
    allowed_semester_ids = {sem.id for sem in semesters}
    if selected_semester_id not in allowed_semester_ids:
        selected_semester_id = student.semester_id
    selected_semester = db.session.get(Semester, selected_semester_id)

    subjects = (
        Subject.query.filter_by(semester_id=selected_semester_id, status=True)
        .order_by(Subject.name)
        .all()
    )

    active_sessions = (
        db.session.query(AttendanceSession, Subject, User)
        .join(Subject, Subject.id == AttendanceSession.subject_id)
        .join(Teacher, Teacher.id == AttendanceSession.teacher_id)
        .join(User, User.id == Teacher.user_id)
        .filter(
            AttendanceSession.is_active.is_(True),
            AttendanceSession.semester_id == selected_semester_id,
            Subject.status.is_(True),
        )
        .order_by(AttendanceSession.start_time.desc())
        .all()
    )
    active_session_by_subject = {}
    for sess, subject, teacher_user in active_sessions:
        if subject.id not in active_session_by_subject:
            active_session_by_subject[subject.id] = (sess, teacher_user)

    marked_session_ids = {
        row[0]
        for row in db.session.query(Attendance.session_id)
        .filter(Attendance.student_id == student.id)
        .all()
    }
    attendance_request_status_by_session = {
        row.session_id: row.status
        for row in AttendanceRequest.query.filter_by(student_id=student.id).all()
    }

    subject_percentages = []

    for subject in subjects:
        total_sessions = AttendanceSession.query.filter_by(subject_id=subject.id).count()
        present = Attendance.query.filter_by(student_id=student.id, subject_id=subject.id).count()
        percentage = round((present / total_sessions) * 100, 2) if total_sessions else 0
        subject_percentages.append(
            {
                "subject": subject,
                "present": present,
                "total": total_sessions,
                "percentage": percentage,
            }
        )

    overall_total_sessions = AttendanceSession.query.filter_by(semester_id=student.semester_id).count()
    overall_present = (
        db.session.query(func.count(Attendance.id))
        .join(Subject, Subject.id == Attendance.subject_id)
        .filter(Attendance.student_id == student.id, Subject.semester_id == student.semester_id)
        .scalar()
        or 0
    )
    overall_percentage = (
        round((overall_present / overall_total_sessions) * 100, 2)
        if overall_total_sessions
        else 0
    )

    # Show semester-wise report only from this student's own attendance history.
    # This avoids unrelated zero rows from semesters where the student has no records.
    semester_percentage_report = []
    my_semesters = (
        db.session.query(Semester.id, Semester.name)
        .join(Subject, Subject.semester_id == Semester.id)
        .join(Attendance, Attendance.subject_id == Subject.id)
        .filter(Attendance.student_id == student.id)
        .group_by(Semester.id, Semester.name)
        .order_by(Semester.id.asc())
        .all()
    )
    for sem_id, sem_name in my_semesters:
        total_sem_sessions = (
            db.session.query(func.count(AttendanceSession.id))
            .filter(AttendanceSession.semester_id == sem_id)
            .scalar()
            or 0
        )
        present_sem = (
            db.session.query(func.count(Attendance.id))
            .join(Subject, Subject.id == Attendance.subject_id)
            .filter(Attendance.student_id == student.id, Subject.semester_id == sem_id)
            .scalar()
            or 0
        )
        sem_percentage = round((present_sem / total_sem_sessions) * 100, 2) if total_sem_sessions else 0
        semester_percentage_report.append(
            {
                "semester": sem_name,
                "present": present_sem,
                "total": total_sem_sessions,
                "percentage": sem_percentage,
            }
        )

    return render_template(
        "student_dashboard.html",
        user=user,
        student=student,
        semester=semester,
        semesters=semesters,
        selected_semester=selected_semester,
        subjects=subjects,
        active_session_by_subject=active_session_by_subject,
        marked_session_ids=marked_session_ids,
        attendance_request_status_by_session=attendance_request_status_by_session,
        subject_percentages=subject_percentages,
        overall_percentage=overall_percentage,
        semester_percentage_report=semester_percentage_report,
    )


@app.route("/teacher/dashboard")
@login_required(role="teacher")
def teacher_dashboard():
    teacher = Teacher.query.filter_by(user_id=session["user_id"]).first_or_404()
    user = db.session.get(User, session["user_id"])
    semesters = get_allowed_semesters()

    # Teachers can start sessions only for subjects assigned by admin.
    teacher_subject_ids = {
        row.subject_id for row in TeacherSubjectMap.query.filter_by(teacher_id=teacher.id).all()
    }
    subjects_query = (
        db.session.query(Subject, Semester)
        .join(Semester, Semester.id == Subject.semester_id)
        .filter(Subject.status.is_(True))
    )
    if teacher_subject_ids:
        subjects_query = subjects_query.filter(Subject.id.in_(teacher_subject_ids))
    else:
        # No admin mapping means no startable subjects for this teacher.
        subjects_query = subjects_query.filter(Subject.id == -1)
    subjects = subjects_query.order_by(Semester.id, Subject.name).all()
    teacher_semester_ids = {sem.id for _, sem in subjects}

    active_sessions = (
        db.session.query(AttendanceSession, Subject, Semester)
        .join(Subject, Subject.id == AttendanceSession.subject_id)
        .join(Semester, Semester.id == AttendanceSession.semester_id)
        .filter(
            AttendanceSession.teacher_id == teacher.id,
            AttendanceSession.is_active.is_(True),
        )
        .order_by(AttendanceSession.start_time.desc())
        .all()
    )

    pending_requests_rows = (
        db.session.query(AttendanceRequest, Student, User, AttendanceSession, Subject, Semester)
        .join(Student, Student.id == AttendanceRequest.student_id)
        .join(User, User.id == Student.user_id)
        .join(AttendanceSession, AttendanceSession.id == AttendanceRequest.session_id)
        .join(Subject, Subject.id == AttendanceRequest.subject_id)
        .join(Semester, Semester.id == AttendanceSession.semester_id)
        .filter(
            AttendanceSession.teacher_id == teacher.id,
            AttendanceRequest.status == "pending",
        )
        .order_by(AttendanceRequest.requested_at.desc())
        .all()
    )
    pending_requests = [
        {
            "id": req.id,
            "student_name": user_row.name,
            "roll_no": student.roll_no,
            "requested_at": req.requested_at,
            "latitude": req.latitude,
            "longitude": req.longitude,
            "accuracy": req.accuracy,
            "subject": subject.name,
            "semester": semester.name,
        }
        for req, student, user_row, session_row, subject, semester in pending_requests_rows
    ]

    student_filter_semester = request.args.get("student_semester_id", type=int)
    allowed_semester_ids = {sem.id for sem in semesters}
    if student_filter_semester and student_filter_semester not in allowed_semester_ids:
        student_filter_semester = None

    show_status = request.args.get("show_status", "").strip() == "1"
    teacher_students_query = (
        db.session.query(Student, User, Semester)
        .join(User, User.id == Student.user_id)
        .join(Semester, Semester.id == Student.semester_id)
    )
    if student_filter_semester:
        if student_filter_semester in teacher_semester_ids:
            teacher_students_query = teacher_students_query.filter(Student.semester_id == student_filter_semester)
        else:
            # Semester is selectable in UI, but show no students unless the teacher has subjects mapped in it.
            teacher_students_query = teacher_students_query.filter(Student.id == -1)
    else:
        if teacher_semester_ids:
            teacher_students_query = teacher_students_query.filter(Student.semester_id.in_(teacher_semester_ids))
        else:
            teacher_students_query = teacher_students_query.filter(Student.id == -1)
    teacher_students = teacher_students_query.order_by(func.lower(User.name).asc(), Student.roll_no.asc()).all()
    attendance_date_param = (request.args.get("attendance_date") or "").strip()
    attendance_date_selected = date.today()
    if attendance_date_param:
        try:
            attendance_date_selected = date.fromisoformat(attendance_date_param)
        except ValueError:
            attendance_date_selected = date.today()

    session_counts_by_semester = dict(
        db.session.query(AttendanceSession.semester_id, func.count(AttendanceSession.id))
        .filter(AttendanceSession.teacher_id == teacher.id)
        .group_by(AttendanceSession.semester_id)
        .all()
    )
    attendance_counts_by_student = dict(
        db.session.query(Attendance.student_id, func.count(Attendance.id))
        .join(AttendanceSession, AttendanceSession.id == Attendance.session_id)
        .filter(AttendanceSession.teacher_id == teacher.id)
        .group_by(Attendance.student_id)
        .all()
    )

    attendance_by_student_on_date = defaultdict(list)
    if show_status:
        attendance_today_query = (
            db.session.query(
                Attendance.student_id,
                Attendance.time,
                Subject.name.label("subject_name"),
            )
            .join(AttendanceSession, AttendanceSession.id == Attendance.session_id)
            .join(Subject, Subject.id == Attendance.subject_id)
            .filter(
                AttendanceSession.teacher_id == teacher.id,
                Attendance.date == attendance_date_selected,
            )
        )

        attendance_today_records = attendance_today_query.order_by(Attendance.time.asc()).all()
        for student_id, attendance_time, subject_name in attendance_today_records:
            formatted_time = (
                attendance_time.strftime("%I:%M %p") if attendance_time else ""
            )
            attendance_by_student_on_date[student_id].append(
                {"subject": subject_name, "time": formatted_time}
            )

    student_attendance_summary = []
    for student, student_user, sem in teacher_students:
        total_sessions = session_counts_by_semester.get(sem.id, 0)
        present_sessions = attendance_counts_by_student.get(student.id, 0)
        percentage = round((present_sessions / total_sessions) * 100, 2) if total_sessions else 0
        student_attendance_summary.append(
            {
                "student": student,
                "user": student_user,
                "semester": sem,
                "percentage": percentage,
                "daily_records": attendance_by_student_on_date.get(student.id, []),
            }
        )
    student_attendance_calendar = []
    attendance_present_count = 0
    attendance_absent_count = 0
    total_students_shown = len(student_attendance_summary)
    if show_status:
        student_attendance_calendar = sorted(
            student_attendance_summary,
            key=lambda row: (
                0 if row["daily_records"] else 1,
                row["semester"].id,
                (row["user"].name or "").strip().lower(),
                (row["student"].roll_no or "").strip().upper(),
            ),
        )
        attendance_present_count = sum(1 for row in student_attendance_summary if row["daily_records"])
        attendance_absent_count = total_students_shown - attendance_present_count

    start_subject_map = {}
    for sem in semesters:
        sem_subjects = [subject for subject, s in subjects if s.id == sem.id]
        start_subject_map[str(sem.id)] = [{"id": subject.id, "name": subject.name} for subject in sem_subjects]

    default_location = get_default_attendance_location()

    return render_template(
        "teacher_dashboard.html",
        user=user,
        start_subject_map=start_subject_map,
        semesters=semesters,
        active_sessions=active_sessions,
        teacher_students=teacher_students,
        student_filter_semester=student_filter_semester,
        student_attendance_summary=student_attendance_summary,
        student_attendance_calendar=student_attendance_calendar,
        attendance_date_iso=attendance_date_selected.isoformat(),
        attendance_date_display=attendance_date_selected.strftime("%d/%m/%Y"),
        attendance_date_parts={
            "day": attendance_date_selected.day,
            "month": attendance_date_selected.strftime("%B"),
            "year": attendance_date_selected.year,
        },
        attendance_present_count=attendance_present_count,
        attendance_absent_count=attendance_absent_count,
        attendance_total_students=total_students_shown if show_status else 0,
        teacher=teacher,
        show_status=show_status,
        pending_requests=pending_requests,
        default_attendance_location=default_location,
    )


@app.post("/teacher/attendance/<int:attendance_id>/verify")
@login_required(role="teacher")
def verify_teacher_marked_attendance(attendance_id):
    teacher = Teacher.query.filter_by(user_id=session["user_id"]).first_or_404()
    record = (
        db.session.query(Attendance)
        .join(AttendanceSession, AttendanceSession.id == Attendance.session_id)
        .filter(
            Attendance.id == attendance_id,
            AttendanceSession.teacher_id == teacher.id,
        )
        .first()
    )

    if not record:
        flash("Attendance record not found for your sessions.", "error")
        return redirect(url_for("teacher_dashboard"))

    record.teacher_verified = True
    db.session.commit()
    flash("Attendance verified successfully.", "success")
    return redirect(url_for("teacher_dashboard"))


def _get_teacher_attendance_request(teacher_id, request_id):
    return (
        db.session.query(AttendanceRequest, AttendanceSession)
        .join(AttendanceSession, AttendanceSession.id == AttendanceRequest.session_id)
        .filter(
            AttendanceRequest.id == request_id,
            AttendanceSession.teacher_id == teacher_id,
        )
        .first()
    )


@app.post("/teacher/attendance-request/<int:request_id>/accept")
@login_required(role="teacher")
def accept_attendance_request(request_id):
    teacher = Teacher.query.filter_by(user_id=session["user_id"]).first_or_404()
    row = _get_teacher_attendance_request(teacher.id, request_id)
    if not row:
        flash("Attendance request not found for your sessions.", "error")
        return redirect(url_for("teacher_dashboard"))

    attendance_request, session_row = row
    if attendance_request.status != "pending":
        flash("Attendance request already processed.", "error")
        return redirect(url_for("teacher_dashboard"))

    student = db.session.get(Student, attendance_request.student_id)
    if not student or not student.verified:
        attendance_request.status = "rejected"
        db.session.commit()
        flash("Student is not verified. Request rejected.", "error")
        return redirect(url_for("teacher_dashboard"))

    existing = Attendance.query.filter_by(
        student_id=student.id,
        session_id=attendance_request.session_id,
    ).first()
    if not existing:
        req_dt = attendance_request.requested_at or datetime.now()
        lat = attendance_request.latitude
        lng = attendance_request.longitude
        if lat is None:
            lat = getattr(session_row, "latitude", 0.0)
        if lng is None:
            lng = getattr(session_row, "longitude", 0.0)
        record = Attendance(
            student_id=student.id,
            subject_id=attendance_request.subject_id,
            session_id=attendance_request.session_id,
            date=req_dt.date(),
            time=req_dt.time().replace(microsecond=0),
            latitude=lat,
            longitude=lng,
            distance_m=attendance_request.distance_m,
            teacher_verified=True,
            admin_verified=True,
        )
        db.session.add(record)

    attendance_request.status = "accepted"
    db.session.commit()
    flash("Attendance request accepted.", "success")
    return redirect(url_for("teacher_dashboard"))


@app.post("/teacher/attendance-request/<int:request_id>/reject")
@login_required(role="teacher")
def reject_attendance_request(request_id):
    teacher = Teacher.query.filter_by(user_id=session["user_id"]).first_or_404()
    row = _get_teacher_attendance_request(teacher.id, request_id)
    if not row:
        flash("Attendance request not found for your sessions.", "error")
        return redirect(url_for("teacher_dashboard"))

    attendance_request, _session_row = row
    if attendance_request.status != "pending":
        flash("Attendance request already processed.", "error")
        return redirect(url_for("teacher_dashboard"))

    attendance_request.status = "rejected"
    db.session.commit()
    flash("Attendance request rejected.", "success")
    return redirect(url_for("teacher_dashboard"))


@app.get("/api/teacher/attendance-requests")
@login_required(role="teacher")
def api_teacher_attendance_requests():
    teacher = Teacher.query.filter_by(user_id=session["user_id"]).first_or_404()
    rows = (
        db.session.query(AttendanceRequest, Student, User, AttendanceSession, Subject, Semester)
        .join(Student, Student.id == AttendanceRequest.student_id)
        .join(User, User.id == Student.user_id)
        .join(AttendanceSession, AttendanceSession.id == AttendanceRequest.session_id)
        .join(Subject, Subject.id == AttendanceRequest.subject_id)
        .join(Semester, Semester.id == AttendanceSession.semester_id)
        .filter(
            AttendanceSession.teacher_id == teacher.id,
            AttendanceRequest.status == "pending",
        )
        .order_by(AttendanceRequest.requested_at.desc())
        .all()
    )
    payload = [
        {
            "id": req.id,
            "student_name": user_row.name,
            "roll_no": student.roll_no,
            "requested_at": req.requested_at.strftime("%d/%m/%Y %I:%M %p")
            if req.requested_at
            else "",
            "latitude": req.latitude,
            "longitude": req.longitude,
            "accuracy": req.accuracy,
            "subject": subject.name,
            "semester": semester.name,
        }
        for req, student, user_row, session_row, subject, semester in rows
    ]
    response = jsonify({"success": True, "requests": payload})
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.route("/admin/dashboard")
@login_required(role="admin")
def admin_dashboard():
    semesters = get_allowed_semesters()
    current_semester = get_current_semester()
    current_academic_session = get_current_academic_session()
    registration_open = is_student_registration_open()
    admin_user = db.session.get(User, session["user_id"])
    subjects = (
        db.session.query(Subject, Semester)
        .join(Semester, Semester.id == Subject.semester_id)
        .filter(Semester.name.in_(ALLOWED_SEMESTER_NAMES))
        .order_by(Semester.id, Subject.name)
        .all()
    )

    students_query = (
        db.session.query(Student, User, Semester)
        .join(User, User.id == Student.user_id)
        .join(Semester, Semester.id == Student.semester_id)
    )
    roster_students = (
        db.session.query(Student, User, Semester)
        .join(User, User.id == Student.user_id)
        .join(Semester, Semester.id == Student.semester_id)
        .order_by(Semester.id.asc(), Student.roll_no.asc())
        .all()
    )

    teachers = (
        db.session.query(Teacher, User) 
        .join(User, User.id == Teacher.user_id)
        .filter(User.role == "teacher")
        .order_by(User.name)
        .all()
    )

    filter_semester = request.args.get("semester_id", type=int)
    filter_subject = request.args.get("subject_id", type=int)
    filter_roll = request.args.get("roll_no", "").strip().upper()
    detail_roll = request.args.get("detail_roll", "").strip().upper()
    report_semester_id = request.args.get("report_semester_id", type=int)
    student_semester_id = request.args.get("student_semester_id", type=int)
    attendance_date_param = (request.args.get("attendance_date") or "").strip()
    attendance_date_selected = None
    if attendance_date_param:
        try:
            attendance_date_selected = date.fromisoformat(attendance_date_param)
        except ValueError:
            attendance_date_selected = None

    allowed_semester_ids = {sem.id for sem in semesters}
    if filter_semester and filter_semester not in allowed_semester_ids:
        filter_semester = None
    if report_semester_id and report_semester_id not in allowed_semester_ids:
        report_semester_id = None
    if student_semester_id and student_semester_id not in allowed_semester_ids:
        student_semester_id = None
    if filter_subject:
        filter_subject_row = db.session.query(Subject).filter(Subject.id == filter_subject).first()
        if not filter_subject_row:
            filter_subject = None
        elif filter_semester and filter_subject_row.semester_id != filter_semester:
            filter_subject = None

    if student_semester_id:
        students_query = students_query.filter(Student.semester_id == student_semester_id)
    students = students_query.order_by(Semester.id, Student.roll_no).all()
    students_semester_wise = []
    for sem in semesters:
        sem_students = [(student, user, semester) for student, user, semester in students if semester.id == sem.id]
        if sem_students:
            students_semester_wise.append((sem, sem_students))

    attendance_query = (
        db.session.query(Attendance, Subject, Student, User, Semester)
        .join(Subject, Subject.id == Attendance.subject_id)
        .join(Student, Student.id == Attendance.student_id)
        .join(User, User.id == Student.user_id)
        .join(Semester, Semester.id == Subject.semester_id)
    )

    if filter_semester:
        attendance_query = attendance_query.filter(Subject.semester_id == filter_semester)
    if filter_subject:
        attendance_query = attendance_query.filter(Attendance.subject_id == filter_subject)
    if filter_roll:
        attendance_query = attendance_query.filter(Student.roll_no == filter_roll)

    attendance_records = attendance_query.order_by(Attendance.date.desc(), Attendance.time.desc()).all()

    teacher_session_records = (
        db.session.query(AttendanceSession, Subject, Semester, Teacher, User)
        .join(Subject, Subject.id == AttendanceSession.subject_id)
        .join(Semester, Semester.id == AttendanceSession.semester_id)
        .join(Teacher, Teacher.id == AttendanceSession.teacher_id)
        .join(User, User.id == Teacher.user_id)
        .order_by(AttendanceSession.start_time.desc())
        .all()
    )
    teacher_sessions_map = {}
    for sess, subject, semester, teacher, user in teacher_session_records:
        entry = teacher_sessions_map.setdefault(
            teacher.id,
            {"teacher": teacher, "user": user, "sessions": []},
        )
        entry["sessions"].append(
            {"session": sess, "subject": subject, "semester": semester}
        )
    teacher_sessions = sorted(
        teacher_sessions_map.values(),
        key=lambda item: (item["user"].name or "").strip().lower(),
    )

    semester_attendance_summary = (
        db.session.query(Semester.name, func.count(Attendance.id))
        .join(Subject, Subject.semester_id == Semester.id)
        .join(Attendance, Attendance.subject_id == Subject.id)
        .group_by(Semester.id, Semester.name)
        .order_by(Semester.id)
        .all()
    )

    student_detail = None
    student_detail_summary = None
    if detail_roll:
        student_detail = (
            db.session.query(Student, User, Semester)
            .join(User, User.id == Student.user_id)
            .join(Semester, Semester.id == Student.semester_id)
            .filter(Student.roll_no == detail_roll)
            .first()
        )
        if student_detail:
            stu, usr, sem = student_detail
            total_sessions = (
                db.session.query(func.count(AttendanceSession.id))
                .join(Subject, Subject.id == AttendanceSession.subject_id)
                .filter(Subject.semester_id == stu.semester_id)
                .scalar()
            )
            present = Attendance.query.filter_by(student_id=stu.id).count()
            percentage = round((present / total_sessions) * 100, 2) if total_sessions else 0
            student_detail_summary = {
                "name": usr.name,
                "roll_no": stu.roll_no,
                "semester": sem.name,
                "total_sessions": total_sessions,
                "attended_sessions": present,
                "percentage": percentage,
                "verified": bool(stu.verified),
                "student_id": stu.id,
            }

    percentage_report = []
    report_total_students = 0
    report_above_75 = 0
    report_semester_name = "All Semesters"
    if report_semester_id:
        built_report = build_percentage_report_for_semester(report_semester_id)
        if built_report:
            percentage_report, report_semester_name = built_report
            report_total_students = len(percentage_report)
            report_above_75 = sum(1 for item in percentage_report if item["percentage"] >= 75)
    total_attendance_records = Attendance.query.count()
    enabled_subject_count = Subject.query.filter_by(status=True).count()
    current_semester_attendance_count = 0
    if current_semester:
        current_semester_attendance_count = (
            db.session.query(func.count(Attendance.id))
            .join(Subject, Subject.id == Attendance.subject_id)
            .filter(Subject.semester_id == current_semester.id)
            .scalar()
            or 0
        )
    if not report_semester_id:
        report_semester_name = "All Semesters"

    filtered_subjects = subjects
    if filter_semester:
        filtered_subjects = [(subject, sem) for subject, sem in subjects if sem.id == filter_semester]

    report_semester_wise_counts = []
    for sem in semesters:
        count = sum(1 for item in percentage_report if item["semester_id"] == sem.id)
        report_semester_wise_counts.append((sem, count))

    default_location = get_default_attendance_location()

    teacher_subject_mappings = (
        db.session.query(TeacherSubjectMap, Teacher, User, Subject, Semester)
        .join(Teacher, Teacher.id == TeacherSubjectMap.teacher_id)
        .join(User, User.id == Teacher.user_id)
        .join(Subject, Subject.id == TeacherSubjectMap.subject_id)
        .join(Semester, Semester.id == Subject.semester_id)
        .order_by(User.name.asc(), Semester.id.asc(), Subject.name.asc())
        .all()
    )
    admin_students_roster = [
        {
            "name": user.name,
            "roll_no": student.roll_no,
            "semester_id": semester.id,
            "semester": semester.name,
        }
        for student, user, semester in roster_students
    ]

    return render_template(
        "admin_dashboard.html",
        admin_user=admin_user,
        current_academic_session=current_academic_session,
        registration_open=registration_open,
        current_semester=current_semester,
        semesters=semesters,
        subjects=subjects,
        filtered_subjects=filtered_subjects,
        students=students,
        teachers=teachers,
        attendance_records=attendance_records,
        teacher_session_records=teacher_session_records,
        teacher_sessions=teacher_sessions,
        semester_attendance_summary=semester_attendance_summary,
        student_detail=student_detail,
        student_detail_summary=student_detail_summary,
        percentage_report=percentage_report,
        filter_semester=filter_semester,
        filter_subject=filter_subject,
        filter_roll=filter_roll,
        detail_roll=detail_roll,
        report_semester_id=report_semester_id,
        student_semester_id=student_semester_id,
        total_attendance_records=total_attendance_records,
        current_semester_attendance_count=current_semester_attendance_count,
        enabled_subject_count=enabled_subject_count,
        report_total_students=report_total_students,
        report_above_75=report_above_75,
        report_semester_name=report_semester_name,
        teacher_subject_mappings=teacher_subject_mappings,
        students_semester_wise=students_semester_wise,
        report_semester_wise_counts=report_semester_wise_counts,
        default_attendance_location=default_location,
        admin_students_roster=admin_students_roster,
        attendance_date_iso=attendance_date_selected.isoformat() if attendance_date_selected else "",
        attendance_date_display=attendance_date_selected.strftime("%d/%m/%Y") if attendance_date_selected else "",
    )


@app.get("/api/admin/report/percentage")
@login_required(role="admin")
def api_admin_percentage_report():
    semester_id = request.args.get("semester_id", type=int)
    if not semester_id:
        return jsonify({"success": False, "message": "Semester is required."}), 400

    built_report = build_percentage_report_for_semester(semester_id)
    if not built_report:
        return jsonify({"success": False, "message": "Invalid semester selected."}), 400

    report, semester_name = built_report
    report_above_75 = sum(1 for item in report if item["percentage"] >= 75)
    return jsonify(
        {
            "success": True,
            "students": report,
            "report_semester_name": semester_name,
            "report_total_students": len(report),
            "report_above_75": report_above_75,
        }
    )


@app.post("/admin/semester")
@login_required(role="admin")
def add_semester():
    flash("Only fixed semesters are allowed. New semesters cannot be added.", "error")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/semester/start")
@login_required(role="admin")
def start_new_semester():
    semester_id = request.form.get("semester_id", type=int)
    semester = db.session.get(Semester, semester_id) if semester_id else None
    if not semester:
        flash("Select a valid semester to start.", "error")
        return redirect(url_for("admin_dashboard"))
    if semester.name not in ALLOWED_SEMESTER_NAMES:
        flash("Only fixed semesters are allowed.", "error")
        return redirect(url_for("admin_dashboard"))

    setting = SystemSetting.query.filter_by(key="current_semester_id").first()
    if not setting:
        setting = SystemSetting(key="current_semester_id", value=str(semester.id))
        db.session.add(setting)
    else:
        setting.value = str(semester.id)

    db.session.commit()
    flash(f"{semester.name} started as current semester.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/semester/current/clear")
@login_required(role="admin")
def clear_current_semester_data():
    current_semester = get_current_semester()
    if not current_semester:
        flash("No current semester selected.", "error")
        return redirect(url_for("admin_dashboard"))

    students_in_semester = Student.query.filter_by(semester_id=current_semester.id).all()
    for student in students_in_semester:
        _delete_student_record(student)
    db.session.commit()
    flash(
        f"Cleared student data for {current_semester.name}.",
        "success",
    )
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/semester/data/delete")
@login_required(role="admin")
def delete_semester_data():
    semester_id = request.form.get("semester_id", type=int)
    semester = db.session.get(Semester, semester_id) if semester_id else None
    if not semester:
        flash("Select a valid semester to delete.", "error")
        return redirect(url_for("admin_dashboard"))
    if semester.name not in ALLOWED_SEMESTER_NAMES:
        flash("Only fixed semesters are allowed.", "error")
        return redirect(url_for("admin_dashboard"))

    students_in_semester = Student.query.filter_by(semester_id=semester.id).all()
    for student in students_in_semester:
        _delete_student_record(student)

    db.session.commit()
    flash(f"Deleted student data for {semester.name}.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/semester/current/delete")
@login_required(role="admin")
def delete_current_semester():
    flash("Semesters cannot be deleted. Only fixed semesters are allowed.", "error")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/account/update")
@login_required(role="admin")
def update_admin_account():
    admin = User.query.filter_by(id=session["user_id"], role="admin").first_or_404()
    new_email = request.form.get("email", "").strip().lower()
    new_password = request.form.get("password", "")
    new_department = request.form.get("department", "").strip()

    if not new_email:
        flash("Email is required.", "error")
        return redirect(url_for("admin_dashboard"))
    if not new_department:
        flash("Department is required.", "error")
        return redirect(url_for("admin_dashboard"))

    existing = User.query.filter(User.email == new_email, User.id != admin.id).first()
    if existing:
        flash("Email already used by another account.", "error")
        return redirect(url_for("admin_dashboard"))

    admin.email = new_email
    admin.department = new_department
    if new_password:
        admin.password = generate_password_hash(new_password)

    db.session.commit()
    flash("Admin account updated successfully.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/student/add")
@login_required(role="admin")
def admin_add_student():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    roll_no = request.form.get("roll_no", "").strip().upper()
    semester_id = request.form.get("semester_id", type=int)
    verified = request.form.get("verified") == "on"

    if not all([name, email, password, roll_no, semester_id]):
        flash("All student fields are required.", "error")
        return redirect(url_for("admin_dashboard"))

    if User.query.filter_by(email=email).first():
        flash("Student email already exists.", "error")
        return redirect(url_for("admin_dashboard"))

    if Student.query.filter_by(roll_no=roll_no).first():
        flash("Roll number already exists.", "error")
        return redirect(url_for("admin_dashboard"))

    semester = db.session.get(Semester, semester_id)
    if not semester:
        flash("Invalid semester selected.", "error")
        return redirect(url_for("admin_dashboard"))
    if semester.name not in ALLOWED_SEMESTER_NAMES:
        flash("Only fixed semesters are allowed.", "error")
        return redirect(url_for("admin_dashboard"))

    user = User(
        role="student",
        name=name,
        email=email,
        password=generate_password_hash(password),
        email_verified=verified,
    )
    db.session.add(user)
    db.session.flush()
    db.session.add(
        Student(
            user_id=user.id,
            roll_no=roll_no,
            mobile_no=None,
            semester_id=semester_id,
            verified=verified,
        )
    )
    db.session.commit()
    flash("Student added successfully by admin.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/student/<int:student_id>/verify")
@login_required(role="admin")
def admin_verify_student(student_id):
    student = Student.query.get_or_404(student_id)
    student.verified = True
    user = db.session.get(User, student.user_id)
    if user:
        user.email_verified = True
    db.session.commit()
    flash("Student verified successfully.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/student/<int:student_id>/unverify")
@login_required(role="admin")
def admin_unverify_student(student_id):
    student = Student.query.get_or_404(student_id)
    student.verified = False
    user = db.session.get(User, student.user_id)
    if user:
        user.email_verified = False
    db.session.commit()
    flash("Student verification removed.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/teacher/add")
@login_required(role="admin")
def admin_add_teacher():
    name = request.form.get("name", "").strip()
    teacher_id = request.form.get("teacher_id", "").strip().upper()
    email_raw = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    department = request.form.get("department", "").strip() or "General"

    email, email_error = normalize_gmail_email(email_raw)
    if email_error:
        flash(email_error, "error")
        return redirect(url_for("admin_dashboard"))

    if not all([teacher_id, name, email, password]):
        flash("Name, Teacher ID, Email, and Password are required.", "error")
        return redirect(url_for("admin_dashboard"))

    if not teacher_id.replace("-", "").replace("_", "").isalnum():
        flash("Teacher ID can contain only letters, numbers, '-' and '_'.", "error")
        return redirect(url_for("admin_dashboard"))

    if User.query.filter_by(email=email).first():
        flash("Email is already registered.", "error")
        return redirect(url_for("admin_dashboard"))
    if Teacher.query.filter_by(teacher_id=teacher_id).first():
        flash("Teacher ID already exists. Please use a unique Teacher ID.", "error")
        return redirect(url_for("admin_dashboard"))

    user = User(
        role="teacher",
        name=name,
        email=email,
        password=generate_password_hash(password),
        email_verified=True,
        department=department,
    )
    db.session.add(user)
    db.session.flush()

    teacher_profile = Teacher(
        user_id=user.id,
        teacher_id=teacher_id,
        department=department,
        mobile_no=None,
    )
    db.session.add(teacher_profile)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("Teacher ID already exists. Please use a unique Teacher ID.", "error")
        return redirect(url_for("admin_dashboard"))

    flash(f"Teacher {teacher_profile.teacher_id} added successfully.", "success")
    return redirect(url_for("admin_dashboard"))


def _delete_student_record(student):
    user = db.session.get(User, student.user_id)

    AttendanceRequest.query.filter_by(student_id=student.id).delete(synchronize_session=False)
    Attendance.query.filter_by(student_id=student.id).delete(synchronize_session=False)
    db.session.delete(student)
    if user:
        db.session.delete(user)


def _delete_teacher_record(teacher):
    user = db.session.get(User, teacher.user_id)

    linked_student_ids = [
        sid
        for (sid,) in (
            db.session.query(Attendance.student_id)
            .join(AttendanceSession, AttendanceSession.id == Attendance.session_id)
            .filter(AttendanceSession.teacher_id == teacher.id)
            .distinct()
            .all()
        )
    ]
    for sid in linked_student_ids:
        student = db.session.get(Student, sid)
        if student:
            _delete_student_record(student)

    teacher_session_ids = db.session.query(AttendanceSession.id).filter_by(teacher_id=teacher.id)
    AttendanceRequest.query.filter(AttendanceRequest.session_id.in_(teacher_session_ids)).delete(
        synchronize_session=False
    )
    Attendance.query.filter(Attendance.session_id.in_(teacher_session_ids)).delete(
        synchronize_session=False
    )
    TeacherSubjectMap.query.filter_by(teacher_id=teacher.id).delete(synchronize_session=False)
    AttendanceSession.query.filter_by(teacher_id=teacher.id).delete(synchronize_session=False)
    db.session.delete(teacher)
    if user:
        db.session.delete(user)


def build_percentage_report_for_semester(semester_id):
    semester = db.session.get(Semester, semester_id)
    if not semester or semester.name not in ALLOWED_SEMESTER_NAMES:
        return None

    percentage_students_query = (
        db.session.query(Student, User, Semester)
        .join(User, User.id == Student.user_id)
        .join(Semester, Semester.id == Student.semester_id)
        .filter(Student.semester_id == semester_id)
        .order_by(func.lower(User.name).asc(), Student.roll_no.asc())
    )
    percentage_students = percentage_students_query.all()

    report = []
    for student, user, sem in percentage_students:
        total_sessions = (
            db.session.query(func.count(AttendanceSession.id))
            .join(Subject, Subject.id == AttendanceSession.subject_id)
            .filter(Subject.semester_id == student.semester_id)
            .scalar()
        )
        present = Attendance.query.filter_by(student_id=student.id).count()
        percentage = round((present / total_sessions) * 100, 2) if total_sessions else 0
        report.append(
            {
                "student_id": student.id,
                "name": user.name,
                "roll_no": student.roll_no,
                "semester": sem.name,
                "semester_id": student.semester_id,
                "present": present,
                "total": total_sessions,
                "percentage": percentage,
            }
        )
    return report, semester.name


@app.post("/admin/student/<int:student_id>/delete")
@login_required(role="admin")
def admin_delete_student(student_id):
    student = Student.query.get_or_404(student_id)
    _delete_student_record(student)
    db.session.commit()

    flash("Student data deleted successfully.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/teacher/<int:teacher_id>/delete")
@login_required(role="admin")
def admin_delete_teacher(teacher_id):
    teacher = Teacher.query.get_or_404(teacher_id)
    _delete_teacher_record(teacher)
    db.session.commit()

    flash("Teacher data deleted successfully.", "success")
    return redirect(url_for("admin_dashboard"))


def _delete_sessions_by_ids(session_ids):
    if not session_ids:
        return
    AttendanceRequest.query.filter(AttendanceRequest.session_id.in_(session_ids)).delete(
        synchronize_session=False
    )
    Attendance.query.filter(Attendance.session_id.in_(session_ids)).delete(
        synchronize_session=False
    )
    TeacherLocationHistory.query.filter(TeacherLocationHistory.session_id.in_(session_ids)).delete(
        synchronize_session=False
    )
    AttendanceSession.query.filter(AttendanceSession.id.in_(session_ids)).delete(
        synchronize_session=False
    )


@app.post("/admin/session/<int:session_id>/delete")
@login_required(role="admin")
def admin_delete_session(session_id):
    session_row = AttendanceSession.query.get_or_404(session_id)
    _delete_sessions_by_ids([session_row.id])
    db.session.commit()
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"success": True, "deleted": 1})
    flash("Session deleted successfully.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/teacher/<int:teacher_id>/sessions/delete")
@login_required(role="admin")
def admin_delete_teacher_sessions(teacher_id):
    teacher = Teacher.query.get_or_404(teacher_id)
    session_ids = [
        row.id for row in AttendanceSession.query.filter_by(teacher_id=teacher.id).all()
    ]
    _delete_sessions_by_ids(session_ids)
    db.session.commit()
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"success": True, "deleted": len(session_ids)})
    flash("All sessions for this teacher deleted successfully.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/student/delete-by-roll")
@login_required(role="admin")
def admin_delete_student_by_roll():
    roll_no = request.form.get("roll_no", "").strip().upper()
    if not roll_no:
        flash("Roll number is required.", "error")
        return redirect(url_for("admin_dashboard"))

    student = Student.query.filter_by(roll_no=roll_no).first()
    if not student:
        flash("Student not found for this roll number.", "error")
        return redirect(url_for("admin_dashboard"))

    _delete_student_record(student)
    db.session.commit()
    flash("Student deleted successfully by roll number.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/teacher/delete-by-id")
@login_required(role="admin")
def admin_delete_teacher_by_teacher_id():
    teacher_id = request.form.get("teacher_id", "").strip().upper()
    if not teacher_id:
        flash("Teacher ID is required.", "error")
        return redirect(url_for("admin_dashboard"))

    teacher = Teacher.query.filter_by(teacher_id=teacher_id).first()
    if not teacher:
        flash("Teacher not found for this ID.", "error")
        return redirect(url_for("admin_dashboard"))

    _delete_teacher_record(teacher)
    db.session.commit()
    flash("Teacher deleted successfully by ID.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/subject")
@login_required(role="admin")
def add_subject():
    name = request.form.get("name", "").strip()
    semester_id = request.form.get("semester_id", type=int)
    status = request.form.get("status") == "on"

    if not name or not semester_id:
        flash("Subject name and semester are required.", "error")
        return redirect(url_for("admin_dashboard"))

    semester = db.session.get(Semester, semester_id)
    if not semester:
        flash("Invalid semester selected.", "error")
        return redirect(url_for("admin_dashboard"))
    if semester.name not in ALLOWED_SEMESTER_NAMES:
        flash("Subjects can be added only for fixed semesters.", "error")
        return redirect(url_for("admin_dashboard"))

    exists = (
        Subject.query.filter_by(semester_id=semester_id)
        .filter(func.lower(Subject.name) == name.lower())
        .first()
    )
    if exists:
        flash("Subject already exists for this semester.", "error")
        return redirect(url_for("admin_dashboard"))

    db.session.add(Subject(name=name, semester_id=semester_id, status=status))
    db.session.commit()
    flash("Subject added successfully.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/subject/<int:subject_id>/toggle")
@login_required(role="admin")
def toggle_subject(subject_id):
    subject = Subject.query.get_or_404(subject_id)
    subject.status = not subject.status
    db.session.commit()
    flash("Subject status updated.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/subject/<int:subject_id>/delete")
@login_required(role="admin")
def delete_subject(subject_id):
    subject = Subject.query.get_or_404(subject_id)

    subject_session_ids = db.session.query(AttendanceSession.id).filter_by(subject_id=subject.id)
    AttendanceRequest.query.filter(AttendanceRequest.session_id.in_(subject_session_ids)).delete(
        synchronize_session=False
    )
    AttendanceRequest.query.filter_by(subject_id=subject.id).delete(synchronize_session=False)
    Attendance.query.filter(Attendance.session_id.in_(subject_session_ids)).delete(
        synchronize_session=False
    )
    TeacherSubjectMap.query.filter_by(subject_id=subject.id).delete(synchronize_session=False)
    Attendance.query.filter_by(subject_id=subject.id).delete(synchronize_session=False)
    AttendanceSession.query.filter_by(subject_id=subject.id).delete(synchronize_session=False)
    db.session.delete(subject)
    db.session.commit()

    flash("Subject data deleted successfully.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/registration/toggle")
@login_required(role="admin")
def toggle_student_registration():
    is_open = is_student_registration_open()
    set_student_registration_open(not is_open)
    status = "open" if not is_open else "closed"
    flash(f"Student registration is now {status}.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/attendance/location")
@login_required(role="admin")
def admin_set_attendance_location():
    latitude = request.form.get("latitude", "").strip()
    longitude = request.form.get("longitude", "").strip()
    if latitude == "" or longitude == "":
        flash("Latitude and longitude are required.", "error")
        return redirect(url_for("admin_dashboard"))
    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError):
        flash("Invalid latitude/longitude values.", "error")
        return redirect(url_for("admin_dashboard"))
    if not (math.isfinite(latitude) and math.isfinite(longitude)):
        flash("Invalid latitude/longitude values.", "error")
        return redirect(url_for("admin_dashboard"))
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        flash("Latitude/longitude out of range.", "error")
        return redirect(url_for("admin_dashboard"))

    set_default_attendance_location(latitude, longitude)
    flash("Default attendance location saved.", "success")
    return redirect(url_for("admin_dashboard"))



@app.post("/admin/teacher-subject/assign")
@login_required(role="admin")
def assign_teacher_subject():
    teacher_id = request.form.get("teacher_id", type=int)
    subject_id = request.form.get("subject_id", type=int)
    if not teacher_id or not subject_id:
        flash("Teacher and subject are required.", "error")
        return redirect(url_for("admin_dashboard"))

    teacher = db.session.get(Teacher, teacher_id)
    subject = db.session.get(Subject, subject_id)
    if not teacher or not subject:
        flash("Invalid teacher or subject.", "error")
        return redirect(url_for("admin_dashboard"))

    existing = TeacherSubjectMap.query.filter_by(teacher_id=teacher_id, subject_id=subject_id).first()
    if existing:
        flash("This subject is already assigned to the selected teacher.", "error")
        return redirect(url_for("admin_dashboard"))

    db.session.add(TeacherSubjectMap(teacher_id=teacher_id, subject_id=subject_id))
    db.session.commit()
    flash("Teacher-subject mapping saved successfully.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/teacher-subject/<int:mapping_id>/delete")
@login_required(role="admin")
def delete_teacher_subject_mapping(mapping_id):
    mapping = db.session.get(TeacherSubjectMap, mapping_id)
    if not mapping:
        flash("Mapping not found.", "error")
        return redirect(url_for("admin_dashboard"))

    db.session.delete(mapping)
    db.session.commit()
    flash("Teacher-subject mapping removed.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/attendance/<int:attendance_id>/delete")
@login_required(role="admin")
def delete_student_attendance(attendance_id):
    attendance = Attendance.query.get_or_404(attendance_id)
    db.session.delete(attendance)
    db.session.commit()
    flash("Attendance record deleted successfully.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/percentage/<int:student_id>/delete")
@login_required(role="admin")
def delete_percentage_report_data(student_id):
    student = db.session.get(Student, student_id)
    if not student:
        flash("Student record not found.", "error")
        return redirect(url_for("admin_dashboard"))

    Attendance.query.filter_by(student_id=student.id).delete(synchronize_session=False)
    db.session.commit()
    flash("Percentage report data cleared for this student.", "success")
    return redirect(url_for("admin_dashboard"))


@app.get("/api/admin/student/details")
@login_required(role="admin")
def api_admin_student_details():
    roll_no = request.args.get("roll_no", "").strip().upper()
    if not roll_no:
        return jsonify({"success": False, "message": "Roll number is required."}), 400

    student_detail = (
        db.session.query(Student, User, Semester)
        .join(User, User.id == Student.user_id)
        .join(Semester, Semester.id == Student.semester_id)
        .filter(Student.roll_no == roll_no)
        .first()
    )
    if not student_detail:
        return jsonify({"success": False, "message": "Student not found."}), 404

    stu, usr, sem = student_detail
    total_sessions = (
        db.session.query(func.count(AttendanceSession.id))
        .join(Subject, Subject.id == AttendanceSession.subject_id)
        .filter(Subject.semester_id == stu.semester_id)
        .scalar()
    )
    present = Attendance.query.filter_by(student_id=stu.id).count()
    percentage = round((present / total_sessions) * 100, 2) if total_sessions else 0

    return jsonify(
        {
            "success": True,
            "summary": {
                "student_id": stu.id,
                "roll_no": stu.roll_no,
                "name": usr.name,
                "semester": sem.name,
                "total_sessions": total_sessions,
                "percentage": percentage,
                "attended_sessions": present,
                "verified": bool(stu.verified),
            },
        }
    )


@app.post("/api/teacher/session/start")
@login_required(role="teacher")
def start_session():
    data = request.get_json(silent=True) or {}
    subject_id = data.get("subject_id")
    device_id = (data.get("device_id") or "").strip()
    # Teacher cannot override admin-set attendance location.
    default_location = get_default_attendance_location()
    if not default_location:
        return jsonify(
            {
                "success": False,
                "message": "Admin has not set the attendance location yet.",
            }
        ), 400
    latitude = default_location["latitude"]
    longitude = default_location["longitude"]
    accuracy = 0.0
    manual_location = True
    test_mode = False

    if subject_id in (None, ""):
        return jsonify({"success": False, "message": "Subject is required."}), 400
    try:
        subject_id = int(subject_id)
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Invalid subject value."}), 400

    # Strict server-side validation of admin-set coordinates.
    if not (math.isfinite(latitude) and math.isfinite(longitude)):
        return jsonify({"success": False, "message": "Invalid attendance location."}), 400
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return jsonify({"success": False, "message": "Invalid attendance location."}), 400

    if not manual_location and not test_mode and (
        not accuracy or accuracy <= 0 or accuracy > GPS_ACCURACY_ACCEPT_MAX_M
    ):
        return (
            jsonify(
                {
                    "success": False,
                    "message": f"GPS accuracy too low (must be <= {int(GPS_ACCURACY_ACCEPT_MAX_M)}m). Turn on Precise location, wait a few seconds, then retry near a window/outdoor.",
                    "teacher_accuracy": accuracy,
                }
            ),
            400,
        )
    if not device_id:
        return jsonify({"success": False, "message": "Device identity required. Please refresh and retry."}), 400

    teacher = Teacher.query.filter_by(user_id=session["user_id"]).first()
    subject = Subject.query.filter_by(id=subject_id, status=True).first()

    if not teacher or not subject:
        return jsonify({"success": False, "message": "Invalid teacher or subject."}), 400

    # Strict admin assignment check: teacher can start only assigned subjects.
    mapped = TeacherSubjectMap.query.filter_by(teacher_id=teacher.id, subject_id=subject.id).first()
    if not mapped:
        return jsonify(
            {
                "success": False,
                "message": "You can start session only for subjects assigned by admin.",
            }
        ), 403

    already_active = AttendanceSession.query.filter_by(
        teacher_id=teacher.id,
        is_active=True,
    ).first()
    if already_active:
        return jsonify({"success": False, "message": "Session already active."}), 400

    new_session = AttendanceSession(
        teacher_id=teacher.id,
        subject_id=subject.id,
        semester_id=subject.semester_id,
        start_time=datetime.now(),
        is_active=True,
        latitude=latitude,
        longitude=longitude,
        location_accuracy=accuracy,
        location_enforced=not test_mode,
        location_source="manual" if manual_location else "gps",
        gps_locked=bool(
            manual_location
            or test_mode
            or (accuracy and accuracy > 0 and accuracy <= GPS_ACCURACY_ACCEPT_MAX_M)
        ),
        is_test_mode=bool(test_mode),
        device_id=device_id,
        device_fingerprint=get_device_fingerprint(),
        last_location_update=datetime.now(),
    )
    db.session.add(new_session)
    db.session.flush()
    db.session.add(
        TeacherLocationHistory(
            session_id=new_session.id,
            teacher_id=teacher.id,
            latitude=latitude,
            longitude=longitude,
            accuracy=accuracy,
        )
    )
    db.session.commit()

    warning = None
    if accuracy > GPS_ACCURACY_ACCEPT_MAX_M:
        warning = "Session started with low GPS accuracy. Please ensure location services are enabled."
    app.logger.info(
        "Session started | teacher_id=%s subject_id=%s lat=%s lon=%s acc=%.2f warning=%s",
        teacher.id,
        subject.id,
        latitude,
        longitude,
        accuracy,
        warning or "",
    )
    message = "Attendance session started successfully."
    if warning:
        message = f"{message} {warning}"
    return jsonify(
        {
            "success": True,
            "message": message,
            "warning": warning,
            "session_id": new_session.id,
            "start_time": fmt_date(new_session.start_time),
        }
    )


@app.post("/api/teacher/session/update-location")
@login_required(role="teacher")
def update_teacher_location():
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id")
    latitude = data.get("latitude")
    longitude = data.get("longitude")
    accuracy = data.get("accuracy")
    device_id = (data.get("device_id") or "").strip()

    if session_id in (None, ""):
        return jsonify({"success": False, "message": "Session is required."}), 400
    try:
        session_id = int(session_id)
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Invalid session value."}), 400

    if not device_id:
        return jsonify({"success": False, "message": "Device identity required."}), 400

    teacher = Teacher.query.filter_by(user_id=session["user_id"]).first()
    if not teacher:
        return jsonify({"success": False, "message": "Teacher profile not found."}), 404

    active_session = AttendanceSession.query.filter_by(
        id=session_id,
        teacher_id=teacher.id,
        is_active=True,
    ).first()
    if not active_session:
        return jsonify({"success": False, "message": "Active session not found."}), 404

    if active_session.device_id and device_id != active_session.device_id:
        return jsonify({"success": False, "message": "Session device mismatch."}), 403
    if (
        active_session.device_fingerprint
        and active_session.device_fingerprint != get_device_fingerprint()
    ):
        return jsonify({"success": False, "message": "Session device mismatch."}), 403

    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Invalid location values."}), 400
    if not (math.isfinite(latitude) and math.isfinite(longitude)):
        return jsonify({"success": False, "message": "Invalid location values."}), 400
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return jsonify({"success": False, "message": "Invalid location coordinates."}), 400
    try:
        accuracy = float(accuracy) if accuracy not in (None, "") else 0.0
    except (TypeError, ValueError):
        accuracy = 0.0
    if accuracy < 0:
        accuracy = 0.0
    # Only accept updates that have a reasonably accurate GPS fix.
    if not accuracy or accuracy <= 0 or accuracy > GPS_ACCURACY_ACCEPT_MAX_M:
        return jsonify({"success": True, "updated": False}), 200

    active_session.latitude = latitude
    active_session.longitude = longitude
    active_session.location_accuracy = accuracy
    active_session.last_location_update = datetime.now()
    active_session.gps_locked = True
    db.session.add(
        TeacherLocationHistory(
            session_id=active_session.id,
            teacher_id=teacher.id,
            latitude=latitude,
            longitude=longitude,
            accuracy=accuracy,
        )
    )
    db.session.commit()

    return jsonify({"success": True})


@app.get("/api/session-location")
@login_required(role="student")
def api_session_location():
    session_id = request.args.get("session_id", type=int)
    student = Student.query.filter_by(user_id=session["user_id"]).first()
    if not student:
        return jsonify({"active": False, "message": "Student profile not found."}), 404

    active_session = None
    if session_id:
        active_session = AttendanceSession.query.filter_by(id=session_id, is_active=True).first()
        if active_session and active_session.semester_id != student.semester_id:
            return jsonify({"active": False, "message": "Session not available."}), 403
    else:
        active_sessions = (
            AttendanceSession.query.filter_by(
                is_active=True,
                semester_id=student.semester_id,
            )
            .order_by(AttendanceSession.start_time.desc())
            .all()
        )
        if len(active_sessions) > 1:
            return jsonify(
                {
                    "active": False,
                    "message": "Multiple active sessions found. Please select a session.",
                }
            ), 400
        if active_sessions:
            active_session = active_sessions[0]

    if not active_session:
        return jsonify({"active": False, "message": "No active session."}), 404

    try:
        session_lat = float(active_session.latitude)
        session_lng = float(active_session.longitude)
    except (TypeError, ValueError):
        return jsonify({"active": False, "message": "Session location unavailable."}), 400

    if not (math.isfinite(session_lat) and math.isfinite(session_lng)):
        return jsonify({"active": False, "message": "Session location unavailable."}), 400
    if not (-90 <= session_lat <= 90 and -180 <= session_lng <= 180):
        return jsonify({"active": False, "message": "Session location unavailable."}), 400

    response = jsonify(
        {
            "active": True,
            "lat": session_lat,
            "lng": session_lng,
            "session_id": active_session.id,
            "accuracy": float(getattr(active_session, "location_accuracy", 0.0) or 0.0),
        }
    )
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.get("/api/active-session")
@login_required(role="student")
def api_active_session():
    session_id = request.args.get("session_id", type=int)
    student = Student.query.filter_by(user_id=session["user_id"]).first()
    if not student:
        return jsonify({"active": False, "message": "Student profile not found."}), 404

    active_session = None
    if session_id:
        active_session = AttendanceSession.query.filter_by(id=session_id, is_active=True).first()
        if active_session and active_session.semester_id != student.semester_id:
            return jsonify({"active": False, "message": "Session not available."}), 403
    else:
        active_sessions = (
            AttendanceSession.query.filter_by(
                is_active=True,
                semester_id=student.semester_id,
            )
            .order_by(AttendanceSession.start_time.desc())
            .all()
        )
        if len(active_sessions) > 1:
            return jsonify(
                {
                    "active": False,
                    "message": "Multiple active sessions found. Please select a session.",
                }
            ), 400
        if active_sessions:
            active_session = active_sessions[0]

    if not active_session:
        return jsonify({"active": False, "message": "No active session."}), 404

    try:
        session_lat = float(active_session.latitude)
        session_lng = float(active_session.longitude)
    except (TypeError, ValueError):
        return jsonify({"active": False, "message": "Session location unavailable."}), 400

    if not (math.isfinite(session_lat) and math.isfinite(session_lng)):
        return jsonify({"active": False, "message": "Session location unavailable."}), 400
    if not (-90 <= session_lat <= 90 and -180 <= session_lng <= 180):
        return jsonify({"active": False, "message": "Session location unavailable."}), 400

    if bool(getattr(active_session, "location_enforced", True)) and (
        (getattr(active_session, "location_source", "") or "").lower() != "manual"
    ):
        last_update = getattr(active_session, "last_location_update", None)
        if not last_update:
            return jsonify({"active": False, "message": "Teacher location is stale. Ask the teacher to retry."}), 400
        if (datetime.now() - last_update).total_seconds() > 12:
            return jsonify({"active": False, "message": "Teacher location is stale. Ask the teacher to retry."}), 400

    response = jsonify(
        {
            "active": True,
            "teacher_lat": session_lat,
            "teacher_lng": session_lng,
            "session_id": active_session.id,
            "accuracy": float(getattr(active_session, "location_accuracy", 0.0) or 0.0),
            "gps_locked": bool(getattr(active_session, "gps_locked", False)),
            "is_test_mode": bool(getattr(active_session, "is_test_mode", False)),
            "last_location_update": (
                active_session.last_location_update.isoformat()
                if getattr(active_session, "last_location_update", None)
                else None
            ),
        }
    )
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.get("/api/teacher/subjects")
@login_required(role="teacher")
def get_teacher_subjects_by_semester():
    semester_id = request.args.get("semester_id", type=int)
    if not semester_id:
        return jsonify({"success": False, "message": "Semester is required."}), 400

    semester = db.session.get(Semester, semester_id)
    if not semester:
        return jsonify({"success": False, "message": "Invalid semester selected."}), 400

    teacher = Teacher.query.filter_by(user_id=session["user_id"]).first()
    if not teacher:
        return jsonify({"success": False, "message": "Teacher profile not found."}), 404

    # Return only admin-assigned enabled subjects for this teacher and semester.
    teacher_subject_ids = {
        row.subject_id for row in TeacherSubjectMap.query.filter_by(teacher_id=teacher.id).all()
    }
    subjects_query = Subject.query.filter_by(semester_id=semester_id, status=True)
    if teacher_subject_ids:
        subjects_query = subjects_query.filter(Subject.id.in_(teacher_subject_ids))
    else:
        subjects_query = subjects_query.filter(Subject.id == -1)
    subjects = subjects_query.order_by(Subject.name.asc()).all()
    response = jsonify(
        {
            "success": True,
            "subjects": [{"id": subject.id, "name": subject.name} for subject in subjects],
        }
    )
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.post("/api/teacher/session/stop/<int:session_id>")
@login_required(role="teacher")
def stop_session(session_id):
    teacher = Teacher.query.filter_by(user_id=session["user_id"]).first()
    active_session = AttendanceSession.query.filter_by(
        id=session_id,
        teacher_id=teacher.id,
        is_active=True,
    ).first()

    if not active_session:
        return jsonify({"success": False, "message": "Active session not found."}), 404

    active_session.is_active = False
    active_session.end_time = datetime.now()
    db.session.commit()

    return jsonify({"success": True, "message": "Session stopped."})


@app.post("/api/student/attendance/mark")
@login_required(role="student")
def mark_attendance():
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id")
    latitude = data.get("latitude")
    longitude = data.get("longitude")
    accuracy = data.get("accuracy")
    device_id = (data.get("device_id") or "").strip()
    teacher_latitude_client = data.get("teacher_latitude")
    teacher_longitude_client = data.get("teacher_longitude")
    test_mode = False

    if session_id in (None, ""):
        return jsonify({"success": False, "message": "Session is required."}), 400
    try:
        session_id = int(session_id)
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Invalid session value."}), 400
    if not device_id:
        return jsonify({"success": False, "message": "Device identity required. Please refresh and retry."}), 400

    student = Student.query.filter_by(user_id=session["user_id"]).first()
    active_session = AttendanceSession.query.filter_by(id=session_id, is_active=True).first()

    if not student or not active_session:
        return jsonify({"success": False, "message": "Active session not found."}), 404

    subject = db.session.get(Subject, active_session.subject_id)
    if not subject or not subject.status:
        return jsonify({"success": False, "message": "Subject is disabled by admin."}), 400

    if student.semester_id != active_session.semester_id:
        return jsonify({"success": False, "message": "Semester mismatch. You cannot mark this attendance."}), 403
    # If teacher's GPS hasn't locked yet, do not attempt strict 50m validation (it will cause false negatives).
    if (
        bool(getattr(active_session, "location_enforced", True))
        and (getattr(active_session, "location_source", "") or "").lower() != "manual"
        and not bool(getattr(active_session, "gps_locked", False))
    ):
        return (
            jsonify(
                {
                    "success": False,
                    "message": "Teacher GPS is still stabilizing. Please wait 5–10 seconds and retry.",
                    "teacher_accuracy": float(getattr(active_session, "location_accuracy", 0.0) or 0.0),
                }
            ),
            409,
        )
    if bool(getattr(active_session, "location_enforced", True)):
        # Allow same-device attendance on mobile to avoid false blocks in real-world usage.
        # Device-based blocking can be reintroduced later with a dedicated admin setting.
        pass

    # Load teacher coordinates early for distance validation.
    try:
        session_latitude = float(active_session.latitude)
        session_longitude = float(active_session.longitude)
    except (TypeError, ValueError):
        return (
            jsonify(
                {
                    "success": False,
                    "message": "Session location is unavailable. Ask the teacher to restart the session.",
                }
            ),
            400,
        )
    if not (math.isfinite(session_latitude) and math.isfinite(session_longitude)):
        return (
            jsonify(
                {
                    "success": False,
                    "message": "Session location is unavailable. Ask the teacher to restart the session.",
                }
            ),
            400,
        )
    if not (-90 <= session_latitude <= 90 and -180 <= session_longitude <= 180):
        return (
            jsonify(
                {
                    "success": False,
                    "message": "Session location is unavailable. Ask the teacher to restart the session.",
                }
            ),
            400,
        )

    # Student GPS is mandatory for attendance.
    has_location = latitude not in (None, "") and longitude not in (None, "")
    if not has_location:
        return jsonify(
            {
                "success": False,
                "message": "Location is required. Please turn on GPS and allow location permission.",
            }
        ), 400
    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Invalid location values."}), 400
    if not (math.isfinite(latitude) and math.isfinite(longitude)):
        return jsonify({"success": False, "message": "Invalid location values."}), 400
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return jsonify({"success": False, "message": "Invalid location coordinates."}), 400
    try:
        accuracy = float(accuracy) if accuracy not in (None, "") else 0.0
    except (TypeError, ValueError):
        accuracy = 0.0
    if accuracy < 0:
        accuracy = 0.0
    if (
        bool(getattr(active_session, "location_enforced", True))
        and (not accuracy or accuracy <= 0 or accuracy > GPS_ACCURACY_ACCEPT_MAX_M)
    ):
        return (
            jsonify(
                {
                    "success": False,
                    "message": f"GPS accuracy too low (must be <= {int(GPS_ACCURACY_ACCEPT_MAX_M)}m). Turn on Precise location, wait a few seconds, then retry near a window/outdoor.",
                    "student_accuracy": accuracy,
                }
            ),
            400,
        )


    # Add a modest GPS-based tolerance to reduce false negatives near the boundary.
    distance = haversine_meters(
        latitude,
        longitude,
        session_latitude,
        session_longitude,
    )
    if not math.isfinite(distance):
        return jsonify({"success": False, "message": "Invalid distance calculation."}), 400

    allowed_radius = ATTENDANCE_ALLOWED_RADIUS_M
    teacher_accuracy = max(float(getattr(active_session, "location_accuracy", 0.0) or 0.0), 0.0)
    student_accuracy = max(float(accuracy or 0.0), 0.0)
    accuracy_tolerance = min(
        ATTENDANCE_MAX_ACCURACY_TOLERANCE_M,
        (teacher_accuracy * 0.25) + (student_accuracy * 0.25),
    )
    base_radius = allowed_radius
    effective_radius = allowed_radius + accuracy_tolerance
    rounded_distance = round(distance, 2)
    app.logger.info(
        "Attendance distance check | student=(%s,%s acc=%.2f) teacher=(%s,%s acc=%.2f) distance_m=%.2f base_radius=%.2f tol=%.2f eff_radius=%.2f",
        latitude,
        longitude,
        accuracy,
        session_latitude,
        session_longitude,
        getattr(active_session, "location_accuracy", 0.0) or 0.0,
        rounded_distance,
        base_radius,
        accuracy_tolerance,
        effective_radius,
    )
    if teacher_latitude_client not in (None, "") and teacher_longitude_client not in (None, ""):
        app.logger.info(
            "Attendance client teacher location | teacher_client=(%s,%s)",
            teacher_latitude_client,
            teacher_longitude_client,
        )
    if distance > effective_radius:
        # Double-check after recalculating to rule out transient float issues.
        distance = haversine_meters(
            latitude,
            longitude,
            session_latitude,
            session_longitude,
        )
        rounded_distance = round(distance, 2)
        if distance > effective_radius:
            return jsonify(
                {
                    "success": False,
                    "message": (
                        f"You are outside of the allowed range. "
                        f"Your distance is {rounded_distance}m and the current limit is "
                        f"{round(effective_radius, 2)}m."
                    ),
                    "distance": rounded_distance,
                    "allowed_radius": allowed_radius,
                    "base_radius": base_radius,
                    "accuracy_tolerance": accuracy_tolerance,
                    "effective_radius": round(effective_radius, 2),
                    "teacher_accuracy": teacher_accuracy,
                    "student_accuracy": student_accuracy,
                    "student_latitude": latitude,
                    "student_longitude": longitude,
                    "teacher_latitude": session_latitude,
                    "teacher_longitude": session_longitude,
                }
            ), 403

    existing = Attendance.query.filter_by(student_id=student.id, session_id=active_session.id).first()
    if existing:
        return jsonify(
        {
            "success": True,
            "message": "Attendance already approved for this session.",
            "already_marked": True,
            "status": "accepted",
            "distance": rounded_distance,
            "effective_radius": effective_radius,
            "base_radius": base_radius,
            "accuracy_tolerance": accuracy_tolerance,
            "teacher_accuracy": teacher_accuracy,
            "student_accuracy": student_accuracy,
            "student_latitude": latitude,
            "student_longitude": longitude,
            "teacher_latitude": session_latitude,
            "teacher_longitude": session_longitude,
        }
        ), 200

    existing_request = AttendanceRequest.query.filter_by(
        student_id=student.id,
        session_id=active_session.id,
    ).first()
    if existing_request:
        if existing_request.status == "accepted":
            return jsonify(
                {
                    "success": True,
                    "message": "Attendance already approved for this session.",
                    "already_marked": True,
                    "status": "accepted",
                }
            ), 200
        if existing_request.status == "rejected":
            return jsonify(
                {
                    "success": False,
                    "message": "Your attendance was rejected for this session. You cannot retry in the same session.",
                    "status": "rejected",
                    "distance": rounded_distance,
                }
            ), 403
        # If previously pending, do not create another request for the same session.
        if existing_request.status == "pending":
            return jsonify(
                {
                    "success": True,
                    "message": "Your attendance request is already pending for this session.",
                    "status": "pending",
                    "distance": rounded_distance,
                }
            ), 200
        # Fallback for unexpected legacy status values: refresh as pending.
        existing_request.status = "pending"
        existing_request.requested_at = datetime.now()
        existing_request.latitude = latitude
        existing_request.longitude = longitude
        existing_request.accuracy = accuracy
        existing_request.device_id = device_id
        existing_request.distance_m = rounded_distance
        db.session.commit()
        return jsonify(
            {
                "success": True,
                "message": "Attendance request sent to teacher for approval.",
                "status": "pending",
                "distance": rounded_distance,
            }
        ), 200

    attendance_request = AttendanceRequest(
        student_id=student.id,
        subject_id=active_session.subject_id,
        session_id=active_session.id,
        status="pending",
        requested_at=datetime.now(),
        latitude=latitude,
        longitude=longitude,
        accuracy=accuracy,
        device_id=device_id,
        distance_m=rounded_distance,
    )
    db.session.add(attendance_request)
    db.session.commit()

    return jsonify(
        {
            "success": True,
            "message": "Attendance request sent to teacher for approval.",
            "status": "pending",
            "distance": rounded_distance,
            "effective_radius": effective_radius,
            "base_radius": base_radius,
            "accuracy_tolerance": accuracy_tolerance,
            "teacher_accuracy": teacher_accuracy,
            "student_accuracy": student_accuracy,
            "student_latitude": latitude,
            "student_longitude": longitude,
            "teacher_latitude": session_latitude,
            "teacher_longitude": session_longitude,
        }
    )


if __name__ == "__main__":
    from waitress import serve

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    display_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    print(f"Server starting (Waitress). Open: http://{display_host}:{port}")
    try:
        serve(app, host=host, port=port)
    except OSError as exc:
        print(f"Server start failed on {host}:{port} -> {exc}")
        print("Try another port: set PORT=5001 && python app.py")
