# 系列视频整改复盘报告

> 日期：2026-08-04（第二次更新：复审整改 + E2E 回归）
> 依据：`docs/series-video-review-findings.md`（review 基线 `docs/series-video-optimization-plan.md` v1.3）
> 整改方式：首轮三轮 Wave（Wave1 安全/Provider、Wave2 工作流/费用/反馈、Wave3 前端）+
> 复审（第零节）四轮 Wave（Wave A 安全+0005 迁移、Wave B 费用/重试闭环、Wave C Provider 真实化、Wave D1 Planner+个人资产 API）
> 最终验证：后端 `pytest tests/` **284 passed / 22 skipped**，`ruff check app tests` 通过；
> 真实 PG 集成测试：迁移 3 passed（head=0005）、tenancy 15 passed、retry 2 passed；
> 真实 PG + uvicorn + 内嵌 worker mock 全链路 E2E **51/51 通过**。

---

## 一、2026-08-04 复审（第零节）整改总览

### Wave A：安全 + 0005 迁移（0.2 P0-3、0.3、0.4）

- `/storage` 项目级权限收口：非 public 文件按 artifact→project→user 鉴权，任意登录用户不再可读全部文件（`routers/artifacts.py`）。
- Feedback 跨项目写入修复（0.3）：`feedback_service.record_feedback` 校验 step 所属 run 的 project_id == artifact.project_id，且 artifact 必须是该 step 关联 job 的产物；step_id 可空时写 Shot 级选择（`Shot.selected_artifact_id`，0005 迁移）。
- 无主 SeriesProject 拒绝（0.4）：归属校验收敛，普通用户访问 `user_id=NULL` 项目一律 403/404；新增 admin 认领接口 `POST /api/series/projects/{id}/claim`（仅无主可认领）。
- Alembic 0005：`generation_jobs` 增加 `step_attempt/logical_job_key/superseded_by`，`shots` 增加 `selected_artifact_id`（`alembic/versions/0005_retry_generation_shot_selection.py`）。

### Wave B：费用 / 重试闭环（0.5 P1-4、0.6、0.7）

- 所有收费节点（image/video/tts）统一 Cost Gate：`requires_cost_confirm=True`，`advance_run` 在 fan-out 前校验 `cost_confirmed` + quote 复核（`workflow_service._cost_gate_passed`）。
- confirm-cost 绑定 quote_id：请求体必填 `{"quote_id": ...}`，校验存在/匹配/未过期（30 分钟）/价格表版本/input_hash（`cost_service.validate_cost_quote`）；quote 携带 input_hash、expires_at、storyboard revision 快照。
- 重试代际：`GenerationJob.step_attempt`（retry_step 递增）+ `logical_job_key` + `superseded_by`（job 级 retry 回填）；`on_job_finished` 只聚合当前 attempt 且未 superseded 的最新一代 job；job 级 retry 使 failed run 恢复 ready 再 advance。
- 原子预算预留：`cost_service.reserve_budget`（重试/展开前预留，不足 409 budget_exceeded）。

### Wave C：Provider 真实化（0.5 P1-1、0.9）

- 火山图片 Provider 接入 ImageProvider 接口，`available_image_providers()` 不再为空（无 key 时仍不可用，标准链路不依赖 Mock 的约束保持）。
- MiniMax H3：协议无法核验，默认禁用 + 路由守卫，balanced 不再默认路由到该 Adapter。

### Wave D1：Planner + 个人资产 API（0.8 P2-1、P2-3）

- LLM Planner 接入创建入口：`POST /api/workflow/runs` 支持 `use_llm_planner`，`extra_nodes` 经 `apply_extra_nodes` 插入模板 DAG 尾部链（白名单校验、node_key 唯一化），不再丢弃。
- 个人资产独立 API：`POST/GET /api/assets/personal`、`POST /api/assets/personal/{id}/versions`、`POST /api/assets/personal/{id}/copy-to-project`（owner 隔离，`routers/assets.py`）；项目资产列表支持 `include_personal=true` 合并。
- Renderer/Planner 消费选定产物：`timeline_renderer` 选视频按 Shot.selected_artifact_id 优先、manifest.shots[].artifact_selection 记录来源（selected/latest/placeholder）；`job_planner` 首帧优先选定 image artifact。

### 本次 E2E 回归新发现并修复的 bug

