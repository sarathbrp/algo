import { test, expect } from '@playwright/test'

/**
 * Manual walkthrough — explore the full UI as a real user would.
 * Takes screenshots at each stage for visual review.
 */

const uniqueId = () => Math.random().toString(36).slice(2, 10)

test.describe('Full UI walkthrough', () => {
  const email = `walkthrough-${uniqueId()}@example.com`
  const password = 'testpassword123'

  test('01 - Login page renders correctly', async ({ page }) => {
    await page.goto('/login')
    await page.screenshot({ path: 'screenshots/01-login-page.png', fullPage: true })

    // Verify all expected elements
    await expect(page.getByRole('button', { name: 'SIGN IN' })).toBeVisible()
    await expect(page.locator('input[type="email"]')).toBeVisible()
    await expect(page.locator('input[type="password"]')).toBeVisible()
  })

  test('02 - Signup page renders correctly', async ({ page }) => {
    await page.goto('/signup')
    await page.screenshot({ path: 'screenshots/02-signup-page.png', fullPage: true })

    await expect(page.locator('input[type="email"]')).toBeVisible()
    await expect(page.locator('input[type="password"]')).toHaveCount(2)
  })

  test('03 - Register new user', async ({ page }) => {
    await page.goto('/signup')
    await page.fill('input[type="email"]', email)
    const pwInputs = page.locator('input[type="password"]')
    await pwInputs.nth(0).fill(password)
    await pwInputs.nth(1).fill(password)
    await page.screenshot({ path: 'screenshots/03-signup-filled.png', fullPage: true })

    await page.click('button[type="submit"]')
    await expect(page).toHaveURL(/\/onboarding/, { timeout: 10_000 })
  })

  test('04 - Onboarding step 1: Connect Broker', async ({ page }) => {
    // Register fresh
    const e = `walk04-${uniqueId()}@example.com`
    await page.goto('/signup')
    await page.fill('input[type="email"]', e)
    const pw = page.locator('input[type="password"]')
    await pw.nth(0).fill(password)
    await pw.nth(1).fill(password)
    await page.click('button[type="submit"]')
    await expect(page).toHaveURL(/\/onboarding/, { timeout: 10_000 })

    await page.screenshot({ path: 'screenshots/04-onboarding-step1.png', fullPage: true })

    // Verify step 1 elements
    await expect(page.getByText('ALPACA CREDENTIALS')).toBeVisible()
    await expect(page.locator('input[placeholder="APCA_API_KEY_ID"]')).toBeVisible()
    await expect(page.locator('input[placeholder="APCA_API_SECRET_KEY"]')).toBeVisible()

    // Check paper/live toggle
    await expect(page.getByRole('button', { name: 'PAPER' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'LIVE' })).toBeVisible()

    // Try switching to LIVE
    await page.getByRole('button', { name: 'LIVE' }).click()
    await page.screenshot({ path: 'screenshots/04b-onboarding-live-mode.png', fullPage: true })

    // Switch back to PAPER
    await page.getByRole('button', { name: 'PAPER' }).click()

    // Fill keys and proceed
    await page.fill('input[placeholder="APCA_API_KEY_ID"]', 'PKTEST_WALKTHROUGH_KEY')
    await page.fill('input[placeholder="APCA_API_SECRET_KEY"]', 'walkthrough_secret_key_123')
    await page.screenshot({ path: 'screenshots/04c-onboarding-keys-filled.png', fullPage: true })
    await page.click('text=CONNECT BROKER →')
  })

  test('05 - Onboarding step 2: Risk Profile selection', async ({ page }) => {
    // Register and get to step 2
    const e = `walk05-${uniqueId()}@example.com`
    await page.goto('/signup')
    await page.fill('input[type="email"]', e)
    const pw = page.locator('input[type="password"]')
    await pw.nth(0).fill(password)
    await pw.nth(1).fill(password)
    await page.click('button[type="submit"]')
    await expect(page).toHaveURL(/\/onboarding/, { timeout: 10_000 })
    await page.fill('input[placeholder="APCA_API_KEY_ID"]', 'PKTEST123')
    await page.fill('input[placeholder="APCA_API_SECRET_KEY"]', 'secret123')
    await page.click('text=CONNECT BROKER →')

    await page.screenshot({ path: 'screenshots/05-onboarding-step2-balanced.png', fullPage: true })

    // Verify all 3 profiles are shown
    await expect(page.getByText('CONSERVATIVE')).toBeVisible()
    await expect(page.getByText('BALANCED')).toBeVisible()
    await expect(page.getByText('AGGRESSIVE')).toBeVisible()

    // Try selecting conservative
    await page.click('text=CONSERVATIVE')
    await page.screenshot({ path: 'screenshots/05b-onboarding-conservative.png', fullPage: true })

    // Try selecting aggressive
    await page.click('text=AGGRESSIVE')
    await page.screenshot({ path: 'screenshots/05c-onboarding-aggressive.png', fullPage: true })

    // Select balanced and proceed
    await page.click('text=BALANCED')
    await page.click('text=CONFIRM PROFILE →')
  })

  test('06 - Onboarding step 3: Confirmation', async ({ page }) => {
    // Register and get to step 3
    const e = `walk06-${uniqueId()}@example.com`
    await page.goto('/signup')
    await page.fill('input[type="email"]', e)
    const pw = page.locator('input[type="password"]')
    await pw.nth(0).fill(password)
    await pw.nth(1).fill(password)
    await page.click('button[type="submit"]')
    await expect(page).toHaveURL(/\/onboarding/, { timeout: 10_000 })
    await page.fill('input[placeholder="APCA_API_KEY_ID"]', 'PKWALK06KEY')
    await page.fill('input[placeholder="APCA_API_SECRET_KEY"]', 'secretwalk06')
    await page.click('text=CONNECT BROKER →')
    await page.click('text=CONSERVATIVE')
    await page.click('text=CONFIRM PROFILE →')

    await page.screenshot({ path: 'screenshots/06-onboarding-confirm.png', fullPage: true })

    // Verify confirmation shows correct summary
    await expect(page.getByText('READY TO LAUNCH')).toBeVisible()
    await expect(page.getByText('PAPER TRADING', { exact: true })).toBeVisible()
    await expect(page.getByText('CONSERVATIVE', { exact: true })).toBeVisible()
    await expect(page.getByText('PKWALK06')).toBeVisible() // Truncated key with ···
  })

  test('07 - Dashboard after onboarding', async ({ page }) => {
    // Full flow to dashboard
    const e = `walk07-${uniqueId()}@example.com`
    await page.goto('/signup')
    await page.fill('input[type="email"]', e)
    const pw = page.locator('input[type="password"]')
    await pw.nth(0).fill(password)
    await pw.nth(1).fill(password)
    await page.click('button[type="submit"]')
    await expect(page).toHaveURL(/\/onboarding/, { timeout: 10_000 })
    await page.fill('input[placeholder="APCA_API_KEY_ID"]', 'PKTEST123')
    await page.fill('input[placeholder="APCA_API_SECRET_KEY"]', 'secret123')
    await page.click('text=CONNECT BROKER →')
    await page.click('text=CONFIRM PROFILE →')
    await page.click('text=START PAPER TRADING')
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 10_000 })

    // Wait for dashboard to fully load
    await page.waitForTimeout(2000)
    await page.screenshot({ path: 'screenshots/07-dashboard.png', fullPage: true })

    // Check header elements
    const header = page.locator('header').first()
    if (await header.isVisible()) {
      await page.screenshot({ path: 'screenshots/07b-dashboard-header.png' })
    }
  })

  test('08 - Dashboard navigation links', async ({ page }) => {
    // Login with existing user
    const e = `walk08-${uniqueId()}@example.com`
    await page.goto('/signup')
    await page.fill('input[type="email"]', e)
    const pw = page.locator('input[type="password"]')
    await pw.nth(0).fill(password)
    await pw.nth(1).fill(password)
    await page.click('button[type="submit"]')
    await expect(page).toHaveURL(/\/onboarding/, { timeout: 10_000 })
    await page.fill('input[placeholder="APCA_API_KEY_ID"]', 'PK123')
    await page.fill('input[placeholder="APCA_API_SECRET_KEY"]', 'secret')
    await page.click('text=CONNECT BROKER →')
    await page.click('text=CONFIRM PROFILE →')
    await page.click('text=START PAPER TRADING')
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 10_000 })
    await page.waitForTimeout(1000)

    // Try navigating to Trades page
    const tradesLink = page.getByText('TRADES', { exact: true })
    if (await tradesLink.isVisible()) {
      await tradesLink.click()
      await page.waitForTimeout(1000)
      await page.screenshot({ path: 'screenshots/08-trades-page.png', fullPage: true })
    }

    // Try navigating to Settings page
    const settingsLink = page.getByText('SETTINGS', { exact: true })
    if (await settingsLink.isVisible()) {
      await settingsLink.click()
      await page.waitForTimeout(1000)
      await page.screenshot({ path: 'screenshots/08b-settings-page.png', fullPage: true })
    }

    // Navigate back to Dashboard
    const dashLink = page.getByText('DASHBOARD', { exact: true })
    if (await dashLink.isVisible()) {
      await dashLink.click()
      await page.waitForTimeout(1000)
      await page.screenshot({ path: 'screenshots/08c-back-to-dashboard.png', fullPage: true })
    }
  })

  test('09 - Logout and re-login', async ({ page }) => {
    const e = `walk09-${uniqueId()}@example.com`
    await page.goto('/signup')
    await page.fill('input[type="email"]', e)
    const pw = page.locator('input[type="password"]')
    await pw.nth(0).fill(password)
    await pw.nth(1).fill(password)
    await page.click('button[type="submit"]')
    await expect(page).toHaveURL(/\/onboarding/, { timeout: 10_000 })
    await page.fill('input[placeholder="APCA_API_KEY_ID"]', 'PK123')
    await page.fill('input[placeholder="APCA_API_SECRET_KEY"]', 'secret')
    await page.click('text=CONNECT BROKER →')
    await page.click('text=CONFIRM PROFILE →')
    await page.click('text=START PAPER TRADING')
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 10_000 })
    await page.waitForTimeout(1000)

    // Look for logout button/link
    const logoutBtn = page.getByText('LOGOUT')
    if (await logoutBtn.isVisible()) {
      await logoutBtn.click()
      await page.waitForTimeout(1000)
      await page.screenshot({ path: 'screenshots/09-after-logout.png', fullPage: true })
      await expect(page).toHaveURL(/\/login/)
    } else {
      // Try clicking user pill or menu
      await page.screenshot({ path: 'screenshots/09-no-logout-found.png', fullPage: true })
    }

    // Re-login
    await page.goto('/login')
    await page.fill('input[type="email"]', e)
    await page.fill('input[type="password"]', password)
    await page.click('button[type="submit"]')
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 10_000 })
    await page.waitForTimeout(1000)
    await page.screenshot({ path: 'screenshots/09b-relogin-dashboard.png', fullPage: true })
  })
})
