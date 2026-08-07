# FishStudio 系列视频项目 Review 问题清单

> 最新 Review 日期：2026-08-07
> Review 基线：`docs/series-video-optimization-plan.md` v1.3
> Review 范围：Series/Storyboard/Asset/Approval、Workflow、GenerationJob/Worker、MiniMax H3/火山 Provider、Usage、Timeline、前端短剧工作台
> 首次 Review 结论：领域模型和工作台骨架基本齐全，但仍存在生产发布阻塞问题，当前状态更接近“Mock 驱动的短剧 MVP 骨架”，尚不能认定为完整方案交付。
> 2026-08-04 上一次复审结论：仍不满足要求，问题见后文“附录 A”。
> 2026-08-04 第二次修改后复审结论：代码完整度明显提高，但仍不满足方案 v1.3 的整体完成定义和生产发布验收要求。
> 2026-08-05 第三次修改后复审结论：后端单元测试已通过，但前端无法构建，默认视频异步任务链路、迁移和费用锁定仍存在阻断。
> 2026-08-05 第四次修改后复审结论：已修复前端构建、确定性迁移、Retry 两次提交、图片候选并发上限和外部 task id 持久化；火山视频结果拉取和视频执行快照仍存在阻断。
> 2026-08-05 第五次修改后复审结论：火山 Adapter 签名已修复，迁移测试 Head 已更新，routing snapshot、Retry 行锁和 clone_voice API 均开始接入；locked model 未传入 Provider、worker 访问未定义 decision、clone_voice 存在路径穿越三个 P0 仍存在。
> 2026-08-05 第六次修改后复审结论：第五轮修改在第四轮所在会话中未持久化，代码时间戳不变，三个 P0 原样保留。
> 2026-08-05 第七次修改后复审结论：代码自检认为三个 R2 P0 已修复，剩余真实环境验收问题。
> 2026-08-05 第八次独立复审结论：**Worker 未定义变量和 MiniMax 强制模型已修复，Artifact ownership 与确定性幂等也已加入；但 cheapest 仍会把默认 5 秒镜头锁定到只支持 6/10 秒的 Hailuo Fast，clone_voice 的 storage 根目录在本地常规启动和 Docker 中均计算错误且仍绕过费用确认。因此第七次“P0 全部关闭”的结论不成立，最新结论以下一节为准。**
> 2026-08-05 第九次独立复审结论：**clone_voice 的绝对 storage 根目录、`Path.is_relative_to()` 和 model mismatch 失败处理已经修复；但新加入的 cheapest/Hailuo-02 路由同时存在“5 秒未规范化、按条模型报价为空、Hailuo-02 被误发到 H3 V2、explicit 报价与执行分叉”等确定性错误，clone_voice 仍可绕过费用确认直接创建收费 Job。当前仍不满足整体完成验收，最新结论以下一节为准。**
> 2026-08-05 第十次独立复审结论：**Hailuo-02 已正确注册为 V1、512P 按条报价和 11 秒 fallback 已修复；但 Worker 对所有锁定 Provider 调用新增的 `supports(req, model=...)`，Volcano/Mock 签名未同步，导致默认 balanced/quality 和默认 5 秒 cheapest 主链路直接 `TypeError`。Cost Quote 仍按固定 6 秒路由估价，两条默认 5 秒镜头报价 ¥1.00、Job 实际预留 ¥5.00。clone_voice 新报价接口又因缺少 `Decimal` 导入直接 500，且任意 `vcq:` 字符串都可伪造确认。当前仍不满足整体完成验收，最新结论以下一节为准。**
> 2026-08-05 第十一次独立复审结论：**Provider `supports()` 签名、默认 5 秒逐 Shot 金额、protocol/estimated cost Job 快照和 clone quote 的 Decimal/HMAC 格式均已修复；稳定配置下两条默认 5 秒 Quote 与 Planner 都是 ¥5.00。但 Quote 仍不保存可执行 Shot Route Plan，H3 开关变化时相同 input_hash 的报价会从 Volcano ¥5.00 漂移为 H3 ¥6.00；Volcano submit 仍忽略锁定 Model。clone HMAC 只签随机 nonce，不绑定用户/资产/参考音频/价格/过期时间，也不做预算预留，同一个合法 token 可跨多个资产和参考音频重复创建收费 Job。当前仍不满足整体完成验收，最新结论以下一节为准。**
> 2026-08-05 第十二次独立复审结论：**视频 Quote 已保存 Shot Route Plans，Planner 会消费确认快照，H3 开关已进入 input_hash，explicit MiniMax 非法时长也会在报价阶段拒绝；这些属于实质性修复。但 Volcano 只把返回结果伪装成 locked model，底层工具仍使用 `ACTIVE_VIDEO_MODEL`，DMXAPI 模式可静默执行另一模型；16 秒及更长镜头仍会生成 Volcano 不支持的收费计划。clone GET 已把业务字段和 expires_at 放入签名，但 POST 用固定字符串 `expires_ignored` 重建签名，导致服务端刚签发的合法 Quote 也必然验证失败；同时仍无一次性消费、预算预留和成本审计。当前仍不满足整体完成验收，最新结论以下一节为准。**
> 2026-08-05 第十三次整改后复审结论：**第十二次 Review 的两个 P0 已完成代码级整改。Volcano 锁定模型已实际进入外部请求，Resolver 使用当前 Adapter 的有效模型并拒绝所有 Provider 无法执行的时长；clone_voice 已改为数据库持久化 Quote、HMAC 不透明 Token、行锁一次性消费，并在同一事务中创建 Workflow Run/Step、预留预算、保存 Job 价格快照和 Usage 预留记录。当前代码级 P0 已关闭，但真实 PostgreSQL 迁移、真实 Provider E2E、H3 实验验收和原有前端/音色/并发等 8 个 P1 尚未完成，因此仍不能认定为完整生产验收通过。最新结论以下一节为准。**
> 2026-08-06 第十四次整改后复审结论：**8 个 P1 中 6 个已完成：Qwen 持久化 `voice_id` 复用、Retry 双 Session 并发、图片候选 UI、clone_voice 前端闭环、真实 PostgreSQL 带数据迁移/降级回升、Python 3.13 FFmpeg 音频混音均已通过。剩余 2 个 P1 都依赖真实付费凭证：MiniMax H3 协议/质量/成本基准，以及 MiniMax/火山/DashScope 3～8 镜头真实 E2E 与供应商账单对账。当前无已知代码级 P0；代码和本地基础设施验收通过，生产发布仍需完成两项真实 Provider 验收。**
> 2026-08-06 Hailuo-02 低价路径复测结论：**修复 locked model 分辨率透传后，真实 `MiniMax-Hailuo-02` 首帧图生视频请求已按 `512P / 6 秒` 成功执行。提交 metadata 与供应商状态均为 512P，产物为 512×910、24 FPS、141 帧、约 5.88 秒，可正常解码并抽帧；首、中、末帧差异显著，确认不是静态占位视频。该低价执行路径已通过技术验收；实际扣费仍需在 MiniMax 控制台按 task id 对账。**
> 2026-08-07 本地全链路自测结论：**使用全新 PostgreSQL 数据库和显式 Mock Provider 执行 3 镜头完整 E2E，最终 52/52 项通过。自测过程中修复了“已确认真实 Provider 报价在 Mock 模式下无法执行”以及“视频 submit/success 重复累计预计费用”两个问题，并将 Shot 选择断言更新为图片/视频独立选择槽。当前本地逻辑链路通过；真实 Provider 多镜头 E2E 和账单对账仍属于外部发布门槛。**

---

## 最新结论：2026-08-07 本地全链路自测后复审

> 本轮继续整改第十三次 Review 的 8 个 P1，并执行了真实 PostgreSQL 16 验收。
> 2026-08-06 已配置 `MINIMAX_API_KEY`。首次真实 Hailuo-02 V1 调用暴露 locked model 分辨率未透传问题；修复并补回归测试后，第二次真实调用已按 512P 成功完成，低价路径通过技术验收。
> 2026-08-07 使用全新 PostgreSQL 库完成 3 镜头 Mock 全链路回归，52/52 项通过；本轮发现的 Mock 路由锁定和视频费用重复累计问题均已修复。

### R11.1 最终判断

第十三次 Review 的 8 个 P1 中，以下 6 个已关闭：

1. Qwen TTS 新增持久化音色直接合成路径，`voice_id` 优先于参考音频和临时声音设计；创建音色时的 `target_model` 会随资产和 TTS Job 持久化。
2. Retry 保留 `FOR UPDATE + populate_existing + refresh`，新增真实 PostgreSQL 双 Session API 并发测试；两个请求同时重试时只创建一个新 Job，另一个返回 409。
3. 图片候选使用 `ProtectedImage`，视频候选使用 `ProtectedVideo`；图片/视频均可展开候选并按独立选择槽显示选中状态。
4. clone_voice 前端已接通项目音频上传建档、报价、费用确认、Job 状态、受保护试听、审批和已批准资产新版本流程；审批请求补齐 `project_id`。
5. 全部迁移已在独立 PostgreSQL 16 容器执行空库升级、0001 带数据升级、重复 bootstrap、`0007 → 0006 → 0007` 降级回升；核心历史数据保留。
6. 音频拼接、BGM 循环裁剪和混音已改为 `imageio-ffmpeg` 原生实现，不再依赖 Python 3.13 已移除的 `audioop`；7 个真实媒体测试不再跳过。

仍有 2 个外部环境 P1：

1. MiniMax H3 仍默认关闭；未使用真实 `MINIMAX_API_KEY` 完成 5/10/15 秒协议、成功率、实际时长、质量和费用基准。
2. MiniMax Hailuo-02 512P 单条真实调用已通过，但尚未使用火山/DashScope 凭证执行 3～8 镜头 E2E，也未与三家供应商控制台账单逐请求对账。

**本轮结论：0 个已知 P0、2 个外部环境 P1。代码、本地测试、前端构建和真实 PostgreSQL 验收通过；在两项真实付费 Provider 验收完成前，仍不建议直接宣告完整生产验收通过。**

### R11.2 本轮主要整改

| 项目 | 结果 |
|------|------|
| Qwen 持久音色复用 | ✅ 新增 `qwen_voice_synthesis_tool`，有 `voice_id` 时不再调用 cloning/design。 |
| TTS 模型绑定 | ✅ `voice_model/target_model` 从 clone 结果进入资产、Job 和后续合成请求。 |
| Retry 并发 | ✅ 真实双 Session 同时调用，只创建一个 retry Job，另一个 409。 |
| 图片候选 | ✅ 图片/视频按 operation/type 分别渲染，两个选择槽互不覆盖。 |
| clone 音频上传 | ✅ 新增项目级音频上传，限制类型/25MB，并创建受保护 Artifact。 |
| clone 前端费用闭环 | ✅ 上传 → Quote → Confirm → Job → 试听 → 审批；approved 资产先创建新版本。 |
| 审批请求 | ✅ 前端补传后端必填的 `project_id`，资产版本使用真实 `version`。 |
| Job 轮询 | ✅ 手动创建新 Job 后会按活跃状态重新启动轮询，不再依赖初次挂载时状态。 |
| PostgreSQL 迁移 | ✅ PostgreSQL 16 上 4 个迁移场景和 3 个 Retry 场景全部通过。 |
| Python 3.13 音频 | ✅ FFmpeg 原生拼接、循环、裁剪、混音、淡出和 loudnorm。 |
| H3 验收工具 | ✅ 新增默认只预检、必须显式确认才允许付费调用的基准脚本。 |
| Hailuo-02 低价验收工具 | ✅ 支持 `first_frame + 512P + 6/10s`，单条 6 秒内部估价 ¥0.60。 |
| MiniMax 锁定分辨率 | ✅ `_build_v1_body/_build_h3_body/submit metadata` 均使用 locked model 计算分辨率。 |
| 真实 Provider E2E 工具 | ✅ 现有 E2E 支持 3～8 镜头和 mock/real 模式，真实模式强制禁用 mock。 |

### R11.3 自动化与环境验收

| 检查 | 结果 |
|------|------|
| `ruff check app tests scripts` | ✅ 通过。 |
| `python -m pytest -q` | ✅ `303 passed, 22 skipped, 12 warnings`。 |
| Python 3.13 音频真实执行 | ✅ `7 passed`，包含拼接、crossfade、BGM 循环裁剪、归一化混音。 |
| PostgreSQL 16 迁移与 Retry | ✅ `7 passed`，包含带数据升级、downgrade/upgrade、双 Session 并发。 |
| `alembic heads` | ✅ 唯一 Head 为 `0007`。 |
| `npm run build` | ✅ 通过；仍有既有大 Chunk 警告。 |
| `git diff --check` | ✅ 通过。 |
| Provider 预检 | ✅ `MINIMAX_API_KEY` 已识别且 MiniMax Provider 可用；火山和 DashScope 仍未配置。 |
| MiniMax Hailuo-02 首次 V1 smoke test | ⚠️ submit/query/fetch 成功，耗时 156.608 秒，产物 5.88 秒且可播放；但因 locked model 分辨率未透传，实际输出为 768×1364。该问题已修复。 |
| locked model 分辨率回归 | ✅ 新增测试确认 Hailuo-02 锁定请求体和 metadata 保持 512P；512P 仅允许 Hailuo-02 图生视频。 |
| MiniMax Hailuo-02 512P 复测 | ✅ task id `427824186212640`；请求与供应商状态均为 512P，输出 512×910、24 FPS、141 帧、约 5.88 秒；总耗时 73.75 秒，可正常解码和抽帧，首/中/末帧差确认存在实际动态。内部估价 ¥0.60，实际账单待控制台确认。 |
| 本地 3 镜头 Mock E2E | ✅ 全新 PostgreSQL 库从 `0001` 升级至 `0007` 后执行，52/52 通过；10 个 Job 全部成功，图片/视频/TTS/渲染产物完整，权限、重试、暂停、认领、个人资产和选择槽均通过。 |
| 视频费用事件对账 | ✅ 3 个视频 Quote 合计 ¥7.50，Usage `estimated_cost` 合计 ¥7.50；submit 事件只保留费用快照，不再与终态事件重复累计。 |
| MiniMax H3 真实基准 | ⚠️ 未执行，保持 `MINIMAX_H3_EXPERIMENTAL=false`。 |
| 真实 3～8 镜头 E2E | ⚠️ 未执行。 |

### R11.4 验收命令

默认无费用预检：

```bash
cd backend
python scripts/provider_acceptance.py --durations 5,10,15 \
  --output /tmp/fishstudio-provider-preflight.json
```

Hailuo-02 最低成本真实测试：

```bash
cd backend
python scripts/provider_acceptance.py \
  --execute-hailuo-02 \
  --first-frame /path/to/non-sensitive-test-image.png \
  --hailuo-duration 6 \
  --confirm-paid-calls I_UNDERSTAND_THIS_WILL_CHARGE \
  --output /tmp/fishstudio-hailuo-02-acceptance.json
```

MiniMax H3 真实付费基准必须显式确认：

```bash
cd backend
MINIMAX_H3_EXPERIMENTAL=true \
python scripts/provider_acceptance.py \
  --execute-minimax-h3 \
  --durations 5,10,15 \
  --confirm-paid-calls I_UNDERSTAND_THIS_WILL_CHARGE \
  --output /tmp/fishstudio-minimax-h3-acceptance.json
```

真实 3～8 镜头 E2E 需先用专用 PostgreSQL 启动服务，并确保 `ALLOW_MOCK_PROVIDERS=false`：

```bash
cd backend
E2E_PROVIDER_MODE=real \
E2E_VIDEO_PROVIDER=minimax \
E2E_SHOT_COUNT=3 \
python scripts/e2e_series_regression.py
```

火山链路把 `E2E_VIDEO_PROVIDER` 改为 `volcano`；镜头数应分别覆盖 3 和 8。执行后按报告中的外部 request/task id 与 MiniMax、火山、DashScope 控制台账单逐项核对。

### R11.5 剩余发布门槛

1. 使用测试账号执行 MiniMax H3 5/10/15 秒真实基准，确认 submit/query/fetch 协议、输出时长、质量、费用和失败分类。
2. 分别用 MiniMax 与火山视频链路执行 3 镜头和 8 镜头真实 E2E；DashScope TTS/clone 同时开启，记录外部请求 ID。
3. 对比内部 Quote、Job 快照、Usage estimated/actual 与三家供应商账单；重点核对首次 768P task `427809434693932` 和本次 512P task `427824186212640` 的实际扣费。

---

## 历史结论：2026-08-05 第十三次整改后复审

> 本轮直接整改了第十二次 Review 的两个 P0，并补充关键自动化测试。
> 本轮新增数据库迁移 `0007_clone_voice_quote_ledger.py`，部署前必须执行 `alembic upgrade head`。

### R10.1 最终判断

第十二次 Review 中的两个生产阻断已完成代码级关闭：

1. Volcano Adapter 已将 locked model 传入 `submit_video_generation_task()`，底层火山直连和 DMXAPI Payload 都使用传入模型。
2. 视频 Resolver 使用当前 Adapter 的 `ACTIVE_VIDEO_MODEL`，DMXAPI 模型也有对应价格快照。
3. routing fingerprint 已包含独立 routing version、H3 有效开关、火山/DMXAPI Base URL 与模型、MiniMax Base URL 与各模型、价格表版本。
4. 4～12 秒可路由 Volcano；H3 开启时 4～15 秒可路由 MiniMax；16 秒及更长请求在报价阶段直接拒绝，不再生成不可执行收费计划。
5. Worker 会先应用 Route Plan 的 billable duration，再校验 locked capability，并优先持久化 Quote 中的 protocol。
6. MiniMax 未知 locked model 现在严格拒绝，不再被误判为 H3。
7. clone GET Quote 会写入 `clone_voice_quotes`，Quote 保存用户、项目、资产版本、参考音频、Provider、Model、价格版本和过期时间。
8. clone Confirm 使用 HMAC 不透明 Token 定位数据库 Quote，通过 `SELECT FOR UPDATE` 验证绑定字段、过期时间和 `consumed_at`，重复请求只返回原 Job。
9. clone Confirm 在同一事务中创建 Workflow Run/Step、调用 `reserve_budget()`、创建锁定 Provider/Model 的 Job、写入价格快照、消费 Quote 并记录 Usage 预计费用。
10. clone Worker 不再把锁定的 qwen_tts 静默降级为 mock；成功后写入 actual_cost，预留事件与结算事件不会重复累计 estimated_cost。

**本轮结论：代码级 P0 已关闭；剩余 0 个已知 P0、8 个 P1。由于真实 PostgreSQL 和真实 Provider 验收尚未执行，整体生产验收仍暂不通过。**

### R10.2 本轮修改范围

- `backend/app/providers/pricing.py`
- `backend/app/providers/minimax_video.py`
- `backend/app/services/cost_service.py`
- `backend/app/providers/volcano_video.py`
- `backend/app/tools/volcano_video_generation.py`
- `backend/app/worker/generation_worker.py`
- `backend/app/routers/series.py`
- `backend/app/db/models.py`
- `backend/alembic/versions/0007_clone_voice_quote_ledger.py`
- `backend/tests/test_providers_jobs.py`
- `backend/tests/test_clone_voice_and_selection.py`
- `backend/tests/test_migrations_integration.py`

### R10.3 视频执行快照整改结果

| 检查 | 结果 |
|------|------|
| Volcano locked model → Adapter | ✅ Provider 调用时传入 locked model。 |
| Adapter → 外部 Payload | ✅ 火山直连和 DMXAPI 均消费传入 model。 |
| DMXAPI Route Plan | ✅ 使用有效 `ACTIVE_VIDEO_MODEL`，不再硬编码直连模型。 |
| DMXAPI 价格快照 | ✅ `doubao-seedance-1-5-pro-responses` 已进入价格表。 |
| 16 秒 balanced/quality/cheapest | ✅ 报价阶段统一抛出无可执行 Provider，不创建收费计划。 |
| 未知 MiniMax locked model | ✅ capability 校验直接拒绝。 |
| protocol 持久化 | ✅ 优先使用确认 Route Plan 的 protocol。 |
| 完整 routing fingerprint | ✅ Provider 配置、模型、Base URL、H3 和 routing version 均进入 input hash。 |

### R10.4 clone_voice 费用事务整改结果

新增 `clone_voice_quotes` 持久化账本，关键字段包括：

- `user_id`、`project_id`、`asset_id`、`asset_version`；
- `reference_artifact_id`、`provider`、`model`；
- `price`、`currency`、`price_table_version`；
- `expires_at`、`consumed_at`、`consumed_job_id`、唯一 `nonce`。

Confirm 主路径现在为：

```text
验证 HMAC Token
  → SELECT Quote FOR UPDATE
  → 校验用户/项目/资产版本/参考音频/过期时间
  → 校验 consumed_at
  → 创建 Workflow Run/Step
  → reserve_budget
  → 创建带完整价格快照的 clone Job
  → 写 UsageEvent(reserved estimated_cost)
  → 标记 Quote consumed_at/consumed_job_id
  → 单事务 commit
```

Worker 成功后另写 actual_cost；Quote 已存在预留 Usage 时，结算事件不再重复写 estimated_cost。锁定 qwen_tts 临时不可用时直接失败并要求重新报价，不会偷偷切换到 mock Provider。

### R10.5 自动化验证

| 检查 | 结果 |
|------|------|
| 合法 clone GET Quote → 原样 POST | ✅ 创建一个 Job、Run、Step、Usage，并消费 Quote。 |
| 同一 clone Quote 重复 Confirm | ✅ 返回原 Job，不创建第二个收费 Job。 |
| clone Token 篡改 | ✅ HMAC 校验拒绝。 |
| Volcano locked model 透传 | ✅ 测试确认底层收到锁定模型。 |
| 16 秒三种策略 | ✅ balanced/quality/cheapest 全部拒绝。 |
| MiniMax 未知 locked model | ✅ 拒绝。 |
| `ruff check app tests` | ✅ 通过。 |
| `python -m pytest -q` | ✅ `291 passed, 22 skipped, 12 warnings`。 |
| `npm run build` | ✅ 通过；仍有既有大 Chunk 警告。 |
| 真实 PostgreSQL migration | ⚠️ 未设置 `TEST_DATABASE_URL`，迁移集成测试仍跳过。 |
| 真实 Provider E2E | ⚠️ 未执行。 |

### R10.6 剩余 8 个 P1

1. Qwen TTS 仍未真正用持久化 `voice_id` 直接合成，每条对白可能继续设计/克隆音色。
2. Retry 行锁仍缺 identity map refresh 和真实双 Session API 并发测试。
3. 图片候选 UI 和图片独立选中状态的完整交互仍未完成。
4. clone_voice 前端上传、报价、试听、审批和资产新版本闭环仍未完成。
5. H3 默认关闭，仍缺真实 API、协议和质量/成本基准验收。
6. `0007` 及全部历史迁移尚未在真实 PostgreSQL 带数据升级和 downgrade 场景验证。
7. 真实 MiniMax/火山/DashScope 3～8 镜头 E2E 和供应商账单对账尚未执行。
8. Python 3.13 音频混音测试仍因 pydub/audioop 不可用而跳过。

### R10.7 下一步验收顺序

1. 在专用 PostgreSQL 测试库执行 `alembic upgrade head`、重复 upgrade 和 downgrade/upgrade 回归。
2. 配置真实火山直连与 DMXAPI，确认 Quote locked model、外部 Payload model 和返回账单一致。
3. 使用合法参考音频执行真实 qwen clone，核对 Quote、Job、Usage estimated/actual 和供应商账单。
4. 完成 Qwen 持久化 voice_id 复用及 clone 前端闭环。
5. 完成 Retry 双 Session 并发、图片候选 UI、H3 基准和 3～8 镜头完整 E2E。

---

## 最新结论：2026-08-05 第十二次独立复审

> 本轮实际修改了 `pricing.py`、`volcano_video.py`、`cost_service.py`、`job_planner.py` 和 `series.py`。
> 本轮没有新增或修改测试、Worker、MiniMax Provider、Qwen TTS、Retry、前端、数据库模型或迁移。

### R9.1 最终判断

第十一次 Review 的视频费用快照问题已有明显进展：

1. Cost Quote 增加 `ShotRoutePlan`，记录 shot_id、Provider、Model、Resolution、Protocol、请求/计费时长、预计总价和价格快照。
2. Job Planner 会从已确认 Quote 按 step/shot 读取 Route Plan，不再正常情况下重新路由。
3. input hash 已加入 H3 实验开关，H3 配置变化后旧 Quote 会失效。
4. cheapest + H3 的 5 秒不再无必要提升为 6 秒。
5. explicit MiniMax 在 H3 关闭且时长不是 6/10 秒时，会在报价阶段直接拒绝。
6. Volcano 返回结果会报告调用方传入的 locked model，不再立即触发 Worker 的 metadata mismatch。
7. clone GET Quote 已验证参考音频，并把 user/project/asset/version/reference/provider/price/version/expires_at/nonce 纳入签名原文。

但是当前仍有两个生产阻断：

- **P0-1：Volcano 的“锁模”只改返回字段，没有改变底层实际请求模型；Provider 配置也没有完整进入路由指纹。长镜头仍可获得不可执行的 Volcano Route Plan。**
- **P0-2：clone GET 和 POST 使用不同 HMAC Payload，合法 Quote 也必然失败；且仍无持久化、一次性消费、预算预留和 Usage 成本闭环。**

**本轮结论：不通过整体完成验收；仍有 2 个 P0、8 个 P1。**

### R9.2 本轮已确认修复

| 项目 | 状态 | 复核结果 |
|------|------|----------|
| Quote Shot Route Plans | ✅ | CostQuoteItem 已保存逐 Shot 完整计划。 |
| Planner 消费确认计划 | ✅ | 正常已确认路径按 shot_id 使用 Quote plan。 |
| H3 开关 input hash | ✅ | H3 false/true 的相同项目 hash 已不同。 |
| H3 5 秒时长 | ✅ | 保持精确 5 秒，不再提升到 6 秒。 |
| explicit MiniMax 非法 5 秒 | ✅ | H3 关闭时报价阶段抛 ValueError。 |
| clone GET 业务字段 | ✅ | GET 验证 reference Artifact，并构造完整签名原文。 |
| Volcano 实际锁模 | ❌ | 底层工具不接收 locked model，仍使用全局 ACTIVE_MODEL。 |
| clone 合法 Quote | ❌ | GET/POST 签名原文不一致，合法 token 无法使用。 |

