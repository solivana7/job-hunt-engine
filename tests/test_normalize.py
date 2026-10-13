from engine.aggregator.normalize import (
    JobPosting,
    infer_remote,
    parse_salary,
    strip_html,
)


def test_parse_salary_full_range():
    assert parse_salary("Compensation: $225,000 - $300,000 plus bonus") == (225000, 300000)


def test_parse_salary_k_format():
    assert parse_salary("$176-336k base") == (176000, 336000)


def test_parse_salary_single_number_is_not_a_range():
    assert parse_salary("salary up to $250,000") == (None, None)


def test_parse_salary_none():
    assert parse_salary("no comp listed") == (None, None)


def test_infer_remote():
    assert infer_remote("Remote", "") is True
    assert infer_remote("New York, NY (hybrid)", "") is False
    assert infer_remote("San Francisco, CA", "this is a fully remote position") is True
    assert infer_remote("Austin, TX", "great team") is None


def test_strip_html():
    assert strip_html("<p>Hello&nbsp; <b>world</b></p>") == "Hello world"


def test_content_hash_stable_case_insensitive():
    a = JobPosting("s", "1", "Acme", "VP Data", None, None, None, None, "u", "desc")
    b = JobPosting("s", "1", "acme", "vp data", None, None, None, None, "u", "desc")
    assert a.content_hash() == b.content_hash()
