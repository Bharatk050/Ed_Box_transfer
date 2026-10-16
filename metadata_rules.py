"""Conservative catalog rules grounded in current-video evidence only."""
import re


def filename_grades(filename, choices):
    """Read explicit grade labels, never ages, resolutions, or example filenames."""
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', filename)
    text = text.replace('_', ' ').lower()
    number = r'(?:1[0-2]|[1-9])'
    values = []
    # Boundaries permit compact Grade3 / 3rdGrade but reject room/grade 30.
    patterns = [rf'\bgrades?\s*({number})(?!\d)(?:st|nd|rd|th)?\b',
                rf'(?<!\w)({number})(?:st|nd|rd|th)?\s*grade\b']
    for pattern in patterns:
        values.extend(match.group(1) for match in re.finditer(pattern, text))
    for match in re.finditer(
            rf'\bgrades?\s*({number})(?:st|nd|rd|th)?\s*(?:-|through|to)\s*({number})(?!\d)\b', text):
        first, last = map(int, match.groups())
        if first <= last:
            values.extend(str(value) for value in range(first, last + 1))
    for pattern, value in [(r'\b(?:pre[\s-]*k|prekindergarten|preschool)\b', 'Pre-K'),
                           (r'\bkindergarten\b|\bgrade\s*k\b|\bk\s*grade\b', 'K')]:
        if re.search(pattern, text):
            values.append(value)
    return [value for value in dict.fromkeys(values) if value in choices]
