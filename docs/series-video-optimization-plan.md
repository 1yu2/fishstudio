# FishStudio 系列视频能力与系统优化方案

> 版本：v1.3（2026-08-03）
> 状态：结合当前项目代码、MiniMax Agent Hub 与 Liblib/LibTV 产品机制复核后的执行版
> 范围：短剧/系列视频能力、ComfyUI/BizyAir 链路、MiniMax/火山视频 Provider、生成任务编排、资产与分镜、API/token 成本、系统管理体验

---

## 一、结论摘要

方案的业务方向可行，但 v1.0 不适合原样实施。

建议保留“系列视频引擎 + Skill 模板”的产品方向，同时重新划分职责：

- **Skill / Workflow Template**：负责业务流程、创作方法、提示词和默认模板。
- **Workflow Run / Step**：负责把模板或 Agent 规划转为可查看、可暂停、可重试、可回放的执行图。
- **后端领域服务**：负责分镜、资产、审批、任务、重试、计费、产物和时间线合成。
- **LLM**：负责创意生成和高层决策，不负责逐镜头搬运参数、轮询任务和维护业务状态。
- **Worker / Provider Adapter**：负责长耗时生成、外部 API 适配、并发限制和失败恢复。

核心判断：

1. **“系列引擎共享、业务 Skill 可插拔”可行**，但核心引擎不能只写在 `SKILL.md` 或 Prompt 中。
2. **BizyAir“已停服”的前提不成立**。截至 2026-08-03，BizyAir 官方文档、Serverless 能力和代码仓库仍在维护；应先诊断当前 CloudBerry/BizyAir 链路，而不是直接按停服替换。
3. **异步任务队列是系列视频的必要地基**，但当前进程内 WebSocket 无法直接承接独立 worker 和多 Uvicorn worker 的事件推送。
4. **分镜必须成为持久化、可版本化的数据模型**，不能继续依靠从聊天 Markdown 中解析 JSON。
5. **当前缺少真正的音画时间线合成器**，只有视频顺序拼接和独立音频混音，无法完整支撑对白、旁白、字幕、转场和多轨音频。
6. 从当前工作区到标准短剧 MVP，单人全栈合理剩余工期约为 **24-32 个工作日**；20 个工作日只能完成严格收缩版，带 Agent Planner、可编辑节点画布和个人资产中心的产品 V1 还需额外迭代。
7. **MiniMax-H3 应纳入整体视频生成并作为默认 balanced 候选，但理由是能力完整而不是单价最低**：截至 2026-08-03，H3 768P/2K 输出价为 ¥0.50/¥0.80 每秒，明显高于 Hailuo-2.3-Fast 768P 的 ¥0.225 每秒；H3 的价值在于统一多模态输入、首尾帧、参考素材和 2K 生成，Fast 仍适合 cheapest/preview，火山继续承担质量对照和兜底。
8. **产品形态应采用“双入口、同一执行内核”**：用户既可以像 MiniMax Hub 一样用自然语言让 Agent 自动规划，也可以像 LibTV 一样在结构化画布中逐节点编辑；两种入口最终都生成同一种 `workflow_run/workflow_steps`。
9. **当前不应直接建设开放式 Skill/工作流市场**。先完成内置短剧模板、版本锁定、资产中心和运行闭环，再逐步开放模板复制、发布、评分和第三方生态。
10. **画布不是业务真相源**。画布只展示和编辑工作流投影，项目、分镜、资产、审批、任务和产物仍以领域表为准，避免再次把关键状态塞进前端 JSON。

---

## 二、当前项目复核结果

### 2.1 BizyAir / ComfyUI 链路

v1.0 将问题定义为“BizyAir 已停服”，该判断需要修正。

截至 2026-08-03：

- BizyAir 官方仍提供 Serverless 和节点使用文档。
- 官方仓库仍在维护。
- 当前代码使用的 `/x/v1/upload/token` 与 `/x/v1/input_resource/commit` 接口仍出现在官方上传文档中。

当前项目已经实现标准 ComfyUI API：

- `/upload/image` 上传图片和音频。
- `/prompt` 提交工作流。
- `/history/{prompt_id}` 查询任务。
- `/view` 下载结果。

位置：`backend/app/tools/virtual_anchor_generation.py:974`。

BizyAir 上传只是 CloudBerry 模式下的附加步骤，位置：

- `backend/app/tools/virtual_anchor_generation.py:1046`
- `backend/app/tools/virtual_anchor_generation.py:1319`

因此当前问题应定义为：

> **CloudBerry/BizyAir 云执行链路未经重新验证，且云模式与本地模式耦合、默认配置不安全。**

需要优先处理的现有代码问题：

- `COMFYUI_CLOUDBERRY_MODE` 默认值为 `true`，自建 ComfyUI 更合理的默认值应为 `false`，见 `backend/app/tools/virtual_anchor_generation.py:566`。
- `drop_first_frame_from_video` 的主体位置错误，正常 `fps` 时函数会直接返回 `None`，见 `backend/app/tools/virtual_anchor_generation.py:218`。
- BizyAir OSS 上传使用了未导入的 `timezone`，见 `backend/app/tools/virtual_anchor_generation.py:1113`。
- 现有抽象只覆盖上传，不覆盖托管 Comfy 平台之间不同的认证、提交、状态、取消和下载协议。

### 2.2 Agent 与上下文

v1.0 对上下文问题的判断部分正确，但需要区分“单次 ReAct 内部”和“跨用户轮次”。

已经具备的能力：

- Skill 已采用 Progressive Loading，只在系统提示中注入名称、描述和路径，正文按需读取，见 `backend/app/services/skill_service.py:175`。
- 前端跨轮发送历史时不会把完整工具结果重新发给模型，只保留可读文本和图片/音频 URL，见 `frontend/src/components/ChatInterface.tsx:594`。

仍然存在的问题：

- `UNDERSTANDING_PROMPT`、`VIDEO_PROMPT`、`DIGITAL_PROMPT`、`AUDIO_PROMPT` 每次全部注入，见 `backend/app/services/prompt.py:359`。
- 工具描述被手工拼进系统提示，同时工具 Schema 还会由模型工具绑定机制携带，存在重复，见 `backend/app/services/agent_service.py:74`。
- 当前完整系统提示本地测量约 17,500 字符，其中手工工具描述约 7,100 字符。
- 在同一次 ReAct 执行内部，完整 `ToolMessage` 仍会进入后续模型步骤。
- 历史由前端组装后回传，不是后端基于持久化业务状态构建的可信上下文。
- 视频、3D 等产物引用在跨轮上下文中的保留并不完整。

因此上下文优化重点应从“实现 Skill 懒加载”改为：

1. 删除重复工具描述。
2. 根据项目类型只注入当前工作流。
3. 用结构化资产、分镜和任务状态替代聊天历史搬运。
4. 将系列生成改为高层批处理命令，减少 ReAct 循环次数。
5. 最后再实施历史滚动摘要。

### 2.3 任务与实时通信

当前视频、3D、虚拟人生成会在工具内部同步轮询：

- 视频最长约 600 秒：`backend/app/tools/volcano_video_generation.py:657`
- 虚拟人最长约 3600 秒：`backend/app/tools/virtual_anchor_generation.py:1375`
- 任务挂在 Agent/SSE 请求上，连接中断后缺少持久化恢复能力。

现有 WebSocket 管理器是进程内字典：`backend/app/services/connection_manager.py:14`。

生产配置运行 4 个 Uvicorn worker：`docker-compose.prod.yml:38`。独立任务 worker 或其他 Uvicorn 进程无法直接访问浏览器连接所在进程的 `ConnectionManager`。

此外，前端 WebSocket 地址硬编码端口 `8000`，与 Compose 默认后端端口及反向代理方式不一致，见 `frontend/src/components/ChatInterface.tsx:458`。

### 2.4 项目、权限和执行上下文

用量统计、资产卡和流程 Gate 都依赖可信的 `user_id/canvas_id/run_id`，但当前工具执行链路没有统一执行上下文。

如果系统面向多用户，还存在必须先处理的隔离问题：

- 获取画布没有按 `owner_id` 过滤：`backend/app/services/history_service.py:29`。
- 更新和删除画布没有校验所有者：`backend/app/services/history_service.py:34`、`:69`。
- WebSocket 订阅没有认证和画布权限校验：`backend/app/main.py:98`。

因此在建设用量、资产和任务系统前，需要先引入统一的 `ExecutionContext`，至少包含：

- `request_id`
- `run_id`
- `user_id`
- `canvas_id`
- `project_id`
- `workflow_run_id`