1. **费用 Gate 复核误杀（新引入严重 bug，E2E 实测复现）**：`compute_input_hash` 基于"未完成 creates_jobs steps"计算，image step 完成后 hash 变化，`validate_cost_quote` 把仍有效的 quote 判失效，`shot.video.generate` 永久卡 `waiting_approval`。修复：input_hash 改为基于**全量** creates_jobs steps（`_run_all_job_steps`），报价后仅分镜/时长/策略变化才失效（`cost_service.py`；`test_workflow_cost_feedback.py` 同步 mock 新函数）。
2. **storyboard 序列化漏 `Shot.selected_artifact_id`**：feedback 已正确写库（E2E DB 直查确认），但 GET storyboard 的 shot 载荷不含该字段，前端无法展示 shot 级"已选定"。修复：`storyboard_service.py` 补字段。

两处修复后 `pytest tests/` 保持 284 passed，E2E 复跑至 51/51。

---

## 二、复审第零节逐项状态表

### 0.2 P0 复审状态

| 条目 | 状态 | 证据 | 遗留 |
|------|------|------|------|
| P0-1 MiniMax H3 Adapter | ⚠️ 部分修复（风险收敛） | H3 已默认禁用 + 路由守卫，balanced 不再默认路由；协议仍无法按公开文档核验 | 无 API key、协议未核验，未真实联调 |
| P0-2 老数据库迁移 | ✅ 已修复 | `TEST_DATABASE_URL` 指向真实 PG 跑 `test_migrations_integration.py` 3 passed（空库/0001 库/历史无版本库 → head=0005） | — |
| P0-3 Artifact 文件权限 | ✅ 已修复 | `/storage` 项目级鉴权；E2E：无 token /storage → 401、无 token download → 401、bob 下载他人 → 404 | 老 `/storage` `?token=` 兼容保留（受控） |
| P0-4 Approval 跨项目写入 | ✅ 已修复 | service 层项目一致性；E2E：bob 伪造 project_id 审批 → 403 | — |
| P0-5 Asset 绑定其他项目 Shot | ✅ 已修复 | `bind_asset_to_shot` 同项目校验 + 单测 | — |
| P0-6 Canvas 绑定越权 | ✅ 已修复 | owner/无主/已绑定校验 + 单测 | — |

### 0.3 新增 P0：Feedback 跨项目改 Step

✅ 已修复。`record_feedback` 强校验 step→run.project_id == artifact.project_id 且 artifact 为该 step 关联 job 产物；Shot 级选择走 `Shot.selected_artifact_id`（0005）。E2E：伪造他项目 step_id → 403；真实 PG 集成：`test_tenancy_integration.py` 15 passed（跨用户/跨项目/跨 step 用例）。

### 0.4 新增 P0：无主 SeriesProject

✅ 已修复。普通用户访问 `user_id=NULL` 项目 403/404（E2E：bob 访问无主项目 → 403）；admin 认领接口 `POST /series/projects/{id}/claim`（E2E：bob 非 admin claim → 403，admin claim 给 bob → 200，bob 之后可访问）；一次性批量认领脚本 `backend/scripts/claim_orphan_projects.py`（--dry-run 可用）；归属校验已收敛为统一 helper（`series_service.get_project` admin/owner 语义）。

### 0.5 P1 复审状态

