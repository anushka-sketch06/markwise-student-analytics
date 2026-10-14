"""SQLite connection and versioned database initialization for Markwise."""

from pathlib import Path
import sqlite3
from datetime import date


ROOT = Path(__file__).resolve().parent
SCHEMA_FILE = ROOT / "schema.sql"
DEFAULT_SUBJECTS = ("Maths", "Science", "English")
DEFAULT_EXAM = "Practice Exam"


def connect_database(database_path):
    """Open a SQLite connection with row dictionaries and foreign-key checks on."""
    path = Path(database_path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def initialize_database(database_path):
    """Apply the initial schema safely and create default school/exam settings."""
    connection = connect_database(database_path)
    try:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                   version INTEGER PRIMARY KEY,
                   applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        current_version = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ).fetchone()[0]

        schema_sql = SCHEMA_FILE.read_text(encoding="utf-8")
        migrations = {
            1: schema_sql,
            2: """CREATE TABLE IF NOT EXISTS analysis_sessions (
                   id INTEGER PRIMARY KEY,
                   exam_id INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
                   pass_mark REAL NOT NULL CHECK (pass_mark >= 0 AND pass_mark <= 100),
                   subject_ids_json TEXT NOT NULL,
                   student_ids_json TEXT NOT NULL,
                   created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               );
               CREATE INDEX IF NOT EXISTS latest_analysis_session ON analysis_sessions(id DESC);""",
            3: """CREATE TRIGGER IF NOT EXISTS student_section_matches_class_on_insert
                   BEFORE INSERT ON students
                   WHEN NEW.section_id IS NOT NULL AND NOT EXISTS (
                       SELECT 1 FROM sections WHERE id = NEW.section_id AND class_id = NEW.class_id
                   )
                   BEGIN
                       SELECT RAISE(ABORT, 'student section must belong to selected class');
                   END;
                   CREATE TRIGGER IF NOT EXISTS student_section_matches_class_on_update
                   BEFORE UPDATE OF section_id, class_id ON students
                   WHEN NEW.section_id IS NOT NULL AND NOT EXISTS (
                       SELECT 1 FROM sections WHERE id = NEW.section_id AND class_id = NEW.class_id
                   )
                   BEGIN
                       SELECT RAISE(ABORT, 'student section must belong to selected class');
                   END;""",
            4: (ROOT / "migrations" / "0004_student_profiles_enrollments.sql").read_text(encoding="utf-8"),
        }
        for version, migration_sql in migrations.items():
            if current_version >= version:
                continue
            try:
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + migration_sql
                    + f"\nPRAGMA user_version = {version};"
                    + f"\nINSERT OR IGNORE INTO schema_migrations(version) VALUES ({version});\nCOMMIT;"
                )
            except sqlite3.Error:
                if connection.in_transaction:
                    connection.rollback()
                raise

        with connection:
            connection.execute(
                "INSERT OR IGNORE INTO schools(id, name) VALUES (1, ?)",
                ("Markwise School",),
            )
            # Give the existing records a clear academic-year home without
            # replacing their legacy class, section, marks, or exam records.
            academic_year = connection.execute(
                "SELECT id FROM academic_years WHERE school_id = 1 AND is_current = 1"
            ).fetchone()
            if academic_year is None:
                start_year = date.today().year
                label = f"{start_year}-{start_year + 1}"
                connection.execute(
                    "UPDATE academic_years SET is_current = 0 WHERE school_id = 1"
                )
                connection.execute(
                    "INSERT OR IGNORE INTO academic_years(school_id, label, is_current) VALUES (1, ?, 1)",
                    (label,),
                )
                connection.execute(
                    "UPDATE academic_years SET is_current = 1 WHERE school_id = 1 AND label = ?",
                    (label,),
                )
                academic_year = connection.execute(
                    "SELECT id FROM academic_years WHERE school_id = 1 AND label = ?",
                    (label,),
                ).fetchone()
            # Snapshot the old class/section fields as the first enrollment.
            connection.execute(
                """INSERT OR IGNORE INTO student_enrollments
                       (student_id, academic_year_id, class_id, section_id, roll_number, status)
                   SELECT id, ?, class_id, section_id, roll_number, enrollment_status
                   FROM students WHERE class_id IS NOT NULL""",
                (academic_year["id"],),
            )
            connection.execute(
                "INSERT OR IGNORE INTO exams(school_id, name) VALUES (1, ?)",
                (DEFAULT_EXAM,),
            )
            exam_id = connection.execute(
                "SELECT id FROM exams WHERE school_id = 1 AND name = ?",
                (DEFAULT_EXAM,),
            ).fetchone()[0]
            for name in DEFAULT_SUBJECTS:
                connection.execute(
                    "INSERT OR IGNORE INTO subjects(school_id, name) VALUES (1, ?)",
                    (name,),
                )
                subject_id = connection.execute(
                    "SELECT id FROM subjects WHERE school_id = 1 AND name = ?",
                    (name,),
                ).fetchone()[0]
                connection.execute(
                    """INSERT OR IGNORE INTO exam_subjects
                           (exam_id, subject_id, maximum_marks, passing_marks)
                       VALUES (?, ?, 100, 40)""",
                    (exam_id, subject_id),
                )
    finally:
        connection.close()