这些字段应由后端认证和路由注入，不能作为普通工具参数交给 LLM 填写。

### 2.5 分镜、资产与一致性

当前分镜由前端正则解析聊天文本，见 `frontend/src/components/storyboard.ts:18`，不能支持：

- 分镜版本与审批。
- 多集、多场景层级。
- 镜头增删改后的局部重新生成。
- 多角色、多音轨、多字幕轨。
- 后台 worker 并发更新镜头状态。

角色一致性也不能仅通过“角色卡 + 单张参考图”保证：

- 当前图片编辑工具只接受一个 `image_url`，见 `backend/app/tools/volcano_image_generation.py:452`。
- 多角色同镜头无法直接传入多张角色参考图。
- 不同 Provider 对多参考图、角色保持、LoRA、FaceID 的能力不同。
- 仍需要人工审核或身份相似度 QA。

音色流程也需要拆分。当前 `qwen_voice_cloning_tool` 每次合成都重新创建音色，见 `backend/app/tools/qwen_tts.py:248`。系列内容应把音色创建与语音合成拆成两个步骤，并持久化 `voice_name/voice_id`。

### 2.6 时间线合成能力缺失

当前视频合成只做尺寸、帧率统一和顺序拼接，见 `backend/app/tools/video_concatenation.py:123`；音频拼接和 BGM 混音独立存在，见 `backend/app/tools/audio_mixing.py:122`。

系列视频还缺少：

- 镜头与对白/旁白时间轴对齐。
- 多角色语音轨、环境音轨和 BGM 轨。
- 字幕文件生成与烧录。
- 转场和片头片尾。
- 口型镜头与普通镜头混排。
- 音频长度与镜头时长协调。
- 横版、竖版、海外平台等导出模板。

该能力应作为独立的 `timeline_renderer` 或 `render_episode` 领域服务建设，而不是继续由 Agent 组合多个底层工具完成。

### 2.7 当前实施进度与必须先修的问题

截至当前工作区，方案已经完成了一部分基础代码，但还没有形成可运行的系列视频闭环。

| 模块 | 当前状态 | 判断与下一步 |
|------|----------|-------------|
| Series/Episode/Scene/StoryboardRevision/Shot | 已有 ORM 与迁移草案 | 保留；补 CRUD、Schema 校验、唯一约束和项目权限 |
| AssetCard/Artifact/引用关系 | 已有 ORM 与迁移草案 | 保留；补版本唯一约束、来源信息、标签和资产查询服务 |
| Approval/GenerationJob/UsageEvent | 已有 ORM 与迁移草案 | 仅是表结构骨架；尚无状态机、Worker、埋点和收费 Gate |
| ExecutionContext | 已有 `ContextVar` 骨架 | 目前中间件只注入 request/run ID；需在认证后注入 user/canvas/project，并修复流式响应期间上下文失效 |
| Canvas 权限 | 已增加 owner 过滤和部分校验 | 仍需区分“画布不存在”和“历史无主画布”，补 WebSocket 鉴权，避免已知 ID 越权 |
| 虚拟人/CloudBerry | 已修复首帧、`timezone` 和默认值问题 | 继续做实际链路回归和 `ComfyBackend` 解耦 |
| Workflow Run/Step | 未实现 | 是连接 Agent 规划、人工画布、领域服务与 GenerationJob 的关键缺口 |
| Series API/高层工具 | 未实现 | 下一迭代优先补齐，避免 Agent 直接操作底层生成工具 |
| Worker/MiniMax Provider/Timeline Renderer | 未实现 | 在权限、Schema 和 Workflow Run 稳定后接入 |
| 系列项目 UI | 未实现 | MVP 先做固定布局工作台，不先做通用低代码编辑器 |

当前代码合入前必须处理：

1. `SeriesProject.canvas_id` 增加 Canvas 外键，并明确一个 Canvas 是否只对应一个 Series Project；MVP 建议唯一绑定。
2. `Artifact.project_id/job_id`、`GenerationJob.project_id/shot_id`、`UsageEvent.project_id/job_id/user_id` 补齐类型一致的外键。
3. `UsageEvent.user_id` 改为与 `User.id` 一致的 Integer；金额从 Float 改为 `Numeric/Decimal`。
4. `Approval.approved_by` 改为用户外键；Episode/Scene/Shot/Asset 和引用表增加必要的组合唯一约束。
5. `chat.py` 保留 `HTTPException`，不能由泛型 `except Exception` 把 403 转为 500。
6. StreamingResponse 的生成器需要显式携带 ExecutionContext；不能依赖中间件在返回 Response 后仍保持 ContextVar。
7. 新领域迁移必须补模型/迁移一致性测试、权限测试和幂等测试后再进入生产库。

### 2.8 MiniMax Hub 与 Liblib/LibTV 对标结论

两类产品值得借鉴的重点不同：

| 参考产品 | 值得借鉴 | FishStudio 的落地方式 | 当前不复制 |
|----------|----------|-----------------------|------------|
| MiniMax Agent Hub | 主 Agent 拆任务、生成节点、并行执行、独立子任务工作区、任务进度、结果评价、Skill 版本生态 | 增加 Planner、Workflow Run/Step、步骤依赖、并行组、运行事件和用户评分；Agent 只生成执行计划并调用高层领域命令 | 首期不做任意 A2A 多 Agent、开放 Skill 商店和复杂隔离沙箱 |
| Liblib/LibTV | 模型/工作流聚合、角色资产库、剧本和分镜导入、批量生成、候选素材选择、一站式剧集工作台 | 增加项目/个人资产中心、Typed Node、节点级模型选择、生成分支对比、候选产物“提升为正式版本” | 首期不做公共模型社区、创作者结算和通用 Comfy 工作流市场 |

产品定位建议：

- **MiniMax Hub 提供“自动规划体验”**，LibTV 提供“专业创作控制”；FishStudio 应把两者组合为“Agent 自动搭建初稿 + 用户在项目工作台精修”。
- Agent 生成的不是一次性聊天答案，而是一个可验证的 `Workflow Plan`；创建收费任务前必须展示步骤、模型、数量和费用上限。
- 工作流节点必须是 Typed Node，例如 `outline.generate`、`storyboard.generate`、`asset.character.lock`、`shot.image.generate`、`shot.video.generate`、`episode.render`，不能允许任意 Prompt 节点直接读写数据库。
- 画布节点绑定 `entity_type/entity_id`，例如一个 Shot 节点绑定 `shots.id`；用户在列表、画布或聊天中修改，最终都调用同一领域服务。
- 生成节点允许创建 2-4 个候选分支，用户比较后将一个 Artifact 提升为 `approved`；未选分支保留成本和来源记录，但不进入最终时间线。
- 用户对候选产物的选择、评分和“重新生成原因”进入质量数据，后续用于 Provider 路由；评分不足时只记录，不立即让在线路由自动学习。
- 资产中心按 `personal/project` 两个作用域组织，支持角色、场景、风格、音色、标签、收藏、搜索、版本和来源追踪；跨项目复用必须由用户显式复制或引用。
- Skill 必须有 manifest、语义版本、输入输出 Schema、所需能力、默认模型策略和兼容版本；每个 Workflow Run 锁定具体 Skill/Template 版本，升级后可回滚和复跑旧版本。

---

## 三、修订后的问题清单

