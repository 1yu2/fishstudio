# 网页端到端测试

真实 Vite、FastAPI、Postgres 和内嵌 worker；模型使用已有 mock，不伪造 API 响应。

首次准备（仓库根目录）：

```sh
python3.11 -m venv e2e/.runtime/venv
e2e/.runtime/venv/bin/pip install -r backend/requirements.txt
# frontend/node_modules 若为共享软链，跳过此步，不往里面安装。
npm ci --prefix frontend
cd e2e
npm ci
npx playwright install chromium
```

Postgres 需在 `127.0.0.1:55432` 提供专用库 `fishstudio_test_c2`，账号/密码均为 `postgres`。每次测试启动会删除并重建**该测试库的 public schema**，禁止存放需要保留的数据；不会停止 PG 容器。CI 使用独立 PG service（5432）。

以后本地一条命令（在 `e2e/`）：

```sh
E2E_PYTHON="$PWD/.runtime/venv/bin/python" npx playwright test
```

若 Python 依赖已在当前环境安装，可直接 `npx playwright test`。`E2E_PYTHON` 也可指定已有 Python；`E2E_DATABASE_URL` 只接受本地 5432/55432 的 `fishstudio_test_c2`。

独占后端 8031、前端 3031，端口占用即失败，不复用其他服务。Playwright webServer 执行迁移、创建 admin 和两名普通用户，再启动 API / worker 和 Vite；结束、失败或 Ctrl-C 都会清理自己启动的子进程。Chromium 只需安装一次；如默认缓存不可写，安装和测试时均设置同一个 `PLAYWRIGHT_BROWSERS_PATH`。

测试账号：`e2e_admin / e2e-admin-password`；`e2e_user_a`、`e2e_user_b` 密码均为 `e2e-user-password`。启动器只传递基础系统变量及测试配置，禁用 dotenv 和 Vite envDir，并拒绝 `.env*` 文件读取与后端外网连接。浏览器也拒绝外网请求。不要 source `.env` 或配置厂商 key。

覆盖：

- 登录 → 新建漫剧 → 分镜页 mock 拆镜头 → mock 图片 / 视频 / 配音 → 成片渲染、预览及 MP4 下载。
- 未登录访问项目显示登录页（应用保留 URL，没有独立 `/login` 路由）。
- 普通用户 B 在独立浏览器上下文打开 A 的真实项目：摘要 API 返回 404、显示无权访问、正文不可见。

失败输出：`test-results/`（trace、截图、录像）和 `playwright-report/`，均忽略提交；`npx playwright show-report` 可查看。CI 初期 `continue-on-error: true`，观察浏览器依赖、mock/渲染耗时和跨平台稳定性；稳定后移除此设置成为必需检查。
