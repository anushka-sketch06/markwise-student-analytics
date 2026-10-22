-- Migration 5: snapshot the academic placement associated with an attendance row.
-- database.py conditionally adds these nullable foreign-key columns so fresh
-- databases (whose schema.sql already includes them) and existing databases
-- both migrate safely. Legacy rows are deliberately left unassigned.
CREATE INDEX IF NOT EXISTS attendance_scope_date
    ON attendance(academic_year_id, class_id, section_id, attendance_date);

CREATE TRIGGER attendance_placement_matches_class_insert
BEFORE INSERT ON attendance
WHEN (NEW.academic_year_id IS NULL AND (NEW.class_id IS NOT NULL OR NEW.section_id IS NOT NULL))
  OR (NEW.class_id IS NULL AND (NEW.academic_year_id IS NOT NULL OR NEW.section_id IS NOT NULL))
  OR (NEW.section_id IS NOT NULL AND NEW.class_id IS NULL)
  OR (NEW.class_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM classes AS cl
      WHERE cl.id = NEW.class_id
        AND EXISTS (SELECT 1 FROM academic_years AS ay WHERE ay.id = NEW.academic_year_id AND ay.school_id = cl.school_id)
  ))
  OR (NEW.section_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM sections AS se WHERE se.id = NEW.section_id AND se.class_id = NEW.class_id
  ))
BEGIN
    SELECT RAISE(ABORT, 'attendance placement must match its academic year, class, and section');
END;

CREATE TRIGGER attendance_placement_matches_class_update
BEFORE UPDATE OF academic_year_id, class_id, section_id ON attendance
WHEN (NEW.academic_year_id IS NULL AND (NEW.class_id IS NOT NULL OR NEW.section_id IS NOT NULL))
  OR (NEW.class_id IS NULL AND (NEW.academic_year_id IS NOT NULL OR NEW.section_id IS NOT NULL))
  OR (NEW.section_id IS NOT NULL AND NEW.class_id IS NULL)
  OR (NEW.class_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM classes AS cl
      WHERE cl.id = NEW.class_id
        AND EXISTS (SELECT 1 FROM academic_years AS ay WHERE ay.id = NEW.academic_year_id AND ay.school_id = cl.school_id)
  ))
  OR (NEW.section_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM sections AS se WHERE se.id = NEW.section_id AND se.class_id = NEW.class_id
  ))
BEGIN
    SELECT RAISE(ABORT, 'attendance placement must match its academic year, class, and section');
END;