| # | 问题 | 修订后的判断 |
|---|------|-------------|
| P0 | 多用户隔离与执行上下文不足 | 用量、资产、任务、审批的共同前置条件 |
| P1 | CloudBerry/BizyAir 链路不稳定 | 需要验证和解耦，不能直接定性为厂商停服 |
| P2 | 无系列视频领域模型 | 缺少 Series/Episode/Scene/Shot 层级 |
| P3 | 成本黑盒 | 无统一调用、用量和费用记录 |
| P4 | Prompt 和 ReAct 上下文冗余 | 工作流全量注入、工具描述重复、单次 ReAct 工具结果膨胀 |
| P5 | 无模型角色分级 | 创作、路由、摘要共用一套模型配置 |
| P6 | 无明确的产物复用策略 | 不应简单透明缓存随机生成结果，应支持显式复用和内容寻址 |
| P7 | 重试与并发不可控 | Agent 可继续重试，Provider 级限流、幂等、熔断缺失 |
| P8 | 无媒体 Provider/Backend 抽象 | 工具直接调用外部 HTTP API，协议和错误格式不统一 |
| P9 | 长任务同步阻塞 | SSE 连接承担第三方任务轮询，断线无法恢复 |
| P10 | 审批 Gate 只靠 Prompt | 分镜、音色确认没有持久化版本与审批记录 |
| P11 | Provider 配置需重启 | 工具在模块导入时读取环境变量，无法真正热更新 |
| P12 | 分镜数据过于简单 | 聊天文本格式无法支撑专业分镜和多语言时间轴 |
| P13 | 角色/风格/音色未资产化 | 参考信息散落在对话与工具结果中 |
| P14 | 产物没有统一索引 | 图片、音频、视频散落在聊天和本地目录 |
| P15 | 无时间线合成器 | 无法可靠完成音画、字幕、转场和导出 |
| P16 | Skill 只有全局启用状态 | 无法为单个项目预激活短剧或诗集工作流 |
| P17 | 实时推送不支持多进程 | 进程内 WebSocket 无法承接独立 worker |
| P18 | 当前 TTS 音色重复创建 | 增加成本，且无法保证全剧音色资产稳定 |
| P19 | 视频 Provider 单一且无成本路由 | 当前只注册火山视频工具，无法按模式、质量、价格和成功率自动选择 MiniMax/火山 |
| P20 | 无 Workflow Run/Step | Agent 规划、人工节点编辑与后端任务没有统一执行记录 |
| P21 | 资产缺少个人空间和选择反馈 | 难以像 LibTV 一样跨项目复用资产，也无法积累 Provider 质量数据 |

---

## 四、目标架构

```text
                用户 / 前端
          ┌──────────┴──────────┐
          ▼                     ▼
   Agent 自然语言入口       项目工作台 / 节点画布
   Planner 生成执行计划      人工编辑 Typed Nodes
          └──────────┬──────────┘
                     ▼
          Skill / Workflow Template Registry
          业务策略、Schema、版本和默认模型策略
                     ▼
             Workflow Run / Steps
       依赖图 / 状态 / 暂停 / 审批 / 重试 / 回放
                     ▼
              Series Orchestrator
 高层命令：保存分镜、锁定资产、批量生成、局部重试、合成
     ┌──────────────┬────────────────┬────────────────┐
     ▼              ▼                ▼                ▼
 Storyboard       Asset            Approval         Timeline
 Service          Service          Service          Renderer
     └──────────────┴────────┬───────┴────────────────┘
                             ▼
                     Generation Jobs
                     持久化 / 幂等 / 重试 / 限流
                             ▼
                     Provider Adapters
        image / video(MiniMax、Volcano) / tts / comfy / 3d
                             │
                 ┌───────────┴───────────┐
                 ▼                       ▼
              Artifacts               Usage Events
           产物与存储索引             用量与成本记录
```

核心原则：

- **Skill 是模板，不是执行引擎。**
- **Agent 规划和人工画布共用一个 Workflow Run，不建设两套流程系统。**
- **Workflow Step 是业务编排，Generation Job 是收费执行；一个 Step 可以创建零个、一个或多个 Job。**
- **LLM 生成结构和决策，代码执行批处理。**
- **审批、任务、资产和产物必须持久化。**
- **所有收费操作必须具备幂等键。**
- **任务完成以数据库状态为准，实时事件只是体验增强。**
- **节点画布是领域数据和运行状态的投影，不是唯一存储。**
- **兼容旧 Canvas，但新的系列数据不再塞进 `Canvas.messages`。**

---

## 五、目标数据模型

### 5.1 项目和分镜

建议新增：

- `series_projects`
  - `id / canvas_id / user_id / project_type / workflow_template_id / title / status / active_skill_ids / created_at / updated_at`
- `episodes`
  - `id / project_id / episode_no / title / synopsis / status / created_at`
- `scenes`
  - `id / episode_id / scene_no / location / time_of_day / description / sort_order`
- `storyboard_revisions`
  - `id / episode_id / revision / schema_version / payload / status / created_by / created_at`
- `shots`
  - `id / scene_id / storyboard_revision_id / shot_no / duration_ms / visual_prompt / camera / transition / aspect_ratio / status / sort_order`

`storyboard_revisions.payload` 可保存完整结构化快照，`shots` 保存需要查询、更新和并发处理的关键字段。

分镜建议结构：

```json
{
  "schema_version": 1,
  "series_id": "series_xxx",
  "episodes": [
    {
      "episode_id": "ep_001",
      "number": 1,
      "title": "第一集",
      "scenes": [
        {
          "scene_id": "scene_001",
          "number": 1,
          "location": "城市天台",
          "time_of_day": "夜晚",
          "shots": [
            {
              "shot_id": "shot_001",
              "number": 1,
              "duration_ms": 5000,
              "visual_prompt": "角色站在天台边缘，城市灯光形成逆光",
              "camera": {"type": "push_in", "description": "缓慢推近"},
              "transition": {"type": "cut"},
              "aspect_ratio": "9:16",
              "character_asset_ids": ["asset_character_001"],
              "dialogue_tracks": [
                {
                  "speaker_asset_id": "asset_character_001",
                  "text": "我终于找到你了。",
                  "lang": "zh",
                  "start_ms": 500,
                  "end_ms": 2800
                }
              ],
              "narration_tracks": [],
              "subtitle_tracks": [
                {"text": "我终于找到你了。", "lang": "zh", "start_ms": 500, "end_ms": 2800}
              ]
            }
          ]
        }
      ]
    }
  ]
}
```

### 5.2 Workflow Template、Skill 与运行记录

建议新增：

- `workflow_templates`
  - `id / key / name / version / project_type / graph_schema / default_policy / status / created_by / created_at`
- `skill_manifests`
  - `id / key / name / version / input_schema / output_schema / required_capabilities / compatible_engine_version / source / status`
- `workflow_runs`
  - `id / project_id / template_id / template_version / planner_model / status / trigger_type(agent|manual|api) / input_snapshot / estimated_cost / cost_limit / started_at / finished_at / created_by`
- `workflow_steps`
  - `id / run_id / node_key / node_type / entity_type / entity_id / status / input_snapshot / output_snapshot / selected_artifact_id / attempt / error_code / started_at / finished_at`
- `workflow_step_dependencies`
  - `run_id / upstream_step_id / downstream_step_id / dependency_type`
- `artifact_feedback`
  - `id / project_id / step_id / artifact_id / user_id / action(selected|rejected|rated) / score / reason / created_at`

设计要求：

- Template 和 Skill 分离：Template 定义节点图，Skill 定义创作方法与领域提示；一个模板可锁定多个 Skill 版本。
- `graph_schema` 只能引用后端注册的 Typed Node，不直接保存可执行 Python、Shell 或任意数据库写入表达式。
- `workflow_runs` 保存模板、Skill、Provider Policy 和输入快照，确保未来模板升级后仍能解释和复跑历史任务。
- `workflow_steps.entity_type/entity_id` 绑定 Series/Episode/Scene/Shot/Asset/Artifact；画布编辑通过领域服务修改实体，再刷新节点投影。
- Planner 只允许生成合法节点与依赖；服务端再次校验 DAG、权限、最大镜头数、最大并发和费用上限。
- Step 的生命周期建议为 `draft -> ready -> waiting_approval -> queued -> running -> succeeded|failed|canceled|skipped`。
- `generation_jobs` 不替代 Workflow Step：例如“生成本集镜头视频”一个 Step 可以拆成 8 个镜头 Job，并按镜头独立重试。
- 用户选择候选 Artifact 后写入 `selected_artifact_id` 和反馈表；Provider Router 先离线聚合质量分，再由管理员发布新路由版本。

### 5.3 资产与产物

资产卡和生成产物应分开管理。

- `asset_cards`
  - `id / project_id / owner_user_id / scope(personal|project) / type(character|style|scene|voice) / name / version / description / tags / source / license_meta / provider_meta / status / created_at`
- `artifacts`
  - `id / project_id / job_id / type(image|audio|video|subtitle|model|document) / storage_key / content_hash / mime_type / duration_ms / width / height / metadata / created_at`
- `asset_artifact_refs`
  - `asset_id / artifact_id / role(reference|preview|approved|source)`
- `shot_asset_refs`
  - `shot_id / asset_id / role(character|style|scene|voice)`

设计要求：

- 业务关系使用稳定 ID，不使用角色名称或临时 URL 作为关联键。
- 数据库存 `storage_key`，访问 URL 在运行时生成，避免域名变化和临时 URL 过期。
- 资产需要版本；重新定版角色后，旧分镜审批不能自动沿用。
- 个人资产复制/引用到项目时锁定具体版本；项目运行期间不能被个人资产后续修改静默覆盖。
- 资产中心支持标签、收藏、搜索和最近使用；这些是工作台效率功能，不等于首期建设公共社区。
- 产物保留 Provider、模型、Prompt、参考资产版本和生成参数，支持复现和局部重试。

