# Markwise School Management System

Markwise is being expanded from a student marks analyzer into a school management system. It preserves the existing HTML, CSS, JavaScript, Flask, and SQLite application while adding student profiles, academic-year enrollment, academic analytics, and attendance.

## Current features (Phases 3–5B)

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
- View a database-backed dashboard for active students, class average, pass percentage, highest performers, grade distribution, and pass/fail outcomes.
- Compare subject averages, highest and lowest marks, and valid mark counts for a selected examination.
- Filter analytics by academic year, class, section, examination, and student name or ID.
- Review a privacy-limited results table with marks entered, percentage, grade, result, and competition rank; guardian and admission details are not returned by the analytics API.
- Display responsive subject, grade, pass/fail, and top-student charts using native HTML and CSS without external chart scripts.
- Record daily attendance for an active class roster by academic year, class, section, date, and session; edit saved Present, Absent, Late, and Excused statuses.
- Review individual and class attendance summaries, configure the low-attendance alert threshold (75% by default), and view below-threshold students.
- Keep attendance placement snapshots so later enrollment edits do not relabel saved attendance. Legacy attendance rows with unknown placement remain unassigned.

## Individual Student Progress (Phase 5A)

The **Student Progress** navigation item opens an individual history view. Search for an active saved student by name, student ID, or roll number, then optionally filter the results by academic year and subject. The page shows only the student's name, student ID, roll number, and class/section; it does not request guardian or admission details. Examination rows use their database exam IDs, so similarly named examinations remain distinct. Results are ordered by academic-year start and exam date, with creation date/ID as a stable fallback.

Every saved mark is normalized to a percentage using that examination subject's own maximum. The overall exam percentage is the mean of valid subject percentages; missing marks (`NULL` or no mark row) are omitted and labeled **Missing**, while a saved zero remains a recorded `0 / maximum (0%)`. The subject filter narrows the subject details and trend line; the per-exam overall column remains the full-exam average. The native SVG trend plots overall percentages unless one subject is selected, and leaves missing points out of the line. A single examination receives an explanatory message instead of a misleading trend.

The existing schema has no separate student-to-examination participation table. Therefore, an examination appears in a student's history only when at least one mark row exists for that student in that exam; within those exams, unentered subject marks appear as missing. Exams with no mark row at all cannot be inferred as attended or assigned and are not shown. Legacy exams without an academic year are labeled **Academic year not assigned** and appear only when all years are selected.

The progress data endpoints are `GET /api/student-progress/students?search=...` (privacy-limited selector data) and `GET /api/students/<student_id>/progress?academicYearId=...&subjectId=...`. Both use parameterized queries and validate filters. No chart package, CDN, or schema migration is required.

The database also has initial relational tables for subjects, exams, marks, staff, fees, payments, timetable slots, and announcements. The app manages student, academic-year, class, section, enrollment, marks, academic analytics, and attendance workflows. Fees, staff, timetables, and announcements remain schema foundations without user workflows.

The old command-line analyzer is still available as a separate tool; it does not write to the Markwise database.

## Technology and architecture

- **Frontend:** existing vanilla HTML, CSS, and JavaScript in `outputs/`.
- **Backend:** Flask routes in `app.py`, returning the same HTML assets and JSON APIs.
- **Database:** SQLite through Python's built-in `sqlite3` module; no ORM is required.
- **Schema and migrations:** `schema.sql` plus versioned initialization in `database.py`.
- **Tests:** Python's built-in `unittest` and Flask's test client.

The API uses parameterized SQLite statements. Validation runs on the server even when a browser form also checks the input. The development server binds only to `127.0.0.1`; this phase does not add login or production deployment security.

## Dashboard analytics rules

The dashboard reads the selected academic year's active students and one selected examination. When the examination filter is left at its default, it selects the most recent available examination for that year (including unassigned legacy exams). It does not combine historical exams, which prevents repeated students or marks from inflating results. Analytics use only saved non-null marks between zero and that subject's configured maximum; a missing mark is not treated as zero. Percentages normalize scores by each subject's maximum marks.