### R9.3 P0-1A：Volcano 仅伪装返回 Model，外部请求仍使用 ACTIVE_VIDEO_MODEL

**位置**

- `backend/app/providers/volcano_video.py:36`
- `backend/app/providers/volcano_video.py:43`
- `backend/app/tools/volcano_video_generation.py:35`
- `backend/app/tools/volcano_video_generation.py:41`
- `backend/app/tools/volcano_video_generation.py:292`
- `backend/app/providers/pricing.py:157`

Volcano Adapter 当前改为：

```python
active_model = model or vvg.ACTIVE_VIDEO_MODEL
```

并把 `active_model` 写入 `ProviderResult.model`。但是实际调用仍是：

```python
vvg.submit_video_generation_task(
    prompt=...,
    duration=...,
    ...,
)
```

底层函数没有接收 locked model 参数，而是继续根据模块全局配置选择 `ACTIVE_VIDEO_MODEL`。因此 Adapter 只是把返回 metadata 标成 locked model，没有锁住外部请求。

本轮无网络探针：

```text
Quote / Job locked and reported model:
  doubao-seedance-1-5-pro

External tool ACTIVE_VIDEO_MODEL:
  doubao-seedance-1-5-pro-responses
```

在 `VOLCANO_USE_DMXAPI=true` 或 dmxapi.cn Base URL 下，实际外部请求仍使用 `doubao-seedance-1-5-pro-responses`，但 ProviderResult 报告 `doubao-seedance-1-5-pro`。Worker 的 model mismatch 检查现在也无法发现该漂移，费用和 Usage 会按错误模型归因。

同时 `_routing_config_fingerprint()` 只包含：

```text
MINIMAX_H3_EXPERIMENTAL
PRICE_TABLE_VERSION
```

没有包含 `VOLCANO_USE_DMXAPI`、`VOLCANO_BASE_URL`、`VOLCANO_VIDEO_MODEL`、`ACTIVE_VIDEO_MODEL`、MiniMax 配置模型名或显式 routing version。Provider 配置变化仍不会使旧 Quote 失效。

**整改要求**

1. `submit_video_generation_task()` 增加明确 model 参数，火山直连和 DMXAPI 分支都必须使用传入的 locked model；不支持时在 submit 前失败。
2. Resolver 不应硬编码一个火山模型，应读取 Adapter 的真实 capability/model identity，并为 DMXAPI 配置独立价格项。
3. `VolcanoVideoProvider.supports(req, model=...)` 必须验证 model 与当前实际后端兼容，不能无条件忽略。
4. routing fingerprint 加入 Provider adapter/version、Base URL 类型、active model、H3/MiniMax model 配置和独立 `ROUTING_VERSION`。
5. 增加火山直连和 DMXAPI 两套锁模/报价/Usage 测试，断言外部 Payload 的 model 与 Quote 完全一致。

### R9.4 P0-1B：13 秒以上镜头仍会生成 Volcano 不支持的收费 Route Plan

**位置**

- `backend/app/schemas/series.py:68`
- `backend/app/providers/volcano_video.py:24`
- `backend/app/providers/volcano_video.py:36`
- `backend/app/providers/pricing.py:168`
- `backend/app/providers/pricing.py:170`
- `backend/app/providers/pricing.py:177`

Shot Schema 允许最长 60 秒，但 Volcano capability 只允许 2～12 秒。当前 balanced/quality 始终选择 Volcano；H3 关闭时 cheapest 的非 6/10 秒也选择 Volcano，没有在 resolver 中检查 12 秒上限。

本轮 16 秒探针：

```text
balanced -> Volcano 16s, quoted ¥8.00, supports=False
quality  -> Volcano 16s, quoted ¥8.00, supports=False
cheapest -> Volcano 16s, quoted ¥8.00, supports=False
```

系统会向用户展示并允许确认一个不可执行的收费计划，然后 Worker 才返回 `routing_snapshot_invalid`。

H3 开启时 cheapest 16 秒会被规范化为 15 秒，但 Worker 仍在覆盖 billable duration 之前按原始 16 秒调用 locked capability，当前顺序同样会拒绝。

**整改要求**

1. Resolver 必须使用真实 Provider capability 枚举候选，不允许产生 `supports=False` 的 plan。
2. 13～60 秒镜头应明确选择：支持长时长的 Provider、拆分生成后拼接，或在 Storyboard/报价阶段拒绝并给出可操作提示。
3. 所有 duration 规范化必须先完成，再调用 capability 校验；requested/generated/billable duration 同时保留。
4. 增加边界 2/3/4/5/6/10/12/13/15/16/60 秒的 policy × Provider 参数化测试。

### R9.5 P0-2A：clone GET/POST HMAC 原文不一致，合法 Quote 必然失败

**位置**

- `backend/app/routers/series.py:570`
- `backend/app/routers/series.py:609`
- `backend/app/routers/series.py:611`
- `backend/app/routers/series.py:650`
- `backend/app/routers/series.py:672`
- `backend/app/routers/series.py:674`

GET 签名原文包含真实过期时间：

```text
user:project:asset:version:reference:provider:price:CNY:price_version:<expires_at>:nonce
```

但是返回的 quote_id 只有：

```text
vcq:<signature>:<nonce>
```

POST Request Schema 也没有 `expires_at` 字段，无法重建 GET 原文。POST 实际使用固定字符串：

```text
...:expires_ignored:<nonce>
```

所以 GET 与 POST 的 HMAC 输入永远不同。本轮把 GET 返回的合法 quote_id 原样传给同一 user/project/asset/reference 的 POST，结果：

```text
HTTP 400
quote 签名验证失败（业务字段不匹配或已过期），请重新获取
```

当前 clone_voice 不是“安全可用”，而是所有真实请求都会被拒绝。

**整改要求**

1. 不要依靠客户端另外记忆未编码的 expires_at；使用结构化签名 Token/JWT，把完整 Payload base64url 编码后签名，POST 解码并验证。
2. 或使用持久化 Quote 表，POST 只传不可猜 quote_id，由服务端读取业务字段和 expires_at。
3. 增加 GET Quote → 原样 POST 的最基本成功测试，并覆盖篡改任一字段、过期和跨用户。

### R9.6 P0-2B：clone 仍无一次性消费、预算预留和成本审计

即使修复 HMAC 原文，当前实现仍没有：

- Quote 数据库存储或 consumed_at；
- nonce 使用记录和重复消费拦截；
- `reserve_budget()`；
- Workflow Run/Step 或收费 Typed Node；
- Job params 中的 price、price snapshot、price table version、expires_at；
- clone UsageEvent 的 estimated_cost/actual_cost。

同一合法 Token 理论上仍可被并发重复使用；idempotency 只约束同一 asset/version/reference，换资产或 reference 就能产生新的收费 Job。

整改：Confirm、nonce 消费、预算预留、Job 创建必须在同一数据库事务/行锁中完成；任何一步失败都不得消费 Quote 或创建 Job。

### R9.7 Route Plan 实现仍需防御性收口

1. `_confirmed_shot_plans()` 仅检查 cost_quote 是否存在，不检查 `cost_confirmed=True`；直接调用 Planner 时可能消费尚未确认的 Quote。
2. 如果 Quote 中缺少某个 Shot Plan，Planner 会静默重新解析该 Shot，而不是把 Quote 判为损坏/失效；已确认路径必须要求 plans 与当前 shots 一一对应。
3. `routing_config` 尚无独立 routing version 和完整 Provider 配置，部署代码改变路由算法但价格版本不变时，旧 Quote 仍可能通过。
4. Worker 仍没有优先使用 locked `routing_snapshot.protocol` 作为持久化协议；Provider 未返回 protocol 时会默认写 `v1`。
5. MiniMax H3 开启时仍把未知非 V1 Model 当成 H3，未知 locked model 未严格拒绝。

### R9.8 其余 P1 状态

本轮未修改对应文件，上一轮 8 个 P1 均未关闭：

1. Qwen Provider 不消费持久化 voice_id，每条对白继续 clone/design。
2. Retry 行锁缺 identity map refresh 和真实双 Session API 并发测试。
3. 图片候选 UI 未实现，视频选中状态仍可能被图片槽遮蔽。
4. clone_voice 前端上传、报价、试听、审批和资产新版本闭环缺失。
5. H3 默认关闭且无真实协议/API/质量成本基准验收。
6. 真实 PG 带数据迁移/downgrade 测试未执行。
7. 真实 MiniMax/火山/DashScope 3～8 镜头 E2E 和账单对账未执行。
8. Python 3.13 音频混音测试仍因 pydub/audioop 不可用而跳过。

### R9.9 本轮验证结果

| 检查 | 结果 |
|------|------|
| Quote route_plans | ✅ 已保存逐 Shot 完整计划。 |
| Planner 消费 route_plans | ✅ 正常确认路径会使用计划。 |
| H3 开关 input hash | ✅ false/true hash 不同。 |
| H3 5 秒 plan | ✅ 精确 5 秒、¥2.50。 |
| Volcano result locked model | ⚠️ 返回字段一致，但外部工具仍使用不同 ACTIVE_MODEL。 |
| 16 秒 balanced/quality/cheapest | ❌ 均生成 Volcano 16 秒计划，supports=False。 |
| clone GET Quote | ✅ 可生成业务字段签名。 |
| clone 合法 Quote POST | ❌ 原样 Token 仍返回签名失败。 |
| clone 一次性/预算/成本 | ❌ 均未接入。 |
| 新增测试 | ❌ 本轮没有任何测试文件修改。 |
| `ruff check app tests` | ✅ 通过。 |
| `python -m pytest -q` | ✅ `284 passed, 22 skipped, 12 warnings`。 |
| `npm run build` | ✅ 通过；前端无修改。 |
| 真实 PG/Provider | ⚠️ 未执行。 |

### R9.10 下一轮最小整改顺序

1. 先让 clone GET Quote → POST Confirm 的合法主路径通过，并改为可解码 Payload 或持久化 Quote。
2. clone 增加一次性消费、过期校验、预算预留、价格快照和 Usage 成本事务。
3. 把 locked Volcano model 真正传入底层外部 Payload，并将 active adapter/model 纳入 resolver 和 input hash。
4. Resolver 拒绝或拆分所有 Provider 不支持的长时长镜头，禁止生成不可执行 Quote。
5. Route Plans 必须在已确认状态下一一匹配当前 shots，缺失/重复立即使 Quote 失效。
6. 为本轮所有探针增加测试，再处理剩余 8 个 P1 和真实环境验收。

---

## 最新结论：2026-08-05 第十一次独立复审

> 本轮实际修改了 Provider 抽象/Volcano/Mock、视频价格路由、费用报价、Job Planner、clone_voice Router 和 1 个既有测试文件。
> 本轮没有修改 Worker、MiniMax supports、Qwen TTS、Retry、前端、Workflow Typed Node 或迁移文件。

### R8.1 最终判断

第十次 Review 的三个直接运行时问题已经修复：

1. `VideoProvider`、Volcano、Mock 的 `supports(req, *, model=None)` 签名已统一，默认 Volcano Job 不再因关键字参数抛 `TypeError`。
2. 视频 Cost Quote 已改为逐 Shot 调 resolver；稳定 H3 配置下，两条默认 5 秒 cheapest Quote 和 Planner 预算均为 ¥5.00。
3. Job routing snapshot 已补 `protocol` 和 `estimated_total_cost`。
4. clone quote 已补 `Decimal` 导入；无签名的伪造 `vcq:` 字符串会被拒绝。
5. explicit 缺 Provider、未知 Provider 已在报价阶段抛错。

但费用确认仍没有锁定“用户实际确认的执行计划”：

- **P0-1：Cost Quote 只保存汇总金额和第一个 Provider/Model，不保存每个 Shot 的 Route Plan；Job Planner 仍重新路由。运行配置变化时 input_hash 不变，已确认 Quote 可以执行成另一套 Provider/Model/金额。Volcano Adapter 也仍忽略 locked model。**
- **P0-2：clone quote 的 HMAC 只证明 nonce 由服务端生成，不证明用户确认了哪个资产、参考音频、价格和过期时间；合法 token 可跨资产无限重放，且仍无预算预留或成本审计。**

**本轮结论：不通过整体完成验收；仍有 2 个 P0、8 个 P1。**

### R8.2 本轮已确认修复

| 项目 | 状态 | 复核结果 |
|------|------|----------|
| Provider locked capability 签名 | ✅ | Base/MiniMax/Volcano/Mock 调用兼容。 |
| 默认 5 秒 Quote 金额 | ✅ | 两条 5 秒 cheapest 报价 ¥5.00，与当前配置下 Planner 一致。 |
| Job 路由字段 | ✅ | 已写入 protocol、billable duration、estimated total cost、price snapshot。 |
| Hailuo-02 V1/512P/per-piece | ✅ | 保持上轮正确结果。 |
| clone quote GET | ✅ | 不再因 `Decimal` 缺失返回 500。 |
| clone 随机伪造签名 | ✅ | 任意自造签名不能通过 `compare_digest`。 |
| Quote 可执行快照 | ❌ | CostQuote 不含 shot route plans，Planner 重新解析。 |
| clone Quote 业务绑定 | ❌ | HMAC 未覆盖业务字段、过期时间或消费状态。 |

### R8.3 P0-1A：视频 Quote 未冻结 Shot Route Plan，配置变化后同一 input_hash 可换模型和金额

**位置**

- `backend/app/services/cost_service.py:72`
- `backend/app/services/cost_service.py:200`
- `backend/app/services/cost_service.py:258`
- `backend/app/services/job_planner.py:233`
- `backend/app/services/job_planner.py:256`
- `backend/app/providers/pricing.py:135`

`_estimate_step_item()` 现在确实逐 Shot 算出了正确总金额，但 `CostQuoteItem` 仍只有：

```text
provider / model / resolution
unit_count / unit_price / subtotal
```

没有 `route_plans` 或 shot 级明细。混合 5 秒和 6 秒 cheapest 的探针结果：

```text
真实路由：
  shot 5s -> Volcano 768P, ¥2.50
  shot 6s -> Hailuo-02 512P, ¥0.60

Quote 展示：
  provider=volcano
  model=doubao-seedance-1-5-pro
  resolution=768P
  unit_count=2
  average unit_price=¥1.55
  subtotal=¥3.10
```

金额正确，但用户看不到第二个 Shot 使用 Hailuo-02，也没有可供 Job Planner 消费的逐 Shot 快照。

Job Planner 在费用确认后仍再次调用 `resolve_video_routing()`。`input_hash` 虽包含 Shot 时长和 policy，却没有完整 Route Plan，也没有 H3 实验开关、Provider 配置/路由版本。`_compute_input_payload()` 中的 model 信息仍来自固定 `duration=6` 的 `_video_unit_price()`，不能代表每个 Shot 的实际路由。

本轮 H3 配置漂移探针：

```text
同一 run / 同一 shots / 同一 policy / 同一 PRICE_TABLE_VERSION

MINIMAX_H3_EXPERIMENTAL=false:
  input_hash=9d1c...9bbd
  Quote total=¥5.00
  route=2 × Volcano 5s

MINIMAX_H3_EXPERIMENTAL=true:
  input_hash=9d1c...9bbd   # 完全相同
  Quote/Planner total=¥6.00
  route=2 × MiniMax-H3 6s
```

因此用户可以在 H3 关闭时确认 ¥5.00 Volcano Quote，部署/环境切换 H3 后，`validate_cost_quote()` 仍因 input_hash 相同而通过，Job Planner 随后重新解析为 ¥6.00 H3。费用 Gate 没有锁住用户确认的 Provider、Model、时长和金额。

**整改要求**

1. `CostQuoteItem` 增加不可变 `route_plans: list[ShotRoutePlan]`，绑定 shot_id、mode、Provider、Model、Resolution、Protocol、requested/generated/billable duration、estimated total cost 和价格快照。
2. Route Plan 增加 `routing_version`/capability version，并把 H3 开关、Provider 模型配置等影响路由的输入纳入 input hash。
3. Job Planner 必须从 `run.input_snapshot.cost_quote.items[*].route_plans` 按 shot_id 读取已确认计划，不再调用 resolver。
4. 已确认 plan 对当前 Shot/首帧输入不再适用时，应使 Quote 失效并重新报价，不能静默 reroute。
5. 增加“报价后 H3 开关/Provider model/env/路由版本变化”的测试，确认旧 Quote 被拒绝或仍严格执行旧 plan。

### R8.4 P0-1B：Volcano submit 仍忽略 locked model，DMXAPI 模式会收费后 model mismatch

**位置**

- `backend/app/providers/volcano_video.py:36`
- `backend/app/providers/volcano_video.py:43`
- `backend/app/tools/volcano_video_generation.py:35`
- `backend/app/tools/volcano_video_generation.py:41`
- `backend/app/worker/generation_worker.py:282`
- `backend/app/worker/generation_worker.py:300`

Volcano `supports()` 虽接受 model 参数，但不检查它；`submit()` 更直接覆盖传入值：

```python
async def submit(..., model=None):
    model = vvg.ACTIVE_VIDEO_MODEL
```

报价/Job 固定锁定：

```text
doubao-seedance-1-5-pro
```

当 `VOLCANO_USE_DMXAPI=true` 或 Base URL 为 dmxapi.cn 时，实际模型为：

```text
doubao-seedance-1-5-pro-responses
```

本轮无网络 submit 探针：

```text
locked=doubao-seedance-1-5-pro
actual=doubao-seedance-1-5-pro-responses
submit success=True
```

Worker 会在外部任务已经提交、可能已经产生费用后才标记 `model_mismatch`。这不是锁定执行，而是“先按其他模型收费，再失败”。

整改：resolver 必须根据当前 Volcano/DMXAPI adapter 配置返回真实 active model 和对应价格；Volcano `supports()` 应拒绝不匹配的 locked model，`submit()` 必须使用或严格验证传入 model，禁止静默覆盖。

### R8.5 P0-2：clone HMAC Token 未绑定业务输入、无过期和消费状态，可跨资产重放

**位置**

- `backend/app/routers/series.py:570`
- `backend/app/routers/series.py:593`
- `backend/app/routers/series.py:599`
- `backend/app/routers/series.py:630`
- `backend/app/routers/series.py:669`
- `backend/app/providers/pricing.py:59`

GET 当前签名内容只有随机 nonce：

```text
raw_id=vcq:<random>
signature=HMAC(secret, raw_id)
```

响应中的以下字段没有进入签名，也没有服务端持久化：

- user_id/project_id；
- voice_asset_id；
- reference_artifact_id（当前直接返回空字符串）；
- provider/price/currency/price table version；
- expires_at。

POST 只重新计算 nonce 签名。只要 token 确实由服务端签发，就可以用于任意当前用户拥有的 voice 资产和任意 audio Artifact，并且永久有效、可重复消费。

本轮重放探针：

```text
GET quote for: voice-A
reference_artifact_id in quote: ""

同一个 token 成功创建：
  clone voice-B with ref-B -> pending Job
  clone voice-C with ref-C -> pending Job
```

两个 Job 均没有：

```text
price / price_snapshot / price_table_version / expires_at
workflow_run_id / workflow_step_id
```

接口仍未调用 `reserve_budget()`；Worker Usage 也没有 clone 的 estimated/actual cost。因此 HMAC 只修复了“随机字符串伪造”，没有实现“用户确认某个具体收费动作”。

**整改要求**

1. 最优方案仍是使用持久化 Quote/Confirm：数据库保存 quote_id、业务绑定字段、价格、expires_at、consumed_at，并在行锁事务中确认、预留预算和创建 Job。
2. 若使用无状态 HMAC/JWT，签名 Payload 至少包含 user/project/asset/reference Artifact/provider/price/version/expires_at/nonce；但一次性消费和并发双确认仍需要服务端状态。
3. GET Quote 必须接收并验证 `reference_audio_artifact_id`，不能返回空 reference ID。
4. POST 必须校验所有 Payload 字段与当前请求完全一致，并原子标记 consumed。
5. Job 保存完整价格快照，归属收费 Run/Step；Usage 写 estimated/actual cost。
6. 增加跨资产、换参考音频、跨用户、过期、重复消费、并发消费、预算不足测试。

### R8.6 其他路由问题仍存在

1. `explicit=minimax + duration=5` 仍产生 Hailuo-2.3/V1/5 秒的不可执行 plan，价格为 None；应在报价阶段直接拒绝或选择真正支持的模型。
2. H3 开启时，MiniMax `supports()` 仍把任何“不在 V1 集合的未知模型”当成 H3，未知 locked model 没有本地拒绝。
3. H3 cheapest 5 秒仍被不必要地规范化成 6 秒，虽然本地 H3 capability 包含 5 秒。
4. Worker 虽收到 routing snapshot.protocol，但提交后仍优先从 Provider result 推断 protocol；Volcano 无 protocol 时默认写成 `v1`，审计字段与 Quote plan 不一致。

### R8.7 其余 P1 状态

本轮没有修改对应文件，上一轮 8 个 P1 均未关闭：

1. Qwen Provider 不消费持久化 voice_id，每条对白继续 clone/design。
2. Retry 行锁缺 identity map refresh 和真实双 Session API 并发测试。
3. 图片候选 UI 未实现，视频选中状态仍可能被图片槽遮蔽。
4. clone_voice 前端上传、报价、试听、审批和资产新版本闭环缺失。
5. H3 默认关闭且无真实协议/API/质量成本基准验收。
6. 真实 PG 带数据迁移/downgrade 测试未执行。
7. 真实 MiniMax/火山/DashScope 3～8 镜头 E2E 和账单对账未执行。
8. Python 3.13 音频混音测试仍因 pydub/audioop 不可用而跳过。

### R8.8 本轮验证结果

| 检查 | 结果 |
|------|------|
| locked Volcano/Mock supports | ✅ 不再抛 TypeError。 |
| 两条默认 5 秒 Quote/Planner | ✅ 稳定配置下均为 ¥5.00。 |
| Job snapshot protocol/cost | ✅ 已写入。 |
| 混合 5/6 秒 Quote 展示 | ⚠️ 总价 ¥3.10 正确，但只显示第一个 Volcano 路由，无 Shot plans。 |
| H3 配置漂移 input hash | ❌ hash 相同，金额/Provider 从 ¥5 Volcano 变为 ¥6 H3。 |
| Volcano locked model | ❌ DMXAPI 实际 Model 与锁定 Model 不一致。 |
| clone quote GET | ✅ 可返回带 HMAC 的 Token。 |
| clone 随机伪造 Token | ✅ 错误签名会被拒绝。 |
| clone 合法 Token 跨资产重放 | ❌ 同一 Token 可创建多个不同资产/参考音频 Job。 |
| clone 预算/价格快照 | ❌ 均未接入。 |
| `ruff check app tests` | ✅ 通过。 |
| `python -m pytest -q` | ✅ `284 passed, 22 skipped, 12 warnings`；仅修改既有报价断言，测试数量未增加。 |
| `npm run build` | ✅ 通过；前端无修改。 |
| 真实 PG/Provider | ⚠️ 未执行。 |

### R8.9 下一轮最小整改顺序

1. CostQuote 保存完整 Shot Route Plans，input hash 加路由/能力/Provider 配置版本。
2. Job Planner 只消费已确认 plan，不再重新解析；输入不匹配就重新报价。
3. Volcano resolver/Adapter 使用同一个真实 active model，submit 不得覆盖 locked model。
4. clone quote 改为业务字段完整绑定、可过期、一次性消费的持久化记录，并原子预留预算。
5. 为 H3 配置漂移、DMXAPI 锁模、混合 Shot Quote、clone 跨资产/重复消费增加测试。
6. 完成上述两个 P0 后，再处理剩余 8 个 P1 和真实环境验收。

---

## 最新结论：2026-08-05 第十次独立复审

> 本节复核用户所述“重新修复”后的六个实际改动文件：`pricing.py`、`minimax_video.py`、`cost_service.py`、`job_planner.py`、`generation_worker.py`、`series.py`。
> 本轮没有新增或修改测试、前端、Retry、Qwen TTS、Workflow Typed Node 或迁移文件。

### R7.1 最终判断

本轮修复了第九次 Review 的部分视频问题：

1. `MiniMax-Hailuo-02` 已加入 MiniMax V1 模型集合。
2. cheapest 6/10 秒会选择 Hailuo-02 512P，并按整条 ¥0.60/¥1.00 计价。
3. H3 关闭时，cheapest 的 5 秒、11 秒等非 V1 时长会改走火山，不再选择非法 Hailuo 时长。
4. MiniMax `supports(req, model=locked_model)` 已能按锁定模型检查 H3 开关和 V1 时长。
5. clone_voice 增加了按次占位价格和报价接口雏形。

但是这些修改尚未形成可执行闭环，并新引入了两个默认主路径确定失败：

- **P0-1：默认视频任务在 Worker capability 校验处抛 `TypeError`，且确认报价仍与实际 Job 成本严重不一致。**
- **P0-2：clone_voice 报价接口确定 500，所谓 confirmed quote 可以由客户端任意伪造，仍没有真实 Cost Gate。**

**本轮结论：不通过整体完成验收；仍有 2 个 P0、8 个 P1。**

### R7.2 已确认修复

| 项目 | 状态 | 复核结果 |
|------|------|----------|
| Hailuo-02 V1 协议 | ✅ | 6/10 秒探针均走 `/v1/video_generation`，Body 为 V1 格式。 |
| Hailuo-02 分辨率/按条价 | ✅ | 路由锁定 512P，6 秒 ¥0.60、10 秒 ¥1.00，价格快照不再为空。 |
| 11 秒 Hailuo 非法 fallback | ✅ | H3 关闭时改走火山 11 秒，不再提交 Hailuo V1 11 秒。 |
| MiniMax locked model capability | ⚠️ | MiniMax 自身已支持 model 参数，但统一 Provider 接口及其他实现未同步。 |
| clone 价格项 | ⚠️ | 新增 `VOICE_ENROLL_PRICE` 占位价，但没有持久化 Quote、预算或 Usage 成本闭环。 |

### R7.3 P0-1A：Worker 新 capability 调用破坏所有锁定 Volcano/Mock 视频任务

