from src.release_notes import normalize_heading, summarize_changes


def test_normalize_heading():
    assert normalize_heading("## fixed bugs") == "Fixed Bugs"


def test_normalize_heading_with_extra_spaces():
    assert normalize_heading("##   fixed   ") == "Fixed"


def test_summarize_changes_counts_bullets_by_section():
    notes = """
## Added
- TUI reload command
- Event matrix

## Fixed
- Redraw flooding
"""
    assert summarize_changes(notes) == {"Added": 2, "Fixed": 1}


def test_summarize_changes_empty_string_returns_empty_dict():
    assert summarize_changes("") == {}


def test_summarize_changes_ignores_bullets_before_any_heading():
    notes = """
- Bullet before heading
- Another bullet

## Added
- Valid bullet
"""
    assert summarize_changes(notes) == {"Added": 1}


def test_summarize_changes_counts_bullets_across_repeated_headings():
    notes = """
## Added
- First item

## Fixed
- One fix

## Added
- Second item
- Third item
"""
    assert summarize_changes(notes) == {"Added": 3, "Fixed": 1}