- A student's average is the arithmetic mean of their entered valid subject percentages. Their result is Pass when that average meets or exceeds the mean configured passing percentage for those same subjects.
- The class average is the arithmetic mean of the percentages for students with at least one valid mark. Each student has equal weight.
- Subject averages use only students with a saved valid mark for that subject. The dashboard also reports raw high/low marks against the subject's maximum.
- Grades are A (90–100%), B (80–89.99%), C (70–79.99%), D (60–69.99%), E (40–59.99%), and F (below 40%). Grade bands are independent from each examination's pass threshold.
- Rank uses unrounded average percentages and competition ranking; tied students share a rank (1, 1, 3).

The charts are rendered with semantic HTML and CSS, so no CDN, chart package, or extra setup is required. Dashboard data is served from `GET /api/dashboard/analytics`; supported query parameters are `academicYearId`, `classId`, `sectionId`, `examId`, and `search`.

## Attendance rules and API

Present and Late count as attended; Absent counts as missed. Excused and unrecorded sessions do not contribute to the denominator. Attendance percentage is `(Present + Late) / (Present + Late + Absent) × 100`. A student or class with no countable sessions is shown as **Not enough data**. The app does not infer attendance on weekends, holidays, or school closure days. The threshold defaults to 75% and can be changed in the Attendance view or with the API's `threshold` parameter.

The Attendance page loads a selected roster with `GET /api/attendance/roster`, saves or edits a batch with `PUT /api/attendance/records`, and displays class summaries with `GET /api/attendance/summary`. Individual history is available through `GET /api/students/<student_id>/attendance`. Roster, date, session, status, enrollment, and threshold values are validated by Flask; SQL uses bound parameters and saves are transactional. The API is for the existing local-only app. There is no sign-in or role-based authorization, so do not expose it to a network or treat it as an authenticated multi-user system.

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

The tests cover student create/list/detail/update/archive, duplicate IDs and roll numbers, input validation, SQL-safe search, marks validation and calculations, persistence across app restarts, database initialization and migration, dashboard empty states and filters, configured pass boundaries, missing marks, different subject maximums, historical exams, tied ranks, analytics privacy, individual progress history with multiple exams, different maxima, subject/year filters, chronological ordering, zero versus missing marks, attendance statuses, edits and repeat saves, validation, enrollment filters, percentage rules, threshold alerts, migration safety, persistence, privacy, empty data, serving the existing page assets, and safe database errors.

## Existing Python command-line analyzer

The original beginner-friendly command-line tool remains available and still uses only Python's standard library:

```powershell
python .\outputs\student_marks_analyzer.py
```

It collects marks interactively and can save a CSV in the current folder. It is independent of the Flask site and SQLite database in this phase.

## Database relationships and migrations

`students` belong to a school. `classes` belong to the school and `sections` belong to classes. `student_enrollments` connects a student to a class, optional section, roll number, status, and academic year. A student can have one enrollment per year, retaining prior-year placement when a new year's assignment is added. Each admission number is unique per school; student IDs remain unique. `exams` connect to `subjects` through `exam_subjects`, where maximum and passing marks are stored. `marks` connect one student to one exam subject; a `NULL` score means not entered, while `0` is a real mark.

Migration history is stored in `schema_migrations` and mirrored in SQLite's `PRAGMA user_version`. Migration 4 is defined in `migrations/0004_student_profiles_enrollments.sql`. It adds admission number, guardian name/contact, admission date, and enrollment status; gives existing students `LEGACY-<student row id>` admission numbers; adds uniqueness and enrollment indexes; and rejects enrollment sections that do not belong to the selected class. Migration 5 adds nullable academic-year, class, and section references to attendance, checks placement consistency, and adds a summary lookup index. Legacy attendance rows are left unassigned rather than receiving guessed placement. Existing student IDs, marks, exams, subjects, and attendance statuses are preserved. Initialization creates a current year labeled for the current calendar year and snapshots existing class/section assignments into enrollment rows. Before migrating an important database, make a separate copy and verify it with `PRAGMA integrity_check`.

Future school tables connect staff to school records, payments to student fees, timetable slots to sections/subjects/staff, and announcements to a school or section. These modules are schema foundations only; they have no user interface or operations yet.

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
