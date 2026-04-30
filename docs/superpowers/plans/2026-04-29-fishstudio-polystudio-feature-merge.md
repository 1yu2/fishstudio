# FishStudio PolyStudio Feature Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the project branded as FishStudio while importing the useful PolyStudio feature modules.

**Architecture:** FishStudio remains the source of truth for branding and current core media-generation tools. PolyStudio modules are merged incrementally as additive features: settings, workspace memory, skills, multimodal understanding, video upload, and WebSocket broadcast. Existing FishStudio implementations for virtual anchor, video generation, and audio mixing must not be overwritten by older PolyStudio versions.

**Tech Stack:** FastAPI, LangGraph, LangChain tools, React, Vite, TypeScript, Excalidraw, Qwen/DashScope compatible API, local filesystem storage.

---

## Global Rules

- [ ] Keep all user-facing project naming as `FishStudio`.
- [ ] Do not rename README title, FastAPI title, frontend title, or visual brand to PolyStudio.
- [ ] Preserve current FishStudio implementations of:
  - `backend/app/tools/virtual_anchor_generation.py`
  - `backend/app/tools/volcano_video_generation.py`
  - `backend/app/tools/audio_mixing.py`
- [ ] Prefer additive merges over directory replacement.
- [ ] After each module, run the module-specific verification commands before moving on.
- [ ] Update this plan by checking off completed items after verification.

## Module 1: Settings Center

**Purpose:** Add a FishStudio settings page and backend API for tool toggles, installed skills, MCP config, environment variables, and workspace file editing.

**Files:**
- Create: `backend/app/routers/settings.py`
- Modify: `backend/app/main.py`
- Create: `frontend/src/components/SettingsPage.tsx`
- Create: `frontend/src/components/SettingsPage.css`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/HomePage.tsx`
- Modify: `frontend/src/components/ChatInterface.tsx`

**Implementation Checklist:**
- [x] Port `settings.py` from PolyStudio and keep comments/title aligned with FishStudio.
- [x] Register settings router in `backend/app/main.py` under `/api`.
- [x] Port `SettingsPage.tsx` and `SettingsPage.css`.
- [x] Replace any PolyStudio display text with FishStudio.
- [x] Add settings navigation from home and chat views.
- [x] Keep existing FishStudio routing behavior intact.

**Verification:**
- [x] Run: `cd backend && python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`
- [x] Check: `curl http://localhost:8000/health`
- [x] Check: `curl http://localhost:8000/api/settings/skills`
- [x] Check: `curl http://localhost:8000/api/settings/mcp`
- [x] Check: `curl http://localhost:8000/api/settings/env`
- [x] Run: `cd frontend && npm run build`
- [ ] Browser check: settings page opens, displays FishStudio, and returns to the previous page.

## Module 2: Workspace Memory

**Purpose:** Add persistent workspace files that can influence Agent behavior and store long-term creative preferences.

**Files:**
- Create: `backend/app/services/workspace_service.py`
- Create: `backend/app/tools/workspace_tools.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/services/agent_service.py`
- Modify: `backend/app/services/prompt.py`
- Create directory: `backend/workspace/`

**Implementation Checklist:**
- [x] Port workspace service from PolyStudio.
- [x] Ensure startup creates default workspace files:
  - `AGENTS.md`
  - `TOOLS.md`
  - `IDENTITY.md`
  - `USER.md`
  - `SOUL.md`
  - `MEMORY.md`
- [x] Keep default identity text branded as FishStudio.
- [x] Add `write_memory` tool to Agent tools.
- [x] Inject non-empty workspace context into system prompt.
- [x] Ensure pure template files are not injected as real memory.

**Verification:**
- [x] Start backend and confirm `backend/workspace/*.md` files exist.
- [ ] Edit `backend/workspace/USER.md` with a simple preference.
- [x] Start a new chat and confirm Agent behavior can reference that preference.
- [ ] Ask Agent to remember a preference and confirm `MEMORY.md` updates.
- [x] Run: `cd backend && python -m pytest tests -v`

## Module 3: Skill System

