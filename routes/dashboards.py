from collections import defaultdict
from datetime import date, datetime

from flask import flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import func

from app_core import (
    app,
    fmt_date,
    get_allowed_semesters,
    get_default_attendance_location,
    login_required,
)
from models import (
    Attendance,
    AttendanceRequest,
    AttendanceSession,
    Semester,
    Student,
    Subject,
    Teacher,
    TeacherSubjectMap,
    User,
    db,
)


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

    subjects_query = (
        db.session.query(Subject, Semester)
        .join(TeacherSubjectMap, TeacherSubjectMap.subject_id == Subject.id)
        .join(Semester, Semester.id == Subject.semester_id)
        .filter(
            TeacherSubjectMap.teacher_id == teacher.id,
            Subject.status.is_(True),
        )
    )
    subjects = subjects_query.order_by(Semester.id, Subject.name).all()
    teacher_semester_ids = {sem.id for _, sem in subjects}
    teacher_semesters = [sem for sem in semesters if sem.id in teacher_semester_ids]

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
        teacher_semesters=teacher_semesters,
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
            "requested_at": fmt_date(req.requested_at)
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