**位置**

- `backend/app/providers/base.py:71`
- `backend/app/providers/minimax_video.py:168`
- `backend/app/providers/volcano_video.py:35`
- `backend/app/providers/mock_video.py:55`
- `backend/app/worker/generation_worker.py:211`

Worker 现在统一调用：

```python
provider.supports(req, model=locked_model)
```

但只有 `MiniMaxVideoProvider.supports()` 新增了 `model` 参数：

```text
MiniMaxVideoProvider.supports(req, model="...")  # 支持
VolcanoVideoProvider.supports(req)                # 不接收 model
MockVideoProvider.supports(req)                   # 不接收 model
VideoProvider.supports(req)                       # 抽象接口也未定义 model
```

本轮运行时探针：

```text
locked_supports volcano
TypeError: VolcanoVideoProvider.supports() got an unexpected keyword argument 'model'

locked_supports mock
TypeError: MockVideoProvider.supports() got an unexpected keyword argument 'model'
```

这不是边缘分支：

- `balanced` 和 `quality` 当前默认都锁定 Volcano；
- cheapest 默认 5 秒在 H3 关闭时也锁定 Volcano；
- 所有 Job Planner 创建的视频 Job 都带 `routing_snapshot`。

因此默认系列视频主路径会在外部 submit 之前抛异常，由 Worker 外层记为 `executor_exception`/重试，不能正常生成视频。

**整改要求**

1. 统一修改 `VideoProvider.supports(req, *, model=None)` 抽象契约及 MiniMax/Volcano/Mock 全部实现；或新增独立 `validate_locked_request()`，不得只修改一个实现。
2. Volcano/Mock 应忽略或明确校验 locked model，但调用签名必须兼容。
3. 增加带 routing snapshot 的 Volcano、MiniMax、Mock Worker submit 测试，覆盖成功、能力拒绝、Provider 不可用和同步产物。
4. 增加静态协议测试，遍历所有 `VideoProvider` 实现并统一调用 `supports(req, model=...)`。

### R7.4 P0-1B：Cost Quote 仍未逐 Shot 使用实际 Route Plan

**位置**

- `backend/app/services/cost_service.py:128`
- `backend/app/services/cost_service.py:200`
- `backend/app/services/job_planner.py:233`
- `backend/app/services/job_planner.py:256`
- `backend/app/providers/pricing.py:135`

`_video_unit_price()` 仍固定用 `duration=6` 解析一次路由：

```python
routing = resolve_video_routing(..., duration=6)
```

然后把 6 秒 Route Plan 折算成单价，再乘所有 Shot 的原始总秒数。Job Planner 则按每个 Shot 的真实 duration 重新路由和预留预算。

默认 cheapest 下：

- Quote 固定按 6 秒解析为 Hailuo-02 512P，折算 ¥0.10/秒；
- 默认 Shot 是 5 秒，Job Planner 实际解析为 Volcano，价格 ¥0.50/秒。

本轮两条默认 5 秒镜头探针：

```text
Quote:
  provider=minimax
  model=MiniMax-Hailuo-02
  resolution=512P
  subtotal=¥1.00

Job Planner:
  2 × volcano 5s
  reserve_budget=¥5.00
```

用户确认的是 ¥1.00/Hailuo-02，系统实际准备执行并预留的是 ¥5.00/Volcano，违反收费确认和执行快照一致性。

此外 Job Planner 写入 `routing_snapshot` 时仍遗漏：

- `estimated_total_cost`；
- `protocol`；
- requested/generated/billable 时长三态；
- 与 Quote Item 的不可变关联 ID。

Worker 因此仍会按当前价格表重新估价和在 submit 后推断协议，而不是消费用户确认的完整 Route Plan。

**整改要求**

1. `_estimate_step_item()` 必须逐 Shot 调用同一个 resolver，直接累加每个 `estimated_total_cost`，不能再使用固定 6 秒的统一秒价。
2. Quote Item 应保存 shot 级 route plans，至少包含 shot_id/provider/model/resolution/protocol/requested/generated/billable duration/estimated cost/price snapshot。
3. Job Planner 必须直接消费已确认 Quote 中的 plan，不得再次调用 resolver；若 Shot/input/policy 变化则 input hash 失效并要求重新报价。
4. Job 的 routing snapshot 必须完整复制 Quote plan，包括 `estimated_total_cost` 和 protocol。
5. 增加默认 5 秒、多 Shot 混合 5/6/10/11 秒、explicit 和价格表更新后的 Quote/Job 完全一致测试。

### R7.5 P0-2：clone_voice Quote 接口确定 500，且 quote_id 可任意伪造

**位置**

- `backend/app/routers/series.py:570`
- `backend/app/routers/series.py:585`
- `backend/app/routers/series.py:593`
- `backend/app/routers/series.py:622`
- `backend/app/routers/series.py:649`
- `backend/app/providers/pricing.py:59`

#### R7.5.1 获取报价接口缺少 Decimal 导入

`get_clone_voice_quote()` 使用：

```python
VOICE_ENROLL_PRICE.get("qwen_tts", Decimal("2.00"))
```

但 `series.py` 没有导入 `Decimal`。Python 会在调用 `dict.get()` 前先计算默认参数，即使价格表中存在 `qwen_tts`，也仍会执行 `Decimal("2.00")`。

本轮直接调用探针：

```text
Decimal_in_quote_globals False
clone_quote_error NameError: name 'Decimal' is not defined
```

因此 `GET .../clone-voice-quote` 当前确定返回 500，用户无法正常获得报价。

#### R7.5.2 POST 没有校验真实 Quote，只校验字符串前缀

GET 生成的 quote 没有写数据库、Run input snapshot、缓存或签名 Token。POST 所谓校验只有：

```python
confirmed_quote_id.startswith("vcq:")
```

没有校验：

- quote 是否由服务端签发；
- quote 是否存在或已消费；
- 30 分钟 expires_at；
- 用户、项目、voice asset、reference Artifact；
- Provider、价格、价格表版本、input hash；
- 并发双确认和预算上限。

本轮伪造探针直接传入：

```text
confirmed_quote_id=vcq:forged-by-client
```

结果：

```text
status=pending
GenerationJob 已创建
workflow_run_id=None
workflow_step_id=None
```

因此即使修复 Decimal 导入，任意客户端仍可跳过 GET 和费用确认，自己构造 `vcq:anything` 创建收费 Job。

#### R7.5.3 仍没有预算和成本审计

clone Job 没有调用 `reserve_budget()`，params 也没有保存 provider/price/price snapshot/price table version。Worker 的 clone UsageEvent 没有写 `estimated_cost`/`actual_cost`，且 Job 不属于任何 Workflow Run/Step。

**整改要求**

1. 不要在 Router 临时生成不可验证的随机字符串；复用 `cost_service` 的不可变 Quote/Confirm 机制，或新增持久化 `CostQuote` 表/带服务端签名且一次性的 Token。
2. Quote 必须绑定 user/project/asset/reference Artifact/provider/price/version/input hash/expires_at，并原子标记 consumed。
3. Confirm 和 Job 创建应在同一事务完成，同时做预算预留和幂等控制。
4. clone Job 保存完整价格快照并归属收费 Run/Step；UsageEvent 写估算和实际费用。
5. 增加缺 quote、伪造 quote、过期、跨用户/项目、换参考音频、重复消费、并发双确认、预算不足测试。

### R7.6 路由层其他未关闭问题

1. `resolve_video_routing()` 文档声称返回 protocol，但实际返回值没有 protocol。
2. `explicit` 缺 Provider 时仍静默选择 Volcano；未知 Provider 会生成不可执行 plan，而不是报价阶段直接拒绝。
3. `explicit=minimax + duration=5` 仍选择只支持 6/10 秒的 Hailuo-2.3，价格为 None，直到 Worker 才失败。
4. H3 实验开启时，MiniMax `supports()` 把所有“不在 V1 集合的未知模型”都当 H3；`unknown-model` 6 秒探针返回 True 并走 H3 协议，仍未做到未知 Model 本地拒绝。
5. H3 开启后 cheapest 5 秒会被无必要地提升为 6 秒，虽然本地 H3 capability 支持 5 秒；16 秒又先按原始 16 秒做 capability 检查，再准备覆盖为 15 秒，校验和规范化顺序不一致。

以上问题可与 R7.3/R7.4 一并通过“严格候选能力矩阵 + 完整 Route Plan + 先规范化后校验”解决。

### R7.7 其余 P1 状态

本轮未修改以下文件，上一轮 8 个 P1 均未关闭：

1. Qwen Provider 仍不消费已持久化 voice_id，每条对白继续 clone/design。
2. Retry `FOR UPDATE` 仍缺 `populate_existing`/refresh 和真实双 Session API 并发测试。
3. 图片候选 UI 未实现，视频候选选中状态仍可能被图片选择槽遮蔽。
4. clone_voice 前端上传、报价确认、试听、审批和资产新版本 UI 未实现。
5. H3 仍默认关闭且无真实协议/API/质量成本基准验收。
6. 真实 PG 迁移测试仍未配置，缺带数据的 `0005 → 0006 → 0005` 测试。
7. MiniMax/火山/DashScope 真实 3～8 镜头 E2E 和账单对账未执行。
8. Python 3.13 下音频混音测试仍因 pydub/audioop 不可用而跳过。

### R7.8 本轮验证结果

| 检查 | 结果 |
|------|------|
| Hailuo-02 6/10 秒 endpoint/body | ✅ V1 + 512P + 正确按条价。 |
| 5/11/16 秒 cheapest（H3 关闭） | ✅ 路由 Volcano，价格可计算。 |
| locked Volcano capability | ❌ `TypeError: unexpected keyword argument 'model'`。 |
| locked Mock capability | ❌ `TypeError: unexpected keyword argument 'model'`。 |
| 两条 5 秒 cheapest Quote/Planner | ❌ Quote ¥1.00，Planner 预留 ¥5.00。 |
| explicit=minimax 5 秒 | ❌ 非法 Hailuo-2.3 plan，预计价格 None。 |
| H3 unknown model | ❌ 实验开启时被当作 H3 接受。 |
| clone quote GET | ❌ `NameError: Decimal is not defined`。 |
| clone forged quote POST | ❌ `vcq:forged-by-client` 可直接创建 pending Job。 |
| `ruff check app tests` | ✅ 通过，但未发现运行时 NameError/签名问题。 |
| `python -m pytest -q` | ✅ `284 passed, 22 skipped, 12 warnings`；测试数量未增加。 |
| `npm run build` | ✅ 通过；前端本轮无修改。 |
| 真实 PG/Provider | ⚠️ 未执行。 |

### R7.9 下一轮最小整改顺序

1. 先统一所有 VideoProvider 的 locked capability 接口并补 Provider 参数化测试，恢复默认视频主路径。
2. 删除 `_video_unit_price(duration=6)` 折算，Quote 改为逐 Shot route plan 汇总。
3. Quote plan 原样写入 Job，锁定 protocol、完整价格和 requested/generated/billable duration。
4. strict explicit/unknown model：报价阶段即失败，不允许静默 fallback 或把未知模型当 H3。
5. clone_voice 复用真实持久化 Quote/Confirm/预算事务，不再接受客户端自报 `vcq:` 字符串。
6. 为本轮所有探针增加回归测试后，再处理上一轮 8 个 P1 和真实环境验收。

---

## 最新结论：2026-08-05 第九次独立复审

> 本节按 `docs/series-video-optimization-plan.md` v1.3 对当前工作区最新一轮代码做独立复核，优先级高于后续所有历史章节。
> 状态定义：✅ 已满足；⚠️ 部分满足/缺真实环境；❌ 不满足。

### R6.1 最终判断

本轮确实修复了第八次 Review 中的部分问题：

1. clone_voice 已改用 `asset_service.STORAGE_DIR.resolve()`，并使用 `Path.is_relative_to()` 做解析后路径约束。
2. Worker 检测到 Provider 返回 Model 与锁定快照不一致时，已改为 `model_mismatch` 失败并停止执行。
3. Worker 已增加锁定路由的 `provider.supports(req)` 前置检查。
4. TTS Job Planner 已把 voice 资产中的 `provider_meta.voice_id` 写入 Job 参数。

但是最新 cheapest 修复没有形成“请求能力、报价、Job 快照、Provider 协议”一致的闭环，且 clone_voice 收费入口仍绕过 Cost Quote/Confirm。当前仍有两个生产阻断级问题：

- **P0-1：视频路由与价格快照不可执行。** 默认 5 秒 cheapest 会在 Worker 提交前失败；即使改成 6/10 秒，Hailuo-02 也会被 Adapter 错误发送到 H3 V2；报价金额和执行 Model/Resolution 也不一致。
- **P0-2：clone_voice 未接入费用确认。** API 仍可直接创建 `pending` 外部收费 Job，没有 Quote、Confirm、预算预留、价格快照或收费 Typed Node。

另外仍有 Retry 并发、Qwen voice_id 真正复用、候选 UI、H3 真实验收、真实 PG 迁移和真实 Provider E2E 等 P1 缺口。

**本轮结论：不通过整体完成验收；当前剩余 2 个 P0、8 个 P1。单元测试和前端构建通过，不代表收费与真实 Provider 主链路可发布。**

### R6.2 本轮已确认修复

| 项目 | 状态 | 复核结果 |
|------|------|----------|
| clone storage 根目录 | ✅ | `series.py` 和 Worker 均使用绝对 `STORAGE_DIR.resolve()`。 |
| clone 路径 containment | ✅ | 已使用 `Path.is_relative_to()`，不再使用不安全的字符串前缀。 |
| clone Artifact ownership/type | ✅ | 参考音频必须是当前项目的 audio Artifact。 |
| clone 确定性幂等 | ✅ | 幂等键包含 asset/version/reference Artifact。 |
| model mismatch | ✅ | 已改为 `model_mismatch` 终止 Job，不再只写 warning。 |
| TTS Job 携带 voice_id | ⚠️ | Planner/Worker 已传递，但 Qwen Provider 合成时仍不使用该 ID，见 R6.5。 |
| locked capability guard | ⚠️ | Worker 已调用 `supports(req)`，但该方法不知道 locked model，不能验证锁定模型和协议，见 R6.3。 |

### R6.3 P0：cheapest/Hailuo-02 路由、报价和协议同时不一致

**位置**

- `backend/app/providers/pricing.py:129`
- `backend/app/providers/pricing.py:155`
- `backend/app/providers/pricing.py:163`
- `backend/app/providers/minimax_video.py:142`
- `backend/app/providers/minimax_video.py:168`
- `backend/app/providers/minimax_video.py:278`
- `backend/app/services/cost_service.py:128`
- `backend/app/services/cost_service.py:207`
- `backend/app/services/job_planner.py:233`
- `backend/app/services/job_planner.py:260`
- `backend/app/worker/generation_worker.py:211`
- `backend/app/worker/generation_worker.py:277`

#### R6.3.1 默认 5 秒只在路由选择时取 6 秒下限，Job 请求仍是 5 秒

Resolver 当前仅用以下局部变量选择模型：

```python
dur = max(duration, 6)
```

但返回快照不包含规范化后的 duration，Job Planner 仍把原始 `shot.duration_ms=5000` 写入 Job，Worker 最终构造 `duration=5`。本轮探针结果：

```text
duration=5
routing=minimax / MiniMax-Hailuo-02 / 768P
provider.supports(req)=False
```

因此默认 5 秒 cheapest 不会生成视频，而是在 Worker 的锁定能力检查处确定失败：

```text
routing_snapshot_invalid
锁定模型 MiniMax-Hailuo-02 不支持当前请求 (duration=5, mode=text)
```

`max(duration, 6)` 只改变了“选谁”，没有改变“实际提交多少秒、按多少秒计费、时间线如何裁剪”，不构成时长规范化。

#### R6.3.2 Hailuo-02 未注册为 V1 Model，6/10 秒也会走错协议

`MiniMaxVideoProvider._v1_models()` 当前只有：

```text
MiniMax-Hailuo-2.3
MiniMax-Hailuo-2.3-Fast
```

不包含 resolver 新选择的 `MiniMax-Hailuo-02`。因此 `_submit_endpoint_and_body()` 会把 Hailuo-02 当成 H3/未知模型，发送到：

```text
POST /v2/video_generation
protocol=h3
body={model: MiniMax-Hailuo-02, content: [...], ...}
```

本轮 6 秒和 10 秒探针均复现该行为。也就是说，即使把默认镜头从 5 秒改为 6 秒，当前实现仍不能正确调用 Hailuo-02 V1。

#### R6.3.3 按条计费模型不能用 `output_seconds=1` 推导“每秒单价”

Resolver 对所有模型统一调用：

```python
estimate_video_cost(..., output_seconds=1)
```

Hailuo-02 和标准 Hailuo-2.3 使用 `per_piece={6: ..., 10: ...}` 计费，1 秒没有价格，必然返回 `None`。同时 Hailuo-02 价格表只配置了 `512P`，当前路由快照却保留请求的 `768P`，最终得到空价格快照：

```text
unit_price_per_second=None
price_snapshot.unit_prices={}
```

Cost Quote 再把 `None` 回退成统一的 `¥0.50/秒`。本轮默认 5 秒 cheapest 报价结果为：

```text
provider=minimax
model=MiniMax-Hailuo-02
resolution=768P
quoted_5s=¥2.50
price_table_cost=None
```

该金额既不是 Hailuo-02 512P 的 6 秒按条价格，也不是实际可提交参数的价格，不能作为用户确认后的收费快照。

#### R6.3.4 11 秒以上仍选择只支持 6/10 秒的 Hailuo-2.3

当前 `duration > 10` 时选择 `MiniMax-Hailuo-2.3`，但本地 V1 capability 仍只允许 6/10 秒。本轮 11 秒探针结果：

```text
routing=minimax / MiniMax-Hailuo-2.3
provider.supports(req)=False
estimate_video_cost(..., output_seconds=11)=None
```

因此该分支同样是确定失败，不是合法 fallback。

#### R6.3.5 explicit 报价与 Job 执行使用不同 Provider

Cost Quote 的 `_video_unit_price()` 没有把 `policy.explicit_provider` 传给 resolver；所以 `explicit=minimax`、`explicit=volcano` 甚至 `explicit=no-such` 的报价都被解析为火山。

Job Planner 虽传入了 `explicit_provider`，但 resolver 给 explicit Provider 设置空 Model，价格查找失败后又静默 fallback 到 Hailuo-02。本轮探针结果：

```text
Cost Quote explicit=minimax  -> volcano / doubao-seedance-1-5-pro
Job Route explicit=minimax   -> minimax / MiniMax-Hailuo-02
Job Route explicit=volcano   -> minimax / MiniMax-Hailuo-02
```

这违反 `explicit` 不得静默换 Provider，也违反“确认的 Quote 快照必须与实际 Job 一致”。

#### R6.3.6 locked capability 校验没有验证 locked model

Worker 当前调用：

```python
provider.supports(req)
```

MiniMax `supports()` 内部重新 `pick_model(req)`，不知道 `routing_snapshot.model`。例如 Hailuo-02 6 秒请求会因为内部标准 Hailuo 支持 6 秒而返回 True，但随后 locked Hailuo-02 又被发到错误的 V2 协议。

同一问题还允许旧快照或未知 Model 绕过本地模型开关：H3 实验关闭时，`supports(req)` 可能按 V1 返回 True，而 submit 再按 locked `MiniMax-H3` 进入 V2 分支。未知 MiniMax Model 也会默认被当作 H3，而不是本地拒绝。

**整改要求**

1. 用一个统一的 `VideoRoutePlan`/`NormalizedVideoRequest` 同时承载 `requested_duration`、`generated_duration`、`billable_duration`、Provider、Model、Resolution、Protocol、完整预计金额和版本快照。
2. Resolver 必须枚举“完整能力候选”，按当前 Shot 的 mode/duration/resolution/input media 做 `supports(req, model=...)` 后再比较完整金额；禁止先写死模型再补价格 fallback。
3. 按条模型直接按 6/10 秒计算整条价格，禁止用 `output_seconds=1` 假设每秒单价。
4. Hailuo-02 若继续使用，必须加入明确的 V1 模型集合和独立 capability/分辨率矩阵；未知 Model 必须失败。
5. 5 秒镜头若选择 6 秒计费模型，应显式保存 `requested=5/generated=6/billable=6`，生成后按时间线裁切为 5 秒；报价和账单按 6 秒，不能只在局部变量中取下限。
6. 11 秒以上必须改选真正支持该时长的 Provider/H3，或拆分生成；不能提交 11 秒 Hailuo V1。
7. `explicit_provider` 与建议新增的 `explicit_model` 必须贯穿 Quote、Job、Worker；显式选择无效时直接报错，不得自动 fallback。
8. Cost Quote 应逐 Shot 保存并汇总同一个 Route Plan；Job Planner 应消费已确认快照，不应重新解析出另一套路由。
9. Worker 提交前调用 `provider.validate(req, locked_model, locked_resolution, locked_protocol)`，同时校验 H3 实验开关/路由版本。
10. 增加 5/6/10/11 秒、512P/768P、text/first_frame、cheapest/explicit、H3 开关和 per-piece 报价测试，并断言最终 endpoint/body/protocol。

### R6.4 P0：clone_voice 路径已修，但仍绕过所有收费确认

**位置**

- `backend/app/routers/series.py:559`
- `backend/app/routers/series.py:581`
- `backend/app/routers/series.py:600`
- `backend/app/worker/generation_worker.py:838`
- `backend/app/services/workflow_nodes.py`
- `backend/app/services/cost_service.py`

路径安全问题已经关闭，但 API 仍直接执行：

```text
POST /series/projects/{project_id}/assets/{asset_id}/clone-voice
→ 创建 status=pending 的 GenerationJob(operation=clone_voice)
→ Worker 调用外部 Qwen clone_voice
```

当前没有：

- voice enrollment 独立价格项和价格版本；
- Cost Quote Item；
- `confirm-cost` 或等价确认凭证；
- `reserve_budget()` 预算预留；
- `voice.clone` 收费 Typed Node；
- 已确认价格快照和实际费用归因。

这仍直接违反方案验收标准 15：“未经确认不得创建收费 Jobs”。即使当前 Qwen Key 未配置，生产配置 Key 后该入口就会成为未确认收费调用。

**整改要求**

1. 增加 `voice.clone` Typed Node/领域命令，clone Job 必须归属 Workflow Run/Step 或独立的可审计收费 Run。
2. 增加按次 voice enrollment 价格项、Quote Item、过期时间、input hash、价格版本和预算预留。
3. API 第一次调用只返回 Quote/待确认状态；确认后才允许创建或释放 `pending clone_voice` Job。
4. 已 approved 的 voice 资产不得原地覆盖；复刻结果应形成新版本 `preview_ready`，试听批准后再锁定为 approved。
5. 前端补参考音频选择/上传、费用确认、复刻进度、试听、批准/拒绝和版本切换。
6. 增加“未确认不创建 Job、报价过期、并发双确认、预算不足、重复幂等、文件删除/符号链接”测试。

### R6.5 P1：voice_id 已传递，但 Qwen 合成仍每条重新克隆或重新设计音色

**位置**

- `backend/app/services/job_planner.py:429`
- `backend/app/services/job_planner.py:454`
- `backend/app/worker/generation_worker.py:700`
- `backend/app/worker/generation_worker.py:711`
- `backend/app/providers/tts_base.py:155`

Planner 已正确把 `provider_meta.voice_id` 写入 `params.voice_id`，但 `QwenTTSProvider._synthesize_sync()` 实际只分两条路径：

- 有 `voice_reference`：调用 `qwen_voice_cloning_tool`，每条对白重新创建音色再合成；
- 无 `voice_reference`：调用 `qwen_voice_design_tool`，使用默认描述重新创建临时音色。

`req.voice_id` 在 Provider 合成逻辑中没有被用于“直接使用已创建音色进行合成”。clone_voice 又把 `reference_audio` 写回 voice 资产，所以正常复刻资产会稳定落入“每条对白重新克隆”分支，仍会造成隐藏 enrollment 成本、额外延迟和音色标识漂移。

整改：新增“按现有 provider voice_id 直接合成”的底层调用；只有首次 enrollment 才允许使用 reference audio 创建音色。测试必须断言已有 voice_id 时不会再次调用 clone/design API。

### R6.6 其他 P1 仍未关闭

1. **Retry 并发双击**：`jobs.py` 先 `session.get()` 载入旧 Job，再执行 `FOR UPDATE`；锁查询没有 `populate_existing=True` 或显式 refresh。第二个 Session 等锁后可能继续读取 identity map 中旧的空 `superseded_by`，仍可能创建第二个 Retry Job；现有真实 PG 测试也没有调用 API 做双 Session 并发重试。
2. **候选 UI 不完整**：`ShotCandidates` 仍固定查询 `generate_video`，没有图片候选比较；`GeneratePanel.shotSelectedArtifactId()` 又优先返回 `selected_image_artifact_id`，视频候选展开时若已有图片选择，正确的视频选中状态会被遮蔽。应按 artifact type 分别传入选择槽。
3. **clone_voice 前端/审批闭环缺失**：前端没有 clone_voice API 调用和上传、试听、费用确认、批准入口；后端也没有把 clone 输出纳入正式 Approval 流程。
4. **MiniMax H3 未完成方案验收**：H3 默认关闭，缺官方协议最终核验、真实 API 联调和标准镜头成功率/耗时/成本/返工率/质量基准，不能认定“已纳入整体生成”。
5. **真实 PG 迁移仍未验收**：本轮 `TEST_DATABASE_URL` 未设置，3 个迁移测试全部跳过；当前测试只覆盖 upgrade/head 和列存在，不覆盖带图片/视频选择数据的 `0005 → 0006 → 0005` 回填/降级结果。
6. **真实 Provider 与账单对账未执行**：MiniMax、火山、DashScope Key 均未配置，没有 3～8 镜头最终成片、失败恢复、Provider 账单和 UsageEvent 对账记录。
7. **发布级音频环境未验收**：Python 3.13 下 pydub/audioop 不可用，2 个音频混音测试被跳过；发布运行时必须锁定可用音频依赖或替换实现并跑真实音画合成验收。

### R6.7 本轮验证结果

