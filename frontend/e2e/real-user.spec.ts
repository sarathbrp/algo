import { test, expect } from '@playwright/test'

test('Real user dashboard debug', async ({ page }) => {
  const token = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJhNGZmNWJmNTVjNzA0MzcyIiwicm9sZSI6InRyYWRlciIsImlhdCI6MTc3NTE0MDAxMywiZXhwIjoxNzc1MjI2NDEzfQ.NYQPFO7Ur0XcufSNYHj87qAR5N6C1-V-XM39_iyqbM4'
  const userId = 'a4ff5bf55c704372'

  // Listen for API responses
  const apiResponses: string[] = []
  page.on('response', (resp) => {
    if (resp.url().includes('/api/')) {
      apiResponses.push(`${resp.status()} ${resp.url().replace('http://localhost:3080', '')}`)
    }
  })
  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      console.log('CONSOLE ERROR:', msg.text())
    }
  })

  await page.goto('/login')
  await page.evaluate(({ token, userId }) => {
    localStorage.setItem('algosphere-auth', JSON.stringify({
      state: { token, userId, email: 'psbr.27@gmail.com', role: 'trader', paper: true },
      version: 0,
    }))
  }, { token, userId })

  await page.goto('/dashboard')
  await page.waitForTimeout(10000)

  console.log('API responses:', apiResponses.join('\n'))
  await page.screenshot({ path: 'screenshots/real-debug.png', fullPage: true })
})
