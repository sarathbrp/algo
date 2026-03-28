import { test, expect } from '@playwright/test'

/**
 * E2E tests for the signup → onboarding → dashboard flow.
 * Requires Docker Compose stack running on localhost:3080.
 */

const uniqueId = () => Math.random().toString(36).slice(2, 10)

test.describe('Login page', () => {
  test('renders login form', async ({ page }) => {
    await page.goto('/login')
    await expect(page.getByText('ALGOSPHERE', { exact: true }).first()).toBeVisible()
    await expect(page.locator('input[type="email"]')).toBeVisible()
    await expect(page.locator('input[type="password"]')).toBeVisible()
  })

  test('shows error on invalid credentials', async ({ page }) => {
    await page.goto('/login')
    await page.fill('input[type="email"]', 'bad@example.com')
    await page.fill('input[type="password"]', 'wrongpassword')
    await page.click('button[type="submit"]')
    await expect(page.getByText('Invalid email or password')).toBeVisible({ timeout: 10_000 })
  })

  test('has link to signup page', async ({ page }) => {
    await page.goto('/login')
    await page.click('text=CREATE ONE')
    await expect(page).toHaveURL(/\/signup/)
  })
})

test.describe('Signup page', () => {
  test('renders signup form', async ({ page }) => {
    await page.goto('/signup')
    await expect(page.getByRole('button', { name: 'CREATE ACCOUNT' })).toBeVisible()
    await expect(page.locator('input[type="email"]')).toBeVisible()
    const passwordInputs = page.locator('input[type="password"]')
    await expect(passwordInputs).toHaveCount(2)
  })

  test('shows error when passwords do not match', async ({ page }) => {
    await page.goto('/signup')
    await page.fill('input[type="email"]', 'test@example.com')
    const passwordInputs = page.locator('input[type="password"]')
    await passwordInputs.nth(0).fill('password123')
    await passwordInputs.nth(1).fill('differentpassword')
    await page.click('button[type="submit"]')
    await expect(page.getByText('Passwords do not match')).toBeVisible()
  })

  test('shows error for short password', async ({ page }) => {
    await page.goto('/signup')
    await page.fill('input[type="email"]', 'test@example.com')
    const passwordInputs = page.locator('input[type="password"]')
    await passwordInputs.nth(0).fill('short')
    await passwordInputs.nth(1).fill('short')
    await page.click('button[type="submit"]')
    await expect(page.getByText('at least 8 characters')).toBeVisible()
  })

  test('shows error when fields are empty', async ({ page }) => {
    await page.goto('/signup')
    await page.click('button[type="submit"]')
    await expect(page.getByText('All fields are required')).toBeVisible()
  })

  test('successfully registers and navigates to onboarding', async ({ page }) => {
    const email = `signup-${uniqueId()}@example.com`
    await page.goto('/signup')
    await page.fill('input[type="email"]', email)
    const passwordInputs = page.locator('input[type="password"]')
    await passwordInputs.nth(0).fill('testpassword123')
    await passwordInputs.nth(1).fill('testpassword123')
    await page.click('button[type="submit"]')
    await expect(page).toHaveURL(/\/onboarding/, { timeout: 10_000 })
  })

  test('shows error for duplicate email', async ({ page }) => {
    const email = `dupe-${uniqueId()}@example.com`
    // Register first time
    await page.goto('/signup')
    await page.fill('input[type="email"]', email)
    const pwInputs = page.locator('input[type="password"]')
    await pwInputs.nth(0).fill('testpassword123')
    await pwInputs.nth(1).fill('testpassword123')
    await page.click('button[type="submit"]')
    await expect(page).toHaveURL(/\/onboarding/, { timeout: 10_000 })

    // Clear auth and try same email again
    await page.goto('/login')
    await page.evaluate(() => localStorage.clear())
    await page.goto('/signup')
    await page.fill('input[type="email"]', email)
    const pwInputs2 = page.locator('input[type="password"]')
    await pwInputs2.nth(0).fill('testpassword123')
    await pwInputs2.nth(1).fill('testpassword123')
    await page.click('button[type="submit"]')
    await expect(page.getByText('already exists')).toBeVisible({ timeout: 5_000 })
  })
})

