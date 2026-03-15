from datetime import date

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import CheckConstraint, UniqueConstraint, text


db = SQLAlchemy()
ALLOWED_USER_ROLES = ("admin", "teacher", "student")


class User(db.Model):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'teacher', 'student')", name="ck_users_role_allowed"),
    )

    id = db.Column(db.Integer, primary_key=True)
    role = db.Column(db.String(20), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), nullable=False, unique=True)
    password = db.Column(db.String(255), nullable=False)
    email_verified = db.Column(db.Boolean, default=False, nullable=False)
    department = db.Column(db.String(120), nullable=True)
    last_login_at = db.Column(db.DateTime, nullable=True)


class BlockedStudentRegistration(db.Model):
    __tablename__ = "blocked_student_registrations"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, unique=True)
    roll_no = db.Column(db.String(40), nullable=False, unique=True)
    reason = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.current_timestamp(), nullable=False)


class Semester(db.Model):
    __tablename__ = "semesters"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(40), nullable=False, unique=True)


class SystemSetting(db.Model):
    __tablename__ = "system_settings"

    key = db.Column(db.String(120), primary_key=True)
    value = db.Column(db.String(255), nullable=False)


class Student(db.Model):
    __tablename__ = "students"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True)
    roll_no = db.Column(db.String(40), nullable=False, unique=True)
    mobile_no = db.Column(db.String(20), nullable=True)
    semester_id = db.Column(db.Integer, db.ForeignKey("semesters.id"), nullable=False)
    verified = db.Column(db.Boolean, default=False, nullable=False)


class Teacher(db.Model):
    __tablename__ = "teachers"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True)
    teacher_id = db.Column(db.String(30), nullable=False, unique=True)
    department = db.Column(db.String(120), nullable=False)
    mobile_no = db.Column(db.String(20), nullable=True)


class TeacherSubjectMap(db.Model):
    __tablename__ = "teacher_subject_map"
    __table_args__ = (UniqueConstraint("teacher_id", "subject_id", name="uq_teacher_subject_map"),)

    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("teachers.id"), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=False)


class Subject(db.Model):
    __tablename__ = "subjects"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    semester_id = db.Column(db.Integer, db.ForeignKey("semesters.id"), nullable=False)
    status = db.Column(db.Boolean, default=True, nullable=False)


class AttendanceSession(db.Model):
    __tablename__ = "attendance_sessions"

    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("teachers.id"), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=False)
    semester_id = db.Column(db.Integer, db.ForeignKey("semesters.id"), nullable=False)
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    location_enforced = db.Column(db.Boolean, default=True, nullable=False)
    attendance_verified = db.Column(db.Boolean, default=False, nullable=False)


class Attendance(db.Model):
    __tablename__ = "attendance"
    __table_args__ = (UniqueConstraint("student_id", "session_id", name="uq_student_session"),)

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subjects.id"), nullable=False)
    session_id = db.Column(db.Integer, db.ForeignKey("attendance_sessions.id"), nullable=False)
    date = db.Column(db.Date, default=date.today, nullable=False)
    time = db.Column(db.Time, nullable=False)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    teacher_verified = db.Column(db.Boolean, default=False, nullable=False)
    admin_verified = db.Column(db.Boolean, default=False, nullable=False)


def _add_column_if_missing(table_name, column_name, ddl):
    pragma = db.session.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
    existing = {row[1] for row in pragma}
    if column_name not in existing:
        db.session.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {ddl}"))


def _create_index_if_missing(index_name, ddl_sql):
    rows = db.session.execute(
        text("SELECT name FROM sqlite_master WHERE type='index' AND name=:name"),
        {"name": index_name},
    ).fetchall()
    if not rows:
        db.session.execute(text(ddl_sql))


def _create_trigger_if_missing(trigger_name, ddl_sql):
    rows = db.session.execute(
        text("SELECT name FROM sqlite_master WHERE type='trigger' AND name=:name"),
        {"name": trigger_name},
    ).fetchall()
    if not rows:
        db.session.execute(text(ddl_sql))