| 条目 | 状态 | 证据 | 遗留 |
|------|------|------|------|
| P1-1 真实图片生成 | ⚠️ 部分修复 | 火山图片 Provider 已接入 ImageProvider 接口 | 无 API key 未联调 |
| P1-2 首帧传递 | ✅ 已修复 | 首帧优先 Shot 选定 image artifact，回退最新成功；E2E：3 个 video job `params.first_frame.storage_key` 非空 | — |
| P1-3 Mock 视频静默兜底 | ✅ 已修复 | `ALLOW_MOCK_PROVIDERS` 默认 false，`no_provider` 终态 | — |
| P1-4 完整费用确认 | ✅ 已修复 | 所有收费节点统一 Cost Gate；E2E：确认前 jobs 为空（image 也不建）、缺 quote_id → 422、错误 quote_id → 409、正确 → 200 后展开 | — |
| P1-5 累计 Cost Limit | ⚠️ 基本修复 | 累计判定 + `reserve_budget` 原子预留（重试不足 409 budget_exceeded） | Provider 回退的预算重检为粗估 |
| P1-6 Step 重试恢复 Run | ✅ 已修复 | 当前 attempt 聚合（step_attempt 过滤）；E2E + `test_retry_integration.py` 2 passed（真实 PG） | — |
| P1-7 Job 重试外部任务 | ✅ 已修复 | 新 job 不带 external_task_id，旧 job 回填 superseded_by 退出聚合；E2E：旧 failed 不拖累，step/run 恢复 succeeded | — |
| P1-8 终态失败回推 | ✅ 已修复 | `finalize_job` 统一终态回推 | — |
| P1-9 Job 取消回推 | ⚠️ 基本修复 | cancel 原子条件更新 + 回推 | Provider cancel 未实现 |
| P1-10 Pause Run | ⚠️ 基本修复 | lease 排除 paused/canceled；E2E：paused run jobs 0→0 | 已 lease 未提交的 job 不二次校验（submit 前检查未做） |
| P1-11 Final Render 黑场 | ✅ 已修复 | final 缺镜头即 failed；E2E：is_publishable=true，ffprobe 7.30s ≈ 3+3+4s | — |
| P1-12 TTS 闭环 | ⚠️ mock 链路已通 | E2E：3 个 tts job succeeded、对白轨回写 audio_storage_key、进时间线混音 | 真实 Qwen TTS 未联调；TTS 单价占位；clone_voice 占位 |

### 0.6 费用确认

✅ 已修复（1~4）。（1）所有收费 Typed Node 统一 Cost Gate（E2E 确认前 0 job）；（2）confirm-cost 必携 quote_id；（3）quote 保存 input_hash（分镜 revision/shot 时长/候选数/policy/价格表版本）+ expires_at（30 分钟）；（4）展开前复核 quote + `reserve_budget` 原子预留。（5）重试经 `estimate_job_retry_cost` + reserve_budget 消耗预留。**注意**：本次 E2E 发现复核 hash 基于"未完成 steps"导致部分步骤完成后误失效，已改为全量 steps 计算（见第一节）。

### 0.7 Step/Job 重试

✅ 已修复。（1）`step_attempt` + `logical_job_key` 字段（0005）；（2）聚合只统计 `step_attempt == step.attempt`；（3）job 级 retry 回填 `superseded_by`，同 logical key 只取最新一代；（4）真实数据库测试 `test_retry_integration.py` 2 passed；E2E 完整走通"旧 job failed → retry → 新 job succeeded → step succeeded → run 恢复 succeeded"。

### 0.8 P2 复审状态

| 条目 | 状态 | 证据 | 遗留 |
|------|------|------|------|
| P2-1 LLM Agent Planner | ✅ 已修复（入口级） | `use_llm_planner` 接入创建 run；`extra_nodes` 经 `apply_extra_nodes` 落地 DAG（`test_p2_planner_personal_assets.py`） | 默认仍规则版 |
| P2-2 Artifact Feedback | ✅ 已修复 | 跨项目漏洞修复（0.3）；Shot 级选择写入并被 Planner/Renderer 消费；manifest.artifact_selection 记录来源（E2E selections=['latest'×3]，feedback 后 Shot.selected_artifact_id 写入） | — |
| P2-3 Personal Asset | ✅ 已修复 | `/api/assets/personal` 创建/列表/版本/copy-to-project；E2E：bob 创建、列表=1、admin 隔离不可见、copy 到认领项目 | 真实 PG 上 personal 隔离集成测试未补（E2E 已覆盖） |
| P2-4 最新 Workflow Run | ✅ 已修复 | `runs[0]` | — |
| P2-5 Job 进度 | ✅ 已修复 | 0-100 直用 | — |
| P2-6 费用确认 UI | ✅ 已修复 | 报价展示 + quote_id 确认闭环（后端契约已变为必携 quote_id） | 前端无运行时手测 |
| P2-7 WebSocket Canvas 权限 | ✅ 已修复 | 4404/4403 区分 | 历史无主 Canvas 认领迁移未提供 |
| P2-8 Bundle 体积 | ⚠️ 部分修复 | 路由级 lazy import | 仍有 >500KB chunk（Excalidraw/Mermaid 相关） |

### 0.9 MiniMax Provider

