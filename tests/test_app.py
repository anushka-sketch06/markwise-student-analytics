"""Integration tests for Markwise student records, marks, and persistence."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import create_app
from database import SCHEMA_FILE, initialize_database


class MarkwiseAppTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "markwise-test.sqlite3"
        self.app = create_app({
            "TESTING": True,
            "DATABASE": str(self.database_path),
        })
        self.client = self.app.test_client()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def add_student(self, **overrides):
        payload = {
            "studentCode": "S-100",
            "admissionNumber": "ADM-100",
            "name": "Asha Rao",
            "rollNumber": "12",
            "className": "Grade 7",
            "sectionName": "Blue",
        }
        payload.update(overrides)
        return self.client.post("/api/students", json=payload)

    def add_progress_exam(self, student_id, name, exam_date, subject_scores, academic_year_id=None):
        """Create isolated exam history in this test's temporary database."""
        connection = sqlite3.connect(self.database_path)
        try:
            year_id = academic_year_id or connection.execute(
                "SELECT id FROM academic_years WHERE school_id = 1 AND is_current = 1"
            ).fetchone()[0]
            cursor = connection.execute(
                "INSERT INTO exams(school_id, academic_year_id, name, exam_date) VALUES (1, ?, ?, ?)",
                (year_id, name, exam_date),
            )
            exam_id = cursor.lastrowid
            for subject_name, maximum, score in subject_scores:
                subject = connection.execute(
                    "SELECT id FROM subjects WHERE school_id = 1 AND name = ?", (subject_name,)
                ).fetchone()
                subject_id = subject[0] if subject else connection.execute(
                    "INSERT INTO subjects(school_id, name) VALUES (1, ?)", (subject_name,)
                ).lastrowid
                exam_subject_id = connection.execute(
                    "INSERT INTO exam_subjects(exam_id, subject_id, maximum_marks, passing_marks) VALUES (?, ?, ?, ?)",
                    (exam_id, subject_id, maximum, min(40, maximum)),
                ).lastrowid
                connection.execute(
                    "INSERT INTO marks(student_id, exam_subject_id, score) VALUES (?, ?, ?)",
                    (student_id, exam_subject_id, score),
                )
            connection.commit()
            return exam_id
        finally:
            connection.close()

    def attendance_scope(self, student, date_value="2026-10-20", session="full_day"):
        connection = sqlite3.connect(self.database_path)
        try:
            row = connection.execute(
                """SELECT ay.id, en.class_id, en.section_id FROM students AS st
                   JOIN student_enrollments AS en ON en.student_id = st.id
                   JOIN academic_years AS ay ON ay.id = en.academic_year_id
                   WHERE st.id = ? AND en.status = 'active' ORDER BY ay.is_current DESC LIMIT 1""",
                (student["id"],),
            ).fetchone()
            return {"academicYearId": row[0], "classId": row[1], "sectionId": row[2],
                    "date": date_value, "session": session}
        finally:
            connection.close()

    def save_attendance(self, scope, records):
        return self.client.put("/api/attendance/records", json={**scope, "records": records})

    def test_attendance_create_edit_all_statuses_repeat_save_and_privacy(self):
        student = self.add_student(guardianContact="private guardian value").get_json()["student"]
        scope = self.attendance_scope(student)
        roster = self.client.get("/api/attendance/roster", query_string=scope).get_json()
        self.assertEqual(roster["total"], 1)
        self.assertTrue(roster["students"][0]["unrecorded"])
        self.assertIsNone(roster["students"][0]["status"])
        self.assertNotIn("guardian_contact", str(roster))
        self.assertNotIn("private guardian value", str(roster))

        for index, status in enumerate(("present", "absent", "late", "excused")):
            response = self.save_attendance(scope, [{"studentId": student["id"], "status": status}])
            self.assertEqual(response.status_code, 200, response.get_json())
            payload = response.get_json()
            self.assertEqual(payload["created"], 1 if index == 0 else 0)
            self.assertEqual(payload["updated"], 0 if index == 0 else 1)
            saved = self.client.get("/api/attendance/roster", query_string=scope).get_json()["students"][0]
            self.assertEqual(saved["status"], status)
            self.assertFalse(saved["unrecorded"])

        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM attendance").fetchone()[0], 1)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """INSERT INTO attendance(student_id, academic_year_id, class_id, section_id,
                              attendance_date, session, status) VALUES (?, ?, ?, ?, ?, ?, 'present')""",
                    (student["id"], scope["academicYearId"], scope["classId"], scope["sectionId"], scope["date"], scope["session"]),
                )
        finally:
            connection.close()

    def test_attendance_validates_dates_sessions_enrollment_and_duplicate_payloads(self):
        student = self.add_student().get_json()["student"]
        scope = self.attendance_scope(student)
        invalid_date = {**scope, "date": "2026-02-30"}
        self.assertEqual(self.client.get("/api/attendance/roster", query_string=invalid_date).status_code, 400)
        invalid_session = {**scope, "session": "midnight"}
        self.assertEqual(self.client.get("/api/attendance/roster", query_string=invalid_session).status_code, 400)
        for invalid in (True, "Present", "unknown"):
            response = self.save_attendance(scope, [{"studentId": student["id"], "status": invalid}])
            self.assertEqual(response.status_code, 400)
        duplicate = self.save_attendance(scope, [
            {"studentId": student["id"], "status": "present"},
            {"studentId": student["id"], "status": "late"},
        ])
        self.assertEqual(duplicate.status_code, 400)
        wrong_section = {**scope, "sectionId": scope["sectionId"] + 1000}
        self.assertEqual(self.client.get("/api/attendance/roster", query_string=wrong_section).status_code, 400)
        wrong_class = {**scope, "classId": scope["classId"] + 1000}
        rejected = self.save_attendance(wrong_class, [{"studentId": student["id"], "status": "present"}])
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(self.save_attendance({**scope, "academicYearId": 99999}, [
            {"studentId": student["id"], "status": "present"},
        ]).status_code, 400)

    def test_attendance_roster_filters_year_class_section_and_preserves_placement_conflicts(self):
        first = self.add_student(studentCode="ATT-1", admissionNumber="ATT-ADM-1", name="First").get_json()["student"]
        second = self.add_student(studentCode="ATT-2", admissionNumber="ATT-ADM-2", name="Second", rollNumber="13").get_json()["student"]
        first_scope = self.attendance_scope(first)
        second_scope = self.attendance_scope(second)
        self.assertEqual(first_scope["classId"], second_scope["classId"])
        self.assertEqual(first_scope["sectionId"], second_scope["sectionId"])
        all_students = self.client.get("/api/attendance/roster", query_string=first_scope).get_json()
        self.assertEqual(all_students["total"], 2)
        self.assertEqual(self.save_attendance(first_scope, [{"studentId": second["id"], "status": "present"}]).status_code, 200)

        class_record = self.client.post("/api/classes", json={"name": "Attendance Other"}).get_json()["classRecord"]
        section = self.client.post(f"/api/classes/{class_record['id']}/sections", json={"name": "Green"}).get_json()["section"]
        year = first_scope["academicYearId"]
        enrollment = self.client.post(f"/api/students/{second['id']}/enrollments", json={
            "academicYearId": year, "classId": class_record["id"], "sectionId": section["id"], "status": "active",
        })
        self.assertEqual(enrollment.status_code, 201)
        first_enrollment = self.client.post(f"/api/students/{first['id']}/enrollments", json={
            "academicYearId": year, "classId": class_record["id"], "sectionId": section["id"], "status": "active",
        })
        self.assertEqual(first_enrollment.status_code, 201)
        second_class_scope = {**first_scope, "classId": class_record["id"], "sectionId": section["id"]}
        moved_roster = self.client.get("/api/attendance/roster", query_string=second_class_scope).get_json()
        self.assertEqual(moved_roster["total"], 2)
        moved_second = next(row for row in moved_roster["students"] if row["id"] == second["id"])
        self.assertTrue(moved_second["lockedPlacement"])
        self.assertEqual(moved_second["status"], "present")
        mismatch = self.client.get("/api/attendance/roster", query_string={**first_scope, "sectionId": section["id"]})
        self.assertEqual(mismatch.status_code, 400)
        conflict_batch = self.save_attendance(second_class_scope, [
            {"studentId": first["id"], "status": "late"},
            {"studentId": second["id"], "status": "absent"},
        ])
        self.assertEqual(conflict_batch.status_code, 409)
        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM attendance WHERE student_id = ?", (first["id"],)
            ).fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM attendance").fetchone()[0], 1)
        finally:
            connection.close()

        next_year = self.client.post("/api/academic-years", json={"label": "2030-2031"}).get_json()["academicYear"]
        next_enrollment = self.client.post(f"/api/students/{first['id']}/enrollments", json={
            "academicYearId": next_year["id"], "classId": first_scope["classId"],
            "sectionId": first_scope["sectionId"], "status": "active",
        })
        self.assertEqual(next_enrollment.status_code, 201)
        next_scope = {**first_scope, "academicYearId": next_year["id"]}
        self.assertEqual(self.client.get("/api/attendance/roster", query_string=next_scope).get_json()["total"], 1)

    def test_attendance_summaries_exclude_excused_and_unrecorded_and_flag_below_threshold(self):
        first = self.add_student(studentCode="SUM-1", admissionNumber="SUM-ADM-1", name="Summary A").get_json()["student"]
        second = self.add_student(studentCode="SUM-2", admissionNumber="SUM-ADM-2", name="Summary B", rollNumber="13").get_json()["student"]
        base = self.attendance_scope(first)
        for day, status in (("2026-10-20", "present"), ("2026-10-21", "late"),
                            ("2026-10-22", "absent"), ("2026-10-23", "excused")):
            response = self.save_attendance({**base, "date": day}, [{"studentId": first["id"], "status": status}])
            self.assertEqual(response.status_code, 200, response.get_json())
        second_scope = self.attendance_scope(second, "2026-10-20")
        self.assertEqual(self.save_attendance(second_scope, [{"studentId": second["id"], "status": "absent"}]).status_code, 200)

        summary = self.client.get("/api/attendance/summary", query_string={
            "academicYearId": base["academicYearId"], "classId": base["classId"],
            "sectionId": base["sectionId"], "threshold": "75",
        })
        self.assertEqual(summary.status_code, 200, summary.get_json())
        payload = summary.get_json()["summary"]
        first_result = next(item for item in payload["students"] if item["id"] == first["id"])
        second_result = next(item for item in payload["students"] if item["id"] == second["id"])
        self.assertEqual(first_result["attendancePercentage"], 66.67)
        self.assertEqual(first_result["countableSessions"], 3)
        self.assertEqual(first_result["excused"], 1)
        self.assertEqual(second_result["attendancePercentage"], 0)
        self.assertTrue(second_result["belowThreshold"])
        self.assertEqual(payload["class"]["attendancePercentage"], 50)
        self.assertEqual(payload["class"]["studentsBelowThreshold"], 2)
        threshold_boundary = self.client.get("/api/attendance/summary", query_string={
            "academicYearId": base["academicYearId"], "classId": base["classId"],
            "sectionId": base["sectionId"], "threshold": "0",
        }).get_json()["summary"]
        boundary_second = next(item for item in threshold_boundary["students"] if item["id"] == second["id"])
        self.assertFalse(boundary_second["belowThreshold"], "A score equal to threshold is not below it.")
        one_day = self.client.get("/api/attendance/summary", query_string={
            "academicYearId": base["academicYearId"], "classId": base["classId"],
            "sectionId": base["sectionId"], "fromDate": "2026-10-20", "toDate": "2026-10-20",
        }).get_json()["summary"]
        self.assertEqual(one_day["class"]["attendancePercentage"], 50)

        no_records = self.add_student(studentCode="SUM-3", admissionNumber="SUM-ADM-3", name="No Data", rollNumber="14").get_json()["student"]
        no_data = self.client.get(f"/api/students/{no_records['id']}/attendance", query_string={
            "academicYearId": base["academicYearId"],
        }).get_json()["summary"]
        self.assertIsNone(no_data["attendancePercentage"])
        self.assertFalse(no_data["belowThreshold"])
        only_excused = self.client.get(f"/api/students/{first['id']}/attendance", query_string={
            "academicYearId": base["academicYearId"], "fromDate": "2026-10-23", "toDate": "2026-10-23",
        }).get_json()["summary"]
        self.assertIsNone(only_excused["attendancePercentage"])
        self.assertEqual(only_excused["excused"], 1)

    def test_attendance_threshold_date_range_persistence_and_summary_privacy(self):
        student = self.add_student(guardianContact="private guardian value").get_json()["student"]
        scope = self.attendance_scope(student)
        self.assertEqual(self.save_attendance(scope, [{"studentId": student["id"], "status": "late"}]).status_code, 200)
        invalid_threshold = self.client.get("/api/attendance/summary", query_string={
            "academicYearId": scope["academicYearId"], "threshold": "101",
        })
        self.assertEqual(invalid_threshold.status_code, 400)
        invalid_range = self.client.get("/api/attendance/summary", query_string={
            "academicYearId": scope["academicYearId"], "fromDate": "2026-12-01", "toDate": "2026-11-01",
        })
        self.assertEqual(invalid_range.status_code, 400)
        restarted = create_app({"TESTING": True, "DATABASE": str(self.database_path)}).test_client()
        summary = restarted.get(f"/api/students/{student['id']}/attendance?academicYearId={scope['academicYearId']}")
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.get_json()["summary"]["attendancePercentage"], 100)
        self.assertNotIn("guardian_contact", str(summary.get_json()))
        self.assertNotIn("private guardian value", str(summary.get_json()))

    def test_attendance_version_five_migration_preserves_unassigned_legacy_records(self):
        old_database = Path(self.temporary_directory.name) / "attendance-v4.sqlite3"
        schema = SCHEMA_FILE.read_text(encoding="utf-8")
        schema = schema.replace("    academic_year_id INTEGER REFERENCES academic_years(id) ON DELETE RESTRICT,\n    class_id INTEGER REFERENCES classes(id) ON DELETE RESTRICT,\n    section_id INTEGER REFERENCES sections(id) ON DELETE RESTRICT,\n", "")
        schema = schema.replace("CREATE INDEX IF NOT EXISTS attendance_scope_date\n    ON attendance(academic_year_id, class_id, section_id, attendance_date);\n", "")
        connection = sqlite3.connect(old_database)
        try:
            connection.executescript(schema)
            connection.execute("INSERT INTO schema_migrations(version) VALUES (1)")
            connection.execute("PRAGMA user_version = 1")
            connection.execute("INSERT INTO schools(id, name) VALUES (1, 'Test School')")
            class_id = connection.execute("INSERT INTO classes(school_id, name) VALUES (1, 'Legacy Class')").lastrowid
            student_id = connection.execute(
                "INSERT INTO students(school_id, student_code, name, class_id) VALUES (1, 'LEG-1', 'Legacy Student', ?)",
                (class_id,),
            ).lastrowid
            connection.execute(
                "INSERT INTO attendance(student_id, attendance_date, session, status) VALUES (?, '2025-09-01', 'full_day', 'present')",
                (student_id,),
            )
            connection.commit()
        finally:
            connection.close()

        initialize_database(old_database)
        connection = sqlite3.connect(old_database)
        try:
            row = connection.execute(
                "SELECT student_id, attendance_date, session, status, academic_year_id, class_id, section_id FROM attendance"
            ).fetchone()
            self.assertEqual(row, (student_id, "2025-09-01", "full_day", "present", None, None, None))
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 5)
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            indexes = {item[1] for item in connection.execute("PRAGMA index_list(attendance)")}
            self.assertIn("attendance_scope_date", indexes)
            foreign_tables = {item[2] for item in connection.execute("PRAGMA foreign_key_list(attendance)")}
            self.assertTrue({"academic_years", "classes", "sections", "students", "staff"}.issubset(foreign_tables))
        finally:
            connection.close()

    def test_student_progress_orders_exams_normalizes_scores_and_distinguishes_missing_from_zero(self):
        student = self.add_student(guardianContact="private guardian value").get_json()["student"]
        self.add_progress_exam(student["id"], "Term Review 2", "2026-05-15", [
            ("Progress Maths", 200, 100), ("Progress Science", 50, 0),
        ])
        self.add_progress_exam(student["id"], "Term Review 1", "2026-01-15", [
            ("Progress Maths", 100, 80), ("Progress Science", 50, None),
        ])

        response = self.client.get(f"/api/students/{student['id']}/progress")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual([exam["examDate"] for exam in payload["exams"]], ["2026-01-15", "2026-05-15"])
        self.assertNotEqual(payload["exams"][0]["examId"], payload["exams"][1]["examId"])
        first, second = payload["exams"]
        maths = next(item for item in first["subjects"] if item["name"] == "Progress Maths")
        self.assertEqual(maths["percentage"], 80)
        self.assertEqual(first["overallPercentage"], 80)
        science = next(item for item in first["subjects"] if item["name"] == "Progress Science")
        self.assertEqual(science["status"], "Missing")
        self.assertIsNone(science["score"])
        self.assertEqual(second["overallPercentage"], 25)
        zero = next(item for item in second["subjects"] if item["name"] == "Progress Science")
        self.assertEqual(zero["score"], 0)
        self.assertEqual(zero["percentage"], 0)
        self.assertEqual(zero["status"], "Recorded")
        self.assertNotIn("guardian", str(payload).lower())
        self.assertNotIn("private guardian value", str(payload))

    def test_student_progress_subject_and_year_filters_and_validation(self):
        student = self.add_student().get_json()["student"]
        self.add_progress_exam(student["id"], "Subject Filter Exam", "2026-04-10", [
            ("Progress Maths", 100, 75), ("Progress Science", 50, 25),
        ])
        years = self.client.get("/api/academic-years").get_json()["academicYears"]
        year_id = next(year["id"] for year in years if year["is_current"])
        connection = sqlite3.connect(self.database_path)
        try:
            subject_id = connection.execute(
                "SELECT id FROM subjects WHERE name = 'Progress Maths'"
            ).fetchone()[0]
        finally:
            connection.close()

        response = self.client.get(
            f"/api/students/{student['id']}/progress?academicYearId={year_id}&subjectId={subject_id}"
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["selected"], {"academicYearId": year_id, "subjectId": subject_id})
        self.assertEqual(len(payload["exams"]), 1)
        self.assertEqual([item["name"] for item in payload["exams"][0]["subjects"]], ["Progress Maths"])
        self.assertEqual(self.client.get(f"/api/students/{student['id']}/progress?subjectId=bad").status_code, 400)
        self.assertEqual(self.client.get(f"/api/students/{student['id']}/progress?academicYearId=99999").status_code, 400)

    def test_student_progress_keeps_same_named_exams_in_different_years_separate(self):
        student = self.add_student().get_json()["student"]
        old_year = self.client.post("/api/academic-years", json={
            "label": "2025-2026", "startsOn": "2025-06-01", "endsOn": "2026-05-31",
        }).get_json()["academicYear"]
        current_year = next(year for year in self.client.get("/api/academic-years").get_json()["academicYears"] if year["is_current"])
        old_exam_id = self.add_progress_exam(student["id"], "Midterm", "2025-12-10", [
            ("Yearly Maths", 100, 70),
        ], old_year["id"])
        current_exam_id = self.add_progress_exam(student["id"], "Midterm", "2026-10-10", [
            ("Yearly Maths", 100, 80),
        ], current_year["id"])

        exams = self.client.get(f"/api/students/{student['id']}/progress").get_json()["exams"]
        self.assertEqual([exam["examId"] for exam in exams], [old_exam_id, current_exam_id])
        self.assertEqual([exam["name"] for exam in exams], ["Midterm", "Midterm"])
        self.assertEqual([exam["academicYear"] for exam in exams], ["2025-2026", current_year["label"]])

    def test_student_progress_empty_and_unknown_students(self):
        student = self.add_student().get_json()["student"]
        empty = self.client.get(f"/api/students/{student['id']}/progress")
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.get_json()["exams"], [])
        self.assertEqual(self.client.get("/api/students/99999/progress").status_code, 404)
        students = self.client.get("/api/student-progress/students?search=S-100").get_json()["students"]
        self.assertEqual(len(students), 1)
        self.assertNotIn("guardian_contact", students[0])
        self.assertNotIn("admission_number", students[0])

    def test_student_create_list_detail_update_and_archive(self):
        created = self.add_student()
        self.assertEqual(created.status_code, 201)
        student = created.get_json()["student"]
        self.assertEqual(student["student_code"], "S-100")
        self.assertEqual(student["admission_number"], "ADM-100")
        self.assertEqual(student["class_name"], "Grade 7")
        self.assertEqual(student["section_name"], "Blue")

        listed = self.client.get("/api/students?search=S-100")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.get_json()["total"], 1)
        self.assertEqual(self.client.get("/api/students?search=ADM-100").get_json()["total"], 1)

        detail = self.client.get(f"/api/students/{student['id']}")
        self.assertEqual(detail.get_json()["student"]["name"], "Asha Rao")

        updated = self.client.put(f"/api/students/{student['id']}", json={
            "studentCode": "S-100",
            "admissionNumber": "ADM-100",
            "name": "Asha Sharma",
            "rollNumber": "15",
            "className": "Grade 8",
            "sectionName": "Gold",
        })
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["student"]["name"], "Asha Sharma")
        self.assertEqual(updated.get_json()["student"]["class_name"], "Grade 8")
        self.assertEqual(updated.get_json()["student"]["admission_number"], "ADM-100")

        removed = self.client.delete(f"/api/students/{student['id']}")
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(self.client.get("/api/students").get_json()["total"], 0)
        self.assertEqual(self.client.get(f"/api/students/{student['id']}").status_code, 404)

    def test_student_id_and_group_roll_number_must_be_unique(self):
        first = self.add_student()
        self.assertEqual(first.status_code, 201)

        duplicate_code = self.add_student(rollNumber="13", admissionNumber="ADM-101")
        self.assertEqual(duplicate_code.status_code, 409)
        self.assertIn("student ID", duplicate_code.get_json()["error"])

        duplicate_roll = self.add_student(studentCode="S-101", admissionNumber="ADM-102")
        self.assertEqual(duplicate_roll.status_code, 409)
        self.assertIn("roll number", duplicate_roll.get_json()["error"])

        duplicate_admission = self.add_student(studentCode="S-102", rollNumber="14", className="Grade 10", sectionName="Orange")
        self.assertEqual(duplicate_admission.status_code, 409)
        self.assertIn("admission number", duplicate_admission.get_json()["error"])

    def test_validation_rejects_bad_student_data(self):
        blank_name = self.add_student(name="  ")
        self.assertEqual(blank_name.status_code, 400)

        missing_admission = self.add_student(studentCode="S-102", admissionNumber="")
        self.assertEqual(missing_admission.status_code, 400)

        section_without_class = self.add_student(studentCode="S-102", className="", sectionName="Blue")
        self.assertEqual(section_without_class.status_code, 400)

        too_long = self.add_student(studentCode="S-103", name="A" * 101)
        self.assertEqual(too_long.status_code, 400)

        invalid_date = self.add_student(studentCode="S-104", admissionNumber="ADM-104", admissionDate="2026-02-31")
        self.assertEqual(invalid_date.status_code, 400)

    def test_academic_year_class_section_and_student_enrollment_workflows(self):
        student = self.add_student(guardianName="Ravi Rao", guardianContact="+91 90000 00000",
                                   admissionDate="2026-06-01").get_json()["student"]
        self.assertEqual(student["guardian_name"], "Ravi Rao")
        self.assertEqual(student["admission_date"], "2026-06-01")

        years = self.client.get("/api/academic-years").get_json()["academicYears"]
        current_year = next(year for year in years if year["is_current"])
        created_year = self.client.post("/api/academic-years", json={
            "label": "2027-2028", "startsOn": "2027-06-01", "endsOn": "2028-05-31",
        })
        self.assertEqual(created_year.status_code, 201)
        duplicate_year = self.client.post("/api/academic-years", json={"label": "2027-2028"})
        self.assertEqual(duplicate_year.status_code, 409)

        class_response = self.client.post("/api/classes", json={"name": "Grade 9"})
        self.assertEqual(class_response.status_code, 201)
        class_id = class_response.get_json()["classRecord"]["id"]
        duplicate_class = self.client.post("/api/classes", json={"name": "grade 9"})
        self.assertEqual(duplicate_class.status_code, 409, duplicate_class.get_json())
        section_response = self.client.post(f"/api/classes/{class_id}/sections", json={"name": "Green"})
        self.assertEqual(section_response.status_code, 201)
        section_id = section_response.get_json()["section"]["id"]
        duplicate_section = self.client.post(f"/api/classes/{class_id}/sections", json={"name": "green"})
        self.assertEqual(duplicate_section.status_code, 409)
        class_list = self.client.get("/api/classes").get_json()["classes"]
        self.assertIn("Grade 9", {record["name"] for record in class_list})
        self.assertIn("Green", {record["name"] for record in self.client.get("/api/sections").get_json()["sections"]})

        invalid_assignment = self.client.post(f"/api/students/{student['id']}/enrollments", json={
            "academicYearId": created_year.get_json()["academicYear"]["id"],
            "classId": class_id + 100,
            "sectionId": section_id,
        })
        self.assertEqual(invalid_assignment.status_code, 400)

        new_year_id = created_year.get_json()["academicYear"]["id"]
        enrollment = self.client.post(f"/api/students/{student['id']}/enrollments", json={
            "academicYearId": new_year_id, "classId": class_id,
            "sectionId": section_id, "rollNumber": "9", "status": "active",
        })
        self.assertEqual(enrollment.status_code, 201)
        self.assertEqual(enrollment.get_json()["enrollment"]["section_name"], "Green")
        history = self.client.get(f"/api/students/{student['id']}/enrollments").get_json()["enrollments"]
        self.assertEqual({entry["academic_year_id"] for entry in history}, {current_year["id"], new_year_id})
        filtered = self.client.get(f"/api/students?academicYearId={new_year_id}&classId={class_id}&sectionId={section_id}")
        self.assertEqual(filtered.get_json()["total"], 1)
        self.assertEqual(filtered.get_json()["students"][0]["class_name"], "Grade 9")
        restarted = create_app({"TESTING": True, "DATABASE": str(self.database_path)}).test_client()
        self.assertEqual(len(restarted.get(f"/api/students/{student['id']}/enrollments").get_json()["enrollments"]), 2)

    def test_class_and_section_counts_use_current_active_enrollments_and_keep_unused_records(self):
        students = []
        for index in range(4):
            students.append(self.add_student(
                studentCode=f"M-{index + 1}", admissionNumber=f"ADM-M-{index + 1}",
                name=f"Class 10 student {index + 1}", rollNumber=str(index + 1),
                className="10", sectionName="f",
            ).get_json()["student"])
        archived = self.client.delete(f"/api/students/{students[-1]['id']}")
        self.assertEqual(archived.status_code, 200)

        other_class_24 = self.client.post("/api/classes", json={"name": "24"}).get_json()["classRecord"]
        other_class_4 = self.client.post("/api/classes", json={"name": "4"}).get_json()["classRecord"]
        current_class_10 = next(record for record in self.client.get("/api/classes").get_json()["classes"] if record["name"] == "10")
        created = self.client.post(f"/api/classes/{other_class_24['id']}/sections", json={"name": "c"})
        self.assertEqual(created.status_code, 201)
        self.assertEqual(self.client.post(f"/api/classes/{other_class_4['id']}/sections", json={"name": "g"}).status_code, 201)
        self.assertEqual(self.client.post(f"/api/classes/{current_class_10['id']}/sections", json={"name": "c"}).status_code, 201)

        class_rows = self.client.get("/api/classes").get_json()["classes"]
        classes = {row["name"]: row for row in class_rows}
        self.assertEqual(classes["10"]["active_students"], 3)
        self.assertEqual(classes["10"]["section_count"], 2)
        self.assertEqual(classes["24"]["active_students"], 0)
        self.assertEqual(classes["4"]["active_students"], 0)

        section_rows = self.client.get("/api/sections").get_json()["sections"]
        sections = {(row["class_name"], row["name"]): row for row in section_rows}
        self.assertEqual(sections[("10", "f")]["active_students"], 3)
        self.assertEqual(sections[("10", "c")]["active_students"], 0)
        self.assertEqual(sections[("24", "c")]["active_students"], 0)
        self.assertEqual(sections[("4", "g")]["active_students"], 0)

        # Archiving hides the student from current counts but preserves their enrollment row.
        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM student_enrollments WHERE student_id = ?", (students[-1]["id"],)
            ).fetchone()[0], 1)
        finally:
            connection.close()

        dashboard = self.client.get("/api/dashboard/analytics").get_json()
        self.assertEqual(dashboard["summary"]["totalActiveStudents"], 3)

    def test_search_treats_sql_text_as_data(self):
        self.add_student()
        response = self.client.get("/api/students?search=%27%20OR%201%3D1%20--")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["total"], 0)

    def test_marks_are_validated_saved_analyzed_and_persist_after_restart(self):
        first = self.add_student().get_json()["student"]
        second = self.add_student(
            studentCode="S-101", admissionNumber="ADM-101", name="Ben Das", rollNumber="13"
        ).get_json()["student"]
        payload = {
            "passMark": 40,
            "subjects": ["Maths", "Science"],
            "students": [
                {"id": first["id"], "marks": [90, 80]},
                {"id": second["id"], "marks": [35, 40]},
            ],
        }
        result = self.client.post("/api/analysis", json=payload)
        self.assertEqual(result.status_code, 200)
        analysis = result.get_json()["analysis"]
        self.assertEqual(analysis["classAverage"], 61.25)
        self.assertEqual(analysis["highestNames"], ["Asha Rao"])
        self.assertEqual(analysis["students"][0]["average"], 85)
        self.assertEqual(analysis["students"][1]["status"], "Fail")
        self.assertEqual(analysis["passPercentage"], 50)

        detail = self.client.get(f"/api/students/{first['id']}").get_json()["student"]
        self.assertEqual(len(detail["marks"]), 2)
        self.assertEqual(
            {mark["subject_name"]: mark["score"] for mark in detail["marks"]},
            {"Maths": 90, "Science": 80},
        )

        restarted_app = create_app({"TESTING": True, "DATABASE": str(self.database_path)})
        restarted_client = restarted_app.test_client()
        self.assertEqual(restarted_client.get("/api/students").get_json()["total"], 2)
        persisted = restarted_client.get(f"/api/students/{first['id']}").get_json()["student"]
        self.assertEqual(len(persisted["marks"]), 2)
        latest = restarted_client.get("/api/analysis/latest").get_json()["analysis"]
        self.assertEqual(latest["classAverage"], 61.25)
        self.assertEqual(latest["students"][0]["name"], "Asha Rao")

    def test_dashboard_empty_state_and_invalid_filter_validation(self):
        response = self.client.get("/api/dashboard/analytics")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["summary"]["totalActiveStudents"], 0)
        self.assertIsNone(payload["summary"]["classAveragePercentage"])
        self.assertIsNone(payload["summary"]["passPercentage"])
        self.assertEqual(payload["summary"]["highestPerformers"], [])
        self.assertEqual(payload["results"], [])
        self.assertEqual(payload["passFailDistribution"], {"pass": 0, "fail": 0})
        self.assertEqual(self.client.get("/api/dashboard/analytics?academicYearId=bad").status_code, 400)
        self.assertEqual(self.client.get("/api/dashboard/analytics?sectionId=99999").status_code, 400)

    def test_dashboard_calculates_valid_marks_boundaries_and_hides_private_fields(self):
        first = self.add_student().get_json()["student"]
        second = self.add_student(studentCode="S-101", admissionNumber="ADM-101",
                                  name="Ben Das", rollNumber="13").get_json()["student"]
        third = self.add_student(studentCode="S-102", admissionNumber="ADM-102",
                                 name="Cara Shah", rollNumber="14").get_json()["student"]
        saved = self.client.post("/api/analysis", json={
            "passMark": 40, "subjects": ["Maths", "English"],
            "students": [
                {"id": first["id"], "marks": [40, 100]},
                {"id": second["id"], "marks": [39, 40]},
                {"id": third["id"], "marks": [0, 0]},
            ],
        })
        self.assertEqual(saved.status_code, 200)
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("UPDATE marks SET score = NULL WHERE student_id = ?", (third["id"],))
            connection.commit()
        finally:
            connection.close()
        payload = self.client.get("/api/dashboard/analytics").get_json()
        self.assertEqual(payload["summary"]["totalActiveStudents"], 3)
        self.assertEqual(payload["summary"]["studentsWithMarks"], 2)
        self.assertEqual(payload["summary"]["classAveragePercentage"], 54.75)
        self.assertEqual(payload["summary"]["passPercentage"], 50.0)
        self.assertEqual(payload["summary"]["passMarkPercentage"], 40.0)
        self.assertEqual(payload["summary"]["highestPerformers"][0]["name"], "Asha Rao")
        self.assertEqual(payload["gradeDistribution"], {"A": 0, "B": 0, "C": 1, "D": 0, "E": 0, "F": 1})
        maths = next(subject for subject in payload["subjects"] if subject["name"] == "Maths")
        self.assertEqual(maths["studentCount"], 2)
        self.assertEqual(maths["averageMarks"], 39.5)
        self.assertEqual(maths["highestMarks"], 40)
        self.assertEqual(maths["lowestMarks"], 39)
        boundary = next(row for row in payload["results"] if row["id"] == first["id"])
        self.assertEqual(boundary["status"], "Pass")
        self.assertEqual(boundary["rank"], 1)
        missing = next(row for row in payload["results"] if row["id"] == third["id"])
        self.assertEqual(missing["status"], "No marks")
        self.assertIsNone(missing["percentage"])
        self.assertNotIn("guardian", str(payload).lower())
        self.assertNotIn("admission", str(payload).lower())

    def test_dashboard_normalizes_different_maxima_and_tied_ranks(self):
        first = self.add_student().get_json()["student"]
        second = self.add_student(studentCode="S-101", admissionNumber="ADM-101",
                                  name="Ben Das", rollNumber="13").get_json()["student"]
        saved = self.client.post("/api/analysis", json={
            "passMark": 40, "subjects": ["Maths", "English"],
            "students": [
                {"id": first["id"], "marks": [50, 80]},
                {"id": second["id"], "marks": [50, 80]},
            ],
        })
        self.assertEqual(saved.status_code, 200)
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                "UPDATE exam_subjects SET maximum_marks = 200, passing_marks = 80 WHERE subject_id = (SELECT id FROM subjects WHERE name = 'Maths')"
            )
            connection.commit()
        finally:
            connection.close()
        payload = self.client.get("/api/dashboard/analytics").get_json()
        self.assertEqual(payload["summary"]["classAveragePercentage"], 52.5)
        self.assertEqual([row["rank"] for row in payload["results"]], [1, 1])
        maths = next(subject for subject in payload["subjects"] if subject["name"] == "Maths")
        self.assertEqual(maths["averageMarks"], 50)
        self.assertEqual(maths["averagePercentage"], 25)

    def test_dashboard_scopes_duplicate_exam_history_and_filters(self):
        first = self.add_student().get_json()["student"]
        second = self.add_student(studentCode="S-101", admissionNumber="ADM-101",
                                  name="Ben Das", rollNumber="13", className="Grade 8",
                                  sectionName="Gold").get_json()["student"]
        saved = self.client.post("/api/analysis", json={
            "passMark": 40, "subjects": ["Maths"],
            "students": [{"id": first["id"], "marks": [90]}, {"id": second["id"], "marks": [50]}],
        })
        self.assertEqual(saved.status_code, 200)
        connection = sqlite3.connect(self.database_path)
        try:
            year_id = connection.execute("SELECT id FROM academic_years WHERE is_current = 1").fetchone()[0]
            class_id = connection.execute("SELECT id FROM classes WHERE name = 'Grade 7'").fetchone()[0]
            section_id = connection.execute("SELECT id FROM sections WHERE name = 'Blue'").fetchone()[0]
            default_exam_id = connection.execute("SELECT id FROM exams WHERE name = 'Practice Exam' AND academic_year_id IS NULL").fetchone()[0]
            exam_id = connection.execute("INSERT INTO exams(school_id, academic_year_id, name) VALUES (1, ?, 'Practice Exam')", (year_id,)).lastrowid
            subject_id = connection.execute("SELECT id FROM subjects WHERE name = 'Maths'").fetchone()[0]
            exam_subject_id = connection.execute("INSERT INTO exam_subjects(exam_id, subject_id) VALUES (?, ?)", (exam_id, subject_id)).lastrowid
            connection.execute("INSERT INTO marks(student_id, exam_subject_id, score) VALUES (?, ?, 20)", (first["id"], exam_subject_id))
            connection.commit()
        finally:
            connection.close()
        default = self.client.get(f"/api/dashboard/analytics?examId={default_exam_id}").get_json()
        self.assertEqual(default["summary"]["studentsWithMarks"], 2)
        historical = self.client.get(f"/api/dashboard/analytics?examId={exam_id}").get_json()
        self.assertEqual(historical["summary"]["studentsWithMarks"], 1)
        self.assertEqual(historical["summary"]["classAveragePercentage"], 20.0)
        self.assertEqual(len(historical["results"]), 2)
        filtered = self.client.get(
            f"/api/dashboard/analytics?academicYearId={year_id}&classId={class_id}&sectionId={section_id}&search=Asha"
        )
        self.assertEqual(filtered.status_code, 200)
        self.assertEqual([row["name"] for row in filtered.get_json()["results"]], ["Asha Rao"])
        self.assertEqual(self.client.get(f"/api/dashboard/analytics?classId={class_id}&sectionId={section_id + 100}").status_code, 400)

        next_year_response = self.client.post("/api/academic-years", json={"label": "2030-2031"})
        self.assertEqual(next_year_response.status_code, 201)
        next_year_id = next_year_response.get_json()["academicYear"]["id"]
        enrollment = self.client.post(f"/api/students/{first['id']}/enrollments", json={
            "academicYearId": next_year_id, "classId": class_id,
            "sectionId": section_id, "rollNumber": "12", "status": "active",
        })
        self.assertEqual(enrollment.status_code, 201)
        connection = sqlite3.connect(self.database_path)
        try:
            next_exam_id = connection.execute(
                "INSERT INTO exams(school_id, academic_year_id, name) VALUES (1, ?, 'Midterm')",
                (next_year_id,),
            ).lastrowid
            next_exam_subject = connection.execute(
                "INSERT INTO exam_subjects(exam_id, subject_id) VALUES (?, ?)",
                (next_exam_id, subject_id),
            ).lastrowid
            connection.execute(
                "INSERT INTO marks(student_id, exam_subject_id, score) VALUES (?, ?, 75)",
                (first["id"], next_exam_subject),
            )
            connection.commit()
        finally:
            connection.close()
        next_year_dashboard = self.client.get(
            f"/api/dashboard/analytics?academicYearId={next_year_id}&examId={next_exam_id}"
        ).get_json()
        self.assertEqual(next_year_dashboard["summary"]["totalActiveStudents"], 1)
        self.assertEqual([row["name"] for row in next_year_dashboard["results"]], ["Asha Rao"])

    def test_invalid_marks_do_not_save_partial_analysis(self):
        student = self.add_student().get_json()["student"]
        payload = {
            "passMark": 40,
            "subjects": ["Maths", "Science"],
            "students": [{"id": student["id"], "marks": [88, 101]}],
        }
        response = self.client.post("/api/analysis", json=payload)
        self.assertEqual(response.status_code, 400)
        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM marks").fetchone()[0], 0)
        finally:
            connection.close()

    def test_archive_keeps_saved_marks(self):
        student = self.add_student().get_json()["student"]
        saved = self.client.post("/api/analysis", json={
            "passMark": 40,
            "subjects": ["Maths"],
            "students": [{"id": student["id"], "marks": [88]}],
        })
        self.assertEqual(saved.status_code, 200)
        self.client.delete(f"/api/students/{student['id']}")
        connection = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM marks").fetchone()[0], 1)
        finally:
            connection.close()

    def test_future_school_tables_exist_and_seed_is_repeatable(self):
        initialize_again = create_app({"TESTING": True, "DATABASE": str(self.database_path)})
        self.assertIsNotNone(initialize_again)
        connection = sqlite3.connect(self.database_path)
        try:
            table_names = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        finally:
            connection.close()
        self.assertTrue({
            "students", "classes", "sections", "subjects", "exams", "exam_subjects",
            "marks", "analysis_sessions", "staff",
            "attendance", "fee_types", "student_fees", "payments", "timetable_slots",
            "announcements",
        }.issubset(table_names))

    def test_existing_version_one_database_gets_safe_follow_up_migrations(self):
        old_database = Path(self.temporary_directory.name) / "version-one.sqlite3"
        connection = sqlite3.connect(old_database)
        try:
            connection.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
            connection.execute("INSERT INTO schema_migrations(version) VALUES (1)")
            connection.commit()
        finally:
            connection.close()

        initialize_database(old_database)
        connection = sqlite3.connect(old_database)
        try:
            versions = [row[0] for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()]
            trigger_names = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            ).fetchall()}
            self.assertEqual(versions, [1, 2, 3, 4, 5])
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 5)
            self.assertIn("student_section_matches_class_on_insert", trigger_names)
        finally:
            connection.close()

    def test_markwise_page_and_existing_assets_are_served_by_flask(self):
        with self.client.get("/") as page:
            self.assertEqual(page.status_code, 200)
            self.assertIn(b"markwise", page.data.lower())
            self.assertIn(b"href=\"#attendance\"", page.data)
            self.assertIn(b"Attendance", page.data)
            self.assertIn(b"Current active setup", page.data)
            self.assertIn(b"Unused setup records", page.data)
            self.assertIn(b"unused-class-list", page.data)
        with self.client.get("/app.js") as javascript:
            self.assertEqual(javascript.status_code, 200)
        with self.client.get("/styles.css") as styles:
            self.assertEqual(styles.status_code, 200)

    def test_database_open_failure_returns_a_safe_message(self):
        self.app.config["DATABASE"] = self.temporary_directory.name
        response = self.client.get("/api/students")
        self.assertEqual(response.status_code, 503)
        self.assertIn("database", response.get_json()["error"].lower())


if __name__ == "__main__":
    unittest.main()