test.describe('Onboarding flow', () => {
  test.beforeEach(async ({ page }) => {
    const email = `onboard-${uniqueId()}@example.com`
    await page.goto('/signup')
    await page.fill('input[type="email"]', email)
    const pwInputs = page.locator('input[type="password"]')
    await pwInputs.nth(0).fill('testpassword123')
    await pwInputs.nth(1).fill('testpassword123')
    await page.click('button[type="submit"]')
    await expect(page).toHaveURL(/\/onboarding/, { timeout: 10_000 })
  })

  test('step 1: connect broker requires API keys', async ({ page }) => {
    await page.click('text=CONNECT BROKER →')
    await expect(page.getByText('API key and secret are required')).toBeVisible()
  })

  test('step 1: can enter API keys and proceed', async ({ page }) => {
    await page.fill('input[placeholder="APCA_API_KEY_ID"]', 'PKTEST123456')
    await page.fill('input[placeholder="APCA_API_SECRET_KEY"]', 'secretkey789')
    await page.click('text=CONNECT BROKER →')
    await expect(page.getByText('SELECT RISK PROFILE')).toBeVisible()
  })

  test('step 2: can select risk profile and proceed', async ({ page }) => {
    await page.fill('input[placeholder="APCA_API_KEY_ID"]', 'PKTEST123456')
    await page.fill('input[placeholder="APCA_API_SECRET_KEY"]', 'secretkey789')
    await page.click('text=CONNECT BROKER →')
    await page.click('text=CONSERVATIVE')
    await page.click('text=CONFIRM PROFILE →')
    await expect(page.getByText('READY TO LAUNCH')).toBeVisible()
  })

  test('full flow: signup → onboarding → dashboard', async ({ page }) => {
    await page.fill('input[placeholder="APCA_API_KEY_ID"]', 'PKTEST123456')
    await page.fill('input[placeholder="APCA_API_SECRET_KEY"]', 'secretkey789')
    await page.click('text=CONNECT BROKER →')
    await page.click('text=CONFIRM PROFILE →')
    await page.click('text=START PAPER TRADING')
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 10_000 })
  })
})

test.describe('Auth guard', () => {
  test('redirects to login when not authenticated', async ({ page }) => {
    // Navigate to a page first so localStorage is accessible
    await page.goto('/login')
    await page.evaluate(() => localStorage.clear())
    await page.goto('/dashboard')
    await expect(page).toHaveURL(/\/login/)
  })

  test('redirects to login for onboarding when not authenticated', async ({ page }) => {
    await page.goto('/login')
    await page.evaluate(() => localStorage.clear())
    await page.goto('/onboarding')
    await expect(page).toHaveURL(/\/login/)
  })
})

test.describe('Dashboard', () => {
  test('accessible after full signup flow', async ({ page }) => {
    const email = `dash-${uniqueId()}@example.com`
    await page.goto('/signup')
    await page.fill('input[type="email"]', email)
    const pwInputs = page.locator('input[type="password"]')
    await pwInputs.nth(0).fill('testpassword123')
    await pwInputs.nth(1).fill('testpassword123')
    await page.click('button[type="submit"]')
    await expect(page).toHaveURL(/\/onboarding/, { timeout: 10_000 })

    await page.fill('input[placeholder="APCA_API_KEY_ID"]', 'PKTEST123456')
    await page.fill('input[placeholder="APCA_API_SECRET_KEY"]', 'secretkey789')
    await page.click('text=CONNECT BROKER →')
    await page.click('text=CONFIRM PROFILE →')
    await page.click('text=START PAPER TRADING')
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 10_000 })
    await expect(page.getByText('ALGOSPHERE', { exact: true }).first()).toBeVisible()
  })

  test('accessible via login after registration', async ({ page }) => {
    const email = `logintest-${uniqueId()}@example.com`
    const password = 'testpassword123'

    await page.goto('/signup')
    await page.fill('input[type="email"]', email)
    const pwInputs = page.locator('input[type="password"]')
    await pwInputs.nth(0).fill(password)
    await pwInputs.nth(1).fill(password)
    await page.click('button[type="submit"]')
    await expect(page).toHaveURL(/\/onboarding/, { timeout: 10_000 })

    // Clear auth and login
    await page.goto('/login')
    await page.evaluate(() => localStorage.clear())
    await page.goto('/login')
    await page.fill('input[type="email"]', email)
    await page.fill('input[type="password"]', password)
    await page.click('button[type="submit"]')
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 10_000 })
  })
})
