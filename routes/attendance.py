import math
from datetime import datetime

from flask import jsonify, request, session

from app_core import (
    ATTENDANCE_ALLOWED_RADIUS_M,
    ATTENDANCE_MAX_ACCURACY_TOLERANCE_M,
    GPS_ACCURACY_ACCEPT_MAX_M,
    app,
    fmt_date,
    get_default_attendance_location,
    get_device_fingerprint,
    haversine_meters,
    login_required,
)
from models import (
    Attendance,
    AttendanceRequest,
    AttendanceSession,
    Student,
    Subject,
    Teacher,
    TeacherLocationHistory,
    TeacherSubjectMap,
    Semester,
    db,
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

    subjects = (
        Subject.query.join(TeacherSubjectMap, TeacherSubjectMap.subject_id == Subject.id)
        .filter(
            TeacherSubjectMap.teacher_id == teacher.id,
            Subject.semester_id == semester_id,
            Subject.status.is_(True),
        )
        .order_by(Subject.name.asc())
        .all()
    )
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


