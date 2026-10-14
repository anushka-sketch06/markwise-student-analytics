"""Analyze student marks across subjects using only Python's standard library."""

import csv


DEFAULT_PASS_MARK = 40


def get_number(prompt, minimum, maximum=None, allow_blank=False, default=None,
               whole_number=False):
    """Read a number, repeating the question until it is in the allowed range."""
    while True:
        answer = input(prompt).strip()
        if allow_blank and answer == "":
            return default
        try:
            number = float(answer)
            if number.is_integer():
                number = int(number)
            elif whole_number:
                print("Please enter a whole number.")
                continue
            if number >= minimum and (maximum is None or number <= maximum):
                return number
            if maximum is None:
                print(f"Enter a number that is at least {minimum}.")
            else:
                print(f"Enter a number from {minimum} to {maximum}.")
        except ValueError:
            print("Please enter a number.")


def get_non_empty_text(prompt, existing=None):
    """Read non-empty text, optionally preventing a repeated name."""
    while True:
        answer = input(prompt).strip()
        if not answer:
            print("This field cannot be empty.")
        elif existing and answer.casefold() in {item.casefold() for item in existing}:
            print("Please use a name that has not already been entered.")
        else:
            return answer


def collect_subjects():
    """Ask for the number and names of subjects, then return their names."""
    count = get_number("How many subjects? ", 1, whole_number=True)
    subjects = []
    for number in range(1, count + 1):
        subject = get_non_empty_text(f"Enter the name of subject {number}: ", subjects)
        subjects.append(subject)
    return subjects


def collect_students(subjects):
    """Ask for each student's name and marks, returning students sorted by average."""
    count = get_number("How many students? ", 1, whole_number=True)
    students = []
    for number in range(1, count + 1):
        name = get_non_empty_text(f"Enter the name of student {number}: ",
                                  [student["name"] for student in students])
        marks = {}
        for subject in subjects:
            marks[subject] = get_number(f"  Marks for {subject} (0-100): ", 0, 100)
        average = sum(marks.values()) / len(subjects)
        students.append({"name": name, "marks": marks, "average": average})
    return sorted(students, key=lambda student: student["average"], reverse=True)


def show_results(students, subjects, pass_mark):
    """Print the class summary and students in descending average order."""
    class_average = sum(student["average"] for student in students) / len(students)
    highest_average = students[0]["average"]
    highest_scorers = [student["name"] for student in students
                       if student["average"] == highest_average]

    print("\n--- Class Summary ---")
    print(f"Students: {len(students)}")
    print(f"Subjects: {', '.join(subjects)}")
    print(f"Pass mark: {pass_mark:g} / 100")
    print(f"Class average: {class_average:.2f}")
    print(f"Highest average: {highest_average:.2f}")
    print(f"Highest scorer(s): {', '.join(highest_scorers)}")

    print("\n--- Results (highest average first) ---")
    for student in students:
        subject_scores = ", ".join(
            f"{subject}: {student['marks'][subject]:g}" for subject in subjects
        )
        status = "Pass" if student["average"] >= pass_mark else "Fail"
        print(f"{student['name']}: {subject_scores}; average {student['average']:.2f} - {status}")


def save_csv(students, subjects, pass_mark, filename="student_marks_results.csv"):
    """Save subject marks, averages, and pass/fail results in a spreadsheet-friendly file."""
    with open(filename, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["Student", *subjects, "Average", "Result"])
        for student in students:
            status = "Pass" if student["average"] >= pass_mark else "Fail"
            writer.writerow([
                student["name"],
                *(student["marks"][subject] for subject in subjects),
                f"{student['average']:.2f}",
                status,
            ])
    print(f"Results saved to {filename}")


def main():
    """Run the analyzer from data entry through optional CSV export."""
    pass_mark = get_number(
        f"Pass mark from 0 to 100 (press Enter for {DEFAULT_PASS_MARK}): ",
        0, 100, allow_blank=True, default=DEFAULT_PASS_MARK,
    )
    subjects = collect_subjects()
    students = collect_students(subjects)
    show_results(students, subjects, pass_mark)

    answer = input("\nSave these results to CSV? (y/n): ").strip().casefold()
    if answer in ("y", "yes"):
        save_csv(students, subjects, pass_mark)


if __name__ == "__main__":
    main()
