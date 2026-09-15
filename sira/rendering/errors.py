"""Exception raised by the resume renderers."""


class RenderError(Exception):
    """A PDF or DOCX could not be written; the message names the format and cause."""
