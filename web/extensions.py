"""Flask-SocketIO singleton — initialised via socketio.init_app(app) in create_app()."""
import os

os.environ.setdefault("EVENTLET_TESTS", "1")


def _async_mode() -> str:
    try:
        import eventlet  # noqa: F401
        return "eventlet"
    except ImportError:
        return "threading"


from flask_socketio import SocketIO  # noqa: E402

socketio = SocketIO(async_mode=_async_mode())
