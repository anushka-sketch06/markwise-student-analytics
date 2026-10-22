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
                      COUNT(DISTINCT st.id) AS active_students
               FROM classes AS cl LEFT JOIN sections AS se ON se.class_id = cl.id
               LEFT JOIN student_enrollments AS en ON en.class_id = cl.id AND en.status = 'active'
                 AND en.academic_year_id = (SELECT id FROM academic_years WHERE school_id = ? AND is_current = 1)
               LEFT JOIN students AS st ON st.id = en.student_id AND st.school_id = cl.school_id
                 AND st.is_active = 1
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
            """SELECT se.id, se.name, cl.id AS class_id, cl.name AS class_name,
                      COUNT(DISTINCT st.id) AS active_students
               FROM sections AS se JOIN classes AS cl ON cl.id = se.class_id
               LEFT JOIN student_enrollments AS en ON en.section_id = se.id
                 AND en.class_id = se.class_id AND en.status = 'active'
                 AND en.academic_year_id = (SELECT id FROM academic_years WHERE school_id = ? AND is_current = 1)
               LEFT JOIN students AS st ON st.id = en.student_id AND st.school_id = cl.school_id
                 AND st.is_active = 1
               WHERE cl.school_id = ?
               GROUP BY se.id ORDER BY cl.name COLLATE NOCASE, se.name COLLATE NOCASE""",
            (SCHOOL_ID, SCHOOL_ID),
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

    @app.get("/api/dashboard/analytics")
    def dashboard_analytics():
        """Return year- and exam-scoped statistics based only on saved records."""
        connection = _get_database()
        years = connection.execute(
            """SELECT id, label, starts_on, ends_on, is_current FROM academic_years
               WHERE school_id = ? ORDER BY starts_on DESC, label DESC""",
            (SCHOOL_ID,),
        ).fetchall()
        current_year = next((row for row in years if row["is_current"]), None)

        raw_year_id = request.args.get("academicYearId")
        year_id = _optional_positive_id(raw_year_id) if raw_year_id else (
            current_year["id"] if current_year else None
        )
        if raw_year_id and year_id is None:
            return _error("Academic year ID must be a positive whole number.", 400)
        if year_id and not any(row["id"] == year_id for row in years):
            return _error("Academic year not found.", 400)

        raw_class_id = request.args.get("classId")
        class_id = _optional_positive_id(raw_class_id) if raw_class_id else None
        if raw_class_id and class_id is None:
            return _error("Class ID must be a positive whole number.", 400)
        classes = connection.execute(
            "SELECT id, name FROM classes WHERE school_id = ? ORDER BY name COLLATE NOCASE",
            (SCHOOL_ID,),
        ).fetchall()
        if class_id and not any(row["id"] == class_id for row in classes):
            return _error("Class not found.", 400)

        raw_section_id = request.args.get("sectionId")
        section_id = _optional_positive_id(raw_section_id) if raw_section_id else None
        if raw_section_id and section_id is None:
            return _error("Section ID must be a positive whole number.", 400)
        if section_id:
            section = connection.execute(
                """SELECT se.id, se.name, se.class_id, cl.name AS class_name FROM sections AS se
                   JOIN classes AS cl ON cl.id = se.class_id
                   WHERE se.id = ? AND cl.school_id = ?""",
                (section_id, SCHOOL_ID),
            ).fetchone()
            if section is None or (class_id and section["class_id"] != class_id):
                return _error("Section does not belong to the selected class.", 400)

        raw_exam_id = request.args.get("examId")
        exam_id = _optional_positive_id(raw_exam_id) if raw_exam_id else None
        if raw_exam_id and exam_id is None:
            return _error("Exam ID must be a positive whole number.", 400)
        exams_query = """SELECT ex.id, ex.name, ex.exam_date, ex.academic_year_id,
                                 COUNT(DISTINCT es.id) AS subject_count
                          FROM exams AS ex LEFT JOIN exam_subjects AS es ON es.exam_id = ex.id
                          WHERE ex.school_id = ?"""
        exams_parameters = [SCHOOL_ID]
        if year_id:
            exams_query += " AND (ex.academic_year_id = ? OR ex.academic_year_id IS NULL)"
            exams_parameters.append(year_id)
        exams_query += " GROUP BY ex.id ORDER BY ex.exam_date DESC, ex.created_at DESC, ex.id DESC"
        exam_rows = connection.execute(exams_query, exams_parameters).fetchall()
        if exam_id and not any(row["id"] == exam_id for row in exam_rows):
            return _error("Exam is not available for the selected academic year.", 400)
        if exam_id is None:
            exam_id = next((row["id"] for row in exam_rows if row["subject_count"]), None)
        selected_exam = next((row for row in exam_rows if row["id"] == exam_id), None)

        search = request.args.get("search", "").strip()
        if len(search) > 100:
            return _error("Search text must be 100 characters or fewer.", 400)

        # One row per student for the selected year; the enrollment unique key
        # prevents historical enrollment joins from multiplying students.
        is_current_year = bool(current_year and year_id == current_year["id"])
        year_class = "COALESCE(en.class_id, st.class_id)" if is_current_year else "en.class_id"
        year_section = "COALESCE(en.section_id, st.section_id)" if is_current_year else "en.section_id"
        active_condition = "COALESCE(en.status, st.enrollment_status) = 'active'" if is_current_year else "en.status = 'active'"
        student_sql = f"""SELECT st.id, st.student_code, st.name,
                                  {year_class} AS class_id, cl.name AS class_name,
                                  {year_section} AS section_id, se.name AS section_name,
                                  COALESCE(en.status, st.enrollment_status) AS enrollment_status
                           FROM students AS st
                           LEFT JOIN student_enrollments AS en
                             ON en.student_id = st.id AND en.academic_year_id = ?
                           LEFT JOIN classes AS cl ON cl.id = {year_class}
                           LEFT JOIN sections AS se ON se.id = {year_section}
                           WHERE st.school_id = ? AND st.is_active = 1
                             AND {active_condition}"""
        student_parameters = [year_id, SCHOOL_ID]
        if class_id:
            student_sql += f" AND {year_class} = ?"
            student_parameters.append(class_id)
        if section_id:
            student_sql += f" AND {year_section} = ?"
            student_parameters.append(section_id)
        if search:
            student_sql += " AND (st.name LIKE ? OR st.student_code LIKE ? OR st.admission_number LIKE ?)"
            pattern = f"%{search}%"
            student_parameters.extend([pattern, pattern, pattern])
        student_sql += " ORDER BY st.name COLLATE NOCASE, st.student_code COLLATE NOCASE"
        student_rows = connection.execute(student_sql, student_parameters).fetchall()

        subject_rows = []
        mark_rows = []
        if selected_exam:
            subject_rows = connection.execute(
                """SELECT es.id AS exam_subject_id, su.id AS subject_id, su.name,
                          es.maximum_marks, es.passing_marks
                   FROM exam_subjects AS es JOIN subjects AS su ON su.id = es.subject_id
                   WHERE es.exam_id = ? ORDER BY su.name COLLATE NOCASE""",
                (exam_id,),
            ).fetchall()
            if student_rows and subject_rows:
                student_placeholders = ",".join("?" for _ in student_rows)
                mark_rows = connection.execute(
                    f"""SELECT m.student_id, m.exam_subject_id, m.score
                        FROM marks AS m
                        WHERE m.student_id IN ({student_placeholders})
                          AND m.exam_subject_id IN ({','.join('?' for _ in subject_rows)})""",
                    [*[row["id"] for row in student_rows], *[row["exam_subject_id"] for row in subject_rows]],
                ).fetchall()

        analysis = _build_dashboard_analytics(student_rows, subject_rows, mark_rows)
        return jsonify(
            academicYears=[dict(row) for row in years],
            classes=[dict(row) for row in classes],
            sections=[dict(row) for row in connection.execute(
                """SELECT se.id, se.name, se.class_id, cl.name AS class_name FROM sections AS se
                   JOIN classes AS cl ON cl.id = se.class_id
                   WHERE cl.school_id = ? ORDER BY cl.name COLLATE NOCASE, se.name COLLATE NOCASE""",
                (SCHOOL_ID,),
            ).fetchall()],
            exams=[dict(row) for row in exam_rows],
            selected={"academicYearId": year_id, "classId": class_id,
                      "sectionId": section_id, "examId": exam_id},
            **analysis,
        )

    @app.get("/api/student-progress/students")
    def student_progress_students():
        """Return minimal student identifiers for the progress-page selector."""
        search = request.args.get("search", "").strip()
        if len(search) > 100:
            return _error("Search text must be 100 characters or fewer.", 400)
        sql = """SELECT st.id, st.student_code, st.name,
                        COALESCE(en.roll_number, st.roll_number) AS roll_number,
                        COALESCE(cl.name, legacy_class.name) AS class_name,
                        COALESCE(se.name, legacy_section.name) AS section_name
                 FROM students AS st
                 LEFT JOIN academic_years AS current_year
                   ON current_year.school_id = st.school_id AND current_year.is_current = 1
                 LEFT JOIN student_enrollments AS en
                   ON en.student_id = st.id AND en.academic_year_id = current_year.id
                   AND en.status = 'active'
                 LEFT JOIN classes AS cl ON cl.id = en.class_id
                 LEFT JOIN sections AS se ON se.id = en.section_id
                 LEFT JOIN classes AS legacy_class ON legacy_class.id = st.class_id
                 LEFT JOIN sections AS legacy_section ON legacy_section.id = st.section_id
                 WHERE st.school_id = ? AND st.is_active = 1"""
        parameters = [SCHOOL_ID]
        if search:
            pattern = f"%{search}%"
            sql += " AND (st.name LIKE ? OR st.student_code LIKE ? OR COALESCE(en.roll_number, st.roll_number, '') LIKE ?)"
            parameters.extend([pattern, pattern, pattern])
        sql += " ORDER BY st.name COLLATE NOCASE, st.student_code COLLATE NOCASE"
        rows = _get_database().execute(sql, parameters).fetchall()
        return jsonify(students=[dict(row) for row in rows])

    @app.get("/api/students/<int:student_id>/progress")
    def student_progress(student_id):
        """Return one student's saved exam history without private profile fields."""
        connection = _get_database()
        student = connection.execute(
            """SELECT st.id, st.student_code, st.name,
                      COALESCE(en.roll_number, st.roll_number) AS roll_number,
                      COALESCE(cl.name, legacy_class.name) AS class_name,
                      COALESCE(se.name, legacy_section.name) AS section_name
               FROM students AS st
               LEFT JOIN academic_years AS current_year
                 ON current_year.school_id = st.school_id AND current_year.is_current = 1
               LEFT JOIN student_enrollments AS en
                 ON en.student_id = st.id AND en.academic_year_id = current_year.id
                 AND en.status = 'active'
               LEFT JOIN classes AS cl ON cl.id = en.class_id
               LEFT JOIN sections AS se ON se.id = en.section_id
               LEFT JOIN classes AS legacy_class ON legacy_class.id = st.class_id
               LEFT JOIN sections AS legacy_section ON legacy_section.id = st.section_id
               WHERE st.id = ? AND st.school_id = ?""",
            (student_id, SCHOOL_ID),
        ).fetchone()
        if student is None:
            return _error("Student not found.", 404)

        years = connection.execute(
            "SELECT id, label, starts_on, ends_on FROM academic_years WHERE school_id = ? ORDER BY starts_on, label COLLATE NOCASE",
            (SCHOOL_ID,),
        ).fetchall()
        raw_year_id = request.args.get("academicYearId", "").strip()
        year_id = _optional_positive_id(raw_year_id) if raw_year_id else None
        if raw_year_id and year_id is None:
            return _error("Academic year ID must be a positive whole number.", 400)
        if year_id and not any(year["id"] == year_id for year in years):
            return _error("Academic year not found.", 400)

        raw_subject_id = request.args.get("subjectId", "").strip()
        subject_id = _optional_positive_id(raw_subject_id) if raw_subject_id else None
        if raw_subject_id and subject_id is None:
            return _error("Subject ID must be a positive whole number.", 400)
        if subject_id and not connection.execute(
            "SELECT 1 FROM subjects WHERE id = ? AND school_id = ?", (subject_id, SCHOOL_ID)
        ).fetchone():
            return _error("Subject not found.", 400)

        exam_sql = """SELECT DISTINCT ex.id, ex.name, ex.exam_date, ex.created_at,
                               ex.academic_year_id, ay.label AS academic_year,
                               ay.starts_on AS academic_year_starts
                        FROM exams AS ex
                        JOIN exam_subjects AS es ON es.exam_id = ex.id
                        JOIN marks AS m ON m.exam_subject_id = es.id AND m.student_id = ?
                        LEFT JOIN academic_years AS ay ON ay.id = ex.academic_year_id
                        WHERE ex.school_id = ?"""
        exam_parameters = [student_id, SCHOOL_ID]
        if year_id:
            exam_sql += " AND ex.academic_year_id = ?"
            exam_parameters.append(year_id)
        exam_sql += " ORDER BY COALESCE(ay.starts_on, ex.exam_date, ex.created_at), ex.exam_date, ex.created_at, ex.id"
        exam_rows = connection.execute(exam_sql, exam_parameters).fetchall()

        exams = []
        subject_options = connection.execute(
            """SELECT DISTINCT su.id, su.name FROM subjects AS su
               JOIN exam_subjects AS es ON es.subject_id = su.id
               JOIN exams AS ex ON ex.id = es.exam_id
               JOIN marks AS m ON m.exam_subject_id = es.id AND m.student_id = ?
               WHERE su.school_id = ? ORDER BY su.name COLLATE NOCASE, su.id""",
            (student_id, SCHOOL_ID),
        ).fetchall()
        for exam in exam_rows:
            subject_rows = connection.execute(
                """SELECT su.id AS subject_id, su.name AS subject_name,
                          es.id AS exam_subject_id, es.maximum_marks, es.passing_marks, m.score
                   FROM exam_subjects AS es JOIN subjects AS su ON su.id = es.subject_id
                   LEFT JOIN marks AS m ON m.exam_subject_id = es.id AND m.student_id = ?
                   WHERE es.exam_id = ? ORDER BY su.name COLLATE NOCASE, su.id""",
                (student_id, exam["id"]),
            ).fetchall()
            subjects = []
            valid_percentages = []
            for row in subject_rows:
                score = row["score"]
                valid = score is not None and 0 <= score <= row["maximum_marks"]
                percentage = round(score / row["maximum_marks"] * 100, 2) if valid else None
                if valid:
                    valid_percentages.append(percentage)
                if subject_id is None or row["subject_id"] == subject_id:
                    subjects.append({
                        "subjectId": row["subject_id"], "name": row["subject_name"],
                        "score": score if valid else None,
                        "maximumMarks": row["maximum_marks"], "passingMarks": row["passing_marks"],
                        "percentage": percentage, "status": "Recorded" if valid else "Missing",
                    })
            exams.append({
                "examId": exam["id"], "name": exam["name"], "examDate": exam["exam_date"],
                "createdAt": exam["created_at"], "academicYearId": exam["academic_year_id"],
                "academicYear": exam["academic_year"] or "Academic year not assigned",
                "overallPercentage": round(sum(valid_percentages) / len(valid_percentages), 2) if valid_percentages else None,
                "subjects": subjects,
            })

        return jsonify(
            student=dict(student), academicYears=[dict(year) for year in years],
            subjects=[dict(subject) for subject in subject_options],
            exams=exams, selected={"academicYearId": year_id, "subjectId": subject_id},
        )

    @app.get("/api/attendance/roster")
    def attendance_roster():
        scope, error = _validate_attendance_scope(request.args, include_date=True, require_class=True)
        if error:
            return error
        connection = _get_database()
        error = _check_attendance_scope(connection, scope)
        if error:
            return error
        sql = """SELECT st.id, st.student_code, st.name,
                        COALESCE(en.roll_number, st.roll_number) AS roll_number,
                        en.section_id, se.name AS section_name,
                        att.id AS attendance_id, att.status AS attendance_status,
                        att.academic_year_id AS saved_year_id, att.class_id AS saved_class_id,
                        att.section_id AS saved_section_id, att.created_at AS recorded_at
                 FROM student_enrollments AS en
                 JOIN students AS st ON st.id = en.student_id AND st.school_id = ? AND st.is_active = 1
                 LEFT JOIN sections AS se ON se.id = en.section_id
                 LEFT JOIN attendance AS att ON att.student_id = st.id
                   AND att.attendance_date = ? AND att.session = ?
                 WHERE en.academic_year_id = ? AND en.class_id = ? AND en.status = 'active'"""
        parameters = [SCHOOL_ID, scope["attendance_date"], scope["session"], scope["academic_year_id"], scope["class_id"]]
        if scope["section_id"] is not None:
            sql += " AND en.section_id = ?"
            parameters.append(scope["section_id"])
        sql += " ORDER BY se.name COLLATE NOCASE, COALESCE(en.roll_number, st.roll_number), st.name COLLATE NOCASE"
        rows = connection.execute(sql, parameters).fetchall()
        roster = []
        for row in rows:
            item = {
                "id": row["id"], "studentCode": row["student_code"], "name": row["name"],
                "rollNumber": row["roll_number"], "sectionId": row["section_id"],
                "sectionName": row["section_name"], "attendanceId": row["attendance_id"],
                "status": row["attendance_status"], "recordedAt": row["recorded_at"],
                "unrecorded": row["attendance_id"] is None,
            }
            item["lockedPlacement"] = row["attendance_id"] is not None and (
                row["saved_year_id"] != scope["academic_year_id"]
                or row["saved_class_id"] != scope["class_id"]
                or row["saved_section_id"] != row["section_id"]
            )
            roster.append(item)
        return jsonify(scope=scope, students=roster, total=len(roster))

    @app.put("/api/attendance/records")
    def save_attendance_records():
        payload, error = _read_json_object()
        if error:
            return error
        scope, error = _validate_attendance_scope(payload, include_date=True, require_class=True)
        if error:
            return error
        records = payload.get("records")
        if not isinstance(records, list) or not 1 <= len(records) <= 500:
            return _error("Submit between 1 and 500 attendance records.", 400)
        clean_records = []
        allowed_statuses = {"present", "absent", "late", "excused"}
        for record in records:
            if not isinstance(record, dict):
                return _error("Each attendance record must be an object.", 400)
            student_id = record.get("studentId")
            status = record.get("status")
            if isinstance(student_id, bool) or not isinstance(student_id, int) or student_id < 1:
                return _error("Each attendance record needs a valid student ID.", 400)
            if not isinstance(status, str) or status not in allowed_statuses:
                return _error("Choose Present, Absent, Late, or Excused for every submitted student.", 400)
            clean_records.append({"student_id": student_id, "status": status})
        if len({record["student_id"] for record in clean_records}) != len(clean_records):
            return _error("Submit each student only once for this date and session.", 400)

        connection = _get_database()
        error = _check_attendance_scope(connection, scope)
        if error:
            return error
        student_ids = [record["student_id"] for record in clean_records]
        placeholders = ",".join("?" for _ in student_ids)
        created = 0
        updated = 0
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                enrollment_rows = connection.execute(
                    f"""SELECT en.student_id, en.section_id
                        FROM student_enrollments AS en JOIN students AS st ON st.id = en.student_id
                        WHERE st.school_id = ? AND st.is_active = 1 AND en.status = 'active'
                          AND en.academic_year_id = ? AND en.class_id = ?
                          AND (? IS NULL OR en.section_id = ?)
                          AND en.student_id IN ({placeholders})""",
                    [SCHOOL_ID, scope["academic_year_id"], scope["class_id"], scope["section_id"], scope["section_id"], *student_ids],
                ).fetchall()
                enrollment_by_student = {row["student_id"]: row["section_id"] for row in enrollment_rows}
                if set(enrollment_by_student) != set(student_ids):
                    raise _AttendanceEnrollmentConflict
                for record in clean_records:
                    section_id = enrollment_by_student[record["student_id"]]
                    existing = connection.execute(
                        """SELECT id, academic_year_id, class_id, section_id FROM attendance
                           WHERE student_id = ? AND attendance_date = ? AND session = ?""",
                        (record["student_id"], scope["attendance_date"], scope["session"]),
                    ).fetchone()
                    if existing and (
                        existing["academic_year_id"] != scope["academic_year_id"]
                        or existing["class_id"] != scope["class_id"]
                        or existing["section_id"] != section_id
                    ):
                        raise _AttendancePlacementConflict
                    connection.execute(
                        """INSERT INTO attendance
                               (student_id, academic_year_id, class_id, section_id,
                                attendance_date, session, status)
                           VALUES (?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(student_id, attendance_date, session)
                           DO UPDATE SET status = excluded.status
                             WHERE attendance.academic_year_id = excluded.academic_year_id
                               AND attendance.class_id = excluded.class_id
                               AND attendance.section_id IS excluded.section_id""",
                        (record["student_id"], scope["academic_year_id"], scope["class_id"], section_id,
                         scope["attendance_date"], scope["session"], record["status"]),
                    )
                    if existing:
                        updated += 1
                    else:
                        created += 1
        except _AttendancePlacementConflict:
            return _error("An existing attendance record for this student, date, and session belongs to a different or unassigned placement and was not changed.", 409)
        except _AttendanceEnrollmentConflict:
            return _error("One or more students are not actively enrolled in the selected class, section, and academic year.", 400)
        except sqlite3.IntegrityError as error:
            return _integrity_error(error)
        return jsonify(created=created, updated=updated, saved=len(clean_records))

    @app.get("/api/attendance/summary")
    def attendance_summary():
        scope, error = _validate_attendance_scope(request.args, include_date=False, require_class=False)
        if error:
            return error
        date_range, error = _validate_attendance_date_range(request.args)
        if error:
            return error
        threshold, error = _attendance_threshold(request.args.get("threshold", "75"))
        if error:
            return error
        connection = _get_database()
        error = _check_attendance_scope(connection, scope)
        if error:
            return error
        summary = _build_attendance_summary(connection, scope, date_range, threshold)
        return jsonify(summary=summary, threshold=threshold, dateRange=date_range)

    @app.get("/api/students/<int:student_id>/attendance")
    def individual_attendance_summary(student_id):
        scope, error = _validate_attendance_scope(request.args, include_date=False, require_class=False)
        if error:
            return error
        date_range, error = _validate_attendance_date_range(request.args)
        if error:
            return error
        threshold, error = _attendance_threshold(request.args.get("threshold", "75"))
        if error:
            return error
        connection = _get_database()
        student = connection.execute(
            "SELECT id, student_code, name FROM students WHERE id = ? AND school_id = ?",
            (student_id, SCHOOL_ID),
        ).fetchone()
        if not student:
            return _error("Student not found.", 404)
        error = _check_attendance_scope(connection, scope)
        if error:
            return error
        result = _build_student_attendance_summary(connection, student, scope, date_range, threshold)
        return jsonify(summary=result, threshold=threshold, dateRange=date_range)

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


