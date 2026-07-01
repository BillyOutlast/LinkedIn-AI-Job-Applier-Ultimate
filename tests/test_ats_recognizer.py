from src.llm.ats_recognizer import recognize


def test_recognize_workday_by_hostname():
    match = recognize("https://uhaul.wd1.myworkdayjobs.com/en-US/UhaulJobs/job/123")
    assert match is not None
    assert match.name == "workday"
    assert match.confidence == 1.0


def test_recognize_unknown_returns_none():
    assert recognize("https://boards.greenhouse.io/apply/123") is None


def test_recognize_fingerprint_failure_returns_none():
    """Malformed URL still returns None instead of raising."""
    assert recognize("not-a-url") is None


def test_recognize_empty_url_returns_none():
    assert recognize("") is None


def test_recognize_case_insensitive():
    match = recognize("https://ACME.WD5.MYWORKDAYJOBS.COM/job/1")
    assert match is not None
    assert match.name == "workday"