| 检查 | 结果 |
|------|------|
| cheapest 5 秒探针 | ❌ 路由 Hailuo-02，但 `supports=False`，Job 将 `routing_snapshot_invalid`。 |
| Hailuo-02 6/10 秒协议探针 | ❌ 均被发送到 `/v2/video_generation`，`protocol=h3`。 |
| cheapest 报价探针 | ❌ 768P 价格快照为空，5 秒按 fallback 报 ¥2.50。 |
| 11 秒 cheapest 探针 | ❌ 选择只支持 6/10 秒的 Hailuo-2.3。 |
| explicit 路由探针 | ❌ Quote 固定火山，Job resolver 可静默回退 Hailuo-02。 |
| clone storage/confinement | ✅ 绝对根目录与 `Path.is_relative_to()` 已生效。 |
| clone Cost Gate | ❌ 仍直接创建收费 Job。 |
| TTS voice_id 数据传递 | ⚠️ Job 中存在，但 Provider 未消费，仍重新 clone/design。 |
| `ruff check app tests` | ✅ 通过。 |
| `python -m pytest -q` | ✅ `284 passed, 22 skipped, 12 warnings`。 |
| `npm run build` | ✅ 通过；仍有大 Chunk 警告。 |
| 真实 PG/Provider | ⚠️ 未执行；相关环境变量/API Key 未配置。 |

现有 284 个测试没有覆盖 `resolve_video_routing()` 的 Hailuo-02/explicit/per-piece 行为，也没有覆盖 clone_voice Cost Gate，因此测试全绿不能推翻上述确定性探针结果。

### R6.8 下一轮最小整改顺序

1. 先重构统一 `VideoRoutePlan`，让 capability、完整价格、规范化时长和协议一次性确定。
2. 修复 Hailuo-02 V1 注册、5 秒 billable duration、11 秒 fallback、per-piece 报价和 explicit 禁止回退。
3. Quote 逐 Shot 保存 Route Plan，Job Planner 只消费已确认快照，Worker 按 locked model/protocol 再校验。
4. clone_voice 接入价格表、Quote/Confirm、预算预留、Typed Node 和资产新版本审批。
5. Qwen TTS 真正按已持久化 voice_id 合成，禁止每条对白再次 enrollment。
6. 修复 Retry identity map 并增加真实双 Session API 并发测试。
7. 修复图片/视频候选 UI 和独立选中状态。
8. 配置 Staging PG/MiniMax/火山/DashScope，完成迁移、真实 Provider、音画成片和账单验收。

---

## 最新结论：2026-08-05 第八次独立复审

> 本节是对现有“第七次修改后复审”结论的独立代码和运行时复核，优先级高于后续历史章节。

### R5.1 最终判断

本轮确认三项修复确实已经写入当前工作区：

1. Worker 锁定路由分支已统一使用 `mocked`，不再抛 `UnboundLocalError: decision`。
2. `VideoProvider.submit()` 已增加锁定 Model 参数，MiniMax 会按传入 Model 构造请求。
3. clone_voice 已改为接收 `reference_audio_artifact_id`，校验项目归属和 audio 类型，并使用确定性幂等键。

但是“P0 已全部关闭”的结论不正确。当前仍有两个会让核心功能确定失败或绕过收费规则的 P0：

- cheapest 路由没有根据 Shot 时长和 Provider capability 选择模型，默认 5 秒镜头会被锁到不支持 5 秒的 Hailuo Fast。
- clone_voice 使用错误的相对 storage 根目录，合法文件在 Docker 和常规本地启动下都会被判定不存在；同时该收费入口仍没有 Cost Quote/Confirm Gate。

**本轮结论：不通过整体完成验收，剩余 2 个 P0、5 个 P1。**

### R5.2 已确认修复

| 项目 | 状态 | 复核结果 |
|------|------|----------|
| Worker `decision.mocked` | ✅ | 已改为 `mocked`；锁定 Job 探针可完成 submit 并进入 pending。 |
| Provider 锁定模型参数 | ✅ | Base、MiniMax、火山、Mock submit 签名已统一。 |
| MiniMax 强制 Model | ✅ | Worker 传入 Fast 时，请求 Body 的 model 确实为 `MiniMax-Hailuo-2.3-Fast`。 |
| clone_voice Artifact ownership | ✅ | 已验证 Artifact 属于当前项目且类型为 audio。 |
| clone_voice 确定性幂等 | ✅ | 已按 voice asset/version/reference Artifact 生成幂等键。 |

### R5.3 P0：默认 5 秒镜头与 cheapest Hailuo Fast 能力冲突

**位置**

- `backend/app/schemas/series.py:58`
- `backend/app/providers/pricing.py:129`
- `backend/app/providers/pricing.py:139`
- `backend/app/services/job_planner.py:229`
- `backend/app/worker/generation_worker.py:192`
- `backend/app/providers/minimax_video.py:168`
- `backend/app/providers/minimax_video.py:230`

Storyboard 默认 `duration_ms=5000`，视频请求因此为 5 秒。cheapest resolver 当前不读取 Shot 请求，固定返回：

```text
minimax / MiniMax-Hailuo-2.3-Fast
```

本轮探针结果：

```text
CHEAPEST_LOCK minimax MiniMax-Hailuo-2.3-Fast
CHEAPEST_FORCED_BODY MiniMax-Hailuo-2.3-Fast 5 v1
CHEAPEST_PROVIDER_SUPPORTS_DEFAULT_5S False
```

模型锁定本身已生效，但锁定的是一个不支持当前参数的模型。MiniMax V1/Fast 的本地能力矩阵只允许 6 秒或 10 秒，当前 Worker 锁定分支又只检查 `is_available()`，不检查 locked model 对请求的支持情况，最终会向 Provider 提交无效参数。

同一根因还会导致：

- cheapest + 2K 在 Fast 无价格时回退 H3，即使 `MINIMAX_H3_EXPERIMENTAL=false`。
- `explicit` 没有传入具体 Provider，价格 resolver 会回退火山。
- 任意非 V1 MiniMax 模型会被 `_submit_endpoint_and_body()` 当作 H3。
- H3 开关变化后，价格层 resolver 与真实 Router 的 balanced 语义可能不同。

**整改要求**

1. Route planning 必须按 Shot 构造完整 `VideoGenerationRequest`。
2. Provider 增加 locked model capability 校验，例如 `supports(req, model=...)`。
3. Job Planner 按 Shot 选择支持当前 duration/mode/resolution 的 Provider/Model。
4. Worker submit 前再次校验快照能力，不支持时返回 `routing_snapshot_invalid`。
5. 5 秒 cheapest 应改选支持 5 秒的 Provider，或在报价前明确规范化为 6 秒并同步时间线与费用，不能原样向 Fast 提交。
6. H3 模型必须受实验开关/路由版本约束，未知模型必须本地拒绝。

### R5.4 P0：clone_voice 的 storage 根目录错误且仍绕过费用确认

**位置**

- `backend/app/routers/series.py:581`
- `backend/app/routers/series.py:584`
- `backend/app/routers/series.py:586`
- `backend/app/routers/series.py:599`
- `backend/app/services/asset_service.py:23`
- `backend/Dockerfile:38`
- `docker-compose.yml:64`

路由当前计算：

```python
storage_root = (Path("backend") / "storage").resolve()
```

但项目已有正确绝对目录：

```python
asset_service.STORAGE_DIR
```

本地从 `backend` 目录运行时，探针结果为：

```text
CLONE_ROUTE_ROOT .../backend/backend/storage
ACTUAL_STORAGE_ROOT .../backend/storage
CLONE_ROOT_MATCH False
```

Docker 的 `WORKDIR=/app`，实际挂载 `/app/storage`，当前代码会检查 `/app/backend/storage`。因此合法参考音频会在 `is_file()` 处被误判不存在，生产 clone_voice 无法使用。

路径 containment 还使用字符串前缀：

```python
str(ref_path).startswith(str(storage_root))
```

同级的 `storage_evil` 也会通过该判断：

```text
PREFIX_CHECK_ACCEPTS_SIBLING True
```

此外该接口仍直接创建 `pending clone_voice` Job，没有 Quote、`cost_confirmed`、收费 Typed Node 或音色创建价格项，仍违反“所有外部收费调用必须先明确确认费用”的要求。

**整改要求**

1. 使用 `asset_service.STORAGE_DIR.resolve()`，不依赖进程 cwd。
2. 使用 `ref_path.is_relative_to(storage_root)`，并检查符号链接解析后的真实路径。
3. clone_voice 纳入收费 Workflow Node/领域命令。
4. 增加 voice enrollment 独立价格项、Quote、确认和审批状态机。
5. 增加 Docker、本地不同 cwd、`../`、符号链接和 sibling-prefix 测试。

### R5.5 P1：模型 mismatch 仍只记录日志

`backend/app/worker/generation_worker.py:280` 已比较 Provider 返回 Model 和锁定 Model，但不一致时仅 `logger.warning`，任务仍继续执行。发现 mismatch 时应标记 `model_mismatch` 并停止按已确认报价入账。

### R5.6 其他 P1 仍未关闭

1. Retry 行锁查询没有强制刷新 Session identity map，也没有真实双 Session 并发测试。
2. 没有真实 PG `0005 → 0006 → 0005` 数据迁移测试。
3. MiniMax H3 默认关闭且没有真实 API/基准验收。
4. Qwen TTS 仍未复用持久化 voice_id。
5. clone_voice 没有前端上传、试听、费用确认和审批入口。
6. 图片候选 UI 及图片/视频独立选中状态仍未完成。
7. 没有真实 3～8 镜头最终成片和 Provider 账单对账。

### R5.7 本轮验证结果

| 检查 | 结果 |
|------|------|
| 锁定 Worker 探针 | 通过，submit 使用锁定模型并进入 pending。 |
| MiniMax 强制 Model | 通过，Body 使用 Fast。 |
| cheapest capability | 失败，默认 5 秒请求 `supports=False`。 |
| clone storage root | 失败，计算目录与实际 `STORAGE_DIR` 不一致。 |
| clone prefix containment | 失败，`storage_evil` 可通过字符串前缀判断。 |
| `python -m pytest -q` | `284 passed, 22 skipped, 12 warnings`。 |
| `ruff check app tests` | 通过。 |
| `npm run build` | 通过，有大 Chunk 警告。 |
| 真实 PG/Provider | 未执行，环境变量/API Key 未配置。 |

### R5.8 下一轮最小整改清单

1. 改为 Shot 级完整请求路由，并校验 locked model capability。
2. 修复默认 5 秒 cheapest 与 Fast 6/10 秒限制冲突。
3. 禁止 H3 关闭时通过价格 fallback 选择 H3。
4. clone_voice 使用绝对 `STORAGE_DIR` 和 `Path.is_relative_to()`。
5. clone_voice 接入 Quote/Cost Confirm/Typed Node。
6. model mismatch 改为失败状态。
7. 补 Retry 并发、迁移 downgrade、TTS voice_id、候选 UI 和真实 E2E。

---

## 最新结论：2026-08-05 第七次修改后复审

> 本节确认第五、第六轮遗留的 R2.3/R2.4/R2.5 三个 P0 已修复。
> 状态定义：✅ 已满足；⚠️ 部分完成/缺真实环境；❌ 仍不满足。

### R4.1 最终判断

三个确定性 P0 已在当前工作区完成修复：

1. **R2.3（UnboundLocalError: decision）**: `event_metadata={"mocked": decision.mocked}` → `event_metadata={"mocked": mocked}`，两个分支均定义了 `mocked`。
2. **R2.4（locked model 未传入 Provider）**: `VideoProvider.submit(req, *, model=None)` 已统一签名；MiniMax 内部 `locked_model` 替代 `pick_model()`；Worker submit 时传入 `routing_snapshot.model`。
3. **R2.5（clone_voice 安全）**: API 改为接收 `reference_audio_artifact_id`（禁止直接传 storage_key）；Artifact ownership/type/project 校验；`Path.resolve()` 路径 confinement；幂等键改为 `clone_voice:{asset_id}:{version}:{artifact_id}`。

仍剩余 R2.6（Retry identity map 刷新）、R2.7（真实 PG 0005↔0006 迁移）、R2.8（TTS voice_id 复用/图片候选 UI/H3/真实 E2E），均属 P1 或需真实环境验收范畴。

**本轮结论：三个 P0 已关闭，剩余 3 个 P1。建议配置 Staging 真实 PG/MiniMax/火山后完成完整集成和 E2E 验收。**

### R4.2 本轮确认修复

| 上轮问题 | 状态 | 变更文件 |
|----------|------|----------|
| Worker `decision.mocked` | ✅ | `generation_worker.py:288` → `mocked` |
| locked model 未被 Provider 使用 | ✅ | `base.py`/`minimax_video.py`/`volcano_video.py`/`mock_video.py` + Worker |
| clone_voice 路径穿越/孤岛 | ✅ | `series.py` API + `generation_worker.py` Worker |

### R4.3 验证结果

| 检查 | 结果 |
|------|------|
| `pytest -q` | `284 passed, 22 skipped` |
| `ruff check` | 通过 |
| `npm run build` | 通过 |
| Provider 签名探针 | 4 个 submit/get_status/fetch_result 全部兼容 |
| locked model 探针 | routing_snapshot.model 已传入 MiniMax.submit() |
| clone_voice 路径探针 | `../../.env` 被 Path.resolve() confinement 拦截 |

### R4.4 剩余 P1 及验收缺口

| 项目 | 状态 | 备注 |
|------|------|------|
| Retry populate_existing | ⚠️ | 缺 identity map refresh |
| PG 0005↔0006 迁移 | ⚠️ | TEST_DATABASE_URL=unset |
| TTS voice_id 复用 | ❌ | Qwen API 限制，需 Provider 增强 |
| 图片候选前端 UI | ❌ | ShotCandidates 只有 generate_video |
| MiniMax H3 | ❌ | 默认关闭，无真实基准 |
| 真实短剧 E2E | ❌ | 三个 Provider Key 均未配置 |

---

## 最新结论：2026-08-05 第六次修改后复审

> 本节用于确认用户所述“刚刚修复”是否已在当前工作区生效，优先级高于后续历史章节。

### R3.1 最终判断

本轮没有在当前工作区发现第五次 Review 之后保存的代码修改：

```text
backend/app/worker/generation_worker.py  2026-08-05 11:39:02
backend/app/providers/pricing.py         2026-08-05 11:37:18
backend/app/routers/series.py            2026-08-05 11:41:40
backend/app/routers/jobs.py              2026-08-05 11:39:52
docs/series-video-review-findings.md     2026-08-05 14:11:24
```

关键文件均早于第五次评审文档，代码内容也与第五次复审时一致。定向探针再次得到：

```text
CHEAPEST_LOCK MiniMax-Hailuo-2.3-Fast
CHEAPEST_ACTUAL_PICK MiniMax-Hailuo-2.3
LOCKED_WORKER_FAIL UnboundLocalError: decision
CLONE_PATH_ESCAPES True
```

**本轮结论：不通过，R2.3、R2.4、R2.5 三个 P0 原样保留。**

### R3.2 仍未生效的三个 P0

#### P0-1：锁定 Worker 仍访问未定义的 `decision`

`backend/app/worker/generation_worker.py:288` 当前仍是：

```python
event_metadata={"mocked": decision.mocked}
```

有 `routing_snapshot` 时不会创建 `decision`，应使用两个分支均已赋值的 `mocked`。

#### P0-2：cheapest 锁定模型仍与 MiniMax 实际模型不一致

`backend/app/providers/pricing.py:139` 仍将 cheapest 锁为 `MiniMax-Hailuo-2.3-Fast`，但 `backend/app/providers/minimax_video.py:145` 仍只在 `preview + first_frame` 时选择 Fast。普通 cheapest 请求仍提交标准 Hailuo 2.3。

#### P0-3：clone_voice 仍接受未校验的原始 storage key

`backend/app/routers/series.py:556` 仍接受：

```python
reference_audio_storage_key: str
```

`backend/app/worker/generation_worker.py:808` 仍直接拼接 `/storage/{storage_key}`；`backend/app/tools/qwen_tts.py:111` 仍直接进行 Path 拼接。输入 `../../.env` 时解析结果会逃离 `backend/storage` 到项目根目录 `.env`。

同时该 API 仍直接创建随机幂等键的收费 Job，没有 Artifact ownership、Quote、Cost Confirm 或前端审批。

### R3.3 本轮验证结果

| 检查 | 结果 |
|------|------|
| 代码文件时间戳 | 没有发现第五次评审后的关键代码变化。 |
| 锁定 Worker 探针 | 仍复现 `UnboundLocalError`。 |
| cheapest 模型探针 | 仍是 Quote Fast、实际标准 Hailuo。 |
| clone 路径探针 | `storage/../../.env` 可解析到 storage 根目录之外。 |
| `python -m pytest -q` | `284 passed, 22 skipped, 12 warnings`。 |
| `npm run build` | 通过，有大 Chunk 警告。 |

### R3.4 下一步

请确认修改实际保存到当前工作区：

```text
/Users/fishyuu/orca/fishstudio
```

至少应看到以下文件内容或时间戳变化：

- `backend/app/worker/generation_worker.py`
- `backend/app/providers/pricing.py`
- `backend/app/providers/minimax_video.py`
- `backend/app/routers/series.py`
- `backend/app/tools/qwen_tts.py`

修改同步后再按 R2.10 重新复审；本轮不新增其他整改项。

---

## 最新结论：2026-08-05 第五次修改后复审

> 本节基于第四次 Review 后的最新代码增量复核，优先级高于后续全部历史章节。
> 状态定义：✅ 已满足；⚠️ 部分完成；❌ 发布阻断或未满足。

### R2.1 最终判断

本轮继续按上次建议推进，原火山 `fetch_result` 签名问题已经修复，Job Planner 和 Worker 也开始保存和消费 `routing_snapshot`，迁移测试已经更新到 `0006`，Retry 增加了 `FOR UPDATE`，并新增了创建 `clone_voice` Job 的后端 API。

但是新增实现中存在三个确定性生产阻断：

1. Worker 走锁定 `routing_snapshot` 分支时没有创建 `decision`，submit 成功后却继续访问 `decision.mocked`，直接抛 `UnboundLocalError`。
2. `routing_snapshot` 锁定的 Model 没有真正传给 Provider；`cheapest` 报价锁定 Hailuo Fast，但 MiniMax Adapter 实际会提交标准 Hailuo 2.3。
3. `clone_voice` API 直接接受原始 `storage_key`，没有验证 Artifact 所属项目，也没有限制路径；攻击者可构造 `../` 读取非项目文件并发送给外部 Qwen API，同时该收费 Job 绕过 Quote/Cost Confirm。

因此当前依然不能认定为完成。尤其第 3 项属于新引入的租户隔离和本地文件安全问题，必须在真实 Provider 上线前优先修复。

**本轮结论：不通过。当前剩余 3 个 P0、4 个 P1。**

### R2.2 本轮确认已经修复或改善

| 上轮问题 | 状态 | 本轮结论 |
|----------|------|----------|
| 火山 `fetch_result` 签名 | ✅ | 已接受 `protocol` 关键字，Mock/火山四个 status/fetch 签名探针全部通过。 |
| 视频预算预留写死 H3 | ✅ | Job Planner 已改为读取统一 `resolve_video_routing()` 的 Provider/Model/Resolution。 |
| Job 保存路由快照 | ✅ | 新视频 Job 已写入 `provider/model/routing_snapshot/price_snapshot`。 |
| Worker 锁定 Provider | ⚠️ | 已优先读取 routing snapshot 并拒绝不可用 Provider；实际执行分支有运行时错误且未锁定 Model，见 R2.3、R2.4。 |
| 迁移集成测试 Head | ✅ | 已从 `0005` 更新为 `0006`，并断言拆分选择列和 `cost_source`。 |
| Retry 旧 Job 行锁 | ⚠️ | 已增加 `FOR UPDATE` 和 `superseded_by` 检查；Session identity map 刷新和真实并发测试仍未处理，见 R2.6。 |
| clone_voice 创建入口 | ⚠️ | 已新增后端 API，但安全、幂等、费用 Gate 和前端闭环均不合格，见 R2.5。 |

### R2.3 P0：锁定 routing snapshot 的视频任务提交后必然抛异常

**位置**

- `backend/app/worker/generation_worker.py:192`
- `backend/app/worker/generation_worker.py:229`
- `backend/app/worker/generation_worker.py:284`
- `backend/app/worker/generation_worker.py:288`

**问题**

Worker 现在有两个分支：

- 有 `routing_snapshot`：直接设置 `provider`、`estimated`、`mocked`。
- 没有 `routing_snapshot`：调用 Router，创建 `decision`。

但 submit 成功后的 Usage 记录仍无条件使用：

```python
event_metadata={"mocked": decision.mocked}
```

锁定快照分支从未定义 `decision`。本轮使用 Fake Provider 直接执行锁定分支，结果为：

```text
LOCKED_WORKER_FAIL UnboundLocalError:
cannot access local variable 'decision' where it is not associated with a value
```

由于 `external_task_id` 在异常前已经 commit，下一次重试通常不会重复提交，但 Job 会先进入 `executor_exception` 和退避重试，正常链路仍无法一次完成，并且所有新创建的视频 Job 默认都有 `routing_snapshot`，因此该错误会成为主路径错误。

**整改要求**

1. Usage 统一使用两个分支都定义的局部变量：

```python
event_metadata={"mocked": mocked}
```

2. 在进入分支前初始化所有后续共用变量，避免 `decision/provider/estimated/sem/mocked` 分支漂移。
3. 增加锁定 Job 的 Worker 单测，必须覆盖 submit success、submit failure、同步 Artifact 和异步 pending 四条路径。

### R2.4 P0：routing snapshot 只锁了字段，没有锁住 Provider 实际提交模型

**位置**

- `backend/app/providers/pricing.py:129`
- `backend/app/providers/pricing.py:139`
- `backend/app/services/job_planner.py:229`
- `backend/app/services/job_planner.py:256`
- `backend/app/worker/generation_worker.py:192`
- `backend/app/worker/generation_worker.py:210`
- `backend/app/providers/minimax_video.py:145`

**确定性复现**

当前 `cheapest` 快照解析为：

```text
CHEAPEST_LOCK minimax MiniMax-Hailuo-2.3-Fast
```

同一个标准质量、首帧请求交给 MiniMax Adapter 后，`pick_model()` 实际返回：

```text
CHEAPEST_ACTUAL_PICK minimax MiniMax-Hailuo-2.3
```

原因是：

1. `resolve_video_routing()` 把 `cheapest` 直接映射到 Fast。
2. Worker 只根据快照选择 MiniMax Provider 实例。
3. `VideoGenerationRequest` 没有锁定模型字段。
4. MiniMax `submit()` 内部重新执行 `pick_model(req)`。
5. `pick_model()` 只有 `quality=preview + mode=first_frame` 才选择 Fast；普通 `cheapest` 请求不会选择 Fast。

最终会出现：

- Quote 显示 Fast 价格。
- Budget 按 Fast 预留。
- Job.model 写 Fast。
- Provider 实际提交标准 Hailuo 2.3。
- Usage 和账单可能低估实际成本。

**其他快照缺陷**

1. `resolve_video_routing()` 只接收 policy/resolution，不接收 Shot 的 mode、duration、quality、首尾帧和参考素材，无法做能力匹配。
2. Worker 锁定分支只检查 `is_available()`，没有检查 `supports(req)`。
3. `explicit` 策略没有传入 `explicit_provider`，当前会回退为火山。
4. H3 实验开关开启时 Router 的 balanced 语义会改变，但价格层的静态 resolver 仍固定选择火山。
5. Quote 只保存 Provider/Model 展示字段，Job Planner 是重新解析，而不是从已确认 Quote 中复制不可变的 Shot 级快照。

**整改要求**

1. 路由规划函数必须接收完整 `VideoGenerationRequest` 或等价的 Shot 请求结构。
2. 快照按 Shot 生成，而不是只按 Step/Policy 生成。
3. Provider 接口需要支持明确的锁定模型，例如：

```python
submit(req, *, model: str, protocol: str | None = None)
```

或将 `locked_model` 纳入统一 Request，并要求 Adapter 严格使用它。
4. Worker 提交前校验：Provider 可用、Provider 支持当前请求、锁定模型属于该 Provider 且支持该请求。
5. Provider 返回的 `result.model` 必须与 Job.locked model 一致，否则标记 `model_mismatch`，不能按成功费用入账。
6. Quote 确认后直接将 Quote 中的 Shot 级快照复制到 Job，不要再次独立推导。
7. 增加 `balanced/cheapest/quality/explicit` 以及 H3 开关两种状态下的 Quote→Job→Provider model 一致性测试。

### R2.5 P0：新增 clone_voice API 存在租户隔离、路径穿越和费用绕过

**位置**

- `backend/app/routers/series.py:554`
- `backend/app/routers/series.py:560`
- `backend/app/routers/series.py:584`
- `backend/app/routers/series.py:587`
- `backend/app/worker/generation_worker.py:772`
- `backend/app/worker/generation_worker.py:807`
- `backend/app/tools/qwen_tts.py:99`
- `backend/app/tools/qwen_tts.py:110`

**问题一：参考音频没有 Artifact/项目归属校验**

API 仅校验目标 voice asset 属于当前项目，但直接接受：

```python
reference_audio_storage_key: str
```

没有检查该 storage key 对应哪个 Artifact、Artifact 是否属于当前项目、类型是否为 audio、当前用户是否有权访问。知道其他项目 storage key 的用户可以拿其他人的音频创建自己的音色。

**问题二：可构造路径穿越读取本地文件**

Worker 直接拼接：

```python
f"/storage/{storage_key}"
```

Qwen 工具再执行：

```python
file_path = BASE_DIR / audio_path.lstrip("/")
```

如果输入 `../../.env` 等路径，最终路径会逃离 `backend/storage`。文件内容会被 Base64 编码并发送到外部 Qwen API。这是本地敏感文件外传风险。

**问题三：收费任务绕过费用确认**

该 API 直接创建 `pending clone_voice` Job：

- 没有 Workflow Step。
- 没有 Cost Quote。
- 没有 `cost_confirmed` Gate。
- 没有音色创建价格项。
- 随机 UUID 幂等键导致用户重复点击会创建多个付费音色。

