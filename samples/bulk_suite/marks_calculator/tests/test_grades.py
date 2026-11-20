"""Existing tests. They only cover normal inputs, so they pass despite the bugs."""

from grades import (
    add_marks,
    calculate_percentage,
    class_average,
    get_grade,
    parse_marks,
    passed_count,
    report_card,
)


def test_grade_for_normal_marks():
    assert get_grade(95) == "A"
    assert get_grade(80) == "B"
    assert get_grade(65) == "C"
    assert get_grade(45) == "D"
    assert get_grade(10) == "F"


def test_percentage():
    assert calculate_percentage(45, 50) == 90


def test_class_average():
    assert class_average([50, 70, 90]) == 70


def test_passed_count():
    assert passed_count([30, 55, 80]) == 2


def test_parse_marks():
    assert parse_marks("45, 78,90") == [45, 78, 90]


def test_add_marks_first_entry():
    assert add_marks("Asha", 80) == {"Asha": [80]}


def test_report_card():
    assert report_card("Asha", {"Maths": 90, "Science": 80}) == "Asha: average 85.0, grade B"
