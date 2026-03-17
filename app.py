import math
import os
from collections import defaultdict
from datetime import date, datetime
from functools import wraps

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func, or_
from werkzeug.security import check_password_hash, generate_password_hash

from models import (
    Attendance,
    AttendanceSession,
    BlockedStudentRegistration,
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


@app.template_filter("fmt_last_login")
def fmt_last_login(value):
    """Format dashboard last-login timestamps (no seconds, DD/MM/YYYY)."""
    if not value:
        return ""
    try:
        return value.strftime("%d/%m/%Y %H:%M")
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


def get_current_academic_session():
    setting = SystemSetting.query.filter_by(key="current_academic_session").first()
    return setting.value if setting else None


def set_current_academic_session(label):
    setting = SystemSetting.query.filter_by(key="current_academic_session").first()
    if setting:
        setting.value = label
    else:
        setting = SystemSetting(key="current_academic_session", value=label)
        db.session.add(setting)
    db.session.commit()


def generate_next_session_label(current_label=None):
    if current_label:
        parts = current_label.strip().split("-")
        if len(parts) == 2 and parts[0].isdigit() and len(parts[0]) == 4 and parts[1].isdigit():
            start_year = int(parts[0])
            next_start = start_year + 1
            return f"{next_start}-{str(next_start + 1)[-2:]}"
    year = datetime.now().year
    return f"{year}-{str(year + 1)[-2:]}"


def student_self_registration_blocked(email, roll_no):
    email = (email or "").strip().lower()
    roll_no = (roll_no or "").strip().upper()
    return (
        BlockedStudentRegistration.query.filter(
            or_(
                BlockedStudentRegistration.email == email,
                BlockedStudentRegistration.roll_no == roll_no,
            )
        ).first()
        is not None
    )


def block_student_self_registration(email, roll_no, reason="Deleted by admin"):
    email = (email or "").strip().lower()
    roll_no = (roll_no or "").strip().upper()
    if not email or not roll_no:
        return

    row = (
        BlockedStudentRegistration.query.filter(
            or_(
                BlockedStudentRegistration.email == email,
                BlockedStudentRegistration.roll_no == roll_no,
            )
        ).first()
    )
    if row:
        row.email = email
        row.roll_no = roll_no
        row.reason = reason
    else:
        db.session.add(
            BlockedStudentRegistration(
                email=email,
                roll_no=roll_no,
                reason=reason,
            )
        )


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
            existing_student.verified = True
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
                    verified=True,
                )
            )
            db.session.commit()
            flash("Student registration successful.", "success")

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
                "roll_no": stu.roll_no,
                "name": usr.name,
                "semester": sem.name,
                "total_sessions": total_sessions,
                "percentage": percentage,
                "attended_sessions": present,
            },
        }
    )


@app.post("/api/teacher/session/start")
@login_required(role="teacher")
def start_session():
    data = request.get_json(silent=True) or {}
    subject_id = data.get("subject_id")
    latitude = data.get("latitude")
    longitude = data.get("longitude")
    accuracy = data.get("accuracy")

    if subject_id in (None, ""):
        return jsonify({"success": False, "message": "Subject is required."}), 400
    try:
        subject_id = int(subject_id)
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Invalid subject value."}), 400

    has_location = latitude not in (None, "") and longitude not in (None, "")
    if not has_location:
        return jsonify(
            {
                "success": False,
                "message": "Location is required. Please allow GPS permission and try again.",
            }
        ), 400
    if has_location:
        try:
            latitude = float(latitude)
            longitude = float(longitude)
        except (TypeError, ValueError):
            return jsonify({"success": False, "message": "Invalid location values."}), 400
        # Strict server-side GPS validation: session cannot start with invalid coordinates.
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
        location_enforced=True,
    )
    db.session.add(new_session)
    db.session.commit()

    warning = None
    if accuracy > 50:
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
    return jsonify({"success": True, "message": message, "warning": warning})


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

    if session_id in (None, ""):
        return jsonify({"success": False, "message": "Session is required."}), 400
    try:
        session_id = int(session_id)
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Invalid session value."}), 400

    student = Student.query.filter_by(user_id=session["user_id"]).first()
    active_session = AttendanceSession.query.filter_by(id=session_id, is_active=True).first()

    if not student or not active_session:
        return jsonify({"success": False, "message": "Active session not found."}), 404

    subject = db.session.get(Subject, active_session.subject_id)
    if not subject or not subject.status:
        return jsonify({"success": False, "message": "Subject is disabled by admin."}), 400

    if student.semester_id != active_session.semester_id:
        return jsonify({"success": False, "message": "Semester mismatch. You cannot mark this attendance."}), 403

    # Student GPS is mandatory for marking attendance.
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


    # Strict 50-meter geofence check with GPS accuracy tolerance.
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

    distance = haversine_meters(
        latitude,
        longitude,
        session_latitude,
        session_longitude,
    )
    if not math.isfinite(distance):
        return jsonify({"success": False, "message": "Invalid distance calculation."}), 400

    allowed_radius = 50.0
    teacher_accuracy = max(float(getattr(active_session, "location_accuracy", 0.0) or 0.0), 0.0)
    student_accuracy = max(float(accuracy or 0.0), 0.0)
    # Allow a buffer for GPS error from either device, capped to avoid unlimited radius.
    accuracy_buffer = min(max(teacher_accuracy, student_accuracy), 50.0)
    effective_radius = allowed_radius + accuracy_buffer
    rounded_distance = round(distance, 2)
    app.logger.info(
        "Attendance distance check | student=(%s,%s acc=%.2f) teacher=(%s,%s acc=%.2f) distance_m=%.2f radius=%.2f eff_radius=%.2f",
        latitude,
        longitude,
        accuracy,
        session_latitude,
        session_longitude,
        getattr(active_session, "location_accuracy", 0.0) or 0.0,
        rounded_distance,
        effective_radius,
        effective_radius,
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
                    "message": "You are outside the allowed attendance range.",
                    "distance": rounded_distance,
                    "allowed_radius": allowed_radius,
                    "effective_radius": effective_radius,
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
            "message": "Attendance already marked for this active session.",
            "already_marked": True,
            "distance": rounded_distance,
            "effective_radius": effective_radius,
            "student_latitude": latitude,
            "student_longitude": longitude,
            "teacher_latitude": session_latitude,
            "teacher_longitude": session_longitude,
        }
        ), 200

    # Auto-verified because distance check is enforced at marking time.
    record = Attendance(
        student_id=student.id,
        subject_id=active_session.subject_id,
        session_id=active_session.id,
        date=date.today(),
        time=datetime.now().time().replace(microsecond=0),
        latitude=latitude,
        longitude=longitude,
        teacher_verified=True,
        admin_verified=True,
    )
    db.session.add(record)
    db.session.commit()

    message = f"Attendance marked successfully. Distance: {rounded_distance}m."
    return jsonify(
        {
            "success": True,
            "message": message,
            "distance": rounded_distance,
            "effective_radius": effective_radius,
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