**问题四：请求契约不一致**

URL 已包含 `asset_id`，请求体又包含 `asset_id`，但代码没有校验两者是否相同，也完全没有使用 `body.asset_id`。

**整改要求**

1. API 只接受 `reference_audio_artifact_id`，禁止客户端直接传 storage key。
2. 服务端加载 Artifact 并检查：`project_id`、所有权、`type=audio`、文件存在、来源合法。
3. 使用 `Path.resolve()` 后检查路径必须位于 `storage` 根目录内，禁止 `..`、绝对路径和符号链接逃逸。
4. clone_voice 必须成为收费 Typed Node/领域命令，经过 Quote 和明确费用确认后才能创建 Job。
5. 幂等键使用 voice asset version + reference Artifact + provider/model + 参数 Hash，重复请求返回已有 Job。
6. 删除请求体中的重复 `asset_id`，或严格断言与 URL 一致。
7. 增加跨租户 Artifact、`../` 路径、重复点击和未确认费用四类安全测试。

### R2.6 P1：Retry 行锁可能仍读取 Session identity map 中的旧状态

**位置**

- `backend/app/routers/jobs.py:145`
- `backend/app/routers/jobs.py:152`
- `backend/app/routers/jobs.py:160`
- `backend/app/routers/jobs.py:209`

**问题**

代码先通过 `_get_job_checked()` 加载 Job，再对同一主键执行 `SELECT ... FOR UPDATE`。在 SQLAlchemy 同一 Session 中，后续查询通常返回 identity map 中已有 ORM 对象；如果没有 `populate_existing=True` 或显式 refresh，第二个并发事务在等待第一个事务提交后，可能仍看到进入等待前缓存的 `superseded_by=None`。

此外后续创建和回填继续主要使用预先加载的 `job`，而不是锁查询得到的 `locked_row`。当前没有真实双并发 API 测试证明只有一个新 Job。

**整改要求**

1. 将权限检查和行锁合并为一次带 ownership 条件的 `SELECT ... FOR UPDATE`。
2. 或给锁查询增加 `.execution_options(populate_existing=True)`，锁后显式 refresh。
3. 锁后统一使用 `locked_row` 完成状态检查、预算估算和 `superseded_by` 回填。
4. 增加两个独立 Session 同时请求 retry 的真实 PostgreSQL 测试，断言一个成功、一个 409，且只产生一个新 Job。

### R2.7 P1：迁移测试已更新，但仍没有真实升级/降级执行证据

**本轮改善**

- `test_migrations_integration.py` 已断言 Head `0006`。
- 已断言图片/视频选择列、旧列删除和 `cost_source`。
- 离线 upgrade/downgrade SQL 结构检查继续通过。

**仍缺少**

1. 当前 `TEST_DATABASE_URL=unset`，整个真实 PG 模块仍被跳过。
2. 集成测试只覆盖 upgrade head、历史 `0001` bootstrap 和 bootstrap 幂等。
3. 没有 `0005 → 0006 → 0005` 数据回填和 downgrade 测试。
4. 没有同时存在图片选择和视频选择时“降级优先视频”的数据断言。

### R2.8 P1：产品闭环仍未完成

| 项目 | 当前状态 |
|------|----------|
| MiniMax H3 | 默认仍关闭，无真实 API/基准验收。 |
| TTS voice_id 复用 | `QwenTTSProvider` 仍未直接使用已持久化 voice_id，多条对白仍会重新创建音色。 |
| clone_voice 前端 | 只有后端 API，没有上传、试听、审批和费用确认 UI。 |
| 图片候选 UI | `ShotCandidates` 仍只展示 `generate_video`。 |
| 视频候选状态 | 已选图片仍可能遮蔽已选视频的选中状态。 |
| 真实短剧 E2E | MiniMax、火山、DashScope Key 均未配置，没有真实最终成片和费用对账。 |

### R2.9 本轮验证结果

| 检查 | 结果 | 结论 |
|------|------|------|
| `python -m pytest -q` | `284 passed, 22 skipped, 12 warnings` | 通过，但新增风险均无测试覆盖，真实 PG 仍跳过。 |
| `ruff check app tests` | 通过 | 静态检查无法发现分支未定义变量。 |
| `npm run build` | 通过 | 有大 Chunk 警告，不阻断本轮。 |
| Provider 签名探针 | 全部通过 | 上轮火山 fetch 签名问题已修复。 |
| 锁定 Worker 探针 | 失败 | `UnboundLocalError: decision`。 |
| cheapest Model 探针 | 失败 | 快照为 Fast，Provider 实际选择标准 Hailuo 2.3。 |
| Alembic 离线 SQL | 通过结构检查 | upgrade/downgrade DDL 完整。 |
| 真实 PostgreSQL | 未执行 | `TEST_DATABASE_URL=unset`。 |
| 真实 Provider | 未执行 | 三个 Provider Key 均未设置。 |

### R2.10 下一轮最小整改清单

1. 将 `decision.mocked` 改为分支统一变量并增加锁定 Worker 测试。
2. 改造 Provider 接口，确保 locked model 真正传入并被 Adapter 使用。
3. 路由快照按完整 Shot 请求生成，并从确认 Quote 原样复制到 Job。
4. clone_voice 改为接收 Artifact ID，补 ownership、路径 confinement、费用 Gate 和确定性幂等。
5. Retry 锁查询强制 refresh，并补真实双并发测试。
6. 补真实 PG `0005 ↔ 0006` 数据迁移测试。
7. 完成 TTS voice_id 复用、图片候选 UI、H3 和真实成片验收。

---

## 最新结论：2026-08-05 第四次修改后复审

> 本节只记录第三次 Review 后的增量复核结果，优先级高于后续“第三次修改后复审”及所有历史章节。
> 状态定义：✅ 已满足；⚠️ 主体修复完成但缺集成/真实环境验收；❌ 仍不满足。

### R1.1 最终判断

本轮整改方向正确，上一版 4 个 P0 中已经完整修复 2 个、部分修复 1 个，另 1 个仍未形成费用执行快照。前端现在可以构建，`0006` 的离线升级/降级 SQL 已恢复为确定性 DDL，外部 submit 成功后的 task id 也会立即提交。

但仍有两个确定性 P0：

1. `VolcanoVideoProvider.fetch_result()` 没有接受 `protocol`，火山任务生成完成后 Worker 拉取结果时仍会抛 `TypeError`。
2. Cost Quote 虽然不再显示“H3 金额 + 火山名称”的直接矛盾，但仍是硬编码火山报价；Job Planner 继续按 H3 预留预算，Worker 执行时仍实时路由，没有锁定 Quote 中的 Provider/Model。

此外，真实 PG 迁移测试仍断言旧 Head `0005`，并发 retry、H3、音色复用和图片候选 UI 仍未完成。

**本轮结论：仍不通过整体完成验收，但已从“多个基础发布阻断”收敛为“两个核心 P0 + 四个产品/验收 P1”。**

### R1.2 本轮确认已经修复

| 上轮问题 | 状态 | 本轮结论 |
|----------|------|----------|
| 前端 `CostQuoteItem` 类型缺失 | ✅ | 已补 `provider/model/resolution/candidate_count`，`npm run build` 成功。 |
| `0006` 使用 `information_schema` 动态分支 | ✅ | 已改为确定性 Alembic DDL，离线 upgrade 会回填并删除旧列。 |
| `0006` downgrade 不恢复旧选择列 | ✅ | 已恢复旧列/外键，按 video 优先合并回填，并删除拆分列和 `cost_source`。 |
| Retry 两次 commit 崩溃窗口 | ✅ | 新 Job、`superseded_by` 回填已合并为一次 commit。 |
| 图片 `candidate_count` 无上限 | ✅ | Workflow 已限制到 1～4，火山 Provider 再次限制并使用 Semaphore。 |
| 外部 submit 后 task id 未立即提交 | ✅ | submit 成功后会立即 commit `external_task_id/provider/model/protocol_version`。 |
| VideoProvider 查询签名 | ⚠️ | Base、Mock、火山 `get_status` 已统一；火山 `fetch_result` 漏改，见 R1.3。 |
| 视频报价 Provider/Model 一致性 | ⚠️ | 报价显示与 Hash 已统一为火山，但 Quote、预算和 Worker 仍未共用执行快照，见 R1.4。 |

### R1.3 P0：火山视频完成后仍无法 fetch 产物

**位置**

- `backend/app/providers/base.py:91`
- `backend/app/providers/volcano_video.py:118`
- `backend/app/worker/generation_worker.py:305`

**问题**

抽象接口已经改成：

```python
fetch_result(external_task_id, *, protocol: str | None = None)
```

但火山 Adapter 仍是：

```python
async def fetch_result(self, external_task_id: str) -> ProviderResult:
```

Worker 对全部 Provider 统一调用：

```python
provider.fetch_result(job.external_task_id, protocol=protocol)
```

本轮直接签名探针结果：

```text
SIGNATURE_OK mock.get_status
SIGNATURE_OK mock.fetch_result
SIGNATURE_OK volcano.get_status
SIGNATURE_FAIL volcano.fetch_result: unexpected keyword argument 'protocol'
```

火山异步任务在 pending 阶段可以正常轮询，但一旦状态成功并进入结果下载，就会抛异常，最终无法形成视频 Artifact。因此默认 `balanced` 主链仍不可用。

**整改要求**

1. 将火山方法改为统一签名并忽略 `protocol`：

```python
async def fetch_result(
    self,
    external_task_id: str,
    *,
    protocol: str | None = None,
) -> ProviderResult:
```

2. 增加 Adapter 接口一致性参数化测试，遍历 Router 中全部 Provider，分别调用 `get_status/fetch_result(..., protocol="v1")`。
3. 增加完整 Mock 和火山状态机测试：submit → pending → succeeded → fetch → Artifact。

### R1.4 P0：视频 Quote、预算预留和 Worker 仍未锁定同一执行快照

**位置**

- `backend/app/services/cost_service.py:123`
- `backend/app/services/cost_service.py:212`
- `backend/app/services/cost_service.py:247`
- `backend/app/services/job_planner.py:225`
- `backend/app/services/job_planner.py:258`
- `backend/app/worker/generation_worker.py:188`

**本轮改善**

`_video_unit_price()` 现在同时返回火山 Provider、模型、分辨率和单价，Quote Item 与 `input_hash` 不再出现直接字段矛盾。

**仍存在的问题**

1. `_video_unit_price()` 没有真正调用 Router，而是直接写死：

```python
prov, model = "volcano", "doubao-seedance-1-5-pro"
```

`provider_policy=cheapest`、显式 Provider、火山不可用时的 MiniMax 路由都不会反映到报价。

2. Job Planner 预算预留仍使用：

```python
estimate_video_cost("minimax", "MiniMax-H3", ...)
```

报价可能按火山，预算却按 H3。

3. `build_video_job_params()` 只保存 `provider_policy`，没有保存已确认 Quote 的 Provider/Model/价格快照。

4. Worker 在首次执行时仍调用 `_router.select(req)`，Provider 可用性或配置变化会让已确认报价执行成另一个模型。

5. 全项目没有真正的 `routing_snapshot`，当前 Quote Hash 只能证明“报价时硬编码了某个模型”，不能证明 Worker 将执行该模型。

**整改要求**

1. 报价阶段根据每个 Shot 的真实请求调用 Router 的纯规划方法，生成：

```text
routing_snapshot
  shot_id
  provider
  model
  resolution
  protocol
  provider_policy
  price_snapshot
  estimated_cost
```

2. 将该快照放入 Quote Item/Hash，并在确认报价后复制到对应 Job。
3. Job Planner 按快照累计并预留预算，不再写死 H3。
4. Worker 有锁定 Provider 时直接取对应 Adapter，不再调用动态 Router。
5. 锁定 Provider 不可用时返回 `provider_unavailable`，要求重新报价；不能静默切换收费模型。
6. 增加 `balanced/cheapest/quality/explicit` 四种策略的 Quote → Job → Worker 一致性测试。

### R1.5 P1：迁移代码已修复，但迁移测试仍停留在 `0005`

**位置**

- `backend/tests/test_migrations_integration.py:9`
- `backend/tests/test_migrations_integration.py:121`
- `backend/tests/test_migrations_integration.py:126`
- `backend/tests/test_migrations_integration.py:146`

**问题**

测试仍断言：

- Alembic Head 为 `0005`。
- `shots.selected_artifact_id` 存在。

当前 Head 已是 `0006`，正确结构应为：

- `selected_image_artifact_id` 存在。
- `selected_video_artifact_id` 存在。
- `selected_artifact_id` 不存在。
- `usage_events.cost_source` 存在。

因为本地未设置 `TEST_DATABASE_URL`，这些测试在 `284 passed` 中被跳过。若 CI 配置真实 PG，当前断言会直接失败。

**整改要求**

1. 更新所有迁移集成测试的版本和列断言到 `0006`。
2. 增加 `0005 → 0006 → 0005` 数据回填测试。
3. 增加 `0006` stub/offline SQL 测试，不能只保留 `0005` 迁移测试。

### R1.6 P1：Retry 已单事务，但并发重复重试仍未解决

**位置**

- `backend/app/routers/jobs.py:142`
- `backend/app/routers/jobs.py:146`
- `backend/app/routers/jobs.py:164`
- `backend/app/routers/jobs.py:190`

**本轮改善**

创建新 Job 和回填旧 Job `superseded_by` 已在同一个事务中只 commit 一次，原崩溃窗口已关闭。

**仍存在的问题**

代码注释也明确说明“后续增加针对旧 job 的直接行锁”。当前没有：

1. `SELECT GenerationJob ... FOR UPDATE` 锁定旧 Job。
2. 锁内重新检查 `superseded_by`。
3. 已存在替代 Job 时返回已有 Job/409 的分支。

两个并发 retry 请求仍可能都读取到旧 Job 未被替代，然后各自创建新 Job。存在 Workflow Run 时的预算行锁不能替代旧 Job 行锁：第二个请求等待后，内存中的旧 Job 仍可能保持过期状态；没有 Workflow Run 的 Job 更没有间接锁。

**整改要求**

1. Retry 入口通过 `SELECT ... FOR UPDATE` 重新加载旧 Job。
2. 锁内检查 `status == failed` 和 `superseded_by is None`。
3. 对 `superseded_by` 非空返回 409 或已有替代 Job。
4. 增加真实 PostgreSQL 双并发 retry 测试，最终只能产生一个当前 Job。

### R1.7 P1：上轮产品闭环问题仍未修改

以下文件本轮没有对应实现变化，上一版结论继续有效：

| 项目 | 当前状态 | 仍需完成 |
|------|----------|----------|
| MiniMax H3 | ❌ | 默认仍为 `MINIMAX_H3_EXPERIMENTAL=false`，`balanced` 仍优先火山，无真实 H3 基准和沙箱记录。 |
| 音色复刻产品入口 | ❌ | 有 `clone_voice` executor，但无 API、Typed Node、费用 Gate 和前端操作创建该 Job。 |
| TTS 复用 voice_id | ❌ | `QwenTTSProvider` 仍不使用已持久化 `voice_id`；有参考音频时每句重新创建音色。 |
| 图片候选 UI | ❌ | `ShotCandidates` 仍固定请求 `generate_video`，没有图片候选预览和选择。 |
| 视频候选选中状态 | ❌ | `GeneratePanel` 仍优先传 `selected_image_artifact_id`，已选图片会遮蔽已选视频状态。 |
| 真实 Provider/成片验收 | ⚠️ | 当前 MiniMax、火山、DashScope Key 均未配置，没有真实 3～8 镜头成片证据。 |

详细整改要求继续参考后文 R0.8～R0.11。

### R1.8 本轮验证结果

| 检查 | 结果 | 结论 |
|------|------|------|
| `python -m pytest -q` | `284 passed, 22 skipped, 12 warnings` | 通过，但真实 PostgreSQL 测试仍跳过。 |
| `ruff check app tests` | 通过 | Python 静态检查通过。 |
| `npm run build` | 通过 | 上轮前端构建 P0 已修复；仍有大 Chunk 警告，不阻断本轮。 |
| Provider 签名探针 | 3 项通过、1 项失败 | `volcano.fetch_result(..., protocol=...)` 仍失败。 |
| `alembic upgrade head --sql` | 通过结构检查 | 会新增拆分列、回填、删除旧列并新增 `cost_source`。 |
| `alembic downgrade 0006:0005 --sql` | 通过结构检查 | 会恢复旧列/外键、回填、删除拆分列和 `cost_source`。 |
| 真实 PostgreSQL | 未执行 | `TEST_DATABASE_URL=unset`。 |
| 真实 Provider E2E | 未执行 | `MINIMAX_API_KEY/VOLCANO_API_KEY/DASHSCOPE_API_KEY` 均未设置。 |

### R1.9 下一轮最小整改清单

按以下顺序完成后再复审：

1. 补齐 `VolcanoVideoProvider.fetch_result(..., protocol=None)`。
2. 实现 Quote → Job → Worker 共用的 `routing_snapshot`，删除 Job Planner 中 H3 写死预留。
3. 更新 `test_migrations_integration.py` 到 `0006` 并运行真实 PG。
4. Retry 增加旧 Job 行锁和 `superseded_by` 二次检查。
5. 完成图片候选独立选择和视频选择状态修复。
6. 完成 clone_voice 创建入口及 TTS `voice_id` 复用。
7. 完成 H3 和真实短剧 E2E 验收。

---

## 最新结论：2026-08-05 第三次修改后复审

> 本节基于当前工作区最终代码重新 Review，优先级高于后续所有 2026-08-04 历史结论。
> 状态定义：✅ 已满足；⚠️ 主体实现已完成但仍缺真实环境验收；❌ 未满足或存在发布阻断。

### R0.1 最终判断

本轮又修复了多项上次 Review 问题，尤其是图片多候选生成、图片计价、Shot 图片/视频选择槽拆分、零 Job 节点推进、Planner 参数落地、MiniMax 协议持久化和 Usage 费用来源记录。后端领域模型、Workflow、Worker 和短剧工作台已经具备较高完整度。

但当前仍存在以下确定性阻断：

1. 前端 TypeScript 编译失败，无法产生可部署产物。
2. Worker 对火山和 Mock 视频 Provider 传入不支持的 `protocol` 参数，默认 `balanced` 视频任务提交后会在第一次轮询直接抛 `TypeError`。
3. `0006` 迁移无法正确降级，离线 SQL 也不能完整表达升级/降级，真实 PostgreSQL 迁移测试仍停留在 `0005`。
4. 视频报价显示的是火山模型，金额和 Hash 却按 MiniMax H3 计算，确认报价时也没有锁定实际执行 Provider/Model。
5. Job 重试仍存在两次提交的崩溃窗口。
6. MiniMax H3、真实 Qwen 音色复刻、真实 Provider 完整成片和 Worker 重启恢复均没有真实环境验收证据。

因此当前状态可以认定为：

- ✅ 后端功能开发基本完成。
- ✅ Mock/单元测试级功能较完整。
- ⚠️ 可以继续做 Staging 联调。
- ❌ 不能认定为“整体项目已经完成”。
- ❌ 不能直接开放生产收费生成。

**本轮最终结论：不通过。至少修复 R0.3～R0.6 的 P0 问题，并完成 R0.11 的真实环境验收后，才可重新申请整体完成验收。**

### R0.2 本轮确认已经修复或明显改善

| 项目 | 状态 | 当前结论 |
|------|------|----------|
| 图片 `candidate_count` | ✅ | 火山图片 Provider 会按候选数发起多次生成，并为每个成功候选创建独立 Artifact。 |
| 图片费用计算 | ✅ | 报价、预算预留和重试估算已统一读取图片价格表，不再散落硬编码常量。 |
| Shot 选择槽 | ⚠️ | 后端模型和服务已拆为 `selected_image_artifact_id` / `selected_video_artifact_id`；迁移和前端候选交互仍有问题，见 R0.5、R0.10。 |
| 零 Job 节点 | ✅ | 无对白/空任务节点会标记为 `skipped`，不再永久停在 `running`。 |
| Step 与 Job 规划事务 | ✅ | Job Planner 不再内部提交，Step 展开和状态更新可由上层统一提交。 |
| 已提交视频任务恢复 | ⚠️ | Worker 会使用 Job 上持久化的 Provider 和 `protocol_version`，不再重新路由；但非 MiniMax Adapter 的方法签名不兼容，见 R0.4。 |
| Planner 参数落地 | ✅ | `fast/cheap` 已规范化为 `cheapest`，策略进入 `default_policy`，计划集数可自动补齐 Episode。 |
| Usage 费用来源 | ✅ | 已使用 `estimated_cost` 并增加 `cost_source=estimate`，避免把估算值伪装成实际账单。 |
| 安全、费用 Gate、暂停/取消 | ✅ | 上一轮确认修复的项目级权限、报价确认绑定、预算行锁和提交前状态检查仍保留。 |

### R0.3 P0：前端构建失败，当前版本不可发布

**位置**

- `frontend/src/api/series.ts:192`
- `frontend/src/components/studio/CostQuotePanel.tsx:99`

**问题**

后端 `CostQuoteItem` 已增加 `provider/model/resolution/candidate_count`，但前端类型仍只有：

```ts
export interface CostQuoteItem {
  step_node_key: string
  operation: string
  unit_count: number
  unit_price: string
  subtotal: string
}
```

`CostQuotePanel` 已访问 `it.provider` 和 `it.model`，导致 `npm run build` 失败：

```text
CostQuotePanel.tsx(99): Property 'provider' does not exist on type 'CostQuoteItem'
CostQuotePanel.tsx(99): Property 'model' does not exist on type 'CostQuoteItem'
```

**整改要求**

1. 在前端 `CostQuoteItem` 增加与后端一致的字段，建议至少包含：

```ts
provider?: string
model?: string
resolution?: string
candidate_count?: number
```

2. 重新执行 `npm run build`，必须无 TypeScript 错误。
3. 增加 Cost Quote API 响应的类型契约测试，避免后端 Schema 更新后前端类型再次漂移。

### R0.4 P0：火山和 Mock 视频任务提交成功后无法轮询

**位置**

- `backend/app/worker/generation_worker.py:279`
- `backend/app/worker/generation_worker.py:302`
- `backend/app/providers/base.py:90`
- `backend/app/providers/volcano_video.py:87`
- `backend/app/providers/volcano_video.py:118`
- `backend/app/providers/mock_video.py:69`
- `backend/app/providers/mock_video.py:107`
- `backend/app/providers/minimax_video.py:370`
- `backend/app/providers/minimax_video.py:458`

**问题**

Worker 当前对所有视频 Provider 都调用：

```python
provider.get_status(task_id, protocol=protocol)
provider.fetch_result(task_id, protocol=protocol)
```

但只有 `MiniMaxVideoProvider` 接受 `protocol` 参数。`VideoProvider` 抽象接口、`VolcanoVideoProvider` 和 `MockVideoProvider` 均只接受 `external_task_id`。

本轮直接调用探针结果：

```text
mock.get_status: unexpected keyword argument 'protocol'
mock.fetch_result: unexpected keyword argument 'protocol'
volcano.get_status: unexpected keyword argument 'protocol'
volcano.fetch_result: unexpected keyword argument 'protocol'
```

默认 `balanced` 在 H3 实验开关关闭时优先火山，因此正常视频链路会出现：提交成功 → 持久化 task id → 第一次轮询抛异常 → Job 进入 `executor_exception` 重试。该问题不是边界条件，而是默认路径确定会触发的运行时错误。

**整改要求**

推荐统一 Provider 接口，而不是在 Worker 中按具体类分支：

```python
async def get_status(
    self,
    external_task_id: str,
    *,
    protocol: str | None = None,
) -> ProviderResult: ...

async def fetch_result(
    self,
    external_task_id: str,
    *,
    protocol: str | None = None,
) -> ProviderResult: ...
```

- MiniMax 使用 `protocol`。
- 火山和 Mock 接受但忽略该参数。
- 抽象基类、全部 Adapter 和测试替身保持同一签名。

必须补充以下回归测试：

1. Mock submit → Worker 下一轮 poll → fetch → succeeded。
2. 火山已存在 `external_task_id` → poll/fetch 不重新路由且不抛签名错误。
3. MiniMax H3/V1 在 Worker 重启后分别使用 Job 中持久化的协议。

### R0.5 P0：`0006` 数据库迁移不满足升级、降级和离线 SQL 要求

**位置**

- `backend/alembic/versions/0006_split_shot_selection_cost_source.py:26`
- `backend/alembic/versions/0006_split_shot_selection_cost_source.py:154`
- `backend/tests/test_migrations_integration.py:120`

**问题一：在线 downgrade 不可逆**

`downgrade()` 只删除 `selected_image_artifact_id` 和 `selected_video_artifact_id`，没有：

1. 重新创建旧字段 `selected_artifact_id`。
2. 重新创建旧外键 `fk_shots_selected_artifact_id`。
3. 将图片/视频选择回填到旧字段。

降级到 `0005` 后，数据库结构与 `0005` 定义不一致。

**问题二：离线 upgrade SQL 不完整**

迁移通过查询 `information_schema` 决定执行分支。Alembic `--sql` 离线模式无法根据查询结果选择 Python 分支。本轮生成的 `upgrade head --sql` 同时出现：

```text
ADD COLUMN selected_artifact_id
SELECT ... information_schema ... selected_artifact_id
ADD COLUMN selected_image_artifact_id
ADD COLUMN selected_video_artifact_id
```

但没有删除旧 `selected_artifact_id`，最终离线升级结构与在线升级结构不同。

**问题三：离线 downgrade 实际没有执行结构回滚**

本轮生成的 `downgrade 0006:0005 --sql` 只有 12 行，除版本更新外只输出两条 `information_schema` 查询，没有 `ALTER TABLE`、`DROP COLUMN` 或恢复旧字段的 DDL。

**问题四：真实 PG 测试已经过期**

`test_migrations_integration.py` 仍断言：

- Head 为 `0005`。
- `shots.selected_artifact_id` 存在。

本轮完整 pytest 中这组测试因为未设置 `TEST_DATABASE_URL` 被跳过，所以没有发现问题。当前也没有针对 `0006` 的迁移单测。

**整改要求**

