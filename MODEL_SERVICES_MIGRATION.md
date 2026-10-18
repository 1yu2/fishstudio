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

远端 Gateway/ComfyUI 的详细部署仍见 `MINIMAX_H3_REMOTE_DEPLOYMENT.md`。