**Purpose:** Add Progressive Loading skills so Agent can discover, load, and use specialized workflows only when relevant.

**Files:**
- Create: `backend/app/services/skill_service.py`
- Create: `backend/app/tools/skill_tools.py`
- Create directory: `backend/skills/public/`
- Create directory: `backend/skills/custom/`
- Modify: `backend/app/services/agent_service.py`
- Modify: `backend/app/services/prompt.py`
- Modify: `backend/app/services/stream_processor.py`
- Modify: `frontend/src/components/ChatInterface.tsx`
- Modify: `frontend/src/components/ChatInterface.css`

**Implementation Checklist:**
- [x] Port skill scanning and settings-state logic.
- [x] Add installed skill settings API support through Module 1 router.
- [x] Port baseline skills from PolyStudio:
  - `public/skill-creator`
  - `custom/xiaohongshu-copywriter`
  - `custom/video-creator`
  - `custom/virtual-anchor`
  - `custom/podcast-creator`
  - `custom/paper-writing`
- [x] Review skill text and replace PolyStudio branding with FishStudio where user-facing.
- [x] Add skill tools: `read_skill_file`, `list_skill_dir`, `init_skill`, `write_skill_file`, `delete_skill_file`.
- [x] Inject only skill metadata into prompt, not full skill bodies.
- [x] Extend stream processor to emit `skill_matched` events.
- [x] Add ChatInterface skill badge and skill tool folding behavior.

**Verification:**
- [x] Check: `curl http://localhost:8000/api/settings/skills/installed`
- [x] Enable/disable a custom skill from settings page.
- [ ] Prompt: `帮我写一篇小红书文案`
- [x] Confirm UI shows `正在使用技能：...`.
- [x] Confirm tool calls include `read_skill_file` or `list_skill_dir`.
- [x] Run: `cd backend && python -m pytest tests -v`
- [x] Run: `cd frontend && npm run build`

## Module 4: Video Upload

**Purpose:** Let users upload videos in chat and pass uploaded video URLs into the conversation for later analysis or reuse.

**Files:**
- Modify: `backend/app/routers/chat.py`
- Modify: `frontend/src/components/ChatInterface.tsx`
- Modify: `frontend/src/components/ChatInterface.css`
- Optionally modify: `frontend/src/components/HomePage.tsx`

**Implementation Checklist:**
- [x] Add `/api/upload-video` endpoint.
- [x] Accept `mp4`, `mov`, `avi`, `mkv`, and `webm`.
- [x] Save uploaded videos to `backend/storage/videos/`.
- [x] Return relative URL like `/storage/videos/<filename>`.
- [x] Add uploaded video state to chat input.
- [x] Render video preview before send.
- [x] Include `[视频: /storage/videos/xxx.mp4]` in sent message content.
- [x] Store message `videoUrls` for display.

**Verification:**
- [x] Upload an `.mp4` from chat.
- [x] Confirm file exists in `backend/storage/videos/`.
- [x] Confirm message preview can play the video.
- [x] Confirm sent message includes the video path.
- [x] Run: `cd frontend && npm run build`

## Module 5: Qwen3-Omni Multimodal Understanding

**Purpose:** Add a tool that can analyze uploaded images, audio, and videos with Qwen3-Omni via DashScope/OpenAI-compatible API.

**Files:**
- Create: `backend/app/tools/qwen_omni_understanding.py`
- Modify: `backend/app/services/agent_service.py`
- Modify: `backend/app/services/prompt.py`
- Modify: `backend/env.example`
- Modify: `backend/requirements.txt`

**Implementation Checklist:**
- [ ] Port Qwen3-Omni tool from PolyStudio.
- [ ] Register `qwen_omni_understand` in Agent tools.
- [ ] Add prompt guidance for image/audio/video understanding.
- [ ] Support `/storage/images/...`, `/storage/audios/...`, and `/storage/videos/...`.
- [ ] Add clear error when `DASHSCOPE_API_KEY` is missing.
- [ ] Add required dependencies if absent: `openai`, `python-multipart`, `PyYAML`, and any runtime dependency needed by imported modules.
- [ ] Update `env.example` with Qwen3-Omni related comments while preserving FishStudio existing config.

