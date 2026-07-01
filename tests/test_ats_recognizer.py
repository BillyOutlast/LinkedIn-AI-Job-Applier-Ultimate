from src.llm.ats_recognizer import recognize


def test_recognize_workday_by_hostname():
    match = recognize("https://uhaul.wd1.myworkdayjobs.com/en-US/UhaulJobs/job/123")
    assert match is not None
    assert match.name == "workday"
    assert match.confidence == 1.0


def test_recognize_unknown_returns_none():
    assert recognize("https://example.com/apply/123") is None


def test_recognize_fingerprint_failure_returns_none():
    """Malformed URL still returns None instead of raising."""
    assert recognize("not-a-url") is None


def test_recognize_empty_url_returns_none():
    assert recognize("") is None


def test_recognize_case_insensitive():
    match = recognize("https://ACME.WD5.MYWORKDAYJOBS.COM/job/1")
    assert match is not None
    assert match.name == "workday"


def test_recognize_greenhouse_by_hostname():
    match = recognize("https://boards.greenhouse.io/stripe/jobs/12345")
    assert match is not None
    assert match.name == "greenhouse"
    assert match.confidence == 1.0


def test_recognize_greenhouse_job_boards_subdomain():
    match = recognize("https://job-boards.greenhouse.io/anthropic/jobs/5023394008")
    assert match is not None
    assert match.name == "greenhouse"


def test_recognize_taleo_by_hostname():
    match = recognize("https://company.taleo.net/careersection/jobdetail.ftl?job=12345")
    assert match is not None
    assert match.name == "taleo"
    assert match.confidence == 1.0


def test_recognize_taleo_oracle_cloud_by_marker():
    match = recognize(
        "https://acme.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/123"
    )
    assert match is not None
    assert match.name == "taleo"
