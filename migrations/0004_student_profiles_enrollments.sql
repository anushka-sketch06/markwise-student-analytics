-- Migration 4: extend student profiles and protect academic-year enrollments.
-- Existing students get a stable legacy admission number based on their row ID.
-- No marks, exams, subjects, student IDs, or current class links are changed.
ALTER TABLE students ADD COLUMN admission_number TEXT;
ALTER TABLE students ADD COLUMN guardian_name TEXT;
ALTER TABLE students ADD COLUMN guardian_contact TEXT;
ALTER TABLE students ADD COLUMN admission_date TEXT;
ALTER TABLE students ADD COLUMN enrollment_status TEXT NOT NULL DEFAULT 'active'
    CHECK (enrollment_status IN ('active', 'completed', 'transferred', 'archived'));

UPDATE students
SET admission_number = 'LEGACY-' || id,
    enrollment_status = CASE WHEN is_active = 1 THEN 'active' ELSE 'archived' END;

CREATE UNIQUE INDEX unique_student_admission_number
    ON students(school_id, admission_number COLLATE NOCASE)
    WHERE admission_number IS NOT NULL;
CREATE INDEX student_enrollments_year_class
    ON student_enrollments(academic_year_id, class_id, section_id, status);

CREATE TRIGGER enrollment_section_matches_class_on_insert
BEFORE INSERT ON student_enrollments
WHEN NEW.section_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sections WHERE id = NEW.section_id AND class_id = NEW.class_id
)
BEGIN
    SELECT RAISE(ABORT, 'enrollment section must belong to selected class');
END;

CREATE TRIGGER enrollment_section_matches_class_on_update
BEFORE UPDATE OF section_id, class_id ON student_enrollments
WHEN NEW.section_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sections WHERE id = NEW.section_id AND class_id = NEW.class_id
)
BEGIN
    SELECT RAISE(ABORT, 'enrollment section must belong to selected class');
END;