class _AttendancePlacementConflict(Exception):
    """Raised to roll back a batch if a unique-key row belongs elsewhere."""


class _AttendanceEnrollmentConflict(Exception):
    """Raised when the roster changes or contains an out-of-scope student."""


def _validate_attendance_scope(source, include_date, require_class):
    year_raw = source.get("academicYearId", "")
    class_raw = source.get("classId", "")
    section_raw = source.get("sectionId", "")
    year_id = _optional_positive_id(year_raw)
    class_id = _optional_positive_id(class_raw) if class_raw not in (None, "") else None
    section_id = _optional_positive_id(section_raw) if section_raw not in (None, "") else None
    if year_id is None:
        return None, _error("Academic year ID must be a positive whole number.", 400)
    if require_class and class_id is None:
        return None, _error("Class ID must be a positive whole number.", 400)
    if class_raw not in (None, "") and class_id is None:
        return None, _error("Class ID must be a positive whole number.", 400)
    if section_raw not in (None, "") and section_id is None:
        return None, _error("Section ID must be a positive whole number.", 400)
    if section_id is not None and class_id is None:
        return None, _error("Choose a class before filtering by section.", 400)
    scope = {"academic_year_id": year_id, "class_id": class_id, "section_id": section_id}
    if include_date:
        raw_date = source.get("date", "")
        if not isinstance(raw_date, str):
            return None, _error("Attendance date must use YYYY-MM-DD format.", 400)
        try:
            parsed = date.fromisoformat(raw_date)
        except ValueError:
            return None, _error("Attendance date must use YYYY-MM-DD format.", 400)
        if parsed.isoformat() != raw_date:
            return None, _error("Attendance date must use YYYY-MM-DD format.", 400)
        scope["attendance_date"] = raw_date
        session = source.get("session", "full_day")
        if not isinstance(session, str) or session not in {"full_day", "morning", "afternoon"}:
            return None, _error("Choose a valid session: full_day, morning, or afternoon.", 400)
        scope["session"] = session
    return scope, None


