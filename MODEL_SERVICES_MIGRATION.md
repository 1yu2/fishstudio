# 模型服务配置迁移与兼容说明

FishStudio 自 2026-09-20 起使用数据库中的 `model_deployments` 和 `model_routes`
作为模型配置与用途绑定的唯一管理入口。管理员在“设置 → 模型服务”完成服务配置、
连接测试、启停和用途绑定；“高级设置 → 系统环境”不再展示模型变量。

## 首次启动迁移

数据库升级到 Alembic `0022` 后，启动过程会读取现有模型环境变量并按稳定 slug
幂等创建 Deployment，并为可用服务自动创建缺失的用途绑定（例如 `shot_video`、
`image_generation`、`planner`）。已有同 slug/同 purpose 记录一律跳过，不覆盖管理员修改。
迁移版本写入 `settings_kv.model_deployment_bootstrap_version`，日志只记录变量名，不记录值。

旧变量映射覆盖 OpenAI-compatible、火山 Chat/图片/视频、MiniMax 云视频、Local H3、
DashScope TTS 和腾讯 3D。Local H3 的地址、模型、并发、时长、Artifact 限制和自托管
成本会一并迁移，密钥只保存为 `env:LOCAL_H3_API_KEY` 引用。

## 质量档位与视频路由（Alembic 0023）

`model_routes` 主键改为 `(purpose, tier)`，`tier` 取 `draft` / `preview` / `final`；
升级后已有绑定全部成为 `final` 档。某档位未配置时回退到 `final`。

视频镜头（`shot_video`）的报价与 Job 规划现在按数据库 Route 选择 Deployment，
不再使用代码里的静态路由：

- 按镜头真实请求形态路由：有首帧走图生视频能力，有参考图按参考图数量判断；
- 跳过未启用、密钥缺失、能力不满足、没有价格或该时长无价格的候选，并在计划的
  `route.skipped` 中记录原因；没有可用候选时报错，不再静默选中未配置的服务；
- 单价只取 Deployment 的 `pricing`；
- Run 策略可用 `video_tier` 选择档位、`explicit_deployment` 指定 Deployment；
- Catalog 中尚无任何视频 Deployment 时，仍沿用旧静态路由（兼容期）。

## Seedance（火山方舟）原生接入

新增 Driver `ark_video`（设置页「Seedance（火山方舟）」），直连方舟
`/contents/generations/tasks` 异步接口，全程 httpx 异步，替代旧的同步封装。

- 地址填 `https://ark.cn-beijing.volces.com/api/v3`，模型填方舟控制台中的模型 ID
  （如 `doubao-seedance-1-5-pro-251215`），密钥沿用 `VOLCANO_API_KEY`。
- 支持文生视频、首帧、首尾帧；参考图模式需在能力里显式开启 `reference` 并设置
  `max_reference_images`（仅部分模型支持）。
- 默认 `return_last_frame=true`：尾帧随视频一起下载，记录在视频 Artifact 的
  `meta.last_frame_storage_key`，供下一镜接力；默认不生成音频（配音单独做）。
- draft/preview 档默认 480p，final 档默认 720p，可在配置中调整。
- 方舟按 Token 计费，价格建议配置为 `{"output_per_million_tokens": "<元/百万 token>"}`；
  报价按 宽×高×24fps×秒数/1024 估算 Token，实际用量记录在结果 usage 中。
- 结果链接 24 小时失效，任务成功后立即下载到本地存储。
- 首次启动迁移：直连方舟（非 DMXAPI）的 `VOLCANO_API_KEY` 现在创建 `ark_video`
  Deployment；已存在的 `volcano-video` 不会被改写，可在设置页改用新 Driver。

## 节点级成本追踪（Alembic 0024）

`usage_events` 即成本追踪记录：每次模型调用或渲染一条，现在额外记录所属的集
（`episode_id`）、镜头（`shot_id`）、工作流 Run/Step、用途、档位、Deployment，以及
输入/输出/缓存 Token 与 GPU 秒。不设外键，删除镜头或 Run 后成本历史仍可查。

- **实际成本**：视频任务成功后按厂商返回的用量计算——Token（`output_per_million_tokens`）
  > GPU 秒（`gpu_per_second`，自建算力）> 视频秒（`output_per_second`）；按条计费或缺少
  用量时只保留估算，`cost_source=estimate`。
