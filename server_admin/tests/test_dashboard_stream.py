import threading
import time

import httpx
import uvicorn
from fastapi.testclient import TestClient

from server_admin.main import app

# NOTE ON TEST DESIGN: the SSE route's generator loop is intentionally
# infinite (it only exits on real client disconnect). Starlette's
# `TestClient` cannot exercise that: its transport (`_TestClientTransport`)
# calls the ASGI app via a blocking `portal.call(...)` that only returns
# once the app coroutine *completes* and the whole response body has been
# buffered into memory -- there is no incremental client-side read while
# the app is still running. Against an infinite generator this means
# `client.stream(...)` never even yields a response object; it hangs
# forever (verified empirically -- a finite two-event generator streams
# fine under TestClient, an infinite one hangs indefinitely). This is a
# known TestClient limitation, not a bug in the route.
#
# So this test spins up the real app under uvicorn in a background thread
# and drives it with a real httpx client, which streams incrementally over
# a real socket the way a browser's EventSource would. Breaking out of the
# `with client.stream(...)` block below closes the connection for real,
# which is what lets the route's `request.is_disconnected()` check return
# True and the generator exit cleanly.


def test_dashboard_stream_emits_snapshot_then_tick():
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        while not server.started:
            time.sleep(0.05)
        port = server.servers[0].sockets[0].getsockname()[1]

        with httpx.Client(timeout=30) as client:
            with client.stream("GET", f"http://127.0.0.1:{port}/dashboard/stream") as response:
                assert response.status_code == 200
                assert response.headers["cache-control"] == "no-cache"
                assert response.headers["x-accel-buffering"] == "no"

                events = []
                for line in response.iter_lines():
                    if line.startswith("event:"):
                        events.append(line)
                    if len(events) >= 2:
                        break

        assert events[0] == "event: snapshot"
        assert events[1] == "event: tick"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_dashboard_telemetry_and_network_routes_removed():
    with TestClient(app) as client:
        assert client.get("/dashboard/telemetry").status_code == 404
        assert client.get("/dashboard/network").status_code == 404
