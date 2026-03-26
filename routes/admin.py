import math
from datetime import date

from flask import flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash

from app_core import (
    ALLOWED_SEMESTER_NAMES,
    app,
    get_allowed_semesters,
    get_current_academic_session,
    get_current_semester,
    get_default_attendance_location,
    is_student_registration_open,
    login_required,
    normalize_gmail_email,
    set_default_attendance_location,
    set_student_registration_open,
)
from models import (
    Attendance,
    AttendanceRequest,
    AttendanceSession,
    Semester,
    Student,
    Subject,
    SystemSetting,
    Teacher,
    TeacherLocationHistory,
    TeacherSubjectMap,
    User,
    db,
)


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

    existing_mapping = TeacherSubjectMap.query.filter_by(
        teacher_id=teacher_id,
        subject_id=subject_id,
    ).first()
    if existing_mapping:
        flash("This subject is already assigned to the selected teacher.", "error")
        return redirect(url_for("admin_dashboard"))

    db.session.add(TeacherSubjectMap(teacher_id=teacher_id, subject_id=subject_id))
    db.session.commit()
    flash("Teacher subject assigned successfully.", "success")
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