**Verification:**
- [ ] Without `DASHSCOPE_API_KEY`, ask to analyze media and confirm clear error.
- [ ] With `DASHSCOPE_API_KEY`, upload image and ask: `分析这张图`.
- [ ] With `DASHSCOPE_API_KEY`, upload audio and ask: `这段音频讲了什么`.
- [ ] With `DASHSCOPE_API_KEY`, upload video and ask: `这个视频里发生了什么`.
- [ ] Run: `cd backend && python -m pytest tests -v`

## Module 6: WebSocket Broadcast

**Purpose:** Let external `/api/chat` calls broadcast SSE events to the open canvas page subscribed through WebSocket.

**Files:**
- Create: `backend/app/services/connection_manager.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/routers/chat.py`
- Modify: `frontend/src/components/ChatInterface.tsx`

**Implementation Checklist:**
- [ ] Port connection manager.
- [ ] Add `/ws/{canvas_id}` endpoint.
- [ ] Broadcast chat stream events when `/api/chat` receives `canvas_id`.
- [ ] Subscribe frontend chat page to current `canvas_id`.
- [ ] Avoid duplicate rendering when the frontend itself initiated the SSE request.
- [ ] Handle WebSocket disconnect without crashing backend.

**Verification:**
- [ ] Open a FishStudio canvas page.
- [ ] Send external request to `/api/chat` with matching `canvas_id`.
- [ ] Confirm page receives assistant delta and tool events live.
- [ ] Send normal frontend chat message and confirm no duplicate assistant message appears.
- [ ] Run: `cd frontend && npm run build`

## Module 7: Compatibility Regression

**Purpose:** Ensure imported PolyStudio features do not regress existing FishStudio media-generation workflows.

**Implementation Checklist:**
- [ ] Confirm `virtual_anchor_generation.py` was not overwritten with the shorter PolyStudio version.
- [ ] Confirm `volcano_video_generation.py` keeps FishStudio DMXAPI/Volcano compatibility.
- [ ] Confirm `audio_mixing.py` keeps FishStudio ffmpeg/pydub compatibility.
- [ ] Confirm existing chat, project, canvas, storage, image, video, 3D, TTS, and virtual-anchor paths still work.

**Verification:**
- [ ] Run: `cd backend && python -m pytest tests/test_audio_mixing.py -v`
- [ ] Run: `cd backend && python -m pytest tests/test_virtual_anchor_workflow.py -v`
- [ ] Run: `cd backend && python -m pytest tests -v`
- [ ] Run: `cd frontend && npm run build`

## Module 8: README and Environment Documentation

**Purpose:** Update documentation only after functionality is implemented and verified.

**Files:**
- Modify: `README.md`
- Modify: `backend/env.example`

**Implementation Checklist:**
- [ ] Keep README title and product name as FishStudio.
- [ ] Add setting center description.
- [ ] Add Skill system description.
- [ ] Add workspace memory description.
- [ ] Add Qwen3-Omni multimodal understanding description.
- [ ] Add video upload description.
- [ ] Add WebSocket broadcast description.
- [ ] Update screenshots/assets only if current UI changed enough to require them.
- [ ] Keep FishStudio existing ComfyUI, DMXAPI, and media-generation configuration notes.

**Verification:**
- [ ] README quick-start commands still match actual project.
- [ ] `backend/env.example` includes all required new env vars.
- [ ] No README mention incorrectly renames the app to PolyStudio.

## Final Acceptance Checklist

- [ ] Backend tests pass.
- [ ] Frontend build passes.
- [ ] Settings page works.
- [ ] Workspace memory works.
- [ ] Skill matching and loading works.
- [ ] Video upload works.
- [ ] Qwen3-Omni media understanding works when configured.
- [ ] WebSocket broadcast works.
- [ ] Existing FishStudio image/video/3D/TTS/virtual-anchor workflows still work.
- [ ] README and env docs are updated after verification.