### 5.4 审批、任务和用量

- `approvals`
  - `id / project_id / target_type / target_id / target_revision / status / approved_by / approved_at / comment`
- `generation_jobs`
  - `id / project_id / shot_id / operation / provider / model / params / idempotency_key / external_task_id / status / progress / attempt / max_attempts / error_code / error / lease_until / started_at / finished_at / created_at`
- `usage_events`
  - `id / request_id / run_id / job_id / user_id / project_id / operation / provider / model / input_units / output_units / unit_type / unit_price_snapshot / estimated_cost / actual_cost / currency / latency_ms / status / attempt / cache_hit / provider_request_id / error_code / metadata / created_at`

用量表不应只保存输入元数据，还需要：

- 输出数量或输出时长。
- Provider 请求 ID。
- 重试次数。
- 缓存/复用命中情况。
- 当时价格快照和币种。
- 估算费用与最终费用。

---

## 六、修订后的实施方案

### S0 执行上下文与租户隔离

**解决**：P0，并为所有后续方案提供基础。

内容：

- 画布列表、保存、删除按 `owner_id` 校验。
- WebSocket 或后续任务事件接口增加认证和项目权限校验。
- 建立 `ExecutionContext`，通过 LangGraph config、`ContextVar` 或服务调用参数注入可信上下文。
- 所有收费操作生成 `request_id/run_id/idempotency_key`。

**产出**：用量、资产、任务、审批能够正确归属用户和项目。
**工期**：约 2-3 天。

### S1 ComfyBackend 解耦与虚拟人链路修复

**解决**：P1。

内容：

- 先验证当前 BizyAir Key、上传接口、CloudBerry 节点、工作流和输出下载链路。
- 修复 `drop_first_frame_from_video`、`timezone` 等当前缺陷。
- 本地/自建模式默认 `COMFYUI_CLOUDBERRY_MODE=false`。
- 将 `ComfyUIClient + BizyairUploader` 抽象为完整 `ComfyBackend`：
  - `upload_input`
  - `submit_workflow`
  - `get_status`
  - `cancel`
  - `download_outputs`
- 首期实现：
  - `StandardComfyBackend`
  - 可选 `BizyAirBackend`
- ComfyDeploy、RunComfy 等托管服务仅作为后续 Provider Adapter，不在首期同时接入。

**产出**：虚拟人链路可验证、可切换，云端和自建不再混用同一套隐式逻辑。
**工期**：代码约 2-4 天；GPU/工作流部署和模型下载单独评估。

### S2 结构化分镜与系列领域模型

**解决**：P2、P12。

内容：

- 增加 Series/Episode/Scene/Shot 数据模型。
- 使用 Pydantic/JSON Schema 校验分镜。
- 增加 `save_storyboard_tool`、`update_shot_tool`、`list_storyboard_tool`。
- LLM 通过工具调用保存结构化分镜，不直接要求前端解析聊天 JSON。
- 前端从 API 渲染结构化分镜，可按集、场景分组并支持编辑。
- 旧 `VIDEO_PROMPT` 和旧正则解析保留为兼容入口；旧文本可转换为 revision 1。

**产出**：分镜可查询、可编辑、可审批、可局部重试。
**工期**：约 4-6 天。

### S3 资产卡与产物系统

**解决**：P13、P14、P18。

内容：

- 建立 `asset_cards`、`artifacts` 和引用关系表。
- 资产作用域区分 `project` 与 `personal`；MVP 先实现项目资产，个人资产在数据模型中预留后再开放 UI。
- 新增高层资产工具：
  - `create_asset_card_tool`
  - `approve_asset_version_tool`
  - `list_project_assets_tool`
  - `bind_asset_to_shot_tool`
- 角色资产记录参考图、固定外观描述、Provider 能力和版本。
- 风格资产记录风格图、固定风格 Prompt 和输出规格。
- 音色流程拆分为：
  - 创建/克隆音色资产。
  - 使用已存在的 `voice_name/voice_id` 合成。
- 所有生成产物统一进入 `artifacts`，聊天消息只保存引用摘要。
- 增加标签、搜索、收藏、来源和许可信息；项目引用个人资产时锁定具体版本。
- 同一生成步骤可以关联多个候选 Artifact，用户将选中项提升为正式资产或镜头产物。

一致性策略：

- 单角色镜头可使用角色参考图编辑。
- 多角色镜头根据 Provider 能力选择多图参考或生成角色参考拼板。
- 使用固定角色描述、参考资产版本、模型和可选 seed。
- 增加人工审核；条件允许时增加人脸/身份相似度检测。

**产出**：角色、风格和音色成为可版本化、可复用的项目资产。
**工期**：约 4-7 天。

### S4 版本化审批与工作流状态机

**解决**：P10。

内容：

- 审批对象绑定具体版本，而不是只保存一个 Canvas 状态。
- 分镜状态：`draft → ready_for_review → approved → generating → rendered → completed`。
- 资产状态：`draft → preview_ready → approved → retired`。
- 用户通过前端明确按钮审批，不仅依赖“可以”“满意”等自然语言判断。
- 分镜或资产修改后自动使旧审批失效。
- 生成命令检查：
  - 当前分镜 revision 已审批。
  - 所引用的角色、风格、音色版本已审批。
  - 项目和用户权限合法。

**产出**：未经批准不能产生批量收费任务，修改后不会误用旧审批。
**工期**：约 3-5 天。

### S4A Workflow Template、运行图与 Agent Planner

**解决**：P20，并吸收 MiniMax Hub 的自动规划体验和 LibTV 的结构化工作台能力。

内容：

- 建立 `workflow_templates/workflow_runs/workflow_steps/workflow_step_dependencies`。
- 内置短剧模板使用受控 Typed Node，不把底层 Tool 名称直接暴露给模板：
  - `project.initialize`
  - `outline.generate`
  - `storyboard.generate`
  - `asset.character.generate`
  - `asset.version.approve`
  - `shot.image.generate`
  - `shot.video.generate`
  - `episode.render`
- Template 描述默认 DAG、输入输出 Schema、审批点、可并行节点和默认 Provider Policy。
- Skill manifest 描述创作规则、兼容引擎版本和所需能力；Workflow Run 创建时锁定 Template/Skill 版本。
- Agent Planner 将用户目标转换为“选择模板 + 填充参数 + 可选节点增删”的计划，不直接创建收费 Job。
- 服务端校验计划中的节点类型、依赖关系、实体权限、批量大小、并发和费用上限；校验通过后生成 Run/Steps。
- 收费 fan-out 前展示预计镜头数、候选数、模型、分辨率和费用区间，并进入 `waiting_approval`。
- 人工画布与 Agent 入口都读写同一个 Run；画布拖拽只修改允许编辑的依赖和参数，不直接绕过领域状态机。
- Worker 消费的是 Step 产生的 GenerationJob；Job 完成后由编排器推进下游 Step，不能由前端串联。
- MVP 的 Workflow 画布只需只读 DAG + 节点详情 + 暂停/重试；自由组装节点和发布模板放到后续版本。

**产出**：自动规划、人工编辑、任务执行和历史回放统一，避免形成“聊天流程”和“工作台流程”两套系统。
**工期**：后端最小运行层约 4-6 天；可编辑节点画布另需约 4-7 天。

### S5 持久化生成任务与事件机制

**解决**：P7、P9、P17。

内容：

- 所有生成类调用改为任务：提交后立即返回 `job_id`。
- Worker 根据 `generation_jobs` 执行 Provider 调用或轮询外部任务。
- 任务支持：
  - 幂等键。
  - 最大重试次数。
  - 指数退避和错误分类。
  - Provider/用户级并发限制。
  - 任务取消。
  - Worker lease 与崩溃恢复。
  - 单镜头重试。
- MVP 优先使用 Postgres 持久化任务 + Worker 轮询/`FOR UPDATE SKIP LOCKED`，避免一开始引入 Redis。
- 前端先通过任务 API 轮询；需要实时体验时再增加 Postgres `LISTEN/NOTIFY` 或 Redis Pub/Sub。
- 实时事件不能作为唯一状态来源，刷新页面必须能从数据库恢复。

Agent 不再逐镜头调用底层工具，而是调用：

- `start_shot_batch_generation`
- `retry_failed_shots`
- `render_episode`

由代码展开镜头、资产引用和 Provider 参数。

