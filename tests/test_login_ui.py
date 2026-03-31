"""Playwright UI tests for the AlgoSphere login page."""
import pytest
from playwright.sync_api import sync_playwright, expect

BASE_URL = "http://localhost:3080"


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    ctx = browser.new_context()
    pg = ctx.new_page()
    yield pg
    ctx.close()


def goto_login(page):
    page.goto(f"{BASE_URL}/login")
    page.wait_for_load_state("networkidle")


# ---------------------------------------------------------------------------
# Login page structure
# ---------------------------------------------------------------------------

def test_login_page_loads(page):
    goto_login(page)
    assert page.url == f"{BASE_URL}/login"


def test_login_page_has_algosphere_title(page):
    goto_login(page)
    assert "ALGOSPHERE" in page.locator("body").inner_text()


def test_login_has_email_input(page):
    goto_login(page)
    assert page.locator('input[type="email"]').is_visible()


def test_login_has_password_input(page):
    goto_login(page)
    assert page.locator('input[type="password"]').is_visible()


def test_login_has_sign_in_button(page):
    goto_login(page)
    assert page.locator('button:has-text("SIGN IN")').is_visible()


def test_login_has_google_button(page):
    goto_login(page)
    # Google button renders inside an iframe from accounts.google.com
    google_frame = next(
        (f for f in page.frames if "accounts.google.com" in f.url), None
    )
    assert google_frame is not None, "Google Sign-In iframe not found"


def test_login_has_create_account_link(page):
    goto_login(page)
    link = page.locator('a:has-text("CREATE ONE")')
    assert link.is_visible()
    assert link.get_attribute("href") in ("/signup", "#")


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

def test_root_redirects_to_login(page):
    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle")
    assert "/login" in page.url


def test_create_account_link_goes_to_signup(page):
    goto_login(page)
    page.locator('a:has-text("CREATE ONE")').click()
    page.wait_for_load_state("networkidle")
    assert "/signup" in page.url


# ---------------------------------------------------------------------------
# Form validation
# ---------------------------------------------------------------------------

def test_empty_submit_shows_error(page):
    goto_login(page)
    page.locator('button:has-text("SIGN IN")').click()
    page.wait_for_timeout(500)
    body_text = page.locator("body").inner_text()
    assert "required" in body_text.lower() or "invalid" in body_text.lower()


def test_invalid_credentials_show_error(page):
    goto_login(page)
    page.fill('input[type="email"]', "nobody@example.com")
    page.fill('input[type="password"]', "wrongpassword")
    page.locator('button:has-text("SIGN IN")').click()
    page.wait_for_timeout(1500)
    body_text = page.locator("body").inner_text()
    assert "invalid" in body_text.lower() or "password" in body_text.lower()
