from focused_crawler.classifier import COURSE, FACULTY, LAB, OTHER
from focused_crawler.extractor import extract_fields
from focused_crawler.parser import parse_html
from tests.fakes import fixture_html


def page(name, url="https://www.cc.gatech.edu/x"):
    return parse_html(fixture_html(name), url)


def test_course_fields():
    fields = extract_fields(page("course.html"), COURSE)
    assert fields["course_code"] == "CS 6675"
    assert "CS 6250" in fields["course_codes"]
    assert fields["title"] == "CS 6675 Advanced Internet Computing Systems"
    assert fields["instructor"] == "Ling Liu"
    assert fields["credit_hours"] == "3"


def test_faculty_fields():
    fields = extract_fields(page("faculty.html"), FACULTY)
    assert fields["name"] == "Ling Liu"
    assert fields["email"] == "lingliu@cc.gatech.edu"
    assert fields["position"] == "Professor"
    assert fields["department"].startswith("School of Computer Science")
    assert fields["research_keywords"] == [
        "Distributed systems",
        "Big data systems",
        "Machine learning security",
    ]


def test_lab_fields():
    fields = extract_fields(page("lab.html"), LAB)
    assert fields["name"] == "Distributed Data Intensive Systems Lab"
    assert fields["pi"] == "Ling Liu"
    assert fields["department"].startswith("School of Computer Science")


def test_other_has_no_builtin_fields():
    assert extract_fields(page("news.html"), OTHER) == {}


def test_custom_selectors_text_and_attribute():
    selectors = {
        "*": {"canonical": "link[rel=canonical]@href"},
        FACULTY: {"photo": "img.profile-photo@src", "name": "h1"},
    }
    fields = extract_fields(page("faculty.html"), FACULTY, selectors)
    assert fields["photo"] == "/img/ling-liu.jpg"
    assert "canonical" not in fields  # no match -> not set
    course = extract_fields(page("course.html"), COURSE, selectors)
    assert course["canonical"] == "https://www.cc.gatech.edu/courses/cs6675"


def test_selector_with_multiple_matches_returns_list():
    fields = extract_fields(page("faculty.html"), OTHER, {OTHER: {"items": "li"}})
    assert fields["items"] == [
        "Distributed systems",
        "Big data systems",
        "Machine learning security",
    ]