def init_db(app):
    with app.app_context():
        db.create_all()
        _add_column_if_missing(
            "attendance_sessions",
            "attendance_verified",
            "attendance_verified BOOLEAN NOT NULL DEFAULT 0",
        )
        _add_column_if_missing(
            "attendance_sessions",
            "location_enforced",
            "location_enforced BOOLEAN NOT NULL DEFAULT 1",
        )
        _add_column_if_missing(
            "attendance",
            "admin_verified",
            "admin_verified BOOLEAN NOT NULL DEFAULT 0",
        )
        _add_column_if_missing(
            "attendance",
            "teacher_verified",
            "teacher_verified BOOLEAN NOT NULL DEFAULT 0",
        )
        _add_column_if_missing(
            "users",
            "email_verified",
            "email_verified BOOLEAN NOT NULL DEFAULT 0",
        )
        _add_column_if_missing(
            "users",
            "department",
            "department VARCHAR(120)",
        )
        _add_column_if_missing(
            "users",
            "last_login_at",
            "last_login_at DATETIME",
        )
        _add_column_if_missing(
            "students",
            "mobile_no",
            "mobile_no VARCHAR(20)",
        )
        _add_column_if_missing(
            "teachers",
            "mobile_no",
            "mobile_no VARCHAR(20)",
        )
        _add_column_if_missing(
            "teachers",
            "teacher_id",
            "teacher_id VARCHAR(30)",
        )
        # Backward compatibility: old projects may still have teacher_code.
        _add_column_if_missing(
            "teachers",
            "teacher_code",
            "teacher_code VARCHAR(30)",
        )
        _create_index_if_missing(
            "ux_teachers_teacher_id",
            "CREATE UNIQUE INDEX ux_teachers_teacher_id ON teachers(teacher_id)",
        )
        _create_trigger_if_missing(
            "trg_users_role_insert",
            """
            CREATE TRIGGER trg_users_role_insert
            BEFORE INSERT ON users
            FOR EACH ROW
            WHEN NEW.role NOT IN ('admin', 'teacher', 'student')
            BEGIN
                SELECT RAISE(ABORT, 'invalid user role');
            END
            """,
        )
        _create_trigger_if_missing(
            "trg_users_role_update",
            """
            CREATE TRIGGER trg_users_role_update
            BEFORE UPDATE OF role ON users
            FOR EACH ROW
            WHEN NEW.role NOT IN ('admin', 'teacher', 'student')
            BEGIN
                SELECT RAISE(ABORT, 'invalid user role');
            END
            """,
        )
        _create_trigger_if_missing(
            "trg_teachers_teacher_id_insert",
            """
            CREATE TRIGGER trg_teachers_teacher_id_insert
            BEFORE INSERT ON teachers
            FOR EACH ROW
            WHEN NEW.teacher_id IS NULL OR trim(NEW.teacher_id) = ''
            BEGIN
                SELECT RAISE(ABORT, 'teacher_id is required');
            END
            """,
        )
        _create_trigger_if_missing(
            "trg_teachers_teacher_id_update",
            """
            CREATE TRIGGER trg_teachers_teacher_id_update
            BEFORE UPDATE OF teacher_id ON teachers
            FOR EACH ROW
            WHEN NEW.teacher_id IS NULL OR trim(NEW.teacher_id) = ''
            BEGIN
                SELECT RAISE(ABORT, 'teacher_id is required');
            END
            """,
        )

        for sem in range(1, 7):
            sem_name = f"Semester {sem}"
            if not Semester.query.filter_by(name=sem_name).first():
                db.session.add(Semester(name=sem_name))

        # Cleanup invalid/debug subject entries if present.
        Subject.query.filter(db.func.lower(Subject.name) == "bug subject").delete(
            synchronize_session=False
        )

        first_semester = Semester.query.order_by(Semester.id).first()
        if first_semester and not SystemSetting.query.filter_by(key="current_semester_id").first():
            db.session.add(SystemSetting(key="current_semester_id", value=str(first_semester.id)))

        # Data repair: ensure profiles in teachers table are mapped to teacher role users.
        valid_roles = set(ALLOWED_USER_ROLES)
        for user in User.query.all():
            normalized_role = (user.role or "").strip().lower()
            if normalized_role in valid_roles:
                user.role = normalized_role
                continue
            if Teacher.query.filter_by(user_id=user.id).first():
                user.role = "teacher"
            elif Student.query.filter_by(user_id=user.id).first():
                user.role = "student"
            else:
                user.role = "student"

        for teacher in Teacher.query.all():
            user = db.session.get(User, teacher.user_id)
            if user and user.role != "teacher":
                user.role = "teacher"
                user.email_verified = True
            # Ensure existing rows get a stable, unique teacher_id during migration.
            if not teacher.teacher_id:
                legacy_code = getattr(teacher, "teacher_code", None)
                teacher.teacher_id = legacy_code if legacy_code else f"TCH{teacher.id:04d}"

        db.session.commit()