def _check_attendance_scope(connection, scope):
    if not connection.execute(
        "SELECT 1 FROM academic_years WHERE id = ? AND school_id = ?",
        (scope["academic_year_id"], SCHOOL_ID),
    ).fetchone():
        return _error("Academic year not found.", 400)
    if scope["class_id"] is not None and not connection.execute(
        "SELECT 1 FROM classes WHERE id = ? AND school_id = ?", (scope["class_id"], SCHOOL_ID)
    ).fetchone():
        return _error("Class not found.", 400)
    if scope["section_id"] is not None and not connection.execute(
        "SELECT 1 FROM sections WHERE id = ? AND class_id = ?",
        (scope["section_id"], scope["class_id"]),
    ).fetchone():
        return _error("Section does not belong to the selected class.", 400)
    return None


def _validate_attendance_date_range(source):
    start = source.get("fromDate", "")
    end = source.get("toDate", "")
    if bool(start) != bool(end):
        return None, _error("Choose both a start and end date, or leave both blank.", 400)
    if not start:
        return {"fromDate": None, "toDate": None}, None
    parsed = []
    for value in (start, end):
        if not isinstance(value, str):
            return None, _error("Date filters must use YYYY-MM-DD format.", 400)
        try:
            candidate = date.fromisoformat(value)
        except ValueError:
            return None, _error("Date filters must use YYYY-MM-DD format.", 400)
        if candidate.isoformat() != value:
            return None, _error("Date filters must use YYYY-MM-DD format.", 400)
        parsed.append(candidate)
    if parsed[0] > parsed[1]:
        return None, _error("Start date must not be after end date.", 400)
    return {"fromDate": start, "toDate": end}, None


