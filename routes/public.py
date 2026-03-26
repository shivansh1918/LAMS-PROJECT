from datetime import datetime

from flask import flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from app_core import (
    ALLOWED_ROLES,
    ALLOWED_SEMESTER_NAMES,
    app,
    block_registration_for_logged_in_user,
    get_allowed_semesters,
    get_current_academic_session,
    is_admin_registration_locked,
    is_student_registration_open,
    login_required,
    normalize_gmail_email,
    role_dashboard,
    set_admin_registration_lock,
)
from models import Semester, Student, User, db


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