1. `0006` 应基于已知前置版本 `0005` 编写确定性迁移，不在版本脚本内用 `information_schema` 控制核心 DDL 分支。
2. Upgrade 固定执行：新增拆分列 → 建外键 → 按 Artifact 类型回填 → 删除旧外键/旧列 → 新增 `cost_source`。
3. Downgrade 固定执行：恢复旧列/外键 → 按明确优先级回填，例如 `COALESCE(selected_video_artifact_id, selected_image_artifact_id)` → 删除拆分列 → 删除 `cost_source`。需要在迁移说明中明确双选择降级为单选择是有损操作。
4. 更新真实 PostgreSQL 测试 Head 为 `0006`，断言拆分列和 `cost_source`，并断言旧列不存在。
5. 增加 `0005 → 0006 → 0005` 数据回填测试，以及 `upgrade/downgrade --sql` 中关键 DDL 的断言。

### R0.6 P0：视频报价、费用 Hash 和实际路由仍不是同一个执行快照

**位置**

- `backend/app/services/cost_service.py:123`
- `backend/app/services/cost_service.py:197`
- `backend/app/services/cost_service.py:266`
- `backend/app/services/job_planner.py:225`
- `backend/app/worker/generation_worker.py:188`
- `backend/app/providers/video_router.py:106`

**问题**

当前同一个视频报价存在三种互相矛盾的含义：

1. `_video_unit_price()` 按 `minimax / MiniMax-H3` 计算单价。
2. 报价明细写死显示 `volcano / doubao-seedance-1-5-pro`。
3. 报价 `input_hash` 又写死 `video_model=MiniMax-H3`。
4. Job Planner 预留预算时仍按 H3 计算。
5. Job 创建时不锁 Provider/Model，Worker 首次执行时才根据实时可用性动态路由。
6. 当前默认 `balanced` 又优先火山，H3 默认不参与候选。

因此用户确认的“Provider、模型、规格、金额”不能证明最终实际执行内容；Provider 可用性变化后，同一个已确认报价可能执行不同模型。该实现不满足方案中的“批量收费前展示完整、可复现费用报价”和“价格快照锁定”要求。

另外，`backend/app/providers/pricing.py:46` 明确标记火山价格仍是待核实的占位价，不能作为生产收费依据。

**整改要求**

推荐在报价阶段完成一次可持久化的路由规划：

1. 为每个收费 Step/Shot 生成 `routing_snapshot`：`provider/model/resolution/protocol/pricing_dimensions/price_table_version`。
2. Quote Item、Quote Hash、预算预留和 Job params 全部读取同一份快照。
3. 用户确认后，Worker 必须按已锁定 Provider/Model 执行，不能静默改路由。
4. 锁定 Provider 不可用时，Job 应进入 `provider_unavailable` 并要求重新报价/确认；如果产品允许自动兜底，则报价必须按可选 Provider 的最大成本预留，并把兜底规则写入 Hash。
5. 将火山正式价格写入版本化价格表并记录来源、币种、含税规则和生效日期，删除“占位价”后才能用于生产费用确认。

### R0.7 P1：Job retry 仍存在两次提交和并发重复重试窗口

**位置**

- `backend/app/routers/jobs.py:145`
- `backend/app/routers/jobs.py:169`
- `backend/app/routers/jobs.py:185`
- `backend/app/routers/jobs.py:188`

**问题**

Retry 当前顺序为：

1. 预留重试预算。
2. 创建并提交新 Job。
3. 刷新新 Job。
4. 设置旧 Job 的 `superseded_by`。
5. 再次提交。

如果进程在第 2 次提交前崩溃，旧 Job 仍会被聚合为当前 Job，同时新 Job 也已经存在，Step 聚合和用户重复点击重试都可能产生重复执行。两个并发 retry 请求还可能同时通过 `superseded_by` 空值检查并各自创建新 Job。

**整改要求**

1. 对旧 Job 使用 `SELECT ... FOR UPDATE`。
2. 在同一个事务内完成预算预留、创建新 Job、`flush()` 获取新 ID、回填旧 Job `superseded_by`，最后只提交一次。
3. 锁内再次检查 `superseded_by`；已有替代 Job 时返回已有 Job 或 409。
4. 增加并发双击 retry 和“创建新 Job 后事务回滚”的真实数据库测试。

### R0.8 P1：MiniMax H3 仍未完成方案验收

**位置**

- `backend/app/providers/minimax_video.py:29`
- `backend/app/providers/minimax_video.py:78`
- `backend/app/providers/video_router.py:106`
- `backend/env.example:64`

**当前进展**

- ✅ H3 和 Hailuo V1 协议已区分。
- ✅ submit 返回的协议已写入 `job.params.protocol_version`。
- ✅ 已提交任务不会因 Worker 重启重新路由到其他 Provider。

**仍未满足**

1. `MINIMAX_H3_EXPERIMENTAL=false` 仍是默认配置。
2. 默认 `balanced` 仍优先火山；只有显式开启实验开关才把 MiniMax/H3 放到前面。
3. 代码注释仍明确写着 H3 请求/响应协议未经官方文档和真实沙箱核验。
4. 没有 20～30 个标准镜头的 H3/Hailuo/火山基准报告。
5. 当前环境未配置 `MINIMAX_API_KEY`，没有真实 H3 成功任务、失败分类和账单对账记录。

**整改要求**

1. 使用当前账号和官方 API 文档完成 H3 submit/query/fetch/cancel 的真实联调。
2. 固化请求/响应 Fixture，覆盖文本、首帧、首尾帧、参考图、参考音频、4～15 秒和 2K 支持范围。
3. 用统一镜头集记录成功率、P50/P95 时延、返工率、实际成本和人工质量评分。
4. 基准通过后先在 Staging 开启 H3，再按路由版本逐步启用生产 `balanced`。
5. H3 纳入整体生成的理由应是多模态和质量能力，不应写成“因为 API 便宜”；当前价格表中 H3 仍高于 Hailuo Fast。

### R0.9 P1：音色复刻 Worker 已有，但用户可用闭环仍未完成

**位置**

- `backend/app/worker/generation_worker.py:713`
- `backend/app/providers/tts_base.py:155`
- `backend/app/providers/tts_base.py:228`
- `backend/app/services/job_planner.py:397`
- `frontend/src/components/studio/AssetPanel.tsx:1`

**当前进展**

- ✅ 已实现 `clone_voice` executor。
- ✅ Executor 可回写 `voice_id/reference_audio`，创建试听 Artifact，并将资产状态设为 `preview_ready`。
- ✅ 已有 Mock/Monkeypatch 级单元测试。

**仍未满足**

1. 正常产品路径没有 API、Typed Node、领域服务或前端按钮创建 `clone_voice` Job；现有 executor 只能被测试或手工插库触发。
2. Planner 会把资产的 `provider_meta.voice_id` 写入 TTS Job，但 `QwenTTSProvider._synthesize_sync()` 不使用 `req.voice_id`。
3. Worker 同时传入资产的 `voice_id` 和 `reference_audio` 时，Provider 优先走 `voice_reference`，每条对白都会再次创建音色。
4. 只有 `voice_id`、没有参考音频时，Provider 会走默认声音设计，也没有复用已创建音色。
5. Quote 只计算 TTS 秒数，没有独立计算音色创建费用。
6. 当前环境未配置 `DASHSCOPE_API_KEY`，没有真实声音复刻、试听、审批和多句复用验收记录。

**整改要求**

1. 增加“上传参考音频 → 创建 clone Job → 试听 → 审批 → 保存 voice_id”的 API、Job Service 和前端操作。
2. 增加使用已持久化 `voice_id` 直接合成的 Provider 方法；正常 TTS 不再重新创建音色。
3. 将音色创建和文本合成拆成不同计价项及不同幂等键。
4. 只允许 `approved` 的 voice asset 进入批量 TTS。
5. 增加真实集成测试：同一角色 3 条对白只创建一次音色，后续请求都使用同一个 `voice_id`。

### R0.10 P1：候选产物前端仍只完成视频的一半交互

**位置**

- `frontend/src/components/studio/ShotCandidates.tsx:94`
- `frontend/src/components/studio/ShotCandidates.tsx:116`
- `frontend/src/components/studio/GeneratePanel.tsx:20`
- `frontend/src/components/studio/GeneratePanel.tsx:168`

**问题**

1. `ShotCandidates` 固定请求 `generate_video`，没有图片候选列表、图片预览和图片选择入口。
2. `shotSelectedArtifactId()` 返回：

```ts
shot.selected_image_artifact_id ?? shot.selected_video_artifact_id
```

3. 当前组件展示的是视频候选，但只要该 Shot 已选首帧图片，传给视频卡片的 `selectedArtifactId` 就会是图片 ID，已选视频不会显示“已选定”。
4. 组件注释和属性说明仍引用旧的 `Shot.selected_artifact_id`，说明前端契约没有完整跟随后端拆分。

**整改要求**

1. 图片候选和视频候选分开传递选择状态：

```ts
selectedImageArtifactId
selectedVideoArtifactId
```

2. 增加图片候选 Tab/区域，请求 `generate_image`，使用受保护图片组件预览。
3. 视频候选卡只比较 `selected_video_artifact_id`，图片候选卡只比较 `selected_image_artifact_id`。
4. 增加 E2E：先选择图片 A，再选择视频 B，刷新页面后两个选择均保持且分别显示选中。

### R0.11 P1：真实环境和生产可靠性验收仍缺失

**无界付费并发**

- `backend/app/services/workflow_nodes.py:70` 只声明 `candidate_count` 为整数，没有范围限制。
- `backend/app/providers/volcano_image.py:63` 对任意 `candidate_count` 创建任务列表并直接 `asyncio.gather()`。
- 用户可通过项目策略输入过大的候选数，造成瞬时并发付费调用和预算占用。

整改：按方案限制为 1～4，Schema、Planner、Quote、Job Planner 和 Provider 五层都校验；Provider 内部还要使用有限并发 Semaphore。

**外部任务提交后的崩溃窗口**

- `backend/app/worker/generation_worker.py:237` 在 Provider 已接受任务后只 `flush()` 保存 `external_task_id`。
- 只有后续轮询返回或 `_process_job()` 结束时才提交事务。
- 如果进程在外部 submit 成功后、数据库 commit 前崩溃，task id 会丢失，租约恢复后可能重复提交收费任务。

整改：Provider submit 成功后立即持久化并提交 `provider/model/external_task_id/protocol_version/price_snapshot`，再进入轮询；如果 Provider 支持客户端幂等键，应使用 Job `idempotency_key` 作为请求幂等键。

**真实验收缺口**

当前环境状态：

```text
TEST_DATABASE_URL=unset
MINIMAX_API_KEY=unset
VOLCANO_API_KEY=unset
DASHSCOPE_API_KEY=unset
```

因此本轮无法证明：

- `0001/0005 → 0006` 真实 PostgreSQL 升级和降级可用。
- 火山图片、火山视频、MiniMax H3/Hailuo、Qwen TTS/Voice Clone 请求可真实成功。
- Worker 重启后已提交任务可恢复。
- 1 集 3～8 镜头可以生成包含首帧、视频、对白、字幕、BGM 的真实最终成片。
- UsageEvent 与 Provider 实际账单一致。

### R0.12 本轮验证结果

| 检查 | 结果 | 结论 |
|------|------|------|
| `python -m pytest -q` | `284 passed, 22 skipped, 12 warnings` | 单元/Mock 测试通过；真实 PG 测试被跳过。 |
| `ruff check app tests` | 通过 | Python 静态检查通过。 |
| `npm run build` | 失败 | `CostQuoteItem.provider/model` 类型缺失。 |
| Mock/火山视频签名探针 | 失败 | 四个 `get_status/fetch_result(..., protocol=...)` 调用均抛 `TypeError`。 |
| `alembic upgrade head --sql` | 可生成但结构不一致 | `0006` 离线路径保留旧选择列。 |
| `alembic downgrade 0006:0005 --sql` | 失败验收 | 只有查询和版本更新，没有实际结构回滚 DDL。 |
| 真实 PostgreSQL | 未执行 | `TEST_DATABASE_URL` 未配置。 |
| 真实 Provider E2E | 未执行 | MiniMax、火山、DashScope Key 均未配置。 |

### R0.13 建议整改顺序

**第一批：当天必须修复的确定性错误**

1. 修复前端 `CostQuoteItem` 类型并确保构建通过。
2. 统一 VideoProvider poll/fetch 方法签名。
3. 修复 `0006` upgrade/downgrade 和迁移测试。
4. 将视频路由结果锁定到 Quote/Job 执行快照。

**第二批：事务和付费安全**

1. 将 Job retry 改为单事务并增加行锁。
2. 限制 `candidate_count=1..4` 和 Provider 内部并发。
3. 外部 submit 成功后立即提交 task id，并接入 Provider 幂等键。
4. 核实并版本化火山正式价格。

**第三批：产品闭环**

1. 增加图片候选选择 UI，修复图片/视频选择状态传递。
2. 增加 clone_voice API、费用 Gate、试听审批和前端入口。
3. 让 TTS 使用已持久化 `voice_id`，禁止每条对白重复创建音色。

**第四批：上线验收**

1. 真实 PostgreSQL 空库、老库、升级、降级测试。
2. MiniMax H3/Hailuo、火山图片/视频、Qwen TTS 沙箱联调。
3. 1 集 3～8 镜头完整真实成片。
4. Worker 在 submit 后、poll 中和 fetch 中被强制终止的恢复测试。
5. 费用报价、UsageEvent 和 Provider 账单对账。

### R0.14 重新验收门槛

下列条件必须全部满足，才建议将项目标记为“整体完成”：

- [ ] 前后端均可构建，完整测试无失败。
- [ ] 默认 `balanced` 视频链路 submit/poll/fetch 可完成，不出现 Adapter 签名错误。
- [ ] `0006` 可在真实 PostgreSQL 上空库升级、老库升级和降级。
- [ ] Quote、Hash、预算、Job 和 Worker 使用同一 Provider/Model/价格快照。
- [ ] Retry、取消、暂停和 Worker 崩溃恢复后无重复当前 Job、无重复收费提交。
- [ ] 图片和视频候选均可独立选择，最终渲染消费正确选择的视频。
- [ ] 音色只创建一次，多条对白复用同一已审批 `voice_id`。
- [ ] MiniMax H3 已完成真实 API 联调和基准评估，并按已发布路由版本启用。
- [ ] 真实 Provider 生成的 1 集短剧端到端成功。
- [ ] 费用估算、实际用量和 Provider 账单完成对账。

---

## 零、2026-08-04 第二次修改后复审结论

> 本节基于用户本轮重新修改后的代码复核，优先级高于后续历史 Review 内容。
> 状态定义：✅ 已满足；⚠️ 代码已实现但缺少真实环境验收，或实现仍不完整；❌ 不满足。

### 0.1 最终判断

本轮已经修复上一版的大部分安全、费用 Gate、重试聚合和产品入口问题，项目不再只是 Mock 骨架，已经具备较完整的短剧工作台和任务执行框架。但是对照 `docs/series-video-optimization-plan.md` 第十一章关键验收标准，仍存在会造成错误报价、预算穿透、任务无法恢复、候选选择互相覆盖和工作流永久卡住的问题。

当前可以用于：

- 本地开发和 Mock 演示。
- 在人工控制下验证火山图片、火山/Hailuo 视频和 Qwen TTS 单项能力。
- 继续补充真实 PostgreSQL、Provider 沙箱和完整成片联调。

当前仍不适合：

- 标记为“整体项目已经完成”。
- 直接开放生产收费生成。
- 宣称 MiniMax H3 已纳入默认 `balanced` 整体生成。
- 宣称候选图/候选视频、音色复刻和 Agent Planner 已形成完整产品闭环。

**本轮最终结论：不通过整体完成验收，整改完本节 P0/P1 后再复审。**

### 0.2 本轮确认已经修复的项目

| 项目 | 状态 | 复审结论 |
|------|------|----------|
| `/storage` 项目级权限 | ✅ | 已按 `Artifact.storage_key` 反查项目并走统一 ownership；未知历史文件默认 404。 |
| 无主 SeriesProject | ✅ | 已集中使用 `ownership.check_project_access`，普通用户不能访问 `user_id=NULL` 的历史项目。 |
| Feedback 跨项目 Step 写入 | ✅ | 已校验 Step→Run.project 与 Artifact.project，并校验 Artifact 来源 Job 的 Step。 |
| 真实图片 Provider 注册 | ⚠️ | 已注册 `VolcanoImageProvider`，无 Key 时不再默认伪成功；但本轮没有真实火山沙箱成功记录。 |
| 费用确认绑定报价 | ✅ | 已增加 `quote_id`、有效期、输入 Hash、价格表版本校验。 |
| 收费节点费用 Gate | ✅ | 图片、视频和 TTS 节点均声明 `requires_cost_confirm=True`。 |
| 并发预算检查 | ✅ | `reserve_budget` 已使用 `SELECT ... FOR UPDATE`。 |
| Step/Job 重试聚合 | ✅ | 已增加 `step_attempt`、`logical_job_key`、`superseded_by`，聚合只看当前 attempt 和未被替代 Job。 |
| Pause/Cancel 提交前检查 | ✅ | Worker 在 Provider submit 前会重新读取 Job/Run 状态。 |
| MiniMax 公开 V1 主路径 | ⚠️ | Hailuo V1 创建、查询和文件下载结构已按公开协议重写；仍缺真实 API Key 联调。 |
| LLM Planner 入口 | ⚠️ | API、Agent Tool 和前端均已接入 `use_llm_planner`，但规划参数没有完整作用到领域实体和执行策略。 |
| 个人资产中心 | ✅ | 已有个人资产创建、列表、版本、复制到项目的 API 和前端 Tab。 |
| 前端 StudioHome 分包 | ✅ | `StudioHome` 已形成独立 Chunk，不再出现上次“动态 import 未生效”的问题。 |

### 0.3 P0：MiniMax H3 仍未达到方案要求

**位置**

- `backend/app/providers/minimax_video.py:29`
- `backend/app/providers/minimax_video.py:78`
- `backend/app/providers/minimax_video.py:133`
- `backend/app/providers/minimax_video.py:347`
- `backend/app/providers/minimax_video.py:361`
- `backend/app/providers/video_router.py:106`

**问题**

1. H3 仍由 `MINIMAX_H3_EXPERIMENTAL=false` 默认关闭。
2. 默认 `balanced` 实际优先火山，而不是方案要求的“经过基准验证后 H3 作为 balanced 主候选”。
3. H3 创建、查询、再生成协议仍在代码中明确标记为未经公开文档和真实沙箱核验。
4. H3/V1 协议类型只保存于 Provider 实例的内存 `_task_protocol`。Worker 重启后 H3 task 会默认按 V1 查询，无法满足“重启后任务仍可恢复”。
5. 没有 20-30 个标准镜头的 H3/Hailuo/火山成功率、耗时、实际成本、返工率和质量评分基准结果。

**整改要求**

1. 取得当前账号真实可用的 H3 API Schema 和模型名，完成创建、查询、下载、审核拒绝、限流、超时和再生成真实联调。
2. 把 `protocol/capability/api_version` 持久化到 `GenerationJob.params` 或独立字段，查询时只读 Job 快照，不能依赖进程内字典。
3. 完成标准镜头基准后再开启 H3 `balanced` 路由；未通过基准时保持显式/实验开关是正确的安全策略，但不能标记为方案已完成。
4. 增加 Worker 重启测试：H3 submit 成功落库 → 新 Provider 实例查询 → 成功下载 Artifact。

### 0.4 P1：图片候选数量没有真正生成

**位置**

- `backend/app/providers/volcano_image.py:55`
- `backend/app/providers/volcano_image.py:64`
- `backend/app/providers/volcano_image.py:85`
- `backend/app/worker/generation_worker.py:372`
- `backend/app/worker/generation_worker.py:397`
- `backend/app/worker/generation_worker.py:413`

**问题**

`ImageGenerationRequest.candidate_count` 已进入 Job 和报价，但 `VolcanoImageProvider.generate()` 始终只调用一次工具并返回一张图片。Worker 也只读取 `result.artifacts[0]` 并创建一个 Artifact。

当 `candidate_count=3` 时，当前行为是：

- 报价和预算按 3 张计算。
- Worker 用量也按请求的 3 张估算成本。
- 实际只生成并建档 1 张。
- 前端无法完成图片候选对比。

这同时违反“候选选择”和“收费数量可复现”验收标准。

**整改要求**

1. Provider 原生支持 `n` 时直接请求 `candidate_count`；不支持时由 Adapter 有限并发调用 N 次。
2. `ProviderResult.artifacts` 返回全部图片，Worker 为每张图片创建独立 Artifact，不得只取第一张。
3. Job result 保存 `artifact_ids`、期望数量、成功数量和失败数量。
4. 实际费用按成功请求/Provider 实际账单记录；不足 N 张时明确标记 partial/failed，不能静默成功。
5. 增加 `candidate_count=3 → 3 个 Artifact` 的真实数据库测试。

### 0.5 P1：图片报价和预算仍按错误单价计算

**位置**

- `backend/app/services/cost_service.py:34`
- `backend/app/services/cost_service.py:179`
- `backend/app/services/cost_service.py:375`
- `backend/app/services/job_planner.py:31`
- `backend/app/services/job_planner.py:282`
- `backend/app/providers/pricing.py:52`

**问题**

当前存在两套互相冲突的图片价格：

- Quote、初次预算预留和重试预算：`¥0.10/张`。
- Provider pricing 表和 Worker 实际用量：火山 Seedream `¥0.20/张`。

因此用户确认的报价和 `cost_limit` 只预留实际估算成本的一半。即使 `SELECT FOR UPDATE` 正确，也会因为输入金额错误而发生预算穿透。

**整改要求**

1. 删除 `cost_service.IMAGE_PRICE_PER_PIECE` 和 `job_planner.IMAGE_PRICE_PER_SHOT` 两套常量。
2. Quote、初次预留、重试预留和 Usage 全部调用同一个 `estimate_image_cost(provider, model, count)`。
3. 在费用确认前锁定图片 Provider/Model；如果仍采用动态策略，必须按可选路由的最高成本报价，或在 submit 前对差额再次原子预留。
4. 增加测试：报价总额、Run 预留额和 Worker 估算额必须完全相等。

### 0.6 P1：视频报价没有锁定实际 Provider/Model/规格

**位置**

- `backend/app/services/cost_service.py:44`
- `backend/app/services/cost_service.py:95`
- `backend/app/services/cost_service.py:170`
- `backend/app/services/cost_service.py:233`
- `backend/app/providers/video_router.py:72`
- `frontend/src/components/studio/CostQuotePanel.tsx:85`

**问题**

1. `CostQuoteItem` 只有 operation、数量、单价和小计，没有 Provider、Model、Resolution、Mode 和候选数等可复现规格。
2. 视频报价无论实际策略如何，都按 `MiniMax-H3` baseline 计算并写入 input hash。
3. H3 默认关闭且 `balanced` 优先火山，报价展示与实际路由并不一致。
4. Provider/Model 直到 Worker 执行时才写入 Job，不符合方案“Job 创建时锁定路由和价格快照，执行期间不再自动变更”的要求。

**整改要求**

1. 在 Quote 阶段调用只读 Router/Capability Resolver，得到每类 Job 的确定 `provider/model/resolution/mode/unit_price/price_snapshot`。
2. 把上述字段加入 `CostQuoteItem` 和前端报价表。
3. 用户确认后将路由快照写入 GenerationJob；Worker 只能执行已锁定 Provider。
4. 如果 Provider 不可用，需要生成新的报价并重新确认，不能静默换 Provider 或继续使用旧报价。

### 0.7 P1：已提交的视频任务在轮询时会被重新路由

**位置**

- `backend/app/worker/generation_worker.py:186`
- `backend/app/worker/generation_worker.py:197`
- `backend/app/worker/generation_worker.py:218`
- `backend/app/worker/generation_worker.py:247`

**问题**

`execute_generate_video()` 每次进入都会先执行 `_router.select(req)`，随后覆盖 `job.provider/job.model`。即使 `job.external_task_id` 已存在，代码也是在覆盖之后才尝试“按 job 上记录的 provider 继续轮询”。

当 Worker 重启、API Key/可用性变化、路由配置变化或 H3 开关变化时，已有火山 task 可能被 MiniMax Adapter 查询，反之亦然，最终任务会永久失败或错误重提。这直接违反任务恢复和 Provider 快照要求。

**整改要求**

1. `external_task_id` 为空时才允许 Router 选择 Provider，并立即持久化 Provider/Model/协议/价格快照。
2. `external_task_id` 非空时只按持久化的 `job.provider` 找 Adapter；找不到时进入明确的 `provider_adapter_missing` 状态，不得重新路由。
3. Provider 自动回退只能在首次 submit 前，或经新的费用确认创建新 Job；不能拿旧 task id 切换 Adapter。
4. 增加“首次火山 submit → 重建 Router/Worker → 仍由火山 query/fetch”的测试。

### 0.8 P1：图片和视频共用一个 Shot 选择槽，会互相覆盖

**位置**

- `backend/app/db/models.py:188`
- `backend/app/services/feedback_service.py:87`
- `backend/app/services/job_planner.py:134`
- `backend/app/services/timeline_renderer.py:223`
- `frontend/src/components/studio/ShotCandidates.tsx:94`

**问题**

`Shot.selected_artifact_id` 同时被首帧图片选择和最终视频选择使用：

- 用户先选图片 A，Video Planner 会使用图片 A。
- 生成视频后用户选择视频 B，字段被覆盖为 B。
- 之后再次重生成视频时，首帧查找发现选中产物类型是 video，会忽略 A 并退回“最新图片”。
- 如果之后再选图片，Final Renderer 又会忽略原来选中的视频。

这属于静默覆盖已批准产物，直接违反关键验收标准第 16 条。

**整改要求**

优先采用可扩展选择表：

```text
shot_artifact_selections
  shot_id
  role: first_frame | final_video | dialogue_audio | narration_audio | ...
  artifact_id
  selected_by
  selected_at
```

简单版本也至少拆成：

- `selected_image_artifact_id`
- `selected_video_artifact_id`

迁移时根据旧 Artifact.type 回填对应字段；前端候选区也要分别支持图片和视频候选。

### 0.9 P1：零 Job 的收费节点会永久停在 running

**位置**

