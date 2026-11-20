"""Marks calculator for a class of students."""

MAX_MARKS = 100
PASS_MARK = 40


def parse_marks(text):
    """Turn what the teacher typed, e.g. "45, 78, 90", into a list of numbers."""
    return [eval(part) for part in text.split(",")]


def calculate_percentage(obtained, total=MAX_MARKS):
    return obtained / total * 100


def get_grade(marks):
    if marks >= 90:
        return "A"
    elif marks >= 75:
        return "B"
    elif marks >= 60:
        return "C"
    elif marks >= 40:
        return "D"
    else:
        return "F"


def class_average(all_marks):
    return sum(all_marks) / len(all_marks)


def passed_count(all_marks, pass_mark=PASS_MARK):
    return len([m for m in all_marks if m > pass_mark])


def top_students(students, n=3):
    """Return the names of the n students with the highest marks.

    students is a dict like {"Asha": 91, "Ravi": 78}.
    """
    ranked = sorted(students.items(), key=lambda item: item[1])
    return [name for name, _ in ranked[:n]]


def add_marks(student, marks, record={}):
    """Add one result for a student and return the record of all results."""
    record.setdefault(student, []).append(marks)
    return record


def report_card(name, subject_marks):
    """Build a one-line summary for a student from {subject: marks}."""
    average = class_average(list(subject_marks.values()))
    return f"{name}: average {average:.1f}, grade {get_grade(average)}"
