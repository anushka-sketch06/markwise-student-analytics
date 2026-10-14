# Markwise School Management System

Markwise is being expanded from a student marks analyzer into a school management system. Phase 3 adds student profiles, academic years, classes, sections, and year-based enrollment history to the existing Flask and SQLite app while keeping the Markwise HTML, CSS, JavaScript, and marks analyzer.

## Current features (Phase 3)

- Add, view, edit, archive, and search students by name, student ID, admission number, or roll number.
- Store admission number, guardian name and contact, admission date, and enrollment status in student profiles.
- Create and list academic years, classes, and class sections; set an academic year as current.
- Enroll students in a class and section by academic year, review historical enrollment, and filter the student list.
- Store student IDs uniquely. Roll numbers are unique within a class and section.
- Save class and section names into relational records when a student is created or edited.
- Select saved students in the existing marks analyzer.
- Validate marks and pass thresholds on both the page and the server.
- Save marks under a seeded `Practice Exam`; calculate and display the class average, highest scorer(s), and pass/fail results from the saved marks.
- Restore the most recent analysis and form entries after refreshing or restarting Markwise.
- Download the analyzed results as CSV.
- Keep archived students' saved marks in the database.

The database also has initial relational tables for subjects, exams, marks, staff, attendance, fees, payments, timetable slots, and announcements. Only student, academic-year, class, section, enrollment, and marks workflows are currently managed by the app. There are no fees, staff, attendance, timetable, announcements, or analytics dashboard workflows yet.

The old command-line analyzer is still available as a separate tool; it does not write to the Markwise database.

## Technology and architecture

- **Frontend:** existing vanilla HTML, CSS, and JavaScript in `outputs/`.
- **Backend:** Flask routes in `app.py`, returning the same HTML assets and JSON APIs.
- **Database:** SQLite through Python's built-in `sqlite3` module; no ORM is required.
- **Schema and migrations:** `schema.sql` plus versioned initialization in `database.py`.
- **Tests:** Python's built-in `unittest` and Flask's test client.

The API uses parameterized SQLite statements. Validation runs on the server even when a browser form also checks the input. The development server binds only to `127.0.0.1`; this phase does not add login or production deployment security.

## Install and run on Windows PowerShell

From the project root:

```powershell
python -m venv .venv
python -m pip --python .\.venv\Scripts\python.exe install -r requirements.txt
\.venv\Scripts\python.exe app.py
```

Then visit <http://127.0.0.1:5000/>. The database is initialized automatically at `instance/markwise.sqlite3`; the `instance/` folder is ignored by Git. First startup creates a Markwise school record, a current academic year, a `Practice Exam`, and the default Maths, Science, and English subjects. It does **not** create sample students or fabricated analytics.

Open the site at the Flask URL above. Opening `outputs/index.html` directly in a file preview (`file://`) only displays the static page; it does not start Flask or connect to SQLite. Markwise shows a notice in that preview and explains how to start the backend. API calls intentionally remain same-origin; cross-origin access is not enabled.

The pip `--python` option installs Flask into the virtual environment without requiring PowerShell activation. If your virtual environment already has pip, this equivalent install command also works:

```powershell
\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

To store the SQLite file elsewhere, set `MARKWISE_DATABASE` before starting the app:

```powershell
$env:MARKWISE_DATABASE = 'D:\MarkwiseData\markwise.sqlite3'
\.venv\Scripts\python.exe app.py
```

Keep the app bound to your own computer; it is not ready to expose to the public internet because authentication and production security have not been added.

## Run the tests

From the project root, while the virtual environment is available:

```powershell
\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The tests cover student create/list/detail/update/archive, duplicate IDs and roll numbers, input validation, SQL-safe search, marks validation and calculations, persistence across app restarts, database initialization and migration, serving the existing page assets, and safe database errors.

## Existing Python command-line analyzer

The original beginner-friendly command-line tool remains available and still uses only Python's standard library:

```powershell
python .\outputs\student_marks_analyzer.py
```

It collects marks interactively and can save a CSV in the current folder. It is independent of the Flask site and SQLite database in this phase.

## Database relationships and migrations

`students` belong to a school. `classes` belong to the school and `sections` belong to classes. `student_enrollments` connects a student to a class, optional section, roll number, status, and academic year. A student can have one enrollment per year, retaining prior-year placement when a new year's assignment is added. Each admission number is unique per school; student IDs remain unique. `exams` connect to `subjects` through `exam_subjects`, where maximum and passing marks are stored. `marks` connect one student to one exam subject; a `NULL` score means not entered, while `0` is a real mark.

Migration history is stored in `schema_migrations` and mirrored in SQLite's `PRAGMA user_version`. Migration 4 is defined in `migrations/0004_student_profiles_enrollments.sql`. It adds admission number, guardian name/contact, admission date, and enrollment status; gives existing students `LEGACY-<student row id>` admission numbers; adds uniqueness and enrollment indexes; and rejects enrollment sections that do not belong to the selected class. It does not modify existing student IDs, marks, exams, or subjects. Initialization creates a current year labeled for the current calendar year and snapshots existing class/section assignments into enrollment rows. Before migrating an important database, make a separate copy and verify it with `PRAGMA integrity_check`.

Future school tables connect staff to school records, attendance to students and staff, student fees to fee types and academic years, payments to fees, timetable slots to sections/subjects/staff, and announcements to a school or section. These modules are schema foundations only; they have no user interface or operations yet.

Student removal is an archive (`is_active = 0`) so saved marks and historical enrollments are not destroyed. Archived records are hidden from the active student search and analysis selectors. A future records-management phase can add restoration and access controls.

## Files

```text
app.py                 Flask app and JSON endpoints
database.py            SQLite connection and versioned initialization
schema.sql             Relational schema
requirements.txt       Flask dependency
tests/test_app.py      API, validation, and persistence tests
outputs/               Existing Markwise website and Python analyzer
instance/               Local SQLite database, generated on first run
migrations/             Versioned SQL migration scripts
```
