"""Start the explicit Playwright app with a handle for graceful teardown."""

import os

import uvicorn


if os.environ.get("ETSY_EMPLOYEE_TEST_MODE") != "1":
    raise RuntimeError("The Playwright server requires explicit test mode.")

from tests.e2e_app import app


config = uvicorn.Config(app, host="127.0.0.1", port=58765, log_level="info")
server = uvicorn.Server(config)
app.state.e2e_server = server
server.run()
