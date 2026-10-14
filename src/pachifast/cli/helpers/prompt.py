"""Line editing for interactive prompts."""


def enable_line_editing() -> bool:
    """Load ``readline`` so prompts support cursor keys, Home/End and history.

    Python's ``input()`` (used by ``typer.prompt``) only handles arrow keys when ``readline`` has been imported;
    otherwise the terminal escape sequences end up in the typed text. ``readline`` does not exist on Windows,
    whose console handles cursor keys natively.

    Returns:
        ``True`` if ``readline`` is available.
    """
    try:
        import readline  # noqa: F401
    except ImportError:
        return False
    return True