- **成本报告**：`GET /api/usage/report?project_id=…&episode_id=…` 返回总计（估算 / 实际 /
  有效成本、Token、GPU 秒、视频秒、失败次数、无价格调用数、估算偏差百分比），并按用途、
  Deployment、档位、镜头（前 20）、自建 vs 云端、操作拆分。
- **调用树**：`GET /api/workflow/runs/{run_id}/trace` 返回 步骤 → 任务 → 模型调用 → 用量，
  每层带成本小计；不挂在任务上的 LLM 调用挂在所属步骤上。
- 本地 H3 若想按电费计实际成本，在其 Deployment 价格里加 `gpu_per_second`
  （例如 300W、0.6 元/kWh ≈ `0.00005`）；报价仍按 `output_per_second` 估算。

## 兼容边界

- 兼容周期：保留到下一个模型配置大版本；数据库没有任何 Deployment 时，Runtime
  仍可从旧环境变量临时构建，并输出迁移提示。
- 一旦数据库存在 Deployment，Catalog 是模型地址、模型名、能力和价格的真相源；环境
  变量只用于 bootstrap 和解析 `credential_ref`。
- API Key 是 write-only。管理 API 只返回 `credential_configured`，不会返回引用或密钥值。
- Mock Provider 仅在 `ALLOW_MOCK_PROVIDERS=true` 时允许，且不会被写入生产 Catalog。

## 热加载与故障处理

配置保存后执行 `validate → build → atomic swap`。失败时数据库保留待修配置，但 API 和
Worker 继续使用上一版有效 Snapshot；模型服务页会显示最近一次加载错误。也可调用：

```text
POST /api/model-runtime/reload
POST /api/model-runtime/reload?probe=true
```

探测失败不会删除配置，也不会清空当前 Catalog。异步视频获得远端 task id 后禁止自动
切换 Provider，轮询/下载失败进入显式重试或 reconcile，避免重复付费推理。

## Local H3 联调顺序

1. 确认 Gateway `/health` 可访问且 Token 正确。
2. 在模型服务页新增 `Local H3 Gateway`，填写稳定 slug、Gateway 地址、模型和 API Key。
3. 高级设置保持单卡 16GB 建议并发 `1`，再执行“测试连接”。
4. 在“用途绑定”把 `shot_video` 的主模型设为该 Deployment。
5. 先执行单镜头 smoke，检查 Job 与 ModelInvocation 的 deployment revision、轮询和 Artifact。

### 开启 Local H3 图生视频（首帧 / 首尾帧）

Gateway 默认只开放文生视频。量化 H3 工作流里的 `MiniMaxH3ImageToVideo` 节点本身支持
首帧/尾帧图片输入，开启步骤：

1. 在 ComfyUI 里给该节点接一个 `LoadImage`，手动跑通一次首帧生成，记下图片输入名。
2. Gateway 环境变量设置 `H3_GATEWAY_MODES=text,first_frame`（首尾帧再加 `first_last_frame`）。
   输入名可留空，由 Gateway 从 `/object_info/MiniMaxH3ImageToVideo` 自动识别；识别不到时
   用 `H3_GATEWAY_FIRST_FRAME_INPUT` / `H3_GATEWAY_LAST_FRAME_INPUT` 显式指定。
3. 重启 Gateway，确认 `/health` 的 `capabilities.modes` 已包含新模式。
4. 在 FishStudio 模型服务页把 Local H3 Deployment 的能力 `modes` 改为同一列表。
   之后有首帧的镜头才会被路由到本地 H3（见上文按真实请求形态路由）。

FishStudio 会把本地存储里的帧以 base64 内联发送；Gateway 只接受内联 PNG / JPEG / WebP，
不会去拉取任意 URL，单张上限 `H3_GATEWAY_MAX_IMAGE_BYTES`（默认 10MB）。图片在提交时
上传到 ComfyUI input 目录并通过 `LoadImage` 节点接入工作流。

远端 Gateway/ComfyUI 的详细部署仍见 `MINIMAX_H3_REMOTE_DEPLOYMENT.md`。