⚠️ 风险已收敛但未联调：H3 协议按公开文档无法核验（`/v2/video_generation`、`MiniMax-H3`、`/v2/query/...` 均无官方佐证），已默认禁用 + 路由守卫，balanced 不默认路由；待官方协议确认 + 真实 key 联调前仍不能视为接入完成。

### 0.10 测试与构建

| 检查 | 本次结果 |
|------|----------|
| `pytest tests/` | **284 passed, 22 skipped**（首轮基线 201 → 复审整改后 284） |
| `ruff check app tests` | 通过 |
| 真实 PG 迁移 | ✅ `test_migrations_integration.py` 3 passed（head=0005） |
| 真实 PG 权限/重试 | ✅ tenancy 15 passed、retry 2 passed |
| mock 全链路 E2E | ✅ **51/51**（见第三节） |
| MiniMax/火山图片/Qwen TTS 沙箱 | 未执行（无 API key） |
| 前端运行时手测 | 未执行 |

---

## 三、E2E 回归实录（2026-08-04 复审整改后）

### 3.1 真实 PG 集成测试

```
TEST_DATABASE_URL=.../fishstudio_test_mig      pytest tests/test_migrations_integration.py → 3 passed
TEST_DATABASE_URL=.../fishstudio_test_tenancy  pytest tests/test_tenancy_integration.py   → 15 passed
TEST_DATABASE_URL=.../fishstudio_test_retry    pytest tests/test_retry_integration.py     → 2 passed
```

迁移测试验证 head=0005（重试代际字段 + Shot 级选择）在真实 PG 上 upgrade 成功；三个测试库用后均已 drop。

### 3.2 mock 全链路 E2E（真实 PG fishstudio_e2e3 + uvicorn + EMBEDDED_WORKER）

脚本：`backend/scripts/e2e_series_regression.py`（可重复执行，`E2E_DB` 环境变量指定库）。最终结果 **51/51 通过**：

- 流程：登录 → 建项目 → 建 run → complete outline → import-text 3 镜头 → PATCH 3 条对白轨 → 角色资产+分镜审批 → generation-gate ok → complete asset.character.generate → cost-quote → confirm-cost → run 自动推进到终态。
- **费用闭环**：确认前 `GET /api/jobs` 为空（image/video/tts 均不建）；quote 含 quote_id/expires_at，ops={generate_image, generate_video, tts, render_episode}，total=5.45；缺 quote_id → 422、错误 quote_id → 409、正确 quote_id → 200 后展开。
- 终态：run succeeded，10 个 step 全 succeeded，10 个 job 全 succeeded（3 image + 3 video + 3 tts + 1 render）。
- 首帧：3 个 video job `params.first_frame.storage_key` 非空；TTS：3 条对白轨回写 audio_storage_key；每镜头 {image, video, audio} artifacts 齐全。
- 渲染：final mp4 158KB，ffprobe Duration 7.30s（镜头 3+3+4s）；`is_publishable=true`；manifest.shots[].artifact_selection=['latest','latest','latest']。
- **选定产物**：feedback selected（无 step_id）→ 200，`Shot.selected_artifact_id` 写入并在 GET storyboard 可见。
- **重试闭环**：video job 置 failed + step/run 置 failed → POST retry → 新 job（`params.retry_of_job_id` 指回旧 job，旧 job `superseded_by`=新 job、状态保持 failed）→ 新 job succeeded → step 恢复 succeeded、run 恢复 succeeded（当前 attempt 聚合，旧 failed 不拖累）。
- **安全**：无 token /storage → 401、download → 401；bob 访问他人 project → 403、下载他人 artifact → 404、伪造 project_id 审批 → 403、访问他人 jobs → 404；feedback 伪造他项目 step_id → 403。
- **无主项目**：DB 直插 user_id=NULL 项目 → bob 访问 403、非 admin claim 403 → admin claim 给 bob → bob(owner) 访问 200。
- **个人资产**：bob 创建 personal 资产 → 列表=1；admin 列表看不到 bob 的（隔离）；copy-to-project 到认领项目 → 新行 project_id 正确。
- **pause/retry_step**：paused run 4s 内 jobs 0→0；failed run retry_step 后恢复推进。
- 清理：uvicorn 已停，fishstudio_e2e3 及三个集成测试库均已 drop，既有 fishstudio 库未动。

E2E 过程中发现并修复 2 个真实 bug（费用 Gate 复核误杀、storyboard 漏 selected_artifact_id 序列化，见第一节），修复后复跑至 51/51。