def _attendance_threshold(value):
    try:
        threshold = float(value)
    except (TypeError, ValueError):
        return None, _error("Attendance threshold must be a number from 0 to 100.", 400)
    if not math.isfinite(threshold) or not 0 <= threshold <= 100:
        return None, _error("Attendance threshold must be a number from 0 to 100.", 400)
    return threshold, None


def _attendance_filter_sql(scope, date_range, alias="att"):
    clauses = [f"{alias}.academic_year_id = ?"]
    parameters = [scope["academic_year_id"]]
    if scope["class_id"] is not None:
        clauses.append(f"{alias}.class_id = ?")
        parameters.append(scope["class_id"])
    if scope["section_id"] is not None:
        clauses.append(f"{alias}.section_id = ?")
        parameters.append(scope["section_id"])
    if date_range["fromDate"]:
        clauses.extend([f"{alias}.attendance_date >= ?", f"{alias}.attendance_date <= ?"])
        parameters.extend([date_range["fromDate"], date_range["toDate"]])
    return " AND ".join(clauses), parameters


def _empty_attendance_counts():
    return {"present": 0, "late": 0, "absent": 0, "excused": 0}


def _attendance_count_result(counts):
    attended = counts["present"] + counts["late"]
    countable = attended + counts["absent"]
    percentage = round(attended * 100 / countable, 2) if countable else None
    return {
        "present": counts["present"], "late": counts["late"], "absent": counts["absent"],
        "excused": counts["excused"], "attendedSessions": attended,
        "countableSessions": countable, "attendancePercentage": percentage,
    }


