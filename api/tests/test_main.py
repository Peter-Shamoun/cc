"""``python -m api`` launches uvicorn without the Server header (the middleware can't strip uvicorn's own)."""
from unittest import mock

import api.__main__ as entry


def test_launcher_disables_server_header_and_access_log():
    with mock.patch.object(entry.uvicorn, "run") as run:
        entry.main(["--port", "8199"])
    kwargs = run.call_args.kwargs
    assert run.call_args.args[0] == "api.app:app"
    assert kwargs["server_header"] is False and kwargs["access_log"] is False and kwargs["port"] == 8199
    assert kwargs["host"] == "127.0.0.1"