**产出**：断线不丢任务、重复点击不重复扣费、多镜头可受控并行。
**工期**：约 5-8 天。

### S6 Provider 网关与统一执行结果

**解决**：P8，并支撑 P5、P6、P7、P11。

内容：

- 新建 `backend/app/providers/`。
- 按能力定义接口：
  - image
  - video
  - tts
  - comfy
  - 3d
- 对异步 Provider 使用统一协议：
  - `submit`
  - `get_status`
  - `cancel`
  - `fetch_result`
- 定义统一结果对象，不再混用普通错误字符串和 JSON 字符串：

```text
ProviderResult
  success
  provider
  model
  external_request_id
  artifacts
  usage
  retryable
  error_code
  error_message
  raw_metadata
```

- 迁移时保留现有 LangChain Tool 名称，Tool 作为 Provider Service 的薄适配层，避免一次性破坏 Agent。
- 重试只在任务层执行，Agent 不自行无限重试。

#### MiniMax 视频 Provider 首批接入

MiniMax 不作为新的独立 Agent 工具暴露，而是作为统一 `generate_video` 领域命令背后的 Provider Adapter。

建议首批实现：

- `MiniMaxVideoProvider`
- `VolcanoVideoProvider`（由现有 `volcano_video_generation.py` 迁移）
- `VideoProviderRouter`

统一请求：

```text
VideoGenerationRequest
  mode: text | first_frame | first_last_frame | reference | regenerate
  prompt
  first_frame_artifact_id
  last_frame_artifact_id
  reference_image_artifact_ids
  reference_video_artifact_ids
  reference_audio_artifact_ids
  source_video_artifact_id
  duration
  resolution
  aspect_ratio
  quality: preview | standard | final
  provider_policy: cheapest | balanced | quality | explicit
  explicit_provider
```

MiniMax 能力定位：

| 模型 | 主要能力 | 推荐用途 |
|------|---------|---------|
| `MiniMax-H3` | V2 统一多模态视频生成；支持文本、图片、视频、音频输入，覆盖文生、首帧/首尾帧、参考素材和 2K 直出 | 系列视频默认 `balanced` 路由，尤其适合带角色/场景/动作/声音参考的整体镜头生成 |
| `MiniMax-Hailuo-2.3-Fast` | 低成本图生视频，规格固定、输入能力较窄 | 通过基准后作为 `cheapest/preview` 路由，不再作为整体能力主干 |
| `MiniMax-Hailuo-2.3` | 旧版文生/图生质量档 | 已有兼容需求时保留，不建议新架构围绕旧接口设计 |
| `MiniMax-Hailuo-02` | 低规格预览和部分旧能力 | 仅作为历史兼容或极低成本动作草稿，按 API 可用性和实测启用 |
| 现有火山视频模型 | 已接入 text/image/start_end 路径 | Provider 对照、特定首尾帧能力和 MiniMax 失败时的人工兜底 |

默认路由建议：

1. `provider_policy=balanced`：优先 `MiniMax-H3 768P`，把角色图、场景图、动作参考视频和参考音频按节点输入映射到 H3 V2 content。
2. `provider_policy=cheapest + mode=first_frame + quality=preview`：在 Hailuo-2.3-Fast 仍可用且基准达标时优先 Fast，否则使用 H3 768P。
3. `mode=first_last_frame/reference`：优先选择原生支持该输入组合的 H3；火山作为已验证能力兜底。
4. `quality=final`：优先在 H3 2K、H3 768P 和火山质量档之间依据项目基准选择，不把“更高分辨率”直接等同于“角色更稳定”。
5. `mode=regenerate` 或 768P 升级 2K：使用 H3 V2 对应的再生成能力时必须关联源 Artifact 和原始 Job，禁止覆盖原产物。
6. 用户或管理员可以显式锁定 Provider；重要镜头不得自动切换到未验证模型，自动回退前必须展示潜在追加费用。

路由算法：

1. 按模式、时长、分辨率、首尾帧、参考图片/视频/音频数量和再生成能力过滤 Provider。
2. 根据 `provider_policy` 对价格、近期成功率、平均延迟和质量评分排序。
3. 将最终 Provider、模型和价格快照写入 `generation_jobs`，任务执行期间不再自动变更。
4. Provider 失败后，只有被标记为可重试/可切换的错误才能回退到其他 Provider；内容审核失败不得自动跨 Provider 重试。

接入细节：

- 新接入直接围绕 MiniMax V2 设计，Adapter 负责把 Artifact 转换为 H3 的多模态 content；旧 Hailuo API 作为独立 capability 保留，不能让旧请求结构限制 H3。
- MiniMax 视频 API 使用“创建任务 → 查询任务 → 获取文件”的异步流程，可直接接入 `generation_jobs + worker`。
- 本地 `/storage/...` 输入由 Adapter 转换为 Provider 支持的上传、URL 或 Data URL 形式，不要求业务层感知文件协议。
- 第一版使用任务轮询；Webhook/Callback 作为后续实时优化，不能成为任务完成的唯一依据。
- MiniMax 的 `task_id`、`file_id`、请求耗时、模型、输入媒体计费量、输出时长和分辨率写入 Job、Artifact 和 Usage Event。
- H3 Context-IR、再生成等高级端点先映射为独立 capability，完成语义和费用验证后再出现在通用节点配置中。

接入前必须建立 20-30 个标准镜头的 Provider 基准集，覆盖：

- 单人和多人动作。
- 写实、动漫、产品和空镜。
- 推、拉、摇、移、跟随和固定运镜。
- 角色身份保持。
- 多参考图、参考视频和参考音频对一致性的实际收益。
- H3 768P 与 2K 的质量收益是否值得约 1.6 倍输出单价。
- Prompt 遵循度。
- 任务成功率、排队耗时、实际费用和内容审核通过率。

H3 可以立即纳入整体生成，但“默认 balanced 路由”仍应通过小规模基准后发布；Hailuo Fast 是否作为 cheapest 路由也必须看实际成功率和返工率，不能只比较单次 API 价格。

**产出**：Provider 可替换、错误可分类、用量可统计、任务可恢复。
**工期**：统一视频接口 + MiniMax/火山两个 Adapter 约 3-5 天；完整跨能力 Provider 网关约 5-8 天，其他 Provider 按需增量迁移。

### S7 用量与成本体系

**解决**：P3。

内容：

- 使用 `usage_events` 记录 LLM、图片、视频、TTS、Comfy、3D 调用。
- LLM 通过 callback/response metadata 获取输入输出 token。
- 媒体任务在 Provider Adapter 和 Job 层记录数量、时长、尺寸、模型和重试次数。
- 记录价格快照，不依赖后续价格表反算历史费用。
- 用量写入失败不得影响生成主流程，可异步补偿。
- 管理页按用户、项目、Provider、模型、操作和时间聚合。

MiniMax 视频价格快照（2026-08-03，人民币按量价格，仅用于方案测算，运行时以官方价格和后台配置为准）：

| 模型与规格 | 公开单价 | 6 秒输出 | 10 秒输出 | 推荐定位 |
|-----------|----------|----------|-----------|----------|
| `MiniMax-H3` 768P | ¥0.50/秒 | ¥3.00 | ¥5.00 | 默认 balanced、统一多模态输入 |
| `MiniMax-H3` 2K | ¥0.80/秒 | ¥4.80 | ¥8.00 | 重要镜头和最终质量候选 |
| `MiniMax-Hailuo-2.3-Fast` 768P | ¥0.225/秒 | ¥1.35 | ¥2.25 | 更便宜的图生预览候选 |
| `MiniMax-Hailuo-2.3` 768P | ¥2.00/6 秒、¥4.00/10 秒 | ¥2.00 | ¥4.00 | 旧版兼容质量档 |
| `MiniMax-Hailuo-02` 512P | ¥0.60/6 秒、¥1.00/10 秒 | ¥0.60 | ¥1.00 | 极低成本草稿，按可用性启用 |

H3 还会对输入视频计费：按对应输出分辨率分别为 768P ¥0.50/秒、2K ¥0.80/秒；音频输入免费，图片输入前 5 张免费、超出部分 ¥0.20/张。H3 再生成输出价为 ¥0.30/秒，但原始文本、图片、视频和音频输入会再次计费。价格引擎必须分别记录 `output_video_seconds/input_video_seconds/input_image_count/input_audio_seconds/regeneration_output_seconds`，不能只按最终视频时长估算。

