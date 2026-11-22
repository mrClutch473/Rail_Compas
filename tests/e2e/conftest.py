import os

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

# Playwright's sync API keeps an event loop in the main thread; Django would refuse DB calls.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


@pytest.fixture(scope="session")
def browser():
    with sync_api.sync_playwright() as pw:
        errors = []
        # bundled Chromium does not start on some Windows builds (D-21), so fall back to Chrome/Edge
        for channel in (None, "chrome", "msedge"):
            try:
                browser = pw.chromium.launch(channel=channel)
            except sync_api.Error as exc:
                errors.append(f"{channel}: {exc.message.splitlines()[0]}")
                continue
            yield browser
            browser.close()
            return
        pytest.skip("no browser: " + "; ".join(errors))


@pytest.fixture
def page(browser, live_server):
    context = browser.new_context(base_url=live_server.url, locale="ru-RU")
    page = context.new_page()
    page.problems = []
    page.on("pageerror", lambda exc: page.problems.append(str(exc)))
    page.on("console", lambda msg: msg.type == "error" and page.problems.append(msg.text))
    yield page
    context.close()
    assert page.problems == []