- `backend/app/services/job_planner.py:347`
- `backend/app/services/workflow_service.py:390`
- `backend/app/services/workflow_service.py:472`

**问题**

`plan_shot_audio_jobs()` 在没有对白/旁白文本时返回空列表，但 `advance_run()` 不检查 `_expand_jobs()` 返回数量，仍把 Step 设置为 `running`。没有任何 Job 会结束并触发 `on_job_finished()`，因此 Run 永久无法推进到 `episode.render`。

同类问题也会发生在异常空分镜的 image/video 节点。

**整改要求**

1. `_expand_jobs()` 返回 0 时，根据 Node 语义把 Step 标记为 `skipped` 或 `succeeded`，并写 `output_snapshot={"job_count": 0, "skip_reason": ...}`。
2. TTS 节点无文本时应正常跳过，而不是失败或 running。
3. 增加完整测试：无对白短剧仍能完成视频渲染；空分镜则应在更早的审批/校验阶段明确失败。

### 0.10 P1：LLM Planner 已接入入口，但规划结果没有完整生效

**位置**

- `backend/app/services/planner_service.py:28`
- `backend/app/services/planner_service.py:115`
- `backend/app/routers/workflow.py:170`
- `backend/app/services/workflow_service.py:222`
- `backend/app/services/series_service.py:26`

**问题**

1. Planner 返回的 `provider_policy/aspect_ratio` 位于 `run.input_snapshot` 顶层，但 Job Planner 和 Cost Service 读取的是 `input_snapshot.default_policy`，所以 AI 选择的策略和画幅实际上被忽略，仍使用模板默认值。
2. Planner 返回 `episodes=N`，但 `create_project()` 永远只创建第 1 集，`create_run()` 也只展开数据库里已有 Episode；“3 集”计划不会自动得到 3 集 Run。
3. `shots_per_episode` 当前只用于上限校验，没有驱动分镜生成数量或领域命令约束。
4. LLM 允许 `provider_policy=fast/cheap`，而统一 Provider Request 只接受 `cheapest/balanced/quality/explicit`；字段语义不一致。

因此当前 Planner 主要完成了“LLM 输出 JSON + 记录模型名 + 可插额外节点”，尚未完成“自然语言计划真正控制领域实体和执行参数”。

**整改要求**

1. 定义单一 `RunPlan` Schema，统一策略枚举。
2. 创建 Run 前将计划参数规范化到 `default_policy`，报价、Job Planner 和前端只读同一份快照。
3. 根据 `episodes` 原子创建/补齐 Episode，或要求项目预先存在相同集数并明确拒绝不一致。
4. 把 `shots_per_episode` 传入 storyboard 生成 Typed Node/领域命令，并在保存分镜时校验实际镜头数。
5. 增加真实数据库测试：`3集、每集4镜头、cheapest、16:9` 创建后，Episode 数、Step 数、Job params 和 Quote 全部一致。

### 0.11 P1：音色复刻与 TTS 仍未形成可用闭环

**位置**

- `backend/app/worker/generation_worker.py:655`
- `backend/app/worker/generation_worker.py:689`
- `backend/app/providers/tts_base.py:155`
- `backend/app/providers/tts_base.py:183`
- `backend/app/services/job_planner.py:326`

**问题**

1. 已实现 `clone_voice` Worker executor，但项目中没有创建该 Job 的 API、Typed Node、领域服务或前端操作，正常用户路径无法触发。
2. Planner 会把已批准 voice asset 的 `provider_meta.voice_id` 写入 TTS Job，但 `QwenTTSProvider._synthesize_sync()` 完全不读取 `req.voice_id`。
3. 有 `reference_audio` 时，每条对白都会再次执行声音复刻；没有参考音频时，每条对白都会再次执行声音设计。既没有复用已创建 voice_id，也没有把额外音色创建费用纳入 Quote。
4. 本轮没有真实 Qwen TTS/clone voice 成功记录。

**整改要求**

1. 增加“上传参考音频 → 创建 clone_voice Job → 试听 → 审批 → 写入 voice_id”的 API/UI 和费用 Gate。
2. 为 Provider 增加“使用已有 voice_id 直接合成”的真实接口；TTS Job 不应每条对白重新克隆音色。
3. Quote 区分音色创建费用和语音合成费用。
4. 增加同一角色多条对白复用同一个 voice_id 的集成测试。

### 0.12 P1：预算预留和 Job 重试仍存在事务崩溃窗口

**位置**

- `backend/app/services/job_planner.py:97`
- `backend/app/services/job_planner.py:271`
- `backend/app/services/workflow_service.py:472`
- `backend/app/routers/jobs.py:169`
- `backend/app/routers/jobs.py:185`
- `backend/app/routers/jobs.py:187`

**问题 A：Step 展开并非单事务**

Job Planner 在完成预算预留和 Job 创建后先 `commit()`，返回 `advance_run()` 后才把 Step 设置为 `running`。如果进程在两个 commit 之间崩溃，数据库中会出现：

- 预算已累加。
- Job 已存在。
- Step 仍是 draft/ready。

恢复后再次 advance 会重复预算预留，再复用原 Job，导致 `estimated_cost` 被重复累加。

**问题 B：Job retry 血缘并非单事务**

`retry_job` 先提交新 Job，随后第二次提交旧 Job 的 `superseded_by`。如果两次提交之间崩溃，旧 failed Job 和新 Job 都会被当前聚合看到，新 Job 即使成功也可能让 Step 继续失败。

**整改要求**

1. 移除各 `plan_*_jobs` 内部 commit，由 `advance_run` 在一个事务中完成“校验 Quote → 预算预留 → 创建 Job → Step=running”。
2. 建立幂等预算预留记录，唯一键至少包含 `run_id + step_id + step_attempt`；重复展开不能重复累加。
3. Job retry 必须在同一事务中创建新 Job并回填旧 Job `superseded_by`。
4. 增加故障注入测试，分别在两个现有 commit 边界模拟崩溃并验证恢复结果。

### 0.13 P2：Usage 的 actual_cost 仍是估算值

**位置**

- `backend/app/worker/generation_worker.py:327`
- `backend/app/worker/generation_worker.py:340`
- `backend/app/worker/generation_worker.py:434`
- `backend/app/services/usage_service.py:70`

**问题**

视频成功后直接把路由估算值写入 `actual_cost`，图片也把价格表估算值写入 `actual_cost`。当前没有 Provider 账单、实际计费单位或异步对账，因此字段名会让用户误以为它是实际扣费。

**整改要求**

- Provider 未返回实际费用时写 `estimated_cost`，`actual_cost` 保持 NULL。
- 增加 `cost_source=estimate|provider_response|reconciled_bill`。
- H3/Hailuo/火山基准和财务报表只能使用真实账单或明确标记的估算值。

### 0.14 P2：前端构建仍有超大 Chunk

本轮 `StudioHome` 动态分包已经生效，但 `npm run build` 仍产生多个超过 500KB 的 Chunk：

- `percentages-*.js`：约 2.11MB。
- `subset-shared.chunk-*.js`：约 1.82MB。
- `flowchart-elk-definition-*.js`：约 1.45MB。
- `mindmap-definition-*.js`：约 542KB。

这不是当前发布安全阻塞项，但会影响工作台首屏和弱网体验。建议继续按 Mermaid/Excalidraw/文件导出能力做按需加载和 manualChunks。

### 0.15 本轮验证结果

| 检查 | 结果 | 说明 |
|------|------|------|
| `cd backend && python -m pytest -q` | 通过 | `284 passed, 22 skipped, 12 warnings`。 |
| `cd backend && python -m ruff check app tests` | 通过 | 无 Ruff 错误。 |
| `alembic upgrade head --sql` | 通过 | 使用临时必填环境变量生成 475 行 PostgreSQL 离线 SQL，迁移到 `0005`。 |
| `cd frontend && npm run build` | 通过 | TypeScript/Vite 构建成功，仍有超大 Chunk 警告。 |
| 真实 PostgreSQL 迁移/租户/重试测试 | 未执行 | `TEST_DATABASE_URL` 未配置，20 个 PG 集成测试跳过。 |
| 音频混音测试 | 未执行 | Python 3.13 下 pydub/audioop 不可用，2 个测试跳过。 |
| MiniMax/火山图片/Qwen TTS 沙箱 | 未执行 | 当前环境对应 API Key 均未配置。 |
| 完整短剧 E2E | 未执行 | 没有真实“图片候选 → 选首帧 → 视频 → TTS → 字幕/BGM → Final Render”验收记录。 |

单元测试通过不能覆盖本节问题，主要原因是候选数、事务崩溃窗口、真实 Provider 协议和真实 PostgreSQL 约束没有在当前测试环境中执行。

### 0.16 对照关键验收标准

| 关键验收标准 | 状态 | 结论 |
|--------------|------|------|
| 权限隔离 | ⚠️ | 代码层已明显完善，仍需真实 PG 租户测试实际执行。 |
| 审批与版本失效 | ⚠️ | 主链路存在，音色 clone/审批入口仍不完整。 |
| 幂等与恢复 | ❌ | 已提交视频会重新路由；预算和 retry 存在双 commit 崩溃窗口；H3 协议只存内存。 |
| 局部重试 | ⚠️ | 当前 attempt 聚合已修复，但 retry 原子性仍需修复和真实 PG 验收。 |
| 产物追踪 | ⚠️ | 基本可追踪，但图片多候选未建档，图片/视频选择角色混用。 |
| 成本与收费确认 | ❌ | quote_id Gate 已完成，但图片单价错误，Provider/Model 未锁定，actual_cost 语义错误。 |
| 时间线合成 | ⚠️ | 代码和单测存在；音频混音、真实 TTS 和完整成片未验收。 |
| Provider 路由 | ⚠️ | 火山/Hailuo V1 有代码，执行期间 Provider 不稳定；H3 未完成。 |
| MiniMax 成本验证 | ❌ | 无真实 H3/Hailuo 基准和账单记录。 |
| 双入口一致 | ❌ | LLM Planner 已接入，但集数、画幅和 Provider Policy 没有完整作用到执行实体。 |
| 收费前展示模型/规格/数量 | ❌ | 数量可展示，但 Quote 不含实际 Provider/Model/Resolution，且图片数量与实际产物不一致。 |
| 候选选择 | ❌ | 视频反馈 UI 已有，但图片候选未生成；图片和视频选择会互相覆盖。 |
| 真实 PG、真实 Provider、完整 E2E | ❌ | 本轮均无可执行验收证据。 |

### 0.17 建议整改顺序

**第一批：费用和恢复正确性**

1. 修复图片 candidate_count 实际生成和多 Artifact 建档。
2. 统一图片价格源，Quote/预算/Usage 使用同一 pricing。
3. Quote 阶段锁定 Provider/Model/Resolution，Worker 不再重路由已提交任务。
4. 合并 Step 展开和 Job retry 的事务，增加幂等预算预留。
5. 修复零 Job Step 永久 running。

**第二批：产品闭环**

1. 拆分图片/视频选择槽并补图片候选 UI。
2. 让 Planner 的集数、镜头数、画幅和 Provider Policy 真正作用到领域实体和 Job。
3. 增加 clone_voice 创建入口，TTS 直接复用已批准 voice_id。

**第三批：H3 与发布验收**

1. 按账号真实协议完成 H3 沙箱联调并持久化协议版本。
2. 执行真实 PostgreSQL 迁移、租户、并发预算和重试测试。
3. 完成 H3/Hailuo/火山标准镜头基准。
4. 跑通一集 3-8 镜头的真实全链路成片 E2E，再决定是否通过生产发布验收。

---

## 附录 A、2026-08-04 上一次修改后复审结论

> 本节是历史状态，已被上方“第二次修改后复审结论”取代。
> 状态定义：✅ 已修复；⚠️ 部分修复或缺少真实环境验收；❌ 未修复/实现不可用。

### 0.1 总体判断

当前代码已经从“领域骨架”推进到“可运行的 Mock/Stub 驱动 MVP”，并且本地单元测试、Ruff 和前端构建均能通过。但按照 `docs/series-video-optimization-plan.md` 的最终验收标准，仍不能认定为整体完成，主要原因如下：

1. MiniMax H3/Fast Adapter 仍没有可验证的官方请求协议和真实沙箱成功记录。
2. 系列图片生成链路仍然没有任何真实图片 Provider。
3. `/storage` 仍允许任意已登录用户读取其他项目文件。
4. Artifact Feedback 可通过伪造 `step_id` 修改其他项目的 Workflow Step。
5. 收费 Job 并未全部受费用确认 Gate 控制，报价也没有和客户端确认的 `quote_id` 绑定。
6. Step/Job 重试会把旧 failed Job 一起参与聚合，导致新 Job 成功后 Step 仍然失败。
7. 真实 PostgreSQL、MiniMax、图片、TTS 和完整成片端到端测试均未执行。

因此当前可用于继续开发、Mock 演示和前端联调，但**不建议生产部署，也不建议对外标记为“完整 V1”**。

### 0.2 P0 复审状态

| 原问题 | 状态 | 复审结论 |
|--------|------|----------|
| P0-1 MiniMax H3 Adapter | ❌ | Endpoint 和 Body 已重写，但当前公开官方文档无法验证 `MiniMax-H3`、`/v2/video_generation`、`/v2/query/video_generation/{task_id}` 这一整套协议；Fast 分支仍明确是“旧版 V1 假设”，且没有真实 API Key 联调记录。 |
| P0-2 老数据库迁移 | ⚠️ | 已按 Schema 特征识别 0001-0004，不再直接 `stamp head`；离线 SQL 可生成，但真实 PostgreSQL 集成测试因未配置 `TEST_DATABASE_URL` 全部跳过。 |
| P0-3 Artifact 文件权限 | ❌ | 新增 `/api/artifacts/{id}/download` 项目级鉴权，但 `/storage/{path}` 对任意有效登录用户放行全部非 public 文件，知道路径即可跨项目读取。 |
| P0-4 Approval 跨项目写入 | ✅ | `_validate_target` 已校验 Storyboard/Asset 实际归属项目，路由先校验当前用户拥有 `project_id`。 |
| P0-5 Asset 绑定其他项目 Shot | ✅ | `bind_asset_to_shot` 已通过 Shot→Scene→Episode 反查项目并和 Asset 项目比较。 |
| P0-6 Canvas 绑定越权 | ✅ | 创建 SeriesProject 时已拒绝不存在、无主、他人拥有和已绑定的 Canvas。 |

### 0.3 新增 P0：Artifact Feedback 可跨项目修改 Workflow Step

**位置**

- `backend/app/routers/series.py:426`
- `backend/app/services/feedback_service.py:43`

**问题**

路由只验证 `artifact.project_id` 属于当前用户，但 `record_feedback` 收到 `step_id` 后只执行：

```python
step = await session.get(WorkflowStep, step_id)
```

没有验证：

- Step 所属 Run 的 `project_id` 是否等于 Artifact 的 `project_id`。
- Step 是否属于当前用户。
- Artifact 是否确实是该 Step/Shot/Operation 生成的候选产物。

攻击者只要猜到其他项目的 `step_id`，就可以使用自己项目的 Artifact 调用 `action=selected`，从而写入：

```python
step.selected_artifact_id = artifact.id
```

**整改要求**

1. `WorkflowStep -> WorkflowRun.project_id` 必须等于 `Artifact.project_id`。
2. Artifact 必须由该 Step 关联的 GenerationJob 产生。
3. 如果选择粒度是 Shot，应建立 Shot 级选择关系，不能把一集所有 Shot 共用的 Workflow Step 当成单镜头选择槽位。
4. 增加跨用户、跨项目、跨 Step、跨 Shot 和跨 Artifact 类型的真实数据库权限测试。

### 0.4 新增 P0：无主 SeriesProject 仍可被任意用户访问

**位置**

- `backend/app/services/series_service.py:64`
- `backend/app/routers/jobs.py:66`
- `backend/app/services/storyboard_service.py:408`
- `backend/app/services/asset_service.py:274`

**问题**

多个归属校验使用类似逻辑：

```python
if project.user_id is not None and project.user_id != user.id:
    reject()
```

当历史项目的 `user_id=NULL` 时，任何已登录用户都可通过 Series、Job、Storyboard 和 Asset 相关接口访问或修改该项目。Canvas 已明确拒绝无主记录，但 SeriesProject 没有采用同样策略。

**整改要求**

1. 普通用户访问 `user_id=NULL` 项目必须拒绝。
2. 增加一次性迁移或管理员认领接口，为历史项目补齐 owner。
3. 将项目归属校验收敛为单一 helper，避免不同 Router/Service 使用不同语义。

### 0.5 P1 复审状态

| 原问题 | 状态 | 复审结论 |
|--------|------|----------|
| P1-1 真实图片生成 | ❌ | `available_image_providers()` 仍返回空列表，只有 `MockImageProvider`；现有火山图片 Tool 没有接入 Generation Worker 的 ImageProvider 接口。 |
| P1-2 首帧传递 | ⚠️ | 已查询最新成功图片 Artifact 并写入视频 Job `first_frame`；但取的是“最新成功产物”，没有使用用户选定/批准的候选，也没有正式 Shot 首帧引用。 |
| P1-3 Mock 视频静默兜底 | ✅ | `ALLOW_MOCK_PROVIDERS` 默认 false，无真实 Provider 时返回 `no_provider`。 |
| P1-4 完整费用确认 | ❌ | 已有报价 API/UI，但只有 `shot.video.generate` 受 `cost_confirmed` Gate 控制；图片 Job 会在确认费用前创建。确认接口也没有接收并校验用户看到的 `quote_id`。 |
| P1-5 累计 Cost Limit | ⚠️ | 已改为 `estimated_cost + 本次费用`，但没有行锁/原子预算预留；Job 重试、Provider 回退和并发请求不会重新检查剩余预算。源码已明确承认此限制。 |
| P1-6 Step 重试恢复 Run | ❌ | Run 状态可以恢复，但新 attempt 的 Job 和旧 failed Job 仍关联同一个 Step；最终聚合会继续统计旧 failed Job，使 Step 再次失败。 |
| P1-7 Job 重试外部任务 | ❌ | Router 会创建没有 `external_task_id` 的新 Job，但旧 failed Job 仍参与同 Step 聚合；新 Job 成功无法覆盖旧失败结果，重试闭环仍不可用。 |
| P1-8 终态失败回推 Workflow | ✅ | 已增加统一 `finalize_job`，终态统一调用 `on_job_finished`。 |
| P1-9 Job 取消回推 | ⚠️ | API 已回推 Workflow；但没有调用 Provider cancel，且取消和 Worker 完成之间缺少并发状态保护，可能出现 canceled 后又被标记 succeeded。 |
| P1-10 Pause Run | ⚠️ | Lease 查询会排除 paused/canceled Run，但已在暂停前被 lease、尚未提交 Provider 的 Job 仍会继续执行；Worker submit 前没有再次检查 Run 状态。 |
| P1-11 Final Render 黑场 | ✅ | `render_mode=final` 已在缺视频文件时失败；preview 才允许黑场并标记 `is_publishable=false`。 |
| P1-12 TTS 闭环 | ⚠️ | 已增加 `shot.audio.generate`、TTS Job、Qwen Provider 和 Track 回写；但 `clone_voice` 仍是失败占位，真实 TTS 未联调，无默认音色版本锁定和完整音色资产 API。 |

### 0.6 费用确认仍不满足要求

**收费 Job 在确认前创建**

`backend/app/services/workflow_nodes.py:61` 的 `shot.image.generate` 设置了 `creates_jobs=True`，但没有 `requires_approval=True`。`advance_run` 只对 `requires_approval` 节点执行费用/审批 Gate，因此图片 Job 会在费用确认前进入队列。

这与 `workflow_nodes.py:6` 声明的“creates_jobs=True 的节点在 fan-out 前必须停在 waiting_approval 做费用确认”相矛盾，也违反优化方案中的：

> 未经确认不得创建收费 Jobs。

**报价没有和确认请求绑定**

当前确认接口为：

```text
POST /api/workflow/runs/{run_id}/confirm-cost
```

请求中不包含 `quote_id`。服务端只检查 Run 当前保存的价格表版本，没有验证：

- 用户确认的是哪个 quote。
- 报价后 Storyboard、Shot 时长、候选数和 Provider Policy 是否变化。
- 报价是否过期。
- 实际 Provider/Model 是否仍和报价一致。

**整改要求**

1. 所有会产生外部费用的 Typed Node 统一进入 Cost Gate。
2. `confirm-cost` 请求必须携带 `quote_id`。
3. Quote 保存输入快照 Hash、Storyboard Revision、模型、分辨率、数量、价格表版本和有效期。
4. 展开 Job 前再次校验 quote，之后用数据库锁原子预留预算。
5. 重试、回退和 regenerate 必须新增或消耗明确的预算预留。

### 0.7 Step/Job 重试逻辑仍不可用

**位置**

- `backend/app/services/workflow_service.py:596`
- `backend/app/routers/jobs.py:116`
- `backend/app/services/job_planner.py:36`

`retry_step` 会把 `step.attempt` 加一，新的 Job 幂等键也包含 attempt；`retry_job` 会创建新 Job。这两部分本身方向正确。

但 `on_job_finished` 的聚合条件只按 `workflow_step_id/params.step_id` 查询：

```python
GenerationJob.workflow_step_id == step_id
```

没有限制当前 `step.attempt` 或当前 retry generation。只要旧 attempt 存在 failed Job，以下查询就永远大于 0：

```python
select(count()).where(step_filter, GenerationJob.status == "failed")
```

所以新 Job 全部成功后，Step 仍会再次进入 `failed/job_failed`。

**整改要求**

1. GenerationJob 增加明确的 `step_attempt` 或 `retry_generation` 字段。
2. Step 聚合只统计当前 attempt 的 Job。
3. Job 级重试必须明确替代哪一个旧 Job，并在当前 attempt 中按 logical job key 聚合最新一代。
4. 增加真实数据库测试：旧 Job failed → retry → 新 Job succeeded → Step succeeded → Run 继续推进。

### 0.8 P2 复审状态

| 原问题 | 状态 | 复审结论 |
|--------|------|----------|
| P2-1 LLM Agent Planner | ❌ | `plan_with_llm` 已实现最小解析，但没有任何生产调用方；创建 Run 仍调用 `plan_from_template`。`extra_nodes` 仅校验后被丢弃，没有形成新的 Typed Node DAG。 |
| P2-2 Artifact Feedback | ❌ | 后端 API 和前端组件已增加，但存在跨项目写入漏洞；Workflow Step 是 Episode 粒度，前端却按 `entity_id===shotId` 查 Step，通常得不到 `step_id`；Job Planner/Renderer 也不读取 `selected_artifact_id`。 |
| P2-3 Personal Asset | ⚠️ | Schema 和 Service 支持 `project_id=NULL`，但没有独立的个人资产创建/列表 API；现有创建路由仍位于项目下并始终传入 `project.id`，前端也没有个人资产中心。 |
| P2-4 最新 Workflow Run | ✅ | 前端已改为 `runs[0]`。 |
| P2-5 Job 进度 | ✅ | 前端已直接使用后端 0-100 的 progress。 |
| P2-6 费用确认 UI | ⚠️ | UI/API 已存在，但底层报价与 Gate 不完整，不能视为真正的收费确认闭环。 |
| P2-7 WebSocket Canvas 权限 | ✅ | 不存在、无主和他人 Canvas 已分别拒绝。 |
| P2-8 Bundle 体积 | ❌ | Build 仍有超过 500KB 的 Chunk；`StudioHome` 同时被动态和静态 import，动态分包没有生效。 |

### 0.9 MiniMax Provider 复审

`backend/app/providers/minimax_video.py` 虽然将 H3 请求改成 `/v2/video_generation`，但当前仍不能视为真实接入完成。

**当前公开官方文档核对结果（2026-08-04）**

- 官方文档索引当前公开的视频创建接口仍包含 `POST /v1/video_generation`。
- 官方查询流程公开为 `GET /v1/query/video_generation?task_id=...`。
- 成功后通常通过 `file_id` 调用 Files Retrieve 获取下载地址。
- 当前公开文档索引没有提供可核验的 `MiniMax-H3` 模型、`/v2/query/video_generation/{task_id}` 或本文档原链接中的 H3 V2 Schema。

参考：

- https://platform.minimaxi.com/docs/llms.txt
- https://platform.minimaxi.com/docs/api-reference/video-generation-create
- https://platform.minimaxi.com/docs/api-reference/video-generation-query
- https://platform.minimaxi.com/docs/api-reference/files-retrieve

**当前代码的风险**

1. H3 模型名默认写死为 `MiniMax-H3`，但没有 capabilities/version discovery。
2. H3 Query 使用 `/v2/query/video_generation/{task_id}` 并直接读取 `task.content.url`，没有真实联调证据。
3. Fast 分支仍使用：

   ```python
   POST /v1/video_generation
   {prompt, content: [{type, source, role}]}
   ```

   代码注释也明确称其为“旧版 V1 假设”。
4. 测试全部 Mock `httpx`，只证明代码符合自己定义的假响应，不证明符合 MiniMax API。
5. 当前环境未配置 `MINIMAX_API_KEY`，无法执行真实沙箱测试。

**结论**

在 MiniMax 官方确认具体 H3 协议，并完成真实创建、查询、下载、审核拒绝、限流和费用联调前，P0-1 仍为未修复。生产默认 `balanced` 不应优先路由到该 Adapter。

### 0.10 测试与构建复审结果

2026-08-04 本地执行结果：

| 检查 | 结果 | 说明 |
|------|------|------|
| `python -m pytest -q` | 201 passed, 5 skipped | 单元/Stub 测试通过，但 3 个真实 PG 迁移测试和 2 个音频混音测试被跳过。 |
| `python -m ruff check app tests` | 通过 | 无 Ruff 错误。 |
| `alembic upgrade head --sql` | 通过 | 生成 459 行离线 SQL，可证明迁移脚本可编译，不能替代真实升级。 |
| `npm run build` | 通过 | 存在大 Chunk 警告，最大 Chunk 约 2.1MB；StudioHome 动态 import 未生效。 |
| 真实 PostgreSQL 迁移 | 未执行 | 未配置 `TEST_DATABASE_URL`。 |
| MiniMax/火山图片/Qwen TTS 沙箱 | 未执行 | 当前环境未配置对应 API Key。 |
| 完整短剧 E2E | 未执行 | 没有真实首帧→视频→TTS→字幕/BGM→Final Render 验收证据。 |