---

## 四、仍未关闭项清单

1. **真实 Provider 联调**（阻塞"整体完成"）：MiniMax H3（协议未核验，已默认禁用+路由守卫）、火山图片、Qwen TTS 均无 API key 未联调。
2. **TTS 单价占位**：`TTS_PRICE_PER_SECOND` 为占位价格，报价金额待真实定价。
3. **P1-10 残留**：已 lease 未提交的 job 在 pause 后不会在 submit 前二次校验 run 状态。
4. **Provider cancel 能力**：`cancel()` 默认 False，已提交外部任务不取消。
5. **ChatInterface 旧 `/storage` 引用**（`ChatInterface.tsx:1436`）：Excalidraw 图片回输输入框透传旧路径（会作为聊天输入发后端，非 `<img>` 渲染），彻底收口需改为上传转存或 artifact 引用。
6. **真实 PG 上 personal 资产隔离的集成测试未补**（E2E 已覆盖隔离语义，pytest 层缺 tenancy 集成用例）。
7. **前端无运行时手测**：费用确认 UI 契约已变为必携 quote_id（旧无 body 调用 400/422），前端构建通过但未跑浏览器手测。
8. **历史无主 Canvas 数据迁移**：无主 Project 已有批量认领脚本（`scripts/claim_orphan_projects.py`）；无主 Canvas 运行时拒绝（4403），批量认领脚本未提供。
9. **Excalidraw/Mermaid chunk 体积**：仍有 >500KB chunk。
10. **Worker 重启崩溃恢复**：靠 lease 过期回收（单测覆盖），未做真实重启演练。

---

## 五、与复审 0.12"最终验收表"逐条对照

| 完成定义 | 状态 | 说明 |
|----------|------|------|
| 老数据库可无损升级 | ✅ 已满足 | 真实 PG 迁移集成测试 3 passed（head=0005） |
| 不能访问/修改其他用户项目和媒体 | ✅ 已满足 | P0-3/4/5/6 + 0.3/0.4 修复；E2E bob 越权、伪造 step_id、无主项目全拦截 |
| 默认生成不依赖 Mock | ⚠️ 代码层满足 | `ALLOW_MOCK_PROVIDERS` 默认 false；图片 Provider 已接入接口，生产有效性待联调 |
| 真实图片作为首帧传入真实视频 Provider | ⚠️ 链路已通，依赖联调 | mock 下 first_frame 传递已验证（选定产物优先） |
| 收费前展示完整可复现报价 | ✅ 已满足 | quote_id 绑定 + input_hash/expires_at/价格表版本校验；确认前 0 收费 job（E2E） |
| Cost Limit 对累计和并发费用有效 | ⚠️ 基本满足 | 累计判定 + reserve_budget 原子预留；回退/并发边界为粗估 |
| Retry/Pause/Cancel/Worker 重启状态一致 | ⚠️ 基本满足 | retry/pause/cancel/retry_step E2E 全验证；Worker 重启恢复仅单测；pause 后已 lease job 不二次校验 |
| Final Render 不含未声明黑场 | ✅ 已满足 | final 缺镜头即 failed；E2E is_publishable=true |
| 对白/旁白自动 TTS 进入时间线 | ⚠️ mock 链路已通 | 真实 TTS Provider 待联调；TTS 单价占位 |
| 前端最新 Run、进度和费用显示正确 | ⚠️ 代码层满足 | 费用契约已对齐 quote_id；无运行时手测 |
| 真实 PostgreSQL、真实 Provider、完整成片测试通过 | ⚠️ 部分满足 | 真实 PG ✅（迁移/权限/重试/E2E）；mock 完整成片 ✅；真实 Provider ❌（无 key） |

**结论**：复审第零节的代码层问题（0.3/0.4 新增 P0、0.6 费用闭环、0.7 重试聚合、0.8 P2-1/2/3 等）已全部修复并经真实 PG + E2E 验证；剩余缺口集中在依赖真实 API key 的 Provider 联调（MiniMax H3 协议未核验已默认禁用、火山图片、Qwen TTS）及若干工程收尾（pause 二次校验、TTS 单价、前端手测）。当前状态可认定为"Mock 驱动但架构与闭环完整的短剧 MVP"，距离生产发布仍差真实 Provider 联调一项关键工作。
