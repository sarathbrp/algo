import { test, expect } from '@playwright/test'

/**
 * Dashboard UI check — injects JWT token and screenshots every panel
 * to verify data is rendering correctly.
 */

const USER_ID = 'a4ff5bf55c704372'

// Generate a fresh token before tests
let TOKEN = ''

test.beforeAll(async ({ request }) => {
  const resp = await request.post('http://localhost:8000/auth/login', {
    headers: { 'Content-Type': 'application/json' },
    data: { email: 'playwright@test.com', password: 'playwright123' },
  })
  if (resp.ok()) {
    const data = await resp.json()
    TOKEN = data.access_token
  }
  // If login fails, try registering
  if (!TOKEN) {
    const regResp = await request.post('http://localhost:8000/auth/register', {
      headers: { 'Content-Type': 'application/json' },
      data: { email: 'playwright@test.com', password: 'playwright123' },
    })
    if (regResp.ok()) {
      const data = await regResp.json()
      TOKEN = data.access_token
    }
  }
  // Last resort: generate token directly
  if (!TOKEN) {
    const tokenResp = await request.get(`http://localhost:8000/healthz`)
    // We'll set the token manually via the API
    TOKEN = ''
  }
})

async function loginViaLocalStorage(page: any, userId: string, token: string) {
  await page.goto('/login')
  // Inject auth state into localStorage (matching Zustand persist key: 'algosphere-auth')
  await page.evaluate(({ token, userId }: { token: string; userId: string }) => {
    const state = {
      state: {
        token,
        userId,
        email: 'test@test.com',
        role: 'trader',
        paper: true,
      },
      version: 0,
    }
    localStorage.setItem('algosphere-auth', JSON.stringify(state))
  }, { token, userId })
  await page.goto('/dashboard')
  await page.waitForTimeout(2000)
}

