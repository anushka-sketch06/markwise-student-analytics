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
            self.assertEqual(versions, [1, 2, 3, 4])
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 4)
            self.assertIn("student_section_matches_class_on_insert", trigger_names)
        finally:
            connection.close()

    def test_markwise_page_and_existing_assets_are_served_by_flask(self):
        with self.client.get("/") as page:
            self.assertEqual(page.status_code, 200)
            self.assertIn(b"markwise", page.data.lower())
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
