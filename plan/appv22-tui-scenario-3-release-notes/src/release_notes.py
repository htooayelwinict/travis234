import re


def normalize_heading(line: str) -> str:
    return line.strip().lstrip("#").strip().title()


def summarize_changes(markdown: str) -> dict[str, int]:
    """Count bullet items under each ## heading, aggregating repeated headings."""
    lines = markdown.splitlines()
    sections = {}
    current_section = None
    for line in lines:
        heading_match = re.match(r"^##\s+(.+)$", line, re.IGNORECASE)
        if heading_match:
            current_section = normalize_heading(heading_match.group(1))
            sections[current_section] = sections.get(current_section, 0)
        elif current_section and line.strip().startswith("-"):
            sections[current_section] += 1
    return sections