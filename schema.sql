-- Version 1 lays out the shared school records used by current and future modules.
-- Student management and marks are active in Phase 2; the remaining tables are foundations.
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS schools (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS academic_years (
    id INTEGER PRIMARY KEY,
    school_id INTEGER NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    starts_on TEXT,
    ends_on TEXT,
    is_current INTEGER NOT NULL DEFAULT 0 CHECK (is_current IN (0, 1)),
    UNIQUE (school_id, label),
    CHECK (starts_on IS NULL OR ends_on IS NULL OR starts_on <= ends_on)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_current_year_per_school
    ON academic_years(school_id) WHERE is_current = 1;

CREATE TABLE IF NOT EXISTS classes (
    id INTEGER PRIMARY KEY,
    school_id INTEGER NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    name TEXT NOT NULL COLLATE NOCASE,
    UNIQUE (school_id, name)
);

CREATE TABLE IF NOT EXISTS sections (
    id INTEGER PRIMARY KEY,
    class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    name TEXT NOT NULL COLLATE NOCASE,
    UNIQUE (class_id, name)
);

CREATE TABLE IF NOT EXISTS student_enrollments (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    academic_year_id INTEGER NOT NULL REFERENCES academic_years(id) ON DELETE RESTRICT,
    class_id INTEGER NOT NULL REFERENCES classes(id) ON DELETE RESTRICT,
    section_id INTEGER REFERENCES sections(id) ON DELETE RESTRICT,
    roll_number TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'completed', 'transferred')),
    enrolled_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (student_id, academic_year_id),
    CHECK (section_id IS NULL OR class_id IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS unique_enrollment_roll_per_section
    ON student_enrollments(academic_year_id, class_id, ifnull(section_id, 0), roll_number COLLATE NOCASE)
    WHERE roll_number IS NOT NULL AND status = 'active';

CREATE TABLE IF NOT EXISTS subjects (
    id INTEGER PRIMARY KEY,
    school_id INTEGER NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    name TEXT NOT NULL COLLATE NOCASE,
    UNIQUE (school_id, name)
);

CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY,
    school_id INTEGER NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    student_code TEXT NOT NULL COLLATE NOCASE UNIQUE,
    name TEXT NOT NULL,
    roll_number TEXT,
    class_id INTEGER REFERENCES classes(id) ON DELETE SET NULL,
    section_id INTEGER REFERENCES sections(id) ON DELETE SET NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (length(trim(student_code)) > 0),
    CHECK (length(trim(name)) > 0),
    CHECK (section_id IS NULL OR class_id IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS unique_active_roll_per_group
    ON students(school_id, ifnull(class_id, 0), ifnull(section_id, 0), roll_number COLLATE NOCASE)
    WHERE roll_number IS NOT NULL AND is_active = 1;
CREATE INDEX IF NOT EXISTS students_name_search ON students(name COLLATE NOCASE);
CREATE TRIGGER IF NOT EXISTS student_section_matches_class_on_insert
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
END;

CREATE TABLE IF NOT EXISTS exams (
    id INTEGER PRIMARY KEY,
    school_id INTEGER NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    academic_year_id INTEGER REFERENCES academic_years(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    exam_date TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS unique_exam_name_per_year
    ON exams(school_id, ifnull(academic_year_id, 0), name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS exam_subjects (
    id INTEGER PRIMARY KEY,
    exam_id INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE RESTRICT,
    maximum_marks REAL NOT NULL DEFAULT 100 CHECK (maximum_marks > 0),
    passing_marks REAL NOT NULL DEFAULT 40 CHECK (passing_marks >= 0 AND passing_marks <= maximum_marks),
    UNIQUE (exam_id, subject_id)
);

CREATE TABLE IF NOT EXISTS marks (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    exam_subject_id INTEGER NOT NULL REFERENCES exam_subjects(id) ON DELETE CASCADE,
    score REAL CHECK (score IS NULL OR score >= 0),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (student_id, exam_subject_id)
);

CREATE TABLE IF NOT EXISTS staff (
    id INTEGER PRIMARY KEY,
    school_id INTEGER NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    employee_code TEXT NOT NULL COLLATE NOCASE,
    name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (school_id, employee_code)
);

CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    academic_year_id INTEGER REFERENCES academic_years(id) ON DELETE RESTRICT,
    class_id INTEGER REFERENCES classes(id) ON DELETE RESTRICT,
    section_id INTEGER REFERENCES sections(id) ON DELETE RESTRICT,
    attendance_date TEXT NOT NULL,
    session TEXT NOT NULL DEFAULT 'full_day',
    status TEXT NOT NULL CHECK (status IN ('present', 'absent', 'late', 'excused')),
    recorded_by INTEGER REFERENCES staff(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (student_id, attendance_date, session)
);
CREATE INDEX IF NOT EXISTS attendance_scope_date
    ON attendance(academic_year_id, class_id, section_id, attendance_date);

CREATE TABLE IF NOT EXISTS fee_types (
    id INTEGER PRIMARY KEY,
    school_id INTEGER NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    name TEXT NOT NULL COLLATE NOCASE,
    UNIQUE (school_id, name)
);

CREATE TABLE IF NOT EXISTS student_fees (
    id INTEGER PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE RESTRICT,
    fee_type_id INTEGER NOT NULL REFERENCES fee_types(id) ON DELETE RESTRICT,
    academic_year_id INTEGER REFERENCES academic_years(id) ON DELETE RESTRICT,
    amount_due REAL NOT NULL CHECK (amount_due >= 0),
    due_date TEXT,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'paid', 'waived')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY,
    student_fee_id INTEGER NOT NULL REFERENCES student_fees(id) ON DELETE RESTRICT,
    amount_paid REAL NOT NULL CHECK (amount_paid > 0),
    paid_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    payment_method TEXT,
    reference TEXT
);

CREATE TABLE IF NOT EXISTS timetable_slots (
    id INTEGER PRIMARY KEY,
    section_id INTEGER NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE RESTRICT,
    staff_id INTEGER REFERENCES staff(id) ON DELETE SET NULL,
    day_of_week INTEGER NOT NULL CHECK (day_of_week BETWEEN 1 AND 7),
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    room TEXT,
    CHECK (starts_at < ends_at)
);

CREATE TABLE IF NOT EXISTS announcements (
    id INTEGER PRIMARY KEY,
    school_id INTEGER NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    audience TEXT NOT NULL DEFAULT 'school' CHECK (audience IN ('school', 'staff', 'families', 'section')),
    section_id INTEGER REFERENCES sections(id) ON DELETE CASCADE,
    published_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