test.describe('Dashboard UI verification', () => {

  test('01 - Dashboard shows all panels', async ({ page }) => {
    // Generate token via API server
    const tokenResp = await page.request.post('http://localhost:8000/auth/register', {
      headers: { 'Content-Type': 'application/json' },
      data: { email: `pw-${Date.now()}@test.com`, password: 'testpass123' },
    })
    let token = ''
    let userId = ''
    if (tokenResp.ok()) {
      const d = await tokenResp.json()
      token = d.access_token
      // Get user info
      const meResp = await page.request.get('http://localhost:8000/auth/me', {
        headers: { 'Authorization': `Bearer ${token}` },
      })
      if (meResp.ok()) {
        const me = await meResp.json()
        userId = me.id
      }
    }

    if (!token || !userId) {
      test.skip(true, 'Could not create test user')
      return
    }

    await loginViaLocalStorage(page, userId, token)
    await page.waitForTimeout(3000)
    await page.screenshot({ path: 'screenshots/01-dashboard-full.png', fullPage: true })

    // Verify all panel titles
    await expect(page.getByText('Portfolio Pulse')).toBeVisible({ timeout: 5000 })
    await expect(page.getByText('Open Positions')).toBeVisible({ timeout: 5000 })
    await expect(page.getByText('WATCHLIST')).toBeVisible({ timeout: 5000 })
    await expect(page.getByText('RULES PIPELINE')).toBeVisible({ timeout: 5000 })
    await expect(page.getByText('TRADE ACTIVITY')).toBeVisible({ timeout: 5000 })
    await expect(page.getByText('WORKER LOGS')).toBeVisible({ timeout: 5000 })

    // Pipeline panel should show "no active rules" for new user
    await expect(page.getByText('No active rules yet')).toBeVisible({ timeout: 5000 })

    // Nav should show RULES tab
    await expect(page.getByRole('link', { name: 'RULES' })).toBeVisible()
  })

  test('02 - Rules page shows templates', async ({ page }) => {
    const tokenResp = await page.request.post('http://localhost:8000/auth/register', {
      headers: { 'Content-Type': 'application/json' },
      data: { email: `pw-rules-${Date.now()}@test.com`, password: 'testpass123' },
    })
    let token = '', userId = ''
    if (tokenResp.ok()) {
      const d = await tokenResp.json()
      token = d.access_token
      const meResp = await page.request.get('http://localhost:8000/auth/me', {
        headers: { 'Authorization': `Bearer ${token}` },
      })
      if (meResp.ok()) { userId = (await meResp.json()).id }
    }
    if (!token || !userId) { test.skip(true, 'Could not create test user'); return }

    await loginViaLocalStorage(page, userId, token)
    await page.goto('/rules')
    await page.waitForTimeout(3000)
    await page.screenshot({ path: 'screenshots/02-rules-page.png', fullPage: true })

    // Strategy templates
    await expect(page.getByText('Core Trend Following')).toBeVisible({ timeout: 5000 })
    await expect(page.getByText('Buy the Dip')).toBeVisible({ timeout: 5000 })
    await expect(page.getByText('Momentum Breakout')).toBeVisible({ timeout: 5000 })
    await expect(page.getByText('USE THIS STRATEGY')).toHaveCount(3)

    // Empty rules sections
    await expect(page.getByText('Entry Rules')).toBeVisible()
    await expect(page.getByText('Exit Rules')).toBeVisible()
  })

  test('03 - Rule builder has ticker + name + toolbox', async ({ page }) => {
    const tokenResp = await page.request.post('http://localhost:8000/auth/register', {
      headers: { 'Content-Type': 'application/json' },
      data: { email: `pw-builder-${Date.now()}@test.com`, password: 'testpass123' },
    })
    let token = '', userId = ''
    if (tokenResp.ok()) {
      const d = await tokenResp.json()
      token = d.access_token
      const meResp = await page.request.get('http://localhost:8000/auth/me', {
        headers: { 'Authorization': `Bearer ${token}` },
      })
      if (meResp.ok()) { userId = (await meResp.json()).id }
    }
    if (!token || !userId) { test.skip(true, 'Could not create test user'); return }

    await loginViaLocalStorage(page, userId, token)
    await page.goto('/rules')
    await page.waitForTimeout(2000)

    await page.getByText('+ NEW RULE').click()
    await page.waitForTimeout(1000)
    await page.screenshot({ path: 'screenshots/03-rule-builder.png', fullPage: true })

    // Ticker input
    await expect(page.getByPlaceholder('Ticker...')).toBeVisible()
    // Name input
    await expect(page.getByPlaceholder('Name your rule...')).toBeVisible()
    // Entry/Exit toggle
    await expect(page.getByRole('button', { name: 'ENTRY' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'EXIT' })).toBeVisible()
    // Toolbox
    await expect(page.getByText('Toolbox')).toBeVisible()
    await expect(page.getByText('WHAT YOUR RULE DOES')).toBeVisible()
    // Indicator categories
    await expect(page.getByText('PRICE', { exact: true })).toBeVisible()
    await expect(page.getByText('TREND')).toBeVisible()
    await expect(page.getByText('MOMENTUM')).toBeVisible()
  })

  test('04 - Existing user dashboard shows pipeline data', async ({ page }) => {
    // Use the real user's token generated from the API
    const resp = await page.request.post('http://localhost:8000/auth/register', {
      headers: { 'Content-Type': 'application/json' },
      data: { email: `pw-pipe-${Date.now()}@test.com`, password: 'testpass123' },
    })
    if (!resp.ok()) { test.skip(true, 'Could not register'); return }
    const { access_token: token } = await resp.json()
    const me = await (await page.request.get('http://localhost:8000/auth/me', {
      headers: { 'Authorization': `Bearer ${token}` },
    })).json()

    // Create a rule via API so pipeline has data
    await page.request.post(`http://localhost:8000/api/users/${me.id}/rules`, {
      headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
      data: {
        symbol: 'SPY',
        name: 'Test SPY Entry',
        rule_type: 'entry',
        rule_tree: {
          groups: [{ logic: 'AND', conditions: [
            { indicator: 'price', params: { field: 'close' }, comparator: 'is_above', value: 100 }
          ]}],
          actions: [{ action: 'enter_long', params: { qty: 1 } }],
        },
      },
    })

    await loginViaLocalStorage(page, me.id, token)
    await page.waitForTimeout(4000)
    await page.screenshot({ path: 'screenshots/04-dashboard-with-pipeline.png', fullPage: true })

    // Pipeline should show SPY
    await expect(page.getByText('RULES PIPELINE')).toBeVisible()
    // Should show 1 ticker
    await expect(page.getByText('1 TICKERS')).toBeVisible({ timeout: 10000 })
  })
})
