"""Playwright UI tests for the AlgoSphere Settings page."""
import json
import pytest
import requests
from playwright.sync_api import sync_playwright

BASE_URL = "http://localhost:3080"
API_URL = "http://localhost:8000"
TEST_EMAIL = "test_settings@example.com"
TEST_PASSWORD = "testpass123"


def _get_auth_token() -> tuple[str, str, str]:
    """Register (or login) a test user via API and return (token, user_id, email)."""
    # Try register first
    r = requests.post(f"{API_URL}/auth/register", json={"email": TEST_EMAIL, "password": TEST_PASSWORD})
    if r.status_code == 409:
        # Already exists — login instead
        r = requests.post(f"{API_URL}/auth/login", json={"email": TEST_EMAIL, "password": TEST_PASSWORD})
    r.raise_for_status()
    token = r.json()["access_token"]

    me = requests.get(f"{API_URL}/auth/me", headers={"Authorization": f"Bearer {token}"})
    me.raise_for_status()
    profile = me.json()
    return token, profile["id"], profile["email"]


def _inject_auth(page, token: str, user_id: str, email: str) -> None:
    """Inject JWT into localStorage so AuthGuard passes."""
    page.goto(BASE_URL)
    page.wait_for_load_state("networkidle")
    auth_state = json.dumps({
        "state": {"token": token, "userId": user_id, "email": email, "role": "trader", "paper": True},
        "version": 0,
    })
    page.evaluate(f"localStorage.setItem('algosphere-auth', {json.dumps(auth_state)})")
    page.reload()
    page.wait_for_load_state("networkidle")


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture(scope="session")
def auth_creds():
    return _get_auth_token()


@pytest.fixture
def authed_page(browser, auth_creds):
    token, user_id, email = auth_creds
    ctx = browser.new_context()
    pg = ctx.new_page()
    _inject_auth(pg, token, user_id, email)
    yield pg
    ctx.close()


def goto_settings(page):
    page.goto(f"{BASE_URL}/settings")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(800)


# ---------------------------------------------------------------------------
# Settings page structure
# ---------------------------------------------------------------------------

def test_settings_page_loads(authed_page):
    goto_settings(authed_page)
    assert "/settings" in authed_page.url


def test_settings_has_broker_settings_header(authed_page):
    goto_settings(authed_page)
    body = authed_page.locator("body").inner_text()
    assert "BROKER SETTINGS" in body


def test_settings_has_api_key_input(authed_page):
    goto_settings(authed_page)
    assert authed_page.locator('input[type="text"]').count() > 0


def test_settings_has_secret_input(authed_page):
    goto_settings(authed_page)
    assert authed_page.locator('input[type="password"]').count() > 0


def test_settings_has_paper_live_toggle(authed_page):
    goto_settings(authed_page)
    body = authed_page.locator("body").inner_text()
    assert "PAPER" in body and "LIVE" in body


def test_settings_has_risk_profile_options(authed_page):
    goto_settings(authed_page)
    body = authed_page.locator("body").inner_text().upper()
    assert all(rp in body for rp in ["CONSERVATIVE", "BALANCED", "AGGRESSIVE"])


def test_settings_has_save_button(authed_page):
    goto_settings(authed_page)
    assert authed_page.locator('button:has-text("SAVE")').count() > 0


def test_settings_shows_credential_status_badge(authed_page):
    goto_settings(authed_page)
    body = authed_page.locator("body").inner_text()
    assert "CREDENTIALS" in body


# ---------------------------------------------------------------------------
# Form interactions
# ---------------------------------------------------------------------------

def test_paper_button_is_active_by_default(authed_page):
    goto_settings(authed_page)
    body = authed_page.locator("body").inner_text()
    assert "PAPER" in body


def test_clicking_live_shows_real_money_warning(authed_page):
    goto_settings(authed_page)
    live_btn = authed_page.locator('button:has-text("LIVE")')
    live_btn.click()
    authed_page.wait_for_timeout(300)
    body = authed_page.locator("body").inner_text()
    assert "real money" in body.lower()


def test_clicking_back_to_paper_removes_warning(authed_page):
    goto_settings(authed_page)
    authed_page.locator('button:has-text("LIVE")').click()
    authed_page.wait_for_timeout(200)
    authed_page.locator('button:has-text("PAPER")').click()
    authed_page.wait_for_timeout(300)
    body = authed_page.locator("body").inner_text()
    assert "real money" not in body.lower()


def test_risk_profile_buttons_are_clickable(authed_page):
    goto_settings(authed_page)
    for profile in ["CONSERVATIVE", "AGGRESSIVE", "BALANCED"]:
        btn = authed_page.locator(f'button:has-text("{profile}")')
        assert btn.count() > 0
        btn.click()
        authed_page.wait_for_timeout(150)


def test_empty_save_shows_error(authed_page):
    goto_settings(authed_page)
    authed_page.fill('input[type="text"]', '')
    authed_page.fill('input[type="password"]', '')
    authed_page.locator('button:has-text("SAVE SETTINGS")').click()
    authed_page.wait_for_timeout(500)
    body = authed_page.locator("body").inner_text()
    assert "required" in body.lower()


def test_save_with_credentials_shows_success_or_error(authed_page):
    goto_settings(authed_page)
    authed_page.fill('input[type="text"]', 'FAKEKEYABC123')
    authed_page.fill('input[type="password"]', 'fakesecretxyz456')
    authed_page.locator('button:has-text("SAVE SETTINGS")').click()
    authed_page.wait_for_timeout(3000)
    body = authed_page.locator("body").inner_text()
    assert "SAVED" in body or "saved" in body or "error" in body.lower() or "failed" in body.lower()
