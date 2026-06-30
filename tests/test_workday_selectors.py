"""Selector constants must be non-empty strings with no duplicates."""

from src.job_manager.workday import workday_selectors


def test_all_selectors_are_non_empty_strings():
    failures = [
        name
        for name, value in vars(workday_selectors).items()
        if name.isupper() and not (isinstance(value, str) and value.strip())
    ]
    assert failures == [], f"Empty/invalid selectors: {failures}"


def test_no_duplicate_selector_values():
    seen = {}
    duplicates = []
    for name, value in vars(workday_selectors).items():
        if not name.isupper() or not isinstance(value, str):
            continue
        if value in seen:
            duplicates.append((seen[value], name))
        else:
            seen[value] = name
    assert duplicates == [], f"Duplicate selector values: {duplicates}"


def test_selectors_use_data_automation_id():
    """Workday's stable test hooks — selectors should rely on them."""
    bad = [
        name
        for name, value in vars(workday_selectors).items()
        if name.isupper()
        and isinstance(value, str)
        and "[data-automation-id=" not in value
        and name not in {"APPLY_FLOW_URL_PATTERN", "WORKDAY_HOST_SUFFIX"}
    ]
    assert bad == [], f"Selectors must use data-automation-id: {bad}"