示例：30 秒视频拆成 5 个 6 秒镜头，全部使用 H3 768P 时，仅输出视频公开单价约为 `30 × ¥0.50 = ¥15.00`；全部使用 Hailuo Fast 768P 则约为 `5 × ¥1.35 = ¥6.75`。H3 单次输出不是低价方案，只有在更多参考输入显著降低角色漂移和返工时，整项目总成本才可能更低，必须用实际返工率验证。

价格不能硬编码在 Provider Adapter 中，应作为带生效时间的价格表配置，并把任务创建时的价格快照写入 `usage_events`。

实施顺序：

1. 先完成事件写入和后台查询。
2. 再做管理仪表盘。
3. 成本优化必须用实际数据验证。

**产出**：能够回答“哪个项目、哪个用户、哪个步骤花了多少钱”。
**工期**：基础记录约 2-4 天，管理仪表盘约 2-3 天。

### S8 上下文工程与模型角色分级

**解决**：P4、P5。

内容按优先级：

1. 删除系统提示中的重复工具描述。
2. 根据 `workflow_template_id` 只注入当前工作流。
3. 系列任务使用高层领域工具，减少 ReAct 次数。
4. 后端根据项目数据构建上下文，不依赖前端完整历史。
5. 工具结果在单次 ReAct 中只保留标准摘要和 Artifact ID。
6. 历史超过阈值后滚动摘要；资产、审批和分镜不进入自然语言摘要，直接读取结构化数据。
7. 模型按角色配置：
   - creative：剧本、分镜、文案。
   - utility：摘要、格式修复、分类。
   - router：优先规则路由，必要时使用小模型。

注意：额外增加一个路由模型调用也会增加延迟和费用，因此简单意图优先使用项目类型和规则路由。

**产出**：降低长会话成本和指令漂移，不再依赖未经验证的“固定降低 50%”结论。
**工期**：约 3-5 天。

### S9 产物复用与生成策略

**解决**：P6。

不建议默认按 Prompt 哈希透明返回历史生成结果，因为生成具有随机性，用户点击“重新生成”通常期望新结果。

建议：

- 使用生成规格指纹：
  - Prompt 规范化结果。
  - Provider 和模型版本。
  - 尺寸、seed、参考资产版本。
  - 内容哈希。
- 默认只提示“已有相同规格产物，可复用”，由用户或工作流明确选择。
- 自动复用仅限 `reuse_policy=allow` 的项目内任务。
- 不跨用户透明复用，避免隐私和版权问题。
- “预览/成品分级”仅在 Provider 确有价格和规格差异时启用。

MiniMax 可作为首个预览/成品分级实例：

- 极低成本动作草稿：可选 Hailuo-02 512P，前提是当前 API 仍可用且质量可接受。
- 低成本单首帧预览：可选 Hailuo-2.3-Fast 768P。
- 标准多模态镜头：优先 H3 768P，使用锁定的角色、场景和可选动作/音频参考。
- 重要镜头或最终质量档：H3 2K、火山或人工锁定的 Provider。
- H3 768P 再生成到 2K 时保留原始 Artifact，并关联源 Job；它是新的收费版本，不是覆盖原文件的免费放大。
- 使用参考视频时把输入视频费用计入成本预估，避免“复用素材反而产生隐藏输入费用”。

**产出**：节约可重复使用的资产成本，同时避免缓存造成错误体验。
**工期**：约 2-4 天。

### S10 时间线合成器

**解决**：P15，并支撑短剧和诗集出海。

内容：

- 新建 `timeline_renderer`，输入为已批准分镜和 Artifacts，不由 LLM 拼装命令。
- 支持：
  - 镜头顺序和时长。
  - 对白、旁白、环境音和 BGM 多轨。
  - 音频 ducking 和响度标准化。
  - 字幕轨、SRT/ASS 生成和可选烧录。
  - 基础转场、片头、片尾。
  - 横版/竖版输出模板。
  - 生成 manifest，记录输入和最终导出参数。
- 第一版使用 FFmpeg 命令生成，避免把复杂时间线继续叠加到 MoviePy 单函数中。

**产出**：真正完成从镜头产物到可发布剧集视频的闭环。
**工期**：基础版约 4-7 天，高级字幕样式和复杂转场后续增量实现。

### S11 配置热更新与密钥管理

**解决**：P11。

内容：

- Provider 配置从模块级环境变量常量迁移到 Provider Factory。
- MiniMax 配置至少包含 API Key、Base URL、Fast/质量模型名、启用状态、默认分辨率、并发上限和价格表版本。
- 数据库存模型名、开关、Endpoint 和非敏感参数。
- API Key 加密存储，使用环境变量中的主加密密钥解密；页面只展示脱敏值。
- 配置修改写审计日志并增加版本号，Provider 缓存在版本变化时失效。
- 任务创建时快照 Provider 和模型，避免配置修改导致正在执行的任务不可复现。
- DB、JWT、加密主密钥、队列连接等基础设施配置仍保留在环境变量。

仅将 Key 写入 `Setting` 表不足以热更新，因为当前工具在模块导入时读取环境变量。

**产出**：Provider 配置可在线修改，同时保持密钥安全和任务可复现。
**工期**：约 3-5 天，依赖 S6。

### S12 双入口工作台、资产中心与业务 Skill

**解决**：P2、P14、P16、P20、P21。

产品入口：

- 首页提供“新建短剧”“新建诗集系列”。
- 同时提供“描述目标，让 Agent 创建”和“从模板创建”两个入口，最终都创建相同的 `series_project + workflow_run`。
- 创建 `series_project` 并设置 `workflow_template_id`、`active_skill_ids`、默认画幅和目标平台。
- 画布按项目类型、状态和标签筛选。

项目界面：

- 顶部保持大纲 → 资产 → 分镜 → 审批 → 生成 → 合成阶段导航。
- 主区提供结构化分镜/镜头列表；Workflow DAG 作为进度和依赖视图，不替代专业分镜编辑。
- 右侧提供节点详情、模型/规格、输入资产、预计费用、候选产物和错误信息。
- 角色/场景/风格/音色资产中心支持项目资产与个人资产切换、标签、搜索、收藏和版本选择。
- 每个生成节点支持显式模型选择：`auto/cheapest/balanced/quality/explicit`；显示能力限制和价格快照。
- 候选产物以分支方式展示，支持比较、选定、拒绝、评分和填写重生成原因。
- 镜头任务支持单节点暂停、取消、局部重试和“使用相同输入换 Provider 重试”。
- 产物时间线展示从输入资产、Step、Job、Provider 到最终导出的完整来源链。

Skill 定位：

- `short-drama/SKILL.md`
  - 剧情结构、分集钩子、角色设定、分镜创作规则。
  - 调用高层系列工具，不逐个调用图片和视频底层工具。
- `poetry-overseas/SKILL.md`
  - 诗集定位、市场和语言策略、风格模板、朗诵/旁白/字幕方案。
  - 复用同一分镜、资产、任务和时间线引擎。

Skill/Template 演进顺序：

1. **MVP**：仅内置 `short-drama`，模板由代码和数据库种子维护，运行时锁版本。
2. **V1**：增加模板复制、项目级参数覆盖、升级预览、回滚和 `poetry-overseas`。
3. **V2**：增加私有模板库、团队分享、评分和导入导出。
4. **生态阶段**：确认权限、沙箱、审核、版权和计费机制后，再考虑公共市场和第三方发布。

**产出**：既保留 Agent 的低门槛，又提供专业工作台的可控性；Skill 可持续扩展，但业务可靠性由代码和数据库保证。
**工期**：固定布局 MVP 工作台约 4-6 天；可编辑 DAG、个人资产中心和候选对比另需约 7-12 天；每个 Skill 文档本身约 1 天，端到端联调另计。

---

## 七、优化后的实施路线图

### Phase A：收口当前未提交基础代码

| 内容 | 工期 | 交付 |
|------|------|------|
| Schema/迁移加固 | 2-3d | 外键、唯一约束、Numeric 金额、模型与迁移一致 |
| S0 执行上下文与权限 | 2-3d | Streaming/WS 可信上下文、Canvas/Project 隔离 |
| 领域模型与权限测试 | 1-2d | 迁移、CRUD、越权和幂等基础测试 |
| S1 BizyAir/Comfy 回归 | 1-2d，可并行 | 标准 Comfy 可用，云模式有明确验证结果 |

阶段验收：

- 新迁移能在空库升级、降级，并能从现有 `0001` 升级。
- HTTP、Streaming、WebSocket、任务查询和 Artifact 下载均使用同一权限规则。
- 403 不被转换成 500；历史无主 Canvas 有明确迁移/认领策略。
- 当前模型表还不能直接作为“已完成系列系统”验收，必须先通过本阶段。