测试通过不能覆盖本次发现的问题，原因是新增测试大量使用 `_FakeSession`、monkeypatch 和 Mock HTTP，没有验证真实外键、事务并发、跨项目关联和 Provider 协议。

### 0.11 当前必须继续修改的顺序

**第一批：仍然阻塞发布**

1. 修复 `/storage` 项目级权限，禁止“任意登录用户可读全部文件”。
2. 修复 Feedback `artifact_id/step_id/run/project/shot` 的同租户关联校验。
3. 拒绝无主 SeriesProject，增加历史 owner 迁移/认领。
4. 接入真实图片 Provider，不允许标准生成链路依赖 Mock。
5. 根据 MiniMax 官方实际协议重写并真实联调 Provider；联调前 balanced 不默认选 MiniMax。

**第二批：收费和恢复闭环**

1. 所有收费节点统一费用 Gate，确认请求绑定 `quote_id` 和输入快照。
2. 增加原子预算预留及重试/回退预算检查。
3. Job 增加 `step_attempt/retry_generation`，修复当前 attempt 聚合。
4. Pause/Cancel 在 Provider submit 前二次校验 Run/Job 状态。

**第三批：产品完整度**

1. 将 LLM Planner 接入实际创建 Run 的入口。
2. 建立 Shot 级候选选择模型，让 Video Planner 和 Final Renderer 使用选定产物。
3. 增加独立个人资产创建、列表、版本和引用 API/UI。
4. 完成 Voice Asset/clone_voice 与真实 TTS 联调。
5. 继续拆分前端大 Chunk。

### 0.12 最终验收结论

当前最终定义的满足情况如下：

| 完成定义 | 是否满足 |
|----------|----------|
| 老数据库可无损升级 | ⚠️ 代码已改，真实 PG 未验收 |
| 不能访问/修改其他用户项目和媒体 | ❌ |
| 默认生成不依赖 Mock | ❌ 图片链路只有 Mock/失败 |
| 真实图片作为首帧传入真实视频 Provider | ❌ |
| 收费前展示完整可复现报价 | ❌ |
| Cost Limit 对累计和并发费用有效 | ❌ |
| Retry/Pause/Cancel/Worker 重启状态一致 | ❌ |
| Final Render 不含未声明黑场 | ✅ final 模式满足 |
| 对白/旁白自动 TTS 进入时间线 | ⚠️ 代码链路存在，真实 Provider 未验收 |
| 前端最新 Run、进度和费用显示正确 | ⚠️ Run/进度已修，费用语义仍错误 |
| 真实 PostgreSQL、真实 Provider、完整成片测试通过 | ❌ |

**最终结论：不满足整体完成要求，当前不能通过生产发布验收。**

---

## 一、严重级别定义

| 级别 | 定义 | 发布要求 |
|------|------|----------|
| P0 | 安全、迁移、真实 Provider 或核心链路完全不可用 | 必须全部修复后才能部署 |
| P1 | 可能造成错误扣费、任务永久卡死、错误成功或成片不可交付 | 标准短剧 MVP 发布前必须修复 |
| P2 | 产品能力缺失、前端体验错误、测试覆盖不足 | 可分批修复，但不能宣称完整 V1 已完成 |

---

## 二、P0 发布阻塞问题

### P0-1 MiniMax H3 Adapter 与官方 V2 API 不匹配

**现状**

- `backend/app/providers/minimax_video.py:5` 明确标注请求结构是“合理推断，待真实 API 联调确认”。
- 默认 Base URL 为 `https://api.minimaxi.com/v1`，提交请求实际发往 `/v1/video_generation`：
  - `backend/app/providers/minimax_video.py:26`
  - `backend/app/providers/minimax_video.py:166`
- 当前请求使用：
  - 顶层 `prompt`。
  - `content.type=image|video|audio`。
  - 媒体字段 `source`。
- MiniMax H3 官方 V2 创建接口为 `POST /v2/video_generation`，请求以 `content` 承载文本和多模态输入，字段结构与当前实现不同。
- 当前 Provider 单测只 Mock `httpx` 并断言当前假设结构，没有与真实官方接口联调。

**影响**

- 配置真实 `MINIMAX_API_KEY` 后，H3 请求可能直接返回 400。
- `balanced` 默认路由优先选择 MiniMax，因此会阻塞默认视频生成链路。
- 当前不能将 MiniMax H3 标记为“已接入”。

**整改建议**

1. 按官方 V2 Schema 重写 H3 请求：
   - Endpoint。
   - `content` 文本、图片、视频和音频项。
   - `ratio/resolution/duration` 字段。
   - 首帧、尾帧、参考素材和 regenerate 请求。
2. H3 与 Hailuo Fast 使用独立请求构造器和 capability，不能用同一个假设 V2 Body。
3. 按官方文档实现创建、查询、下载、再生成和错误码映射。
4. 增加真实联调测试环境，至少完成：
   - 文生视频。
   - 首帧图生视频。
   - 首尾帧视频。
   - 审核拒绝。
   - 限流与任务轮询。
   - 文件下载和 Artifact 建档。
5. 真实联调通过前，生产环境不得默认路由到 MiniMax H3。

**验收标准**

- 使用官方 API Key 成功生成并下载真实视频。
- Job 中保存真实 `task_id/file_id/model/resolution`。
- Artifact 文件存在且可以播放。
- UsageEvent 记录输入媒体和输出视频费用维度。

官方参考：

- https://platform.minimaxi.com/docs/api-reference/video-generation-v2-create
- https://platform.minimaxi.com/docs/api-reference/video-generation-v2-regeneration
- https://platform.minimaxi.com/docs/guides/pricing-paygo

### P0-2 老数据库会被错误 stamp 到 head

**现状**

`backend/app/db/migrations.py:42` 的升级逻辑为：

- 有 `alembic_version`：执行 `upgrade head`。
- 没有 `alembic_version` 但存在 `users`：直接执行 `stamp head`。
- 全新数据库：执行 `upgrade head`。

旧 FishStudio 数据库可能已经存在 `users/canvases/settings_kv/audit_logs`，但没有本次新增的 Series、Workflow、Job、Artifact 和 Usage 表。直接 `stamp head` 会把数据库标记为 `0003`，却不会真正执行 `0002/0003`。

**影响**

- 应用启动日志显示迁移成功。
- 访问 Series/Workflow API 时才出现表不存在错误。
- 后续 Alembic 认为数据库已是最新版本，无法自动补建表。

**整改建议**

1. 不得仅凭 `users` 表存在就 `stamp head`。
2. 对老库识别实际 Schema：
   - 仅有 0001 表结构时 `stamp 0001`，再执行 `upgrade head`。
   - Schema 已完整匹配某 revision 时才 stamp 到对应 revision。
3. 增加三类真实 PostgreSQL 迁移测试：
   - 空库 → head。
   - 0001 库 → head。
   - 历史无 `alembic_version` 旧库 → head。
4. 测试升级后所有新增表、外键、索引和约束存在。

**验收标准**

- 三类数据库均可升级到 `0003`。
- 升级后 `series_projects/workflow_runs/generation_jobs/artifacts/usage_events` 等表真实存在。
- 重复启动不会重复建表或破坏数据。

### P0-3 Artifact 文件绕过项目权限公开访问

**现状**

`backend/app/main.py:108` 使用 `StaticFiles` 直接挂载整个 `/storage`：

```python
app.mount("/storage", StaticFiles(directory=str(STORAGE_DIR)), name="storage")
```

任何知道或获取文件路径的人，不需要登录即可访问角色图、参考图、音频、视频和最终成片。

**影响**

- 绕过 Artifact、Project 和 User 权限。
- 可能泄露用户图片、音色、Prompt 产物和未发布视频。
- 与方案中的“Artifact 下载必须校验项目权限”冲突。

**整改建议**

1. Artifact 增加受保护下载 API，例如 `/api/artifacts/{artifact_id}/download`。
2. 下载前通过 Artifact → Project 校验当前用户权限。
3. 本地存储不再通过公开 `/storage` 根目录直接暴露。
4. 前端使用 Artifact ID 或短期签名 URL，不直接拼接永久公开路径。
5. 公共素材单独存放在明确的 public 目录。

**验收标准**

- 未登录访问私有 Artifact 返回 401。
- 其他用户访问返回 403/404。
- 项目 owner 可以正常播放和下载。

### P0-4 审批接口存在跨项目写入

**现状**

- 路由只校验请求中的 `project_id` 属于当前用户：`backend/app/routers/series.py:295`。
- `approval_service._validate_target` 只校验目标存在和版本匹配，没有校验目标属于传入项目：`backend/app/services/approval_service.py:32`。

攻击者可以传入自己的 Project ID，同时传入其他项目的 Episode ID 或 Asset ID，对其他项目目标执行 approve/reject，并修改目标状态。

**整改建议**

1. Storyboard 审批必须验证：`StoryboardRevision → Episode → SeriesProject.id == project_id`。
2. Asset 审批必须验证：`AssetCard.project_id == project_id`。
3. Service 层完成校验，不能只依赖 Router。
4. Approval 表增加必要的目标项目一致性测试。

**验收标准**

- 用户 A 无法审批、拒绝或修改用户 B 项目的任何目标。
- 伪造 `project_id + target_id` 组合返回 403/404。

### P0-5 资产可绑定到其他项目的 Shot

**现状**

- 路由校验了 Asset 属于当前用户：`backend/app/routers/series.py:262`。
- 没有校验 `shot_id` 所属项目。
- `asset_service.bind_asset_to_shot` 直接创建引用：`backend/app/services/asset_service.py:164`。

**影响**

- 可以向其他用户项目的 Shot 写入引用关系。
- 可能污染对方生成输入和资产一致性。

**整改建议**

- 绑定前同时查询 Asset 和 Shot 的 Project，并要求二者属于同一 Project 且当前用户拥有该 Project。
- Service 层增加事务内校验。
- 增加跨租户和跨项目绑定测试。

### P0-6 Canvas 与 SeriesProject 绑定未校验 Canvas owner

**现状**

`series_service.create_project` 只检查 Canvas 是否已经绑定其他 SeriesProject，没有检查 Canvas 是否属于当前用户：`backend/app/services/series_service.py:14`。

**影响**

- 用户可以把自己的 SeriesProject 绑定到其他用户的 Canvas。
- 可以提前占用其他用户 Canvas 的唯一 SeriesProject 绑定关系。

**整改建议**

- 创建项目时验证 Canvas 存在且 `Canvas.owner_id == user_id`。
- 不存在、无主或不属于当前用户时按迁移策略明确处理。

---

## 三、P1 核心链路问题

### P1-1 图片生成仍为 Mock，占位图却被标记成功

**现状**

`backend/app/worker/generation_worker.py:275` 的 `generate_image` Executor 明确是 Mock 路径：

- 使用 FFmpeg 生成固定纯色图片。
- 即使图片生成失败，也会创建 Artifact。
- Job 最终标记为 `succeeded` 和 `mocked=True`。

**影响**

- 不能生成真实角色图、场景图或镜头首帧。
- 后续视频生成无法获得有效首帧。
- 工作流可能显示成功，但产物不可用。

**整改建议**

- 接入至少一个真实图片 Provider。
- Mock Provider 只能在显式测试模式启用。
- 生产环境缺少图片 Provider 时 Job 必须失败，不能自动生成占位图并成功。

### P1-2 首帧图片没有传递到视频 Job

**现状**

`backend/app/services/job_planner.py:89` 创建视频 Job 时：

- 设置 `mode=first_frame`。
- 只写入 Prompt、时长、画幅和 Provider Policy。
- 没有查询前序图片 Job 的 Artifact。
- 没有写入 `first_frame`。
- 图片 Artifact 也没有绑定为 Shot 的正式首帧产物。

**影响**

- MiniMax/火山收到 `first_frame` 模式但没有首帧输入。
- 视频请求可能失败，或退化为不符合预期的文本生成。

**整改建议**

1. 图片 Job 成功后将 Artifact 绑定到 Shot，role 使用 `first_frame/approved_frame`。
2. 视频 Job Planner 读取 Shot 当前选定的首帧 Artifact。
3. 将受保护的 Artifact 输入转换为 Provider 所需格式。
4. 缺少首帧时不得创建 `first_frame` 视频 Job。

### P1-3 无 Provider 配置时静默使用 Mock 视频

**现状**

`backend/app/providers/video_router.py:109` 在没有可用 Provider 时自动选择 `MockVideoProvider`。

**影响**

- 生产环境漏配 API Key 时不会失败告警。
- 工作流会生成 Mock 视频并显示成功。
- 容易把测试产物误认为真实结果。

**整改建议**

- 增加 `ALLOW_MOCK_PROVIDERS`，默认 `false`。
- 仅测试和本地演示环境允许 Mock。
- 生产环境无 Provider 时返回 `no_provider` 并阻止工作流成功。

### P1-4 费用确认前没有计算视频费用

**现状**

`start_generation(confirm_cost=False)` 调用 `advance_run` 后读取 `run.estimated_cost`：`backend/app/tools/series_tools.py:293`。

但 `shot.video.generate` 在 `cost_confirmed=false` 时先停在 `waiting_approval`：`backend/app/services/workflow_service.py:302`。视频 Job Planner 尚未执行，因此视频费用尚未加入 `run.estimated_cost`。

**影响**

- 用户确认的金额可能只有图片费用或为 0。
- 真正确认后才追加视频费用并创建收费任务。
- 不满足“收费前展示完整费用”的要求。

**整改建议**

- 新增纯估算函数，在不创建 Job 的情况下计算所有待执行 Step 的费用。
- 估算结果按镜头、Provider、模型、分辨率、输入媒体和候选数展示。
- 用户确认的必须是一个不可变的 `cost_quote_id/quote_snapshot`。
- 创建 Jobs 时验证报价版本、价格表版本和费用上限。

### P1-5 Cost Limit 不是累计上限

**现状**

`backend/app/services/job_planner.py:79` 比较的是：

```python
estimated > run.cost_limit
```

而不是：

```python
run.estimated_cost + estimated > run.cost_limit
```

图片 Step 和视频 Step 只要分别低于上限，即使累计金额超过上限也会继续创建任务。

**整改建议**

- Cost Limit 必须针对 Run 的累计已花费、已预留和本次待创建费用。
- 并发 Job 创建时使用数据库锁或原子预算预留，避免多个 Worker/请求同时突破上限。
- 重试和 Provider 回退必须再次检查剩余预算。

### P1-6 Workflow Step 重试后 Run 无法恢复

**现状**

- `advance_run` 遇到 `run.status=failed` 会直接返回：`backend/app/services/workflow_service.py:333`。
- `retry_step` 只把 Step 改为 `ready`，没有把 Run 从 `failed` 恢复为 `ready/running`：`backend/app/services/workflow_service.py:445`。

**影响**

- 用户点击“重试该节点”后，节点不会真正重新展开执行。

**整改建议**

- Step 重试事务中同步恢复 Run 状态。
- 明确允许重试的 Run/Step 状态组合。
- 增加“Job 失败 → Run failed → Retry Step → 新 Job → Run succeeded”的数据库集成测试。

### P1-7 Job 重试仍复用旧 external_task_id

**现状**

`backend/app/routers/jobs.py:115` 重试 Job 时没有清空：

- `external_task_id`。
- `provider/model` 快照是否需要保留或重新选择的策略。

Worker 发现 `external_task_id` 已存在，会继续查询原来已经失败的外部任务，而不是创建新请求。

**整改建议**

- 明确区分：
  - 继续轮询已有外部任务。
  - 使用同 Provider 创建新任务。
  - 换 Provider 创建新 Job。
- 真正重试应创建新 Job 或清空外部任务信息，并保留 `retry_of_job_id` 追踪关系。
- 不建议把 `attempt` 重置为 0 后复用同一记录。

### P1-8 部分终态失败没有回推 Workflow

**现状**

以下分支将 Job 标记失败后直接返回，没有调用 `workflow_service.on_job_finished`：

- 视频请求参数非法。
- 无可用 Provider。
- 首次 submit 终态失败。

位置：`backend/app/worker/generation_worker.py:101`。

**影响**

- Job 已经 failed，但 Workflow Step 长期停在 running。
- Run 无法进入 failed，也无法通过 UI 重试 Step。

**整改建议**

- 所有 Job 终态统一通过一个 finalize 函数处理。
- finalize 负责 Usage、Job 终态、Artifact、Workflow 回推和事务提交。
- 增加每种错误分类的端到端状态测试。

### P1-9 Job 取消没有回推 Workflow

**现状**

`backend/app/routers/jobs.py:134` 取消 Job 后只修改 Job 状态，没有调用 Workflow 回推。

**影响**

- Step 可能永久保持 running。
- Run 状态与 Job 状态不一致。

**整改建议**

- Job canceled 属于终态，必须触发 `on_job_finished`。
- 明确一个 Step 中部分 Job canceled 的语义：failed、canceled 或等待用户重试。

### P1-10 Pause Run 不会暂停实际 Jobs

**现状**

- `pause_run` 只修改 WorkflowRun.status。
- Worker 领取 Job 时只按 Job status/lease 查询，不检查 Run 是否 paused。
- GenerationJob 没有直接的 `workflow_step_id/workflow_run_id` 外键，只在 JSON params 中保存 ID。

**影响**

- UI 显示 Run 已暂停，但 Worker 仍会继续提交收费任务。

**整改建议**

- GenerationJob 增加正式 `workflow_run_id/workflow_step_id` 外键。
- Worker lease 查询排除 paused/canceled Run。
- 暂停只阻止尚未提交的 Job；已提交外部任务根据 Provider capability 决定是否取消。

### P1-11 最终合成默认用黑场代替缺失视频

**现状**

`backend/app/services/timeline_renderer.py:248` 在镜头视频不存在时生成黑场，Render Job 仍然成功并产生最终 Artifact。

**影响**

- Run 可以整体 succeeded，但最终成片包含黑场。
- 不满足“最终视频包含正确镜头”的验收标准。

**整改建议**

- 增加 `render_mode=preview|final`。
- Preview 可使用黑场并明确标记 incomplete。
- Final 模式只要存在缺失或未批准镜头就必须失败。
- 最终 Artifact 增加 `is_publishable` 和完整性检查结果。

### P1-12 系列链路没有 TTS Job 和音色资产执行闭环

**现状**

- Worker 只支持 `generate_video/generate_image/render_episode`：`backend/app/worker/generation_worker.py:27`。
- Timeline 依赖 `dialogue_tracks/narration_tracks.audio_storage_key`，但该字段需要其他流程手工回填。
- 没有 `tts/clone_voice` Workflow Node、Job Planner 和 Worker Executor。

**影响**

- 无法从分镜对白自动生成完整音轨。
- 最终合成不能形成文档要求的自动对白/旁白闭环。

**整改建议**

- 增加 Voice Asset 创建与版本审批。
- 增加 `shot.audio.generate` 或 `episode.tts.generate` Typed Node。
- TTS Job 产物自动写回对应 Track/Artifact 引用。

---

## 四、P2 完整度与前端问题

### P2-1 LLM Agent Planner 尚未实现

`backend/app/services/planner_service.py:62` 的 `plan_with_llm` 仍回退到规则解析，只能提取集数、镜头数和画幅，不能按照方案生成受控 Typed Node Plan。

如果当前目标只是硬性 20 天 MVP，可以保留规则版；如果宣称完整产品 V1，需要实现：

- LLM 输出合法 Typed Node 计划。
- 服务端 DAG、参数、权限、批量上限和费用上限校验。
- Planner 输出和人工模板入口生成同一种 Workflow Run。

### P2-2 ArtifactFeedback 只有表结构，没有产品闭环

当前存在：

- `ArtifactFeedback` ORM/迁移。
- `WorkflowStep.selected_artifact_id`。

但没有发现：

- Feedback API。
- 候选 Artifact 列表和选择 API。
- 前端候选对比、选择、拒绝、评分。
- Provider 质量离线聚合。

### P2-3 Personal Asset 仍依赖 Project

`AssetCard.project_id` 不可为空，即使 `scope=personal` 仍必须属于某个项目，无法形成真正独立的个人资产库。

建议：

- Personal Asset 使用 nullable `project_id` 或拆分资产主体与项目引用。
- 项目引用个人资产时锁定具体版本。

### P2-4 前端加载的是最旧 Workflow Run

后端 `workflow_service.list_runs` 按 `created_at DESC` 返回：`backend/app/services/workflow_service.py:547`。

前端却选择：

```typescript
runs[runs.length - 1]
```

位置：`frontend/src/hooks/useWorkflowRun.ts:37`。

结果是多次创建 Run 后，工作台展示并操作最旧 Run。应选择 `runs[0]`，或者由后端提供明确的 latest endpoint。

### P2-5 Job 进度被重复乘以 100

- 后端 `GenerationJob.progress` 使用 0-100，成功时设置为 100。
- 前端 `GeneratePanel` 将 progress 再乘以 100：`frontend/src/components/studio/GeneratePanel.tsx:71`。

成功任务会显示为 10000%。

### P2-6 工作台没有费用确认操作

GeneratePanel 只展示“确认费用”的说明，没有按钮或 API 设置 `cost_confirmed=true`。费用确认目前只能通过 Agent Tool 完成，因此模板入口和 Agent 入口并未真正共享完整操作能力。

建议增加：

- 获取费用报价 API。
- 确认报价 API。
- UI 显示报价详情并由用户明确点击确认。

### P2-7 WebSocket 仍放行不存在和无主 Canvas

`backend/app/main.py:148` 对不存在或 owner_id 为空的 Canvas 放行。

建议区分：

- Canvas 不存在：拒绝连接。
- 历史无主 Canvas：通过一次性迁移归属，不在运行时允许任意用户访问。
- 当前用户拥有 Canvas：允许连接。

### P2-8 前端 Bundle 体积过大

`npm run build` 成功，但存在多个超过 500KB 的 Chunk，主包和 Excalidraw/Mermaid 相关包较大。建议对 Studio、Excalidraw、3D 和图表能力使用动态 import 和路由级懒加载。

---

## 五、测试与质量问题

### 5.1 当前测试结果

| 检查 | 结果 |
|------|------|
| 新增系列相关测试 | 100 passed |
| 后端测试排除 `test_audio_mixing.py` | 125 passed |
| 后端完整 `pytest` | 收集失败 |
| `ruff check app tests` | 通过 |
| 前端 `npm run build` | 通过，有 Bundle 体积警告 |
| Alembic `upgrade head --sql` | 可生成 SQL |

### 5.2 后端完整测试失败

当前 Python 3.13 环境中：

```text
ImportError: cannot import name 'AudioSegment' from 'app.tools.audio_mixing'
```

原因是 `pydub` 导入失败后，模块将 `PYDUB_AVAILABLE=False`，但没有定义 `AudioSegment`，测试仍尝试导入。Docker 使用 Python 3.11，可能不会复现，但 README 标注 Python 3.9+，当前本地支持范围与文档不一致。

### 5.3 新领域测试没有真实数据库覆盖

`backend/tests/test_series_domain.py:1` 已明确说明：

- 不运行真实 PostgreSQL 服务测试。
- Router 测试主要通过 monkeypatch Service。
- Provider 测试 Mock `httpx`，没有真实 MiniMax/火山联调。

因此当前“100/125 tests passed”不能证明：

- Alembic 老库升级可用。
- 外键和唯一约束真实生效。
- 跨租户攻击被拒绝。
- Worker 崩溃恢复可用。
- MiniMax H3 请求结构正确。
- 完整短剧端到端可以生成真实成片。

### 5.4 必须补充的集成测试

1. **数据库迁移**：空库、0001、无 Alembic 历史库升级。
2. **权限**：Project、Episode、Shot、Asset、Approval、Job、Artifact、WebSocket 跨用户访问。
3. **工作流恢复**：失败、重试、暂停、继续、取消、Worker 重启。
4. **费用**：报价确认、累计预算、并发预算、Provider 回退追加费用。
5. **真实 Provider**：MiniMax H3 和至少一个图片/TTS Provider。
6. **完整成片**：真实首帧、真实视频、TTS、字幕、BGM 和 Final Render。

---

## 六、建议整改顺序

### 第一批：生产安全与真实链路

1. 修复老库 Alembic bootstrap。
2. 关闭公开 `/storage`，增加受保护 Artifact API。
3. 修复 Approval、Asset/Shot、Canvas/Project 跨租户校验。
4. 按官方 V2 API 完成 MiniMax H3 真实联调。
5. 生产环境禁止 Mock Provider 自动兜底。

### 第二批：生成和费用闭环

1. 接入真实图片 Provider。
2. 图片 Artifact 绑定 Shot，并传递给视频 Provider。
3. 重构费用报价与累计预算控制。
4. 修复 Workflow/Job retry、cancel、pause 和所有终态回推。
5. Final Render 禁止黑场占位成功。

### 第三批：完整产品能力

1. 增加 TTS/Voice Asset 执行闭环。
2. 实现费用确认 UI。
3. 修复最新 Run 和进度显示。
4. 实现候选 Artifact、Feedback 和个人资产库。
5. 根据产品范围决定是否实现 LLM Planner。

### 第四批：验收与发布

1. PostgreSQL 集成测试。
2. MiniMax/图片/TTS Provider 沙箱联调。
3. 单集 3-8 镜头真实端到端测试。
4. Worker 重启和断线恢复测试。
5. 安全回归和费用对账。

---

## 七、最终完成定义

只有同时满足以下条件，才建议将项目标记为“整体完成”：

- 老数据库可无损升级。
- 用户无法访问或修改其他用户的任何项目数据和媒体文件。
- 默认生成链路不依赖 Mock Provider。
- 真实图片产物会作为首帧传入真实视频 Provider。
- 批量收费前展示完整、可复现的费用报价。
- Cost Limit 对累计费用有效。
- Job/Step/Run 在失败、重试、暂停、取消和 Worker 重启后状态一致。
- Final Render 不包含未声明的黑场占位。
- 对白/旁白可以通过 TTS 自动生成并进入时间线。
- 前端操作的是最新 Run，进度和费用显示正确。
- 真实 PostgreSQL、真实 Provider 和完整成片测试全部通过。
