"""Flask API and local web server for the Markwise school management project."""

from datetime import date, datetime, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from flask import Flask, current_app, g, jsonify, request, send_from_directory

from database import DEFAULT_EXAM, initialize_database


ROOT = Path(__file__).resolve().parent
OUTPUTS = ROOT / "outputs"
DEFAULT_DATABASE = ROOT / "instance" / "markwise.sqlite3"
SCHOOL_ID = 1
MAX_MARK = 100.0


def create_app(test_config=None):
    """Build an app instance, initialize its database, and register its routes."""
    app = Flask(__name__)
    app.config.from_mapping(
        DATABASE=os.environ.get("MARKWISE_DATABASE", str(DEFAULT_DATABASE)),
        MAX_CONTENT_LENGTH=1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)

    initialize_database(app.config["DATABASE"])

    @app.teardown_appcontext
    def close_database(_error=None):
        connection = g.pop("database", None)
        if connection is not None:
            connection.close()

    @app.get("/")
    def home():
        return send_from_directory(OUTPUTS, "index.html")

    @app.get("/styles.css")
    def styles():
        return send_from_directory(OUTPUTS, "styles.css")

    @app.get("/app.js")
    def javascript():
        return send_from_directory(OUTPUTS, "app.js")

    @app.get("/api/students")
    def list_students():
        search = request.args.get("search", "").strip()
        if len(search) > 100:
            return _error("Search text must be 100 characters or fewer.", 400)
        connection = _get_database()
        filters = ["st.school_id = ?", "st.is_active = 1"]
        parameters = [SCHOOL_ID]
        academic_year_id = _optional_positive_id(request.args.get("academicYearId"))
        class_id = _optional_positive_id(request.args.get("classId"))
        section_id = _optional_positive_id(request.args.get("sectionId"))
        status = request.args.get("status", "").strip()
        if request.args.get("academicYearId") and academic_year_id is None:
            return _error("Academic year ID must be a positive whole number.", 400)
        if request.args.get("classId") and class_id is None:
            return _error("Class ID must be a positive whole number.", 400)
        if request.args.get("sectionId") and section_id is None:
            return _error("Section ID must be a positive whole number.", 400)
        if academic_year_id:
            filters.append("en.academic_year_id = ?")
            parameters.append(academic_year_id)
        if class_id:
            filters.append("en.class_id = ?" if academic_year_id else "COALESCE(en.class_id, st.class_id) = ?")
            parameters.append(class_id)
        if section_id:
            filters.append("en.section_id = ?" if academic_year_id else "COALESCE(en.section_id, st.section_id) = ?")
            parameters.append(section_id)
        if status:
            if status not in {"active", "completed", "transferred", "archived"}:
                return _error("Choose a valid enrollment status.", 400)
            filters.append("COALESCE(en.status, st.enrollment_status) = ?")
            parameters.append(status)
        if search:
            pattern = f"%{search}%"
            filters.append("(st.name LIKE ? OR st.student_code LIKE ? OR st.admission_number LIKE ? OR st.roll_number LIKE ?)")
            parameters.extend([pattern] * 4)
        rows = connection.execute(
                f"""SELECT st.id, st.student_code, st.admission_number, st.name, st.roll_number,
                          cl.name AS class_name, se.name AS section_name,
                          st.guardian_name, st.guardian_contact, st.admission_date,
                          st.enrollment_status,
                          st.created_at, st.updated_at
                   FROM students AS st
                   LEFT JOIN student_enrollments AS en ON en.student_id = st.id
                     AND en.academic_year_id = COALESCE(?, (SELECT id FROM academic_years WHERE school_id = ? AND is_current = 1))
                   LEFT JOIN academic_years AS ay ON ay.id = en.academic_year_id
                   LEFT JOIN classes AS cl ON cl.id = COALESCE(en.class_id, st.class_id)
                   LEFT JOIN sections AS se ON se.id = COALESCE(en.section_id, st.section_id)
                   WHERE {' AND '.join(filters)}
                   ORDER BY st.name COLLATE NOCASE, st.student_code COLLATE NOCASE""",
                [academic_year_id, SCHOOL_ID, *parameters],
            ).fetchall()
        # Keep a single student row even when they have multiple historical enrollments.
        unique_rows = {row["id"]: dict(row) for row in rows}
        return jsonify(students=list(unique_rows.values()), total=len(unique_rows))

    @app.get("/api/academic-years")
    def list_academic_years():
        rows = _get_database().execute(
            "SELECT id, label, starts_on, ends_on, is_current FROM academic_years WHERE school_id = ? ORDER BY starts_on DESC, label DESC",
            (SCHOOL_ID,),
        ).fetchall()
        return jsonify(academicYears=[dict(row) for row in rows])

    @app.post("/api/academic-years")
    def add_academic_year():
        payload, error = _read_json_object()
        if error:
            return error
        values, error = _validate_academic_year(payload)
        if error:
            return error
        connection = _get_database()
        try:
            with connection:
                if values["is_current"]:
                    connection.execute("UPDATE academic_years SET is_current = 0 WHERE school_id = ?", (SCHOOL_ID,))
                cursor = connection.execute(
                    "INSERT INTO academic_years(school_id, label, starts_on, ends_on, is_current) VALUES (?, ?, ?, ?, ?)",
                    (SCHOOL_ID, values["label"], values["starts_on"], values["ends_on"], values["is_current"]),
                )
            row = connection.execute("SELECT id, label, starts_on, ends_on, is_current FROM academic_years WHERE id = ?", (cursor.lastrowid,)).fetchone()
        except sqlite3.IntegrityError as error:
            return _integrity_error(error)
        return jsonify(academicYear=dict(row)), 201

    @app.put("/api/academic-years/<int:year_id>")
    def edit_academic_year(year_id):
        payload, error = _read_json_object()
        if error:
            return error
        values, error = _validate_academic_year(payload)
        if error:
            return error
        connection = _get_database()
        if not connection.execute("SELECT 1 FROM academic_years WHERE id = ? AND school_id = ?", (year_id, SCHOOL_ID)).fetchone():
            return _error("Academic year not found.", 404)
        try:
            with connection:
                if values["is_current"]:
                    connection.execute("UPDATE academic_years SET is_current = 0 WHERE school_id = ?", (SCHOOL_ID,))
                connection.execute(
                    "UPDATE academic_years SET label = ?, starts_on = ?, ends_on = ?, is_current = ? WHERE id = ?",
                    (values["label"], values["starts_on"], values["ends_on"], values["is_current"], year_id),
                )
        except sqlite3.IntegrityError as error:
            return _integrity_error(error)
        row = connection.execute("SELECT id, label, starts_on, ends_on, is_current FROM academic_years WHERE id = ?", (year_id,)).fetchone()
        return jsonify(academicYear=dict(row))

    @app.get("/api/classes")
    def list_classes():
        rows = _get_database().execute(
            """SELECT cl.id, cl.name, COUNT(DISTINCT se.id) AS section_count,
                      COUNT(DISTINCT en.student_id) AS active_students
               FROM classes AS cl LEFT JOIN sections AS se ON se.class_id = cl.id
               LEFT JOIN student_enrollments AS en ON en.class_id = cl.id AND en.status = 'active'
                 AND en.academic_year_id = (SELECT id FROM academic_years WHERE school_id = ? AND is_current = 1)
               WHERE cl.school_id = ? GROUP BY cl.id ORDER BY cl.name COLLATE NOCASE""",
            (SCHOOL_ID, SCHOOL_ID),
        ).fetchall()
        return jsonify(classes=[dict(row) for row in rows])

    @app.post("/api/classes")
    def add_class():
        payload, error = _read_json_object()
        if error:
            return error
        name, error = _clean_text(payload.get("name"), "Class name", required=True, limit=80)
        if error:
            return error
        connection = _get_database()
        try:
            with connection:
                cursor = connection.execute("INSERT INTO classes(school_id, name) VALUES (?, ?)", (SCHOOL_ID, name))
        except sqlite3.IntegrityError as error:
            return _integrity_error(error)
        return jsonify(classRecord={"id": cursor.lastrowid, "name": name, "section_count": 0, "active_students": 0}), 201

    @app.get("/api/sections")
    def list_sections():
        rows = _get_database().execute(
            "SELECT se.id, se.name, cl.id AS class_id, cl.name AS class_name FROM sections AS se JOIN classes AS cl ON cl.id = se.class_id WHERE cl.school_id = ? ORDER BY cl.name COLLATE NOCASE, se.name COLLATE NOCASE",
            (SCHOOL_ID,),
        ).fetchall()
        return jsonify(sections=[dict(row) for row in rows])

    @app.post("/api/classes/<int:class_id>/sections")
    def add_section(class_id):
        payload, error = _read_json_object()
        if error:
            return error
        name, error = _clean_text(payload.get("name"), "Section name", required=True, limit=80)
        if error:
            return error
        connection = _get_database()
        if not connection.execute("SELECT 1 FROM classes WHERE id = ? AND school_id = ?", (class_id, SCHOOL_ID)).fetchone():
            return _error("Class not found.", 404)
        try:
            with connection:
                cursor = connection.execute("INSERT INTO sections(class_id, name) VALUES (?, ?)", (class_id, name))
        except sqlite3.IntegrityError as error:
            return _integrity_error(error)
        return jsonify(section={"id": cursor.lastrowid, "name": name, "class_id": class_id}), 201

    @app.get("/api/students/<int:student_id>/enrollments")
    def list_student_enrollments(student_id):
        if _fetch_student(_get_database(), student_id) is None:
            return _error("Student not found.", 404)
        rows = _get_database().execute(
            """SELECT en.id, en.academic_year_id, ay.label AS academic_year,
                      en.class_id, cl.name AS class_name, en.section_id,
                      se.name AS section_name, en.roll_number, en.status
               FROM student_enrollments AS en
               JOIN academic_years AS ay ON ay.id = en.academic_year_id
               JOIN classes AS cl ON cl.id = en.class_id
               LEFT JOIN sections AS se ON se.id = en.section_id
               WHERE en.student_id = ? ORDER BY ay.starts_on DESC, ay.label DESC""",
            (student_id,),
        ).fetchall()
        return jsonify(enrollments=[dict(row) for row in rows])

    @app.post("/api/students/<int:student_id>/enrollments")
    def enroll_student(student_id):
        payload, error = _read_json_object()
        if error:
            return error
        values, error = _validate_enrollment_payload(payload)
        if error:
            return error
        connection = _get_database()
        if _fetch_student(connection, student_id) is None:
            return _error("Student not found.", 404)
        if not connection.execute("SELECT 1 FROM academic_years WHERE id = ? AND school_id = ?", (values["academic_year_id"], SCHOOL_ID)).fetchone():
            return _error("Choose an existing academic year.", 400)
        if not connection.execute("SELECT 1 FROM classes WHERE id = ? AND school_id = ?", (values["class_id"], SCHOOL_ID)).fetchone():
            return _error("Choose an existing class.", 400)
        if values["section_id"] and not connection.execute("SELECT 1 FROM sections WHERE id = ? AND class_id = ?", (values["section_id"], values["class_id"])).fetchone():
            return _error("The selected section does not belong to that class.", 400)
        try:
            with connection:
                connection.execute(
                    """INSERT INTO student_enrollments
                           (student_id, academic_year_id, class_id, section_id, roll_number, status)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(student_id, academic_year_id) DO UPDATE SET
                           class_id = excluded.class_id, section_id = excluded.section_id,
                           roll_number = excluded.roll_number, status = excluded.status""",
                    (student_id, values["academic_year_id"], values["class_id"], values["section_id"], values["roll_number"], values["status"]),
                )
                if values["academic_year_id"] == connection.execute("SELECT id FROM academic_years WHERE school_id = ? AND is_current = 1", (SCHOOL_ID,)).fetchone()[0]:
                    connection.execute(
                        "UPDATE students SET class_id = ?, section_id = ?, roll_number = ?, enrollment_status = ?, updated_at = ? WHERE id = ?",
                        (values["class_id"], values["section_id"], values["roll_number"], values["status"], _timestamp(), student_id),
                    )
        except sqlite3.IntegrityError as error:
            return _integrity_error(error)
        row = connection.execute(
            """SELECT en.id, en.academic_year_id, ay.label AS academic_year, en.class_id,
                      cl.name AS class_name, en.section_id, se.name AS section_name,
                      en.roll_number, en.status FROM student_enrollments AS en
               JOIN academic_years AS ay ON ay.id = en.academic_year_id
               JOIN classes AS cl ON cl.id = en.class_id
               LEFT JOIN sections AS se ON se.id = en.section_id
               WHERE en.student_id = ? AND en.academic_year_id = ?""",
            (student_id, values["academic_year_id"]),
        ).fetchone()
        return jsonify(enrollment=dict(row)), 201

    @app.post("/api/students")
    def add_student():
        payload, error = _read_json_object()
        if error:
            return error
        values, error = _validate_student_payload(payload)
        if error:
            return error

        connection = _get_database()
        now = _timestamp()
        try:
            with connection:
                class_id, section_id = _resolve_class_and_section(connection, values)
                student_code = values["student_code"] or _new_student_code(connection)
                cursor = connection.execute(
                    """INSERT INTO students
                           (school_id, student_code, admission_number, name, roll_number,
                            class_id, section_id, guardian_name, guardian_contact,
                            admission_date, enrollment_status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (SCHOOL_ID, student_code, values["admission_number"], values["name"],
                     values["roll_number"], class_id, section_id, values["guardian_name"],
                     values["guardian_contact"], values["admission_date"], values["enrollment_status"], now, now),
                )
                student_id = cursor.lastrowid
                _sync_current_enrollment(connection, student_id, class_id, section_id,
                                         values["roll_number"], values["enrollment_status"])
        except sqlite3.IntegrityError as error:
            return _integrity_error(error)
        student = _fetch_student(connection, student_id)
        return jsonify(student=student), 201

    @app.get("/api/students/<int:student_id>")
    def get_student(student_id):
        connection = _get_database()
        student = _fetch_student(connection, student_id)
        if student is None:
            return _error("Student not found.", 404)
        marks = connection.execute(
            """SELECT ex.name AS exam_name, su.name AS subject_name,
                      m.score, es.maximum_marks, es.passing_marks
               FROM marks AS m
               JOIN exam_subjects AS es ON es.id = m.exam_subject_id
               JOIN exams AS ex ON ex.id = es.exam_id
               JOIN subjects AS su ON su.id = es.subject_id
               WHERE m.student_id = ?
               ORDER BY ex.name COLLATE NOCASE, su.name COLLATE NOCASE""",
            (student_id,),
        ).fetchall()
        student["marks"] = [dict(row) for row in marks]
        enrollments = connection.execute(
            """SELECT en.id, en.academic_year_id, ay.label AS academic_year,
                      en.class_id, cl.name AS class_name, en.section_id,
                      se.name AS section_name, en.roll_number, en.status
               FROM student_enrollments AS en
               JOIN academic_years AS ay ON ay.id = en.academic_year_id
               JOIN classes AS cl ON cl.id = en.class_id
               LEFT JOIN sections AS se ON se.id = en.section_id
               WHERE en.student_id = ? ORDER BY ay.starts_on DESC, ay.label DESC""",
            (student_id,),
        ).fetchall()
        student["enrollments"] = [dict(row) for row in enrollments]
        return jsonify(student=student)

    @app.put("/api/students/<int:student_id>")
    def edit_student(student_id):
        payload, error = _read_json_object()
        if error:
            return error
        values, error = _validate_student_payload(payload)
        if error:
            return error

        connection = _get_database()
        existing_student = _fetch_student(connection, student_id)
        if existing_student is None:
            return _error("Student not found.", 404)
        if not values["student_code"]:
            values["student_code"] = existing_student["student_code"]
        try:
            with connection:
                class_id, section_id = _resolve_class_and_section(connection, values)
                connection.execute(
                    """UPDATE students
                       SET student_code = ?, admission_number = ?, name = ?, roll_number = ?,
                           class_id = ?, section_id = ?, guardian_name = ?, guardian_contact = ?,
                           admission_date = ?, enrollment_status = ?, updated_at = ?
                       WHERE id = ? AND school_id = ? AND is_active = 1""",
                    (values["student_code"], values["admission_number"], values["name"],
                     values["roll_number"], class_id, section_id, values["guardian_name"],
                     values["guardian_contact"], values["admission_date"], values["enrollment_status"],
                     _timestamp(), student_id, SCHOOL_ID),
                )
                _sync_current_enrollment(connection, student_id, class_id, section_id,
                                         values["roll_number"], values["enrollment_status"])
        except sqlite3.IntegrityError as error:
            return _integrity_error(error)
        return jsonify(student=_fetch_student(connection, student_id))

    @app.delete("/api/students/<int:student_id>")
    def delete_student(student_id):
        connection = _get_database()
        student = _fetch_student(connection, student_id)
        if student is None:
            return _error("Student not found.", 404)
        # Archive instead of destroying linked examination marks and future school records.
        with connection:
            connection.execute(
                "UPDATE students SET is_active = 0, enrollment_status = 'archived', updated_at = ? WHERE id = ?",
                (_timestamp(), student_id),
            )
        return jsonify(message="Student removed from the active list; saved records were retained.")

    @app.post("/api/analysis")
    def save_and_analyze():
        payload, error = _read_json_object()
        if error:
            return error
        clean, error = _validate_analysis_payload(payload)
        if error:
            return error

        connection = _get_database()
        exam_row = connection.execute(
            "SELECT id FROM exams WHERE school_id = ? AND name = ?",
            (SCHOOL_ID, DEFAULT_EXAM),
        ).fetchone()
        if exam_row is None:
            return _error("The default examination is missing. Restart Markwise to initialize it.", 503)

        student_ids = [student["id"] for student in clean["students"]]
        placeholders = ",".join("?" for _ in student_ids)
        existing_ids = {
            row["id"] for row in connection.execute(
                f"SELECT id FROM students WHERE school_id = ? AND is_active = 1 AND id IN ({placeholders})",
                (SCHOOL_ID, *student_ids),
            ).fetchall()
        }
        if existing_ids != set(student_ids):
            return _error("One or more selected students are no longer active.", 400)

        marks_by_student = {}
        exam_subject_ids = []
        subject_ids = []
        try:
            with connection:
                for subject_name in clean["subjects"]:
                    connection.execute(
                        "INSERT OR IGNORE INTO subjects(school_id, name) VALUES (?, ?)",
                        (SCHOOL_ID, subject_name),
                    )
                    subject_id = connection.execute(
                        "SELECT id FROM subjects WHERE school_id = ? AND name = ? COLLATE NOCASE",
                        (SCHOOL_ID, subject_name),
                    ).fetchone()[0]
                    subject_ids.append(subject_id)
                    connection.execute(
                        """INSERT INTO exam_subjects(exam_id, subject_id, maximum_marks, passing_marks)
                           VALUES (?, ?, ?, ?)
                           ON CONFLICT(exam_id, subject_id) DO UPDATE SET
                               maximum_marks = excluded.maximum_marks,
                               passing_marks = excluded.passing_marks""",
                        (exam_row["id"], subject_id, MAX_MARK, clean["pass_mark"]),
                    )
                    exam_subject_id = connection.execute(
                        "SELECT id FROM exam_subjects WHERE exam_id = ? AND subject_id = ?",
                        (exam_row["id"], subject_id),
                    ).fetchone()[0]
                    exam_subject_ids.append(exam_subject_id)

                for student in clean["students"]:
                    marks_by_student[student["id"]] = []
                    for exam_subject_id, score in zip(exam_subject_ids, student["marks"]):
                        connection.execute(
                            """INSERT INTO marks(student_id, exam_subject_id, score, updated_at)
                               VALUES (?, ?, ?, ?)
                               ON CONFLICT(student_id, exam_subject_id) DO UPDATE SET
                                   score = excluded.score, updated_at = excluded.updated_at""",
                            (student["id"], exam_subject_id, score, _timestamp()),
                        )
                        saved_score = connection.execute(
                            "SELECT score FROM marks WHERE student_id = ? AND exam_subject_id = ?",
                            (student["id"], exam_subject_id),
                        ).fetchone()[0]
                        marks_by_student[student["id"]].append(saved_score)
                connection.execute(
                    """INSERT INTO analysis_sessions
                           (exam_id, pass_mark, subject_ids_json, student_ids_json)
                       VALUES (?, ?, ?, ?)""",
                    (exam_row["id"], clean["pass_mark"], json.dumps(subject_ids),
                     json.dumps(student_ids)),
                )
        except sqlite3.IntegrityError as error:
            return _integrity_error(error)

        rows = connection.execute(
            """SELECT id, student_code, name FROM students
               WHERE school_id = ? AND is_active = 1 AND id IN ({})""".format(
                ",".join("?" for _ in student_ids)
            ),
            (SCHOOL_ID, *student_ids),
        ).fetchall()
        student_by_id = {row["id"]: row for row in rows}
        results = []
        for student in clean["students"]:
            record = student_by_id[student["id"]]
            saved_marks = marks_by_student[student["id"]]
            average = sum(saved_marks) / len(saved_marks)
            results.append({
                "id": record["id"],
                "studentCode": record["student_code"],
                "name": record["name"],
                "marks": saved_marks,
                "average": average,
                "status": "Pass" if average >= clean["pass_mark"] else "Fail",
            })
        return jsonify(analysis=_analysis_summary(
            DEFAULT_EXAM, clean["subjects"], clean["pass_mark"], results
        ))

    @app.get("/api/analysis/latest")
    def latest_analysis():
        connection = _get_database()
        session = connection.execute(
            """SELECT exam_id, pass_mark, subject_ids_json, student_ids_json
               FROM analysis_sessions ORDER BY id DESC LIMIT 1"""
        ).fetchone()
        if session is None:
            return jsonify(analysis=None)

        subject_ids = json.loads(session["subject_ids_json"])
        student_ids = json.loads(session["student_ids_json"])
        subjects = []
        exam_subject_ids = []
        for subject_id in subject_ids:
            row = connection.execute(
                """SELECT su.name, es.id AS exam_subject_id
                   FROM exam_subjects AS es JOIN subjects AS su ON su.id = es.subject_id
                   WHERE es.exam_id = ? AND es.subject_id = ?""",
                (session["exam_id"], subject_id),
            ).fetchone()
            if row is None:
                return _error("Saved analysis settings are incomplete. Check the database.", 503)
            subjects.append(row["name"])
            exam_subject_ids.append(row["exam_subject_id"])

        placeholders = ",".join("?" for _ in student_ids)
        students = connection.execute(
            f"""SELECT id, student_code, name FROM students
                WHERE school_id = ? AND is_active = 1 AND id IN ({placeholders})""",
            (SCHOOL_ID, *student_ids),
        ).fetchall()
        student_by_id = {row["id"]: row for row in students}
        results = []
        for student_id in student_ids:
            record = student_by_id.get(student_id)
            if record is None:
                continue
            saved_marks = []
            for exam_subject_id in exam_subject_ids:
                mark = connection.execute(
                    "SELECT score FROM marks WHERE student_id = ? AND exam_subject_id = ?",
                    (student_id, exam_subject_id),
                ).fetchone()
                if mark is None or mark["score"] is None:
                    return _error("The saved analysis has a missing mark. Check the database.", 503)
                saved_marks.append(mark["score"])
            average = sum(saved_marks) / len(saved_marks)
            results.append({
                "id": record["id"],
                "studentCode": record["student_code"],
                "name": record["name"],
                "marks": saved_marks,
                "average": average,
                "status": "Pass" if average >= session["pass_mark"] else "Fail",
            })
        if not results:
            return jsonify(analysis=None)
        exam = connection.execute(
            "SELECT name FROM exams WHERE id = ?", (session["exam_id"],)
        ).fetchone()
        return jsonify(analysis=_analysis_summary(
            exam["name"], subjects, session["pass_mark"], results
        ))

    @app.errorhandler(404)
    def not_found(_exception):
        return _error("The requested page or record was not found.", 404)

    @app.errorhandler(413)
    def request_too_large(_exception):
        return _error("The request is too large.", 413)

    @app.errorhandler(sqlite3.Error)
    def database_error(error):
        current_app.logger.error("SQLite operation failed: %s", type(error).__name__)
        return _error("The database could not complete that request. Check the database file and try again.", 503)

    @app.errorhandler(Exception)
    def unexpected_error(error):
        current_app.logger.error("Request failed: %s", type(error).__name__)
        return _error("Something went wrong. Please try again.", 500)

    return app


def _get_database():
    """Return this request's SQLite connection, opening it only when needed."""
    if "database" not in g:
        from database import connect_database

        g.database = connect_database(current_app.config["DATABASE"])
    return g.database


def _read_json_object():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return None, _error("Send a JSON object with the required fields.", 400)
    return payload, None


def _validate_student_payload(payload):
    name, error = _clean_text(payload.get("name"), "Student name", required=True, limit=100)
    if error:
        return None, error
    student_code, error = _clean_text(payload.get("studentCode"), "Student ID", limit=40)
    if error:
        return None, error
    roll_number, error = _clean_text(payload.get("rollNumber"), "Roll number", limit=40)
    if error:
        return None, error
    class_name, error = _clean_text(payload.get("className"), "Class", limit=80)
    if error:
        return None, error
    section_name, error = _clean_text(payload.get("sectionName"), "Section", limit=80)
    if error:
        return None, error
    if section_name and not class_name:
        return None, _error("Enter a class before entering a section.", 400)
    admission_number, error = _clean_text(payload.get("admissionNumber"), "Admission number", required=True, limit=60)
    if error:
        return None, error
    guardian_name, error = _clean_text(payload.get("guardianName"), "Guardian name", limit=100)
    if error:
        return None, error
    guardian_contact, error = _clean_text(payload.get("guardianContact"), "Guardian contact", limit=40)
    if error:
        return None, error
    admission_date, error = _clean_text(payload.get("admissionDate"), "Admission date", limit=10)
    if error:
        return None, error
    if admission_date:
        try:
            date.fromisoformat(admission_date)
        except ValueError:
            return None, _error("Admission date must use YYYY-MM-DD format.", 400)
    enrollment_status = payload.get("enrollmentStatus", "active")
    if not isinstance(enrollment_status, str) or enrollment_status not in {"active", "completed", "transferred"}:
        return None, _error("Choose active, completed, or transferred enrollment status.", 400)
    return {
        "name": name,
        "student_code": student_code,
        "admission_number": admission_number,
        "guardian_name": guardian_name or None,
        "guardian_contact": guardian_contact or None,
        "admission_date": admission_date or None,
        "enrollment_status": enrollment_status,
        "roll_number": roll_number or None,
        "class_name": class_name,
        "section_name": section_name,
    }, None


def _validate_analysis_payload(payload):
    pass_mark = _finite_number(payload.get("passMark"), "Pass mark")
    if pass_mark is None or not 0 <= pass_mark <= MAX_MARK:
        return None, _error("Pass mark must be a number from 0 to 100.", 400)
    raw_subjects = payload.get("subjects")
    if not isinstance(raw_subjects, list) or not 1 <= len(raw_subjects) <= 30:
        return None, _error("Enter between 1 and 30 subjects.", 400)
    subjects = []
    for raw_subject in raw_subjects:
        name, error = _clean_text(raw_subject, "Subject name", required=True, limit=80)
        if error:
            return None, error
        subjects.append(name)
    if len({name.casefold() for name in subjects}) != len(subjects):
        return None, _error("Each subject needs a different name.", 400)

    raw_students = payload.get("students")
    if not isinstance(raw_students, list) or not 1 <= len(raw_students) <= 500:
        return None, _error("Select between 1 and 500 students.", 400)
    students = []
    for entry in raw_students:
        if not isinstance(entry, dict):
            return None, _error("Each student entry must be an object.", 400)
        student_id = entry.get("id")
        if isinstance(student_id, bool) or not isinstance(student_id, int) or student_id < 1:
            return None, _error("Each selected student must have a valid ID.", 400)
        marks = entry.get("marks")
        if not isinstance(marks, list) or len(marks) != len(subjects):
            return None, _error("Enter one mark for every subject.", 400)
        clean_marks = []
        for value in marks:
            score = _finite_number(value, "Mark")
            if score is None or not 0 <= score <= MAX_MARK:
                return None, _error("Every mark must be a number from 0 to 100.", 400)
            clean_marks.append(score)
        students.append({"id": student_id, "marks": clean_marks})
    if len({student["id"] for student in students}) != len(students):
        return None, _error("Select each student only once.", 400)

    return {"pass_mark": pass_mark, "subjects": subjects, "students": students}, None


def _finite_number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _clean_text(value, label, required=False, limit=100):
    if value is None and not required:
        return "", None
    if not isinstance(value, str):
        return None, _error(f"{label} must be text.", 400)
    cleaned = value.strip()
    if required and not cleaned:
        return None, _error(f"{label} is required.", 400)
    if len(cleaned) > limit:
        return None, _error(f"{label} must be {limit} characters or fewer.", 400)
    return cleaned, None


def _validate_academic_year(payload):
    label, error = _clean_text(payload.get("label"), "Academic year", required=True, limit=40)
    if error:
        return None, error
    starts_on, error = _clean_text(payload.get("startsOn"), "Start date", limit=10)
    if error:
        return None, error
    ends_on, error = _clean_text(payload.get("endsOn"), "End date", limit=10)
    if error:
        return None, error
    for field, value in (("Start date", starts_on), ("End date", ends_on)):
        if value:
            try:
                date.fromisoformat(value)
            except ValueError:
                return None, _error(f"{field} must use YYYY-MM-DD format.", 400)
    if starts_on and ends_on and starts_on > ends_on:
        return None, _error("End date must be on or after the start date.", 400)
    current = payload.get("isCurrent", False)
    if not isinstance(current, bool):
        return None, _error("Current year must be true or false.", 400)
    return {"label": label, "starts_on": starts_on or None,
            "ends_on": ends_on or None, "is_current": int(current)}, None


def _validate_enrollment_payload(payload):
    year_id = _optional_positive_id(payload.get("academicYearId"))
    class_id = _optional_positive_id(payload.get("classId"))
    section_value = payload.get("sectionId")
    section_id = _optional_positive_id(section_value) if section_value not in (None, "") else None
    if year_id is None or class_id is None:
        return None, _error("Choose a valid academic year and class.", 400)
    if section_value not in (None, "") and section_id is None:
        return None, _error("Section ID must be a positive whole number.", 400)
    roll_number, error = _clean_text(payload.get("rollNumber"), "Roll number", limit=40)
    if error:
        return None, error
    status = payload.get("status", "active")
    if not isinstance(status, str) or status not in {"active", "completed", "transferred"}:
        return None, _error("Choose active, completed, or transferred enrollment status.", 400)
    return {"academic_year_id": year_id, "class_id": class_id,
            "section_id": section_id, "roll_number": roll_number or None,
            "status": status}, None


def _optional_positive_id(value):
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 and str(result) == str(value) else None


def _sync_current_enrollment(connection, student_id, class_id, section_id, roll_number, status):
    if class_id is None:
        return
    year = connection.execute(
        "SELECT id FROM academic_years WHERE school_id = ? AND is_current = 1", (SCHOOL_ID,)
    ).fetchone()
    if year is None:
        return
    connection.execute(
        """INSERT INTO student_enrollments
               (student_id, academic_year_id, class_id, section_id, roll_number, status)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(student_id, academic_year_id) DO UPDATE SET
               class_id = excluded.class_id, section_id = excluded.section_id,
               roll_number = excluded.roll_number, status = excluded.status""",
        (student_id, year["id"], class_id, section_id, roll_number, status),
    )


def _resolve_class_and_section(connection, values):
    class_id = None
    section_id = None
    if values["class_name"]:
        row = connection.execute(
            "SELECT id FROM classes WHERE school_id = ? AND name = ? COLLATE NOCASE",
            (SCHOOL_ID, values["class_name"]),
        ).fetchone()
        if row:
            class_id = row["id"]
        else:
            class_id = connection.execute(
                "INSERT INTO classes(school_id, name) VALUES (?, ?)",
                (SCHOOL_ID, values["class_name"]),
            ).lastrowid
    if values["section_name"]:
        row = connection.execute(
            "SELECT id FROM sections WHERE class_id = ? AND name = ? COLLATE NOCASE",
            (class_id, values["section_name"]),
        ).fetchone()
        if row:
            section_id = row["id"]
        else:
            section_id = connection.execute(
                "INSERT INTO sections(class_id, name) VALUES (?, ?)",
                (class_id, values["section_name"]),
            ).lastrowid
    return class_id, section_id


def _new_student_code(connection):
    while True:
        code = f"STU-{uuid4().hex[:10].upper()}"
        exists = connection.execute(
            "SELECT 1 FROM students WHERE student_code = ? COLLATE NOCASE", (code,)
        ).fetchone()
        if not exists:
            return code


def _fetch_student(connection, student_id):
    row = connection.execute(
        """SELECT st.id, st.student_code, st.admission_number, st.name, st.roll_number,
                  cl.name AS class_name, se.name AS section_name,
                  st.guardian_name, st.guardian_contact, st.admission_date,
                  st.enrollment_status,
                  st.created_at, st.updated_at
           FROM students AS st
           LEFT JOIN classes AS cl ON cl.id = st.class_id
           LEFT JOIN sections AS se ON se.id = st.section_id
           WHERE st.id = ? AND st.school_id = ? AND st.is_active = 1""",
        (student_id, SCHOOL_ID),
    ).fetchone()
    return dict(row) if row else None


def _timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _analysis_summary(exam_name, subjects, pass_mark, students):
    """Calculate a sorted summary from marks read back from SQLite."""
    students.sort(key=lambda student: (-student["average"], student["name"].casefold(), student["id"]))
    highest_average = students[0]["average"]
    highest_students = [student for student in students if student["average"] == highest_average]
    passed_count = sum(student["status"] == "Pass" for student in students)
    return {
        "exam": exam_name,
        "subjects": subjects,
        "passMark": pass_mark,
        "studentCount": len(students),
        "classAverage": sum(student["average"] for student in students) / len(students),
        "highestAverage": highest_average,
        "highestNames": [student["name"] for student in highest_students],
        "highestStudentIds": [student["id"] for student in highest_students],
        "passPercentage": 100 * passed_count / len(students),
        "students": students,
    }


def _integrity_error(error):
    message = str(error).lower()
    if "student_code" in message:
        return _error("That student ID is already in use.", 409)
    if "admission_number" in message or "unique_student_admission_number" in message:
        return _error("That admission number is already in use.", 409)
    if "unique_active_roll_per_group" in message:
        return _error("That roll number is already used in this class and section.", 409)
    if "unique_enrollment_roll_per_section" in message:
        return _error("That roll number is already used in this class and section for the academic year.", 409)
    if "classes.school_id, classes.name" in message:
        return _error("That class already exists.", 409)
    if "sections.class_id, sections.name" in message:
        return _error("That section already exists in this class.", 409)
    if "academic_years.school_id, academic_years.label" in message:
        return _error("That academic year already exists.", 409)
    current_app.logger.error("Database constraint rejected a record: %s", type(error).__name__)
    return _error("The record conflicts with existing school data.", 409)


def _error(message, status):
    return jsonify(error=message), status


if __name__ == "__main__":
    application = create_app()
    application.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")))