### Phase B：先打通无收费的领域与工作流闭环

| 内容 | 工期 | 交付 |
|------|------|------|
| S2 Series/Storyboard Service + API | 3-5d | 项目、集、场景、镜头和 revision CRUD |
| S3 项目资产 + Artifact Service | 3-5d | 角色/场景/风格/音色和产物引用 |
| S4 Approval Gate | 2-3d | 版本审批和失效规则 |
| S4A 最小 Workflow Run/Step | 4-6d | 内置短剧 DAG、步骤状态、依赖和回放 |
| 短剧高层领域命令 | 2-3d | 生成计划、批量任务计划、局部重试计划 |

阶段验收：

- 不调用任何外部收费 API，也能完成“创建项目 → 生成/导入分镜 → 锁定资产 → 审批 → 创建执行计划”。
- Agent 入口与模板入口创建相同结构的 Workflow Run。
- 每个 Step 能定位到对应 Series/Episode/Shot/Asset，画布不保存第二份业务状态。
- 收费步骤停在 `waiting_approval`，能展示数量、规格和费用上限。

### Phase C：接入持久化任务与 MiniMax H3 生成

| 内容 | 工期 | 交付 |
|------|------|------|
| S5 generation_jobs + Worker | 5-8d | 断线恢复、幂等、重试、限流和取消 |
| 最小 VideoProvider 接口 | 1-2d | submit/status/cancel/fetch_result |
| MiniMaxVideoProvider | 2-3d | 以 H3 V2 为主、兼容 Hailuo Fast 的异步任务接入 |
| S7 基础 UsageEvent | 2-3d | Step/Job/Provider 级价格快照和实际用量 |
| 火山 Adapter | 1-2d，可延后 | 复用现有能力作为显式兜底 |

阶段验收：

- Workflow Step 能受控拆成多个镜头 Job，刷新页面和 Worker 重启后可恢复。
- 重复提交相同幂等键不会重复调用 MiniMax 等收费 API。
- MiniMax 失败能区分审核失败、参数错误、限流、超时和可重试服务错误。
- 用户能看到任务创建时的模型、规格、价格快照和最终 Artifact。

### Phase D：完成可交付的短剧 MVP

| 内容 | 工期 | 交付 |
|------|------|------|
| S10 基础时间线合成器 | 4-7d | 对白、字幕、BGM、最终视频 |
| 固定布局短剧工作台 | 4-6d | 分镜、资产、审批、任务、产物和导出 |
| Workflow 只读图与节点详情 | 2-3d | 进度、错误、暂停、重试和费用查看 |
| `short-drama` Template + Skill | 1-2d | 版本锁定的内置业务模板 |
| 端到端与故障恢复测试 | 2-3d | 1 集短剧、失败恢复和权限回归 |

阶段验收：

- 完成一个 1 集、3-8 镜头的短剧项目。
- 至少一个角色在多个镜头使用同一资产版本。
- 对白和字幕与镜头时间轴基本同步。
- 任一镜头失败不需要重跑整集。
- 用户可查看每个镜头实际使用的 Provider、模型和估算费用。
- `balanced` 镜头可路由到 MiniMax-H3 768P，`cheapest + first_frame` 可选择 Hailuo Fast，明确锁定镜头可保留火山。
- 用户通过 Agent 或模板开始项目后，都可以在同一个工作台继续人工修改。

### Phase E：补齐 Hub/LibTV 类专业体验

| 内容 | 工期 | 交付 |
|------|------|------|
| Agent Planner | 3-5d | 自然语言生成受控 Workflow Plan |
| 可编辑 Typed Node 画布 | 4-7d | 节点参数、合法依赖和局部执行 |
| 候选分支与 Artifact Feedback | 3-5d | 对比、选定、拒绝、评分和重生成原因 |
| 个人/项目资产中心 | 3-5d | 标签、搜索、收藏、版本复制和来源追踪 |
| S6 完整 Provider Gateway | 3-5d | 在 Phase B/D 的视频接口上扩展 image/tts/comfy/3d |
| S8 上下文与模型分级 | 3-5d | Prompt 瘦身和职责模型 |
| S9 显式复用策略 | 2-4d | 复用提示和规格指纹 |
| S11 配置热更新 | 3-5d | 安全在线配置 |
| 离线 Provider 质量评分 | 2-4d | 用选择/评分数据发布路由版本 |

### Phase F：模板扩展与生态验证

| 内容 | 工期 | 交付 |
|------|------|------|
| 诗集 Workflow Template + Skill | 1-2d | 市场、语言、风格工作流 |
| 双语音轨与字幕模板 | 2-4d | 中文朗诵、英文旁白/字幕 |
| 导出与验收 | 1-2d | 目标平台规格视频 |
| 私有模板复制/导入导出 | 2-4d | 团队复用、升级和回滚 |

公共 Skill/工作流市场、A2A 多 Agent、第三方结算和开放发布属于独立平台项目，必须在权限沙箱、审核、版权和计费体系成熟后单独立项，不计入本方案 MVP。

### 工期结论

- **从当前工作区到标准短剧 MVP**：单人全栈约 24-32 个工作日；已有 ORM 骨架可节省部分建模时间，但约束、API、Worker 和测试尚未完成，不能按“表已建好”直接扣除整个阶段。
- **加入 Agent Planner、可编辑节点画布、个人资产中心和候选对比的产品 V1**：在短剧 MVP 上再增加约 12-20 个工作日。
- **硬性 20 天版本**：只有采用内置固定模板、只读 Workflow 图、项目级资产、固定 MiniMax-H3 视频 Provider、轮询任务和基础时间线时才有机会完成。
- **公共市场/A2A 生态**：不纳入当前工期；它不是普通页面功能，而是涉及执行安全、版本兼容、审核、版权和结算的新平台能力。

---

## 八、硬性 20 天 MVP 范围建议

如果必须控制在 20 个工作日左右，范围应收缩为：

### 必须包含

- 用户/项目执行上下文和基础权限校验。
- 一个短剧项目类型。
- 一个内置、版本锁定的短剧 Workflow Template。
- 最小 `workflow_runs/workflow_steps`，支持只读进度、审批等待、暂停和单步重试。
- Series/Episode/Scene/Shot 结构化数据。
- 角色、风格、音色基础资产卡。
- 分镜和资产显式审批。
- 持久化任务、幂等、有限重试和单镜头重试。
- 一个图片 Provider、一个新视频 Provider、一个 TTS Provider。
- 新的系列编排链路优先接入 `MiniMax-H3` V2；硬性 20 天版本允许固定使用 H3 768P，不同时实现 Hailuo Fast/火山自动路由，现有火山工具继续保留为人工/旧流程兜底。
- Agent 入口只做“识别短剧意图 + 选择内置模板 + 填充参数”，不在 20 天内自由生成 DAG。
- 对 Agent 只暴露保存分镜、审批、启动批量生成和合成等高层命令，Provider 由项目默认配置确定。
- 基础音画、字幕、BGM 合成。
- 任务状态轮询和断线恢复。

### 暂缓

- 诗集出海。
- 全 Provider 网关迁移。
- ComfyDeploy/RunComfy 多托管平台同时接入。
- 高级成本仪表盘。
- Provider 配置完全热更新。
- 跨项目或跨用户缓存。
- 可编辑通用节点画布、任意节点增删和模板发布。
- 个人资产中心、候选分支对比和自动 Provider 质量学习。
- 公共 Skill/工作流市场与 A2A 多 Agent。
- 复杂转场、高级字幕模板和自动身份相似度 QA。
- MiniMax Webhook、多区域路由和复杂动态比价；MVP 使用轮询和静态价格快照。
- MiniMax/火山自动能力路由、失败自动切换和质量评分；这些能力进入 24-32 天标准短剧 MVP 或后续 V1。

---

## 九、兼容性与迁移策略

- 保留旧 `Canvas`、旧 `VIDEO_PROMPT` 和旧分镜正则解析。
- 新建系列项目时写入 `series_projects`，普通画布继续沿用旧路径。
- 新系列 Canvas 与 `series_project` 建议一对一绑定；Workflow 图只保存节点位置等 UI 元数据，Run/Step 和领域表保存真实状态。
- 旧分镜文本可通过转换工具生成 `storyboard_revision=1`，不要求立即批量迁移全部历史。
- 现有 LangChain Tool 名称暂时保留，内部逐步改为调用 Provider Service 和 Job Service。
- 现有 `/storage/...` 路径第一阶段继续可用，但 Artifact 数据库存储 `storage_key`，为后续对象存储做准备。
- Worker 和后端必须挂载相同存储目录；如果未来横向扩容，必须迁移到共享对象存储。
- 新任务状态和审批状态不写回聊天消息作为唯一数据源。

