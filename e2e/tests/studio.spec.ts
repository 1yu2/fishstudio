import { test, expect, type Page, type BrowserContext } from '@playwright/test'

// 使用真实 API 与 worker；浏览器同样只准访问本地服务，不拦截/伪造业务响应。
async function localOnly(context: BrowserContext) {
  await context.route('**/*', async route => {
    const url = new URL(route.request().url())
    if (['127.0.0.1', 'localhost'].includes(url.hostname) || ['data:', 'blob:'].includes(url.protocol)) await route.continue()
    else await route.abort('blockedbyclient')
  })
}

test.beforeEach(async ({ context }) => { await localOnly(context) })

async function login(page: Page, user = 'e2e_admin') {
  await page.goto('/')
  await page.getByLabel('用户名', { exact: true }).fill(user)
  await page.getByLabel('密码', { exact: true }).fill(user === 'e2e_admin' ? 'e2e-admin-password' : 'e2e-user-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('heading', { name: '工作台', exact: true })).toBeVisible()
}

async function createProject(page: Page, idea: string) {
  await page.getByRole('button', { name: '新建漫剧', exact: true }).first().click()
  const dialog = page.getByRole('dialog', { name: '新建漫剧' })
  await dialog.getByLabel('故事想法').fill(idea)
  await dialog.getByRole('button', { name: '创建', exact: true }).click()
  await expect(page).toHaveURL(/\/studio\/[^/]+\//)
  await expect(page.getByTitle('点击改名')).toBeVisible()
  return /\/studio\/([^/]+)/.exec(page.url())![1]
}

async function step(page: Page, name: string) {
  await page.getByRole('navigation', { name: '制作步骤' }).getByRole('link', { name: new RegExp(name) }).click()
}

test('mock 主流程：登录、新建、拆分镜头、生成素材、渲染并下载', async ({ page }, info) => {
  await login(page)
  const id = await createProject(page, '镜头1：清晨的海面。｜画面：金色阳光照在平静的海面上｜环境：海边｜镜头：固定远景')
  await step(page, '分镜')
  await page.getByRole('spinbutton', { name: '镜头数' }).fill('1')
  const split = page.waitForResponse(r => r.url().includes('/copilot/split') && r.request().method() === 'POST')
  await page.getByRole('button', { name: '拆分镜头（会调用 AI）', exact: true }).click()
  expect((await (await split).json()).mocked).toBe(true)
  await expect(page.getByRole('region', { name: '故事板' })).toBeVisible()
  await page.getByRole('button', { name: /^重新生成图片/ }).click()
  await expect(page.getByRole('img', { name: '镜头 1 画面', exact: true }).first()).toBeVisible()
  await page.getByRole('button', { name: /^生成视频/ }).click()
  await page.getByRole('group', { name: '预览类型' }).getByRole('button', { name: '视频', exact: true }).click()
  await expect(page.locator('.sb-preview video')).toBeVisible({ timeout: 60_000 })
  await step(page, '配音')
  await page.getByLabel('旁白的音色', { exact: true }).selectOption('Cherry')
  // 配音也通过页面入口生成，以覆盖 mock TTS。
  await page.getByRole('button', { name: /^生成剩余 1 句/ }).click()
  await page.getByRole('button', { name: '确认运行', exact: true }).click()
  await expect(page.getByRole('button', { name: '配音完成，去成片 →', exact: true })).toBeEnabled({ timeout: 60_000 })
  await step(page, '成片')
  await page.getByRole('button', { name: '渲染预览', exact: true }).click()
  await expect(page.getByRole('region', { name: '预览', exact: true }).locator('video')).toBeVisible({ timeout: 90_000 })
  const download = page.waitForEvent('download')
  await page.getByRole('region', { name: '渲染与导出' }).getByRole('button', { name: '导出成片', exact: true }).click()
  const confirmation = page.getByRole('button', { name: '仍要导出', exact: true })
  // mock 视频较短，出片检查可能提醒；仅在真实确认框出现时确认。
  await Promise.race([confirmation.waitFor({ state: 'visible' }).then(() => confirmation.click()), download])
  const file = await download
  expect(file.suggestedFilename()).toBe(`成片-${id}.mp4`)
  const saved = info.outputPath('final.mp4')
  await file.saveAs(saved)
  let bytes = 0
  for await (const chunk of (await file.createReadStream())!) bytes += chunk.length
  expect(bytes).toBeGreaterThan(1000)
  expect(await file.failure()).toBeNull()
})

test('未登录打开项目链接时显示登录页', async ({ page }) => {
  await page.goto('/studio/unknown-project/storyboard')
  await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible()
  await expect(page.getByRole('navigation', { name: '制作步骤' })).toHaveCount(0)
})

test('普通用户 B 打开用户 A 的真实项目链接看不到内容', async ({ browser, page }) => {
  await login(page, 'e2e_user_a')
  const secret = `A 的私有故事 ${Date.now()}`
  const id = await createProject(page, secret)
  const other = await browser.newContext({ baseURL: 'http://127.0.0.1:3031' })
  try {
    await localOnly(other)
    const b = await other.newPage()
    await login(b, 'e2e_user_b')
    const denied = b.waitForResponse(r => r.url().includes(`/workspace/projects/${id}`))
    await b.goto(`/studio/${id}/script`)
    expect((await denied).status()).toBe(404)
    await expect(b.getByRole('alert')).toContainText('项目不存在或无权访问')
    await expect(b.getByText(secret, { exact: true })).toHaveCount(0)
    await expect(b.getByRole('navigation', { name: '制作步骤' })).toHaveCount(0)
  } finally { await other.close() }
})
