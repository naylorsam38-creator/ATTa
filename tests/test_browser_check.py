"""Stage 6 (real Chromium) against pages shaped like colanode (drawn late) and plane (skin link gone in
the browser). Needs Playwright + Chromium; skipped where they aren't installed."""
import json
import pytest

pytest.importorskip("playwright.sync_api")
import system_watcher as sw
import fakes



@pytest.fixture(scope="module")
def proxy():
    return fakes.skin_proxy(delay_ms=3000)


def check(url, wait):
    sw.CONTENT_WAIT = wait
    return sw.browser_check(url)


def test_ready_page_passes(proxy):
    assert check(proxy + "/ok", 5)["code"] == "CLEAN_OK"


def test_page_drawn_by_its_javascript_after_load_passes(proxy):
    # colanode: blank at "load", draws 3 s later. v109 failed this EMPTY_BODY.
    r = check(proxy + "/late", 15)
    assert r["status"] == "OK", r
    assert json.loads(r["detail"])["drawn_after_s"] >= 2


def test_page_drawn_later_than_the_wait_still_fails(proxy):
    r = check(proxy + "/late", 1)
    assert r["code"] == "EMPTY_BODY" and json.loads(r["detail"])["waited_s"] == 1


def test_blank_page_still_fails_empty_body(proxy):
    # System-Watcher-Revision-1 T13: an empty <body> must fail EMPTY_BODY.
    assert check(proxy + "/blank", 2)["code"] == "EMPTY_BODY"


def test_spinner_alone_is_not_drawn(proxy):
    assert check(proxy + "/spinner", 2)["code"] == "EMPTY_BODY"


def test_app_painted_on_a_canvas_counts_as_drawn(proxy):
    assert check(proxy + "/canvas", 5)["code"] == "CLEAN_OK"


def test_skin_link_removed_by_the_pages_own_script_is_recorded(proxy):
    r = check(proxy + "/spa", 5)
    assert r["code"] == "BROWSER_SKIN_LINK_COUNT"
    ev = json.loads(r["detail"])
    assert ev["count"] == 0
    assert ev["served"]["has_skin_link"] is True and ev["served"]["has_hook"] is True
    assert ev["removed_by_page"] and ev["removed_by_page"][0]["ref"].startswith("/_cs/")
    assert ev["observed"] == ["REMOVED_BY_PAGE_SCRIPT"]
    assert "<head" in ev["head"] and ev["final_url"].endswith("/spa")


def test_browser_that_moved_to_a_page_served_without_the_skin_is_recorded(proxy):
    r = check(proxy + "/redir", 5)
    assert r["code"] == "BROWSER_SKIN_LINK_COUNT"
    ev = json.loads(r["detail"])
    assert ev["final_url"].endswith("/login")
    assert any(u.endswith("/login") for u in ev["navigations"])
    assert ev["served"]["url"].endswith("/login") and ev["served"]["has_skin_link"] is False
    assert "SERVED_WITHOUT_SKIN" in ev["observed"] and "NAVIGATED_TO:/login" in ev["observed"]
