# FishStudio PolyStudio Merge Status Todo

> Updated from `2026-04-29-fishstudio-polystudio-feature-merge.md` and the actual `fishstudio-polystudio-merge` worktree state.

## Current Todo

- [x] 设置中心 API + 前端设置页
- [x] Workspace 工作空间记忆
- [x] Skill 系统
- [x] 视频上传
- [x] Qwen3-Omni 多模态理解
- [x] WebSocket 实时广播
- [x] 全量后端回归测试
- [x] 前端生产构建
- [x] README/env.example 更新
- [ ] 浏览器人工验收：设置页打开、显示 FishStudio、返回上一页
- [ ] 真实 API 验收：配置 `DASHSCOPE_API_KEY` 后上传图片并询问 `分析这张图`
- [ ] 真实 API 验收：配置 `DASHSCOPE_API_KEY` 后上传音频并询问 `这段音频讲了什么`
- [ ] 真实 API 验收：配置 `DASHSCOPE_API_KEY` 后上传视频并询问 `这个视频里发生了什么`
- [ ] 浏览器人工验收：打开画布页后，用外部 `/api/chat` 请求携带匹配 `canvas_id`，确认页面实时接收 delta 和 tool 事件

## Verification Completed

- [x] `../.venv/bin/python -m pytest tests/test_qwen_omni_understanding.py tests/test_websocket_broadcast.py -v`
- [x] `../.venv/bin/python -m pytest tests/test_workspace_memory.py tests/test_skill_system.py tests/test_video_upload.py -v`
- [x] `../.venv/bin/python -m pytest tests -v`：25 passed, 1 skipped
- [x] `npm run build`：passed

## Notes

- App branding remains FishStudio.
- Existing FishStudio media tools were not replaced by PolyStudio versions: `virtual_anchor_generation.py`, `volcano_video_generation.py`, and `audio_mixing.py` remain in place.
- Remaining unchecked items require a browser session or a real DashScope API key.