def _build_attendance_summary(connection, scope, date_range, threshold):
    roster_sql = """SELECT st.id, st.student_code, st.name,
                           COALESCE(en.roll_number, st.roll_number) AS roll_number,
                           cl.name AS class_name, se.name AS section_name
                    FROM student_enrollments AS en
                    JOIN students AS st ON st.id = en.student_id AND st.school_id = ? AND st.is_active = 1
                    JOIN classes AS cl ON cl.id = en.class_id
                    LEFT JOIN sections AS se ON se.id = en.section_id
                    WHERE en.academic_year_id = ? AND en.status = 'active'"""
    roster_params = [SCHOOL_ID, scope["academic_year_id"]]
    if scope["class_id"] is not None:
        roster_sql += " AND en.class_id = ?"
        roster_params.append(scope["class_id"])
    if scope["section_id"] is not None:
        roster_sql += " AND en.section_id = ?"
        roster_params.append(scope["section_id"])
    roster_rows = connection.execute(roster_sql, roster_params).fetchall()
    students = {row["id"]: {
        "id": row["id"], "studentCode": row["student_code"], "name": row["name"],
        "rollNumber": row["roll_number"], "className": row["class_name"],
        "sectionName": row["section_name"], "counts": _empty_attendance_counts(),
    } for row in roster_rows}

    filters, filter_params = _attendance_filter_sql(scope, date_range)
    attendance_rows = connection.execute(
        f"""SELECT st.id, st.student_code, st.name,
                   MIN(cl.name) AS class_name, GROUP_CONCAT(DISTINCT se.name) AS section_names,
                   SUM(CASE WHEN att.status = 'present' THEN 1 ELSE 0 END) AS present,
                   SUM(CASE WHEN att.status = 'late' THEN 1 ELSE 0 END) AS late,
                   SUM(CASE WHEN att.status = 'absent' THEN 1 ELSE 0 END) AS absent,
                   SUM(CASE WHEN att.status = 'excused' THEN 1 ELSE 0 END) AS excused
            FROM attendance AS att JOIN students AS st ON st.id = att.student_id
            LEFT JOIN classes AS cl ON cl.id = att.class_id
            LEFT JOIN sections AS se ON se.id = att.section_id
            WHERE st.school_id = ? AND {filters}
            GROUP BY st.id ORDER BY st.name COLLATE NOCASE""",
        [SCHOOL_ID, *filter_params],
    ).fetchall()
    for row in attendance_rows:
        student = students.setdefault(row["id"], {
            "id": row["id"], "studentCode": row["student_code"], "name": row["name"],
            "rollNumber": None, "className": row["class_name"],
            "sectionName": row["section_names"], "counts": _empty_attendance_counts(),
        })
        student["counts"] = {key: row[key] or 0 for key in ("present", "late", "absent", "excused")}
        student["className"] = row["class_name"] or student["className"]
        student["sectionName"] = row["section_names"] or student["sectionName"]

    results = []
    totals = _empty_attendance_counts()
    for student in sorted(students.values(), key=lambda item: (item["name"].casefold(), item["id"])):
        counts = student.pop("counts")
        result = {**student, **_attendance_count_result(counts)}
        result["belowThreshold"] = (
            result["attendancePercentage"] is not None
            and result["attendancePercentage"] < threshold
        )
        for key in totals:
            totals[key] += counts[key]
        results.append(result)
    class_result = _attendance_count_result(totals)
    class_result.update({
        "activeStudents": len(roster_rows),
        "studentsWithCountableRecords": sum(item["countableSessions"] > 0 for item in results),
        "studentsBelowThreshold": sum(item["belowThreshold"] for item in results),
        "threshold": threshold,
    })
    return {"class": class_result, "students": results}


