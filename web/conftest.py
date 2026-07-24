"""Project-level pytest conftest for the web/ package.

Attempts to make Flask importable even when pytest is invoked with the system
Python rather than the activated virtual environment.

If Flask still cannot be imported after the path fix, the tests that require
it (test_routes.py, test_auth.py) will be collected but skipped automatically
via the flask_or_skip fixture defined below.

Setup (first time):
    cd web && pip install -r requirements.txt      # inside the venv
    Then: python -m pytest tests/ -v
"""
import sys
import os
import glob as _glob


def _try_add_venv() -> bool:
    """Add the venv's site-packages to sys.path.  Returns True if successful."""
    try:
        import flask  # noqa: F401
        return True  # already importable
    except ImportError:
        pass

    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, ".."))

    # Windows: .venv\Lib\site-packages
    win_sp = os.path.join(root, ".venv", "Lib", "site-packages")
    if os.path.isdir(win_sp):
        sys.path.insert(0, win_sp)
        try:
            import flask  # noqa: F401
            return True
        except ImportError:
            pass

    # Linux / macOS: .venv/lib/pythonX.Y/site-packages
    for sp in sorted(_glob.glob(
        os.path.join(root, ".venv", "lib", "python*", "site-packages")
    ), reverse=True):
        if os.path.isdir(sp):
            sys.path.insert(0, sp)
            try:
                import flask  # noqa: F401
                return True
            except ImportError:
                continue

    return False


FLASK_AVAILABLE = _try_add_venv()


def pytest_configure(config):
    """Register custom markers used in this project."""
    config.addinivalue_line(
        "markers",
        "requires_flask: test requires Flask — skipped if not installed",
    )


def pytest_collection_modifyitems(items):
    """Auto-skip any test in a file that imports Flask when Flask is absent."""
    if FLASK_AVAILABLE:
        return
    import pytest
    skip = pytest.mark.skip(
        reason="Flask not installed — run: pip install -r web/requirements.txt"
    )
    flask_test_files = {"test_routes.py", "test_auth.py"}
    for item in items:
        if os.path.basename(item.fspath) in flask_test_files:
            item.add_marker(skip)