---

## 十、主要风险

### 10.1 外部服务风险

- BizyAir/CloudBerry 的账号、节点和工作流兼容必须实测。
- 自建 ComfyUI 的 GPU、模型、显存和冷启动成本不能计入纯代码工期。
- 托管 Comfy 平台的 API 不是简单上传器替换，需要独立 Adapter。
- MiniMax 的价格、模型能力、速率限制和内容审核策略可能调整，路由必须基于可配置能力矩阵和带生效时间的价格表。

### 10.2 生成一致性风险

- 资产卡只能提高一致性，不能保证角色绝对不崩。
- 多角色镜头需要 Provider 多参考图能力或参考图拼板。
- 模型版本升级可能改变角色和风格效果，因此必须记录模型与资产版本。
- H3 的多模态能力不代表所有人物、动作和画风都优于火山；Hailuo Fast 的输出价格更低也不代表整项目成本更低，默认路由前必须同时比较质量、返工率和输入媒体费用。

### 10.3 任务与费用风险

- 多镜头并行必须有 Provider 级并发上限，不能无限并发。
- 所有重试必须基于幂等键和错误分类。
- 审核失败、内容安全拒绝等错误不应自动重试。
- 用量记录失败不能导致重复执行生成任务。
- Provider 自动回退可能产生第二次费用，必须在 Job 中记录切换原因，并允许项目设置“失败不切换”或费用上限。

### 10.4 数据与安全风险

- Provider API Key 不能以明文形式直接存入通用 JSONB 设置。
- WebSocket、任务查询、Artifact 下载都必须校验项目权限。
- Prompt、参考图和音色可能包含隐私内容，缓存不能默认跨用户共享。

### 10.5 前端复杂度风险

- `frontend/src/components/ChatInterface.tsx` 已超过 2000 行；继续直接加入资产面板、任务面板和工作流状态会提高维护风险。
- 在 Phase B/D 中应拆分 Storyboard、Task、Asset、Artifact、Workflow 等独立组件和状态 hooks。
- 通用节点编辑器容易提前吞噬大量工期；MVP 应先做固定布局工作台和只读 DAG。

### 10.6 Agent 规划与生态风险

- Planner 可能生成非法依赖、遗漏审批或放大镜头数量；服务端必须使用 Typed Node、DAG 校验、批量上限和费用上限兜底。
- 用户评分存在样本少、风格偏好差异和恶意反馈，不能直接实时改变在线 Provider 路由。
- Skill/Template 升级可能导致历史项目不可复现，因此 Run 必须锁定版本并保留输入快照。
- 公共市场会引入第三方代码执行、Prompt 注入、版权、内容审核和收益结算风险，不能只用一个“发布”按钮开放。

---

## 十一、关键验收标准

1. **权限**：用户无法访问其他用户的项目、任务和产物。
2. **审批**：未批准的分镜/音色不能创建批量收费任务。
3. **版本**：分镜或资产修改后旧审批自动失效。
4. **幂等**：重复点击生成不会创建重复 Provider 请求。
5. **恢复**：断开 SSE/WebSocket、刷新页面或重启 Worker 后任务仍可恢复。
6. **局部重试**：单镜头失败只重试该镜头。
7. **追踪**：任一产物可追溯到项目、镜头、资产版本、Provider、模型、Prompt 和任务。
8. **成本**：能够按用户、项目、Provider 和操作查看用量。
9. **合成**：最终视频包含正确顺序的镜头、对白/旁白、字幕和 BGM。
10. **兼容**：普通单图、单视频和旧画布流程不受影响。
11. **Provider 路由**：同一高层视频命令可以根据能力和策略选择 MiniMax/火山，并允许人工锁定。
12. **MiniMax 成本验证**：标准基准镜头记录 H3/Hailuo 的成功率、平均耗时、输入媒体费用、实际单镜头成本、返工率和质量评分，不仅使用公开报价评估。
13. **双入口一致**：Agent 创建和模板创建最终产生相同的 Project、Workflow Run 和领域实体，且能在同一工作台继续编辑。
14. **工作流可恢复**：任一 Step 的状态、依赖、输入快照、输出 Artifact 和下游推进均可从数据库重建。
15. **收费确认**：批量生成前可看到模型、规格、数量和费用上限，未经确认不得创建收费 Jobs。
16. **候选选择**：V1 中用户选择/拒绝候选产物后可追踪反馈，不会静默覆盖已批准产物。

---

## 十二、关键决策记录

1. **系列视频不是独立平行产品，但必须有独立领域模型。** Canvas 继续作为统一项目入口，Series/Episode/Shot 作为可复用的系列内容地基。
2. **Skill 是业务模板，不是可靠性边界。** Prompt 负责指导，数据库和代码负责状态、审批和收费 Gate。
3. **LLM 调用高层命令。** 系列生成不允许 Agent 逐镜头执行几十次底层工具调用。
4. **任务状态以数据库为准。** WebSocket/SSE 只用于通知，不能承担任务持久化。
5. **先完成一个短剧垂直切片。** 验证结构化分镜、资产、任务、审批和时间线后，再扩展诗集出海。
6. **先记录再优化，但埋点要建立在统一执行上下文和结果协议之上。** 单纯给现有工具加装饰器不能得到可靠成本数据。
7. **产物复用必须显式。** 不默认用 Prompt 哈希透明缓存随机生成结果。
8. **本地 Comfy 是默认安全路径，CloudBerry/BizyAir 是可选 Backend。** 是否替换以实测结果决定，不以“已停服”假设决定。
9. **MiniMax 是统一视频 Provider，不是新的 Agent 工具。** Agent 只调用统一视频命令，由 Provider Router 根据能力、输入类型、费用和质量数据选择 H3/Hailuo/火山。
10. **H3 纳入整体生成，但不等于所有镜头强制使用 H3。** H3 作为 balanced 主候选，Hailuo Fast 作为 cheapest 图生候选；默认路由仍需基准验证。
11. **采用双入口、单执行内核。** MiniMax Hub 式 Agent 规划和 LibTV 式人工工作台统一落到 Workflow Run/Step。
12. **Typed Node 是安全边界。** Agent 和模板只能组合注册节点，不能执行任意 Tool、代码或数据库写入。
13. **画布只做投影。** 节点布局可保存在 Canvas UI 数据中，但领域实体、执行状态和产物关系不能以画布 JSON 为准。
14. **先内置模板，后开放生态。** 短剧闭环、版本锁定、回滚、权限和成本控制稳定前，不建设公共 Skill/工作流市场。
15. **反馈先离线使用。** 候选选择和评分用于离线评估并发布路由版本，不直接驱动不可控的实时自动学习。

---

## 十三、外部核验参考

- BizyAir 官方介绍：https://docs.bizyair.cn/cn/introduction
- BizyAir Serverless 文档：https://docs.bizyair.cn/cn/developer-platform/bizyair-serverless
- BizyAir 文件上传文档：https://docs.bizyair.cn/cn/developer-platform/upload-file
- BizyAir 多文件上传文档：https://docs.bizyair.cn/cn/developer-platform/upload_files
- BizyAir 官方仓库：https://github.com/siliconflow/BizyAir
- ComfyDeploy：https://www.comfydeploy.com/
- RunComfy API 文档：https://docs.runcomfy.com/serverless/development/webhooks-and-api-endpoints
- MiniMax 按量价格：https://platform.minimaxi.com/docs/guides/pricing-paygo
- MiniMax 视频 API 概览：https://platform.minimaxi.com/docs/api-reference/api-overview
- MiniMax 图生视频 API：https://platform.minimaxi.com/docs/api-reference/video-generation-i2v
- MiniMax V2 多模态视频生成：https://platform.minimaxi.com/docs/api-reference/video-generation-v2-create
- MiniMax H3 Context-IR：https://platform.minimaxi.com/docs/api-reference/video-generation-v2-h3-context-ir
- MiniMax 视频再生成：https://platform.minimaxi.com/docs/api-reference/video-generation-v2-regeneration
- MiniMax Agent Hub：https://hub.minimaxi.com/
- Liblib/LibTV：https://www.liblib.tv/
- LibTV 官方产品页：https://www.liblib.tv/modelinfo/78880cad895c4a35a488b9f6c5b60804