def _build_student_attendance_summary(connection, student, scope, date_range, threshold):
    filters, parameters = _attendance_filter_sql(scope, date_range)
    rows = connection.execute(
        f"""SELECT att.id, att.attendance_date, att.session, att.status,
                   att.class_id, cl.name AS class_name, att.section_id, se.name AS section_name,
                   att.created_at
            FROM attendance AS att
            LEFT JOIN classes AS cl ON cl.id = att.class_id
            LEFT JOIN sections AS se ON se.id = att.section_id
            WHERE att.student_id = ? AND {filters}
            ORDER BY att.attendance_date, att.session, att.id""",
        [student["id"], *parameters],
    ).fetchall()
    counts = _empty_attendance_counts()
    sessions = []
    for row in rows:
        counts[row["status"]] += 1
        sessions.append({
            "id": row["id"], "date": row["attendance_date"], "session": row["session"],
            "status": row["status"], "className": row["class_name"],
            "sectionName": row["section_name"], "recordedAt": row["created_at"],
        })
    result = {
        "student": {"id": student["id"], "studentCode": student["student_code"], "name": student["name"]},
        "sessions": sessions,
        **_attendance_count_result(counts),
    }
    result["belowThreshold"] = result["attendancePercentage"] is not None and result["attendancePercentage"] < threshold
    return result


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


