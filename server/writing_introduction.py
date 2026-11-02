"""Locate a saved, unnumbered lead without adding a synthetic chapter."""
from .writing_images import paragraphs


def introduction_end(section):
    count = section.get('introductionParagraphCount')
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        return 0
    content = section.get('content', '')
    parts = paragraphs(content)
    # There must still be prose belonging to the first numbered chapter.
    if count >= len(parts):
        return 0
    end = parts[count - 1]['end']
    # The paragraph separator starts at LF, leaving the CR of a Windows
    # newline in the span. Word's line renderer consumes CRLF together.
    return end - 1 if end and content[end - 1] == '\r' else end