def _build_dashboard_analytics(student_rows, subject_rows, mark_rows):
    """Build dashboard statistics from one exam's valid saved marks."""
    subject_by_id = {row["exam_subject_id"]: row for row in subject_rows}
    marks_by_student = {}
    values_by_subject = {row["exam_subject_id"]: [] for row in subject_rows}
    for row in mark_rows:
        subject = subject_by_id.get(row["exam_subject_id"])
        score = row["score"]
        if subject is None or score is None:
            continue
        maximum = subject["maximum_marks"]
        if maximum <= 0 or score < 0 or score > maximum:
            continue
        percent = 100 * score / maximum
        marks_by_student.setdefault(row["student_id"], []).append({
            "examSubjectId": row["exam_subject_id"],
            "subjectId": subject["subject_id"],
            "subject": subject["name"],
            "score": score,
            "maximum": maximum,
            "percent": percent,
            "passingPercent": 100 * subject["passing_marks"] / maximum,
        })
        values_by_subject[row["exam_subject_id"]].append((score, percent))

    subject_analytics = []
    for subject in subject_rows:
        values = values_by_subject[subject["exam_subject_id"]]
        scores = [value[0] for value in values]
        percentages = [value[1] for value in values]
        subject_analytics.append({
            "id": subject["subject_id"],
            "name": subject["name"],
            "maximumMarks": subject["maximum_marks"],
            "passingMarks": subject["passing_marks"],
            "studentCount": len(values),
            "averageMarks": _rounded(sum(scores) / len(scores)) if scores else None,
            "averagePercentage": _rounded(sum(percentages) / len(percentages)) if percentages else None,
            "highestMarks": max(scores) if scores else None,
            "lowestMarks": min(scores) if scores else None,
        })

    results = []
    student_pass_thresholds = []
    for student in student_rows:
        entered = marks_by_student.get(student["id"], [])
        if entered:
            average = sum(mark["percent"] for mark in entered) / len(entered)
            pass_mark = sum(mark["passingPercent"] for mark in entered) / len(entered)
            passed = average >= pass_mark
            student_pass_thresholds.append(pass_mark)
            grade = _grade_for_percentage(average)
            percentage = _rounded(average)
            status = "Pass" if passed else "Fail"
        else:
            average = None
            pass_mark = None
            passed = None
            grade = None
            percentage = None
            status = "No marks"
        results.append({
            "id": student["id"],
            "studentCode": student["student_code"],
            "name": student["name"],
            "className": student["class_name"],
            "sectionName": student["section_name"],
            "percentage": percentage,
            "grade": grade,
            "status": status,
            "marksEntered": len(entered),
            "subjectCount": len(subject_rows),
            "rank": None,
            "_average": average,
            "_passMark": pass_mark,
            "_passed": passed,
        })

    ranked = sorted(
        (row for row in results if row["_average"] is not None),
        key=lambda row: (-row["_average"], row["name"].casefold(), row["id"]),
    )
    previous_average = None
    current_rank = 0
    for index, row in enumerate(ranked, start=1):
        if previous_average is None or row["_average"] != previous_average:
            current_rank = index
        row["rank"] = current_rank
        previous_average = row["_average"]
    graded = [row for row in results if row["_average"] is not None]
    passing = sum(row["status"] == "Pass" for row in graded)
    failing = sum(row["status"] == "Fail" for row in graded)
    grade_distribution = {
        grade: sum(row["grade"] == grade for row in graded)
        for grade in ("A", "B", "C", "D", "E", "F")
    }
    highest_average = max((row["_average"] for row in graded), default=None)
    highest_students = [
        {"id": row["id"], "name": row["name"], "percentage": row["percentage"]}
        for row in graded if row["_average"] == highest_average
    ] if highest_average is not None else []
    class_average = _rounded(sum(row["_average"] for row in graded) / len(graded)) if graded else None
    pass_mark_average = _rounded(sum(student_pass_thresholds) / len(student_pass_thresholds)) if student_pass_thresholds else None
    for row in results:
        row["subjectResults"] = [
            {"subject": mark["subject"], "score": mark["score"],
             "maximum": mark["maximum"], "percentage": _rounded(mark["percent"])}
            for mark in marks_by_student.get(row["id"], [])
        ]
        row.pop("_average", None)
        row.pop("_passMark", None)
        row.pop("_passed", None)

    return {
        "summary": {
            "totalActiveStudents": len(student_rows),
            "studentsWithMarks": len(graded),
            "classAveragePercentage": class_average,
            "passPercentage": _rounded(100 * passing / len(graded)) if graded else None,
            "passMarkPercentage": pass_mark_average,
            "highestPerformers": highest_students,
        },
        "subjects": subject_analytics,
        "gradeDistribution": grade_distribution,
        "passFailDistribution": {"pass": passing, "fail": failing},
        "results": results,
        "rules": {
            "grades": "A: 90–100%, B: 80–89.99%, C: 70–79.99%, D: 60–69.99%, E: 40–59.99%, F: below 40%.",
            "pass": "A student's mean percentage across entered valid subjects must meet or exceed the mean passing percentage configured for those subjects. Missing marks are excluded, not treated as zero.",
            "rank": "Competition ranking by percentage; students tied at the same unrounded percentage share a rank (1, 1, 3).",
            "average": "Class average gives equal weight to each student with at least one valid mark; subject averages use only students with a saved valid mark in that subject.",
        },
    }


def _grade_for_percentage(percentage):
    if percentage >= 90:
        return "A"
    if percentage >= 80:
        return "B"
    if percentage >= 70:
        return "C"
    if percentage >= 60:
        return "D"
    if percentage >= 40:
        return "E"
    return "F"


def _rounded(value):
    return round(value + 1e-10, 2)


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
