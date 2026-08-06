# E2E 全链路回归脚本（真实 PG + 运行中的 uvicorn :8765）。
# Mock: ALLOW_MOCK_PROVIDERS=true python scripts/e2e_series_regression.py
# Real: E2E_PROVIDER_MODE=real E2E_VIDEO_PROVIDER=volcano|minimax E2E_SHOT_COUNT=3..8
#       python scripts/e2e_series_regression.py
# 覆盖（2026-08-04 复审整改后）：费用 quote_id 闭环、确认前不建收费 job、
# 重试代际聚合（superseded_by/step_attempt）、Shot 级选定产物、无主项目认领、
# 个人资产 API、跨项目 step_id 反馈拦截、final manifest.artifact_selection。
import json
import os
import subprocess
import sys
import time

import httpx

BASE = os.environ.get("E2E_BASE", "http://127.0.0.1:8765")
DB = os.environ.get("E2E_DB", "fishstudio_e2e3")
ADMIN = {"username": "admin", "password": "admin12345"}
PROVIDER_MODE = os.environ.get("E2E_PROVIDER_MODE", "mock").strip().lower()
VIDEO_PROVIDER = os.environ.get("E2E_VIDEO_PROVIDER", "volcano").strip().lower()
SHOT_COUNT = int(os.environ.get("E2E_SHOT_COUNT", "3"))

results = []  # (name, ok, detail)


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}", flush=True)


def psql(sql):
    out = subprocess.run(
        ["docker", "exec", "fishstudio-postgres", "psql", "-U", "postgres",
         "-d", DB, "-tAc", sql],
        capture_output=True, text=True)
    return out.stdout.strip()


def wait_job_terminal(c, job_id, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = c.get(f"/api/jobs/{job_id}").json()
        if j["status"] in {"succeeded", "failed", "canceled"}:
            return j
        time.sleep(1)
    raise RuntimeError(f"job {job_id} 未进入终态")


def main():
    if not 3 <= SHOT_COUNT <= 8:
        raise RuntimeError("E2E_SHOT_COUNT 必须在 3-8 之间")
    if PROVIDER_MODE == "real":
        required = ["VOLCANO_API_KEY", "DASHSCOPE_API_KEY"]
        if VIDEO_PROVIDER == "minimax":
            required.append("MINIMAX_API_KEY")
            if os.environ.get("MINIMAX_H3_EXPERIMENTAL", "false").lower() != "true":
                raise RuntimeError("MiniMax 真实 E2E 需要 MINIMAX_H3_EXPERIMENTAL=true")
        missing = [key for key in required if not os.environ.get(key, "").strip()]
        if missing:
            raise RuntimeError(f"真实 Provider E2E 缺少环境变量: {', '.join(missing)}")
        if os.environ.get("ALLOW_MOCK_PROVIDERS", "false").lower() == "true":
            raise RuntimeError("真实 Provider E2E 必须设置 ALLOW_MOCK_PROVIDERS=false")

    c = httpx.Client(base_url=BASE, timeout=30)
    r = c.post("/api/auth/login", json=ADMIN)
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    c.headers["Authorization"] = f"Bearer {token}"
    check("登录 admin", True)

    # ── 项目 / run ──
    provider_policy = "cheapest" if PROVIDER_MODE == "real" and VIDEO_PROVIDER == "minimax" else "balanced"
    title = f"1集{SHOT_COUNT}镜头测试短剧"
    r = c.post("/api/series/projects", json={
        "title": title,
        "project_type": "short_drama",
        "settings": {"provider_policy": provider_policy},
    })
    assert r.status_code in (200, 201), r.text
    pid = r.json()["id"]
    sb = c.get(f"/api/series/projects/{pid}/storyboard").json()
    ep_id = sb[0]["episode_id"] if "episode_id" in sb[0] else sb[0]["id"]
    check("建项目+默认集", True, f"project={pid} episode={ep_id}")

    r = c.post("/api/workflow/runs", json={"project_id": pid, "goal": title})
    assert r.status_code == 200, r.text
    run_id = r.json()["id"]
    check("建 run", True, run_id)

    def run_detail():
        return c.get(f"/api/workflow/runs/{run_id}").json()

    def steps_by_type():
        d = run_detail()
        return {s["node_type"]: s for s in d["steps"]}, d["run"]["status"]

    def wait_step(ntype, statuses, timeout=20):
        t0 = time.time()
        st = {}
        while time.time() - t0 < timeout:
            st, _ = steps_by_type()
            if st[ntype]["status"] in statuses:
                return st[ntype]
            time.sleep(0.5)
        raise RuntimeError(f"step {ntype} 未进入 {statuses}: {st[ntype]['status']}")

    s = wait_step("outline.generate", {"waiting_approval", "queued", "pending"})
    r = c.post(f"/api/workflow/steps/{s['id']}/complete", json={})
    check("complete outline.generate", r.status_code == 200, r.text[:100])

    # storyboard.generate 前先导入分镜文本
    shot_lines = [f"镜头{i}（5秒）【固定】：测试镜头 {i}" for i in range(1, SHOT_COUNT + 1)]
    text = "整体风格：写实\n色调：暖色\n" + "\n".join(shot_lines)
    r = c.post(f"/api/series/episodes/{ep_id}/storyboard/import-text", json={"text": text})
    check(f"import-text {SHOT_COUNT} 镜头", r.status_code == 200, r.text[:120])
    revision = r.json()["revision"]

    sb2 = c.get(f"/api/series/projects/{pid}/storyboard").json()
    shots = sb2[0]["scenes"][0]["shots"]
    check(f"物化 {SHOT_COUNT} 个 shot", len(shots) == SHOT_COUNT, str(len(shots)))
    for i, sh in enumerate(shots):
        r = c.patch(f"/api/series/shots/{sh['id']}", json={
            "dialogue_tracks": [{"speaker_asset_id": None, "text": f"第{i+1}句对白"}]})
        assert r.status_code == 200, r.text
    check(f"PATCH dialogue_tracks ×{SHOT_COUNT}", True)

    s = wait_step("storyboard.generate", {"waiting_approval"})
    c.post(f"/api/workflow/steps/{s['id']}/complete", json={})

    # 角色资产 + 审批
    r = c.post(f"/api/series/projects/{pid}/assets", json={
        "type": "character", "name": "主角", "description": "测试角色"})
    assert r.status_code in (200, 201), r.text
    asset = r.json()
    r = c.post("/api/series/approvals", json={
        "target_type": "asset", "target_id": asset["id"],
        "target_revision": asset.get("version", 1), "action": "approve", "project_id": pid})
    check("角色资产审批", r.status_code == 200, r.text[:120])

    r = c.post("/api/series/approvals", json={
        "target_type": "storyboard", "target_id": ep_id,
        "target_revision": revision, "action": "approve", "project_id": pid})
    check("分镜 revision 审批", r.status_code == 200, r.text[:120])

    r = c.get(f"/api/series/episodes/{ep_id}/generation-gate")
    check("generation-gate ok", r.json().get("ok") is True, r.text[:200])

    s = wait_step("asset.character.generate", {"waiting_approval", "queued", "pending"})
    if s["status"] != "succeeded":
        c.post(f"/api/workflow/steps/{s['id']}/complete", json={})

    # ── 费用闭环（复审 0.6）──
    # 确认前不得创建任何收费 job（含 image）
    pre_jobs = c.get(f"/api/jobs?project_id={pid}").json()["jobs"]
    check("确认前无收费 job（jobs 为空）", len(pre_jobs) == 0, f"n={len(pre_jobs)}")

    r = c.get(f"/api/workflow/runs/{run_id}/cost-quote")
    quote = r.json()
    ops = {i["operation"] for i in quote["items"]}
    check("cost-quote 含 quote_id/expires_at 且明细含 video/image/tts",
          bool(quote.get("quote_id")) and bool(quote.get("expires_at"))
          and {"generate_video", "generate_image", "tts"} <= ops and float(quote["total"]) > 0,
          f"ops={ops} total={quote['total']} qid={quote.get('quote_id', '')[:8]}")

    r = c.post(f"/api/workflow/runs/{run_id}/confirm-cost", json={})
    check("confirm-cost 缺 quote_id → 4xx", r.status_code in (400, 422), str(r.status_code))
    r = c.post(f"/api/workflow/runs/{run_id}/confirm-cost", json={"quote_id": "bogus"})
    check("confirm-cost 错误 quote_id → 409", r.status_code == 409, str(r.status_code))
    r = c.post(f"/api/workflow/runs/{run_id}/confirm-cost", json={"quote_id": quote["quote_id"]})
    check("confirm-cost 正确 quote_id → 200", r.status_code == 200, r.text[:120])

    # 等 run 终态
    t0 = time.time()
    final = None
    while time.time() - t0 < 300:
        st, status = steps_by_type()
        if status in {"succeeded", "failed"}:
            final = (st, status)
            break
        time.sleep(2)
    st, status = final if final else steps_by_type()
    check("run 终态 succeeded", status == "succeeded",
          f"status={status} steps=" + ",".join(f"{k}={v['status']}" for k, v in st.items()))

    # 等所有 job 终态
    t0 = time.time()
    jobs = []
    while time.time() - t0 < 120:
        jobs = c.get(f"/api/jobs?project_id={pid}").json()["jobs"]
        if jobs and all(j["status"] in {"succeeded", "failed", "canceled"} for j in jobs):
            break
        time.sleep(2)
    jobs = c.get(f"/api/jobs?project_id={pid}").json()["jobs"]
    by_status = {}
    for j in jobs:
        by_status[j["status"]] = by_status.get(j["status"], 0) + 1
    check("jobs 全 succeeded", by_status == {"succeeded": len(jobs)} and len(jobs) > 0,
          f"n={len(jobs)} {by_status}")

    video_jobs = [j for j in jobs if j["operation"] == "generate_video"]
    def _ff_key(j):
        ff = (j.get("params") or {}).get("first_frame")
        if isinstance(ff, dict):
            return ff.get("storage_key")
        return ff
    ff_ok = all(_ff_key(j) for j in video_jobs)
    check("video job first_frame.storage_key 传入", ff_ok and len(video_jobs) == SHOT_COUNT, f"video_jobs={len(video_jobs)}")

    tts_jobs = [j for j in jobs if j["operation"] == "tts"]
    check(f"tts job ×{SHOT_COUNT} succeeded", len(tts_jobs) == SHOT_COUNT and all(j["status"] == "succeeded" for j in tts_jobs))

    sb3 = c.get(f"/api/series/projects/{pid}/storyboard").json()
    all_shots = [s for sc in sb3[0]["scenes"] for s in sc["shots"]]
    tracks = [t for sh in all_shots for t in (sh.get("dialogue_tracks") or [])]
    check("shot 对白轨回写 audio_storage_key",
          len(tracks) == SHOT_COUNT and all(t.get("audio_storage_key") for t in tracks), json.dumps(tracks)[:150])

    art_ok = True
    detail = []
    shot_artifacts = {}
    for sh in all_shots:
        arts = c.get(f"/api/series/shots/{sh['id']}/artifacts").json()
        arts = arts if isinstance(arts, list) else arts.get("artifacts", [])
        shot_artifacts[sh["id"]] = arts
        kinds = {a.get("kind") or a.get("type") for a in arts}
        detail.append(str(kinds))
        if not ({"image", "video"} <= kinds):
            art_ok = False
    check("每镜头 image+video artifacts", art_ok, ";".join(detail))

    usage = c.get("/api/usage/summary").json()
    check("usage_events 有记录", bool(usage.get("rows")), json.dumps(usage)[:200])
    print("  quote total =", quote["total"], " usage =", json.dumps(usage)[:300])

    # 渲染产物
    render_jobs = [j for j in jobs if j["operation"] == "render_episode"]
    rj = c.get(f"/api/jobs/{render_jobs[0]['id']}").json() if render_jobs else {}
    out = (rj.get("result") or rj.get("output") or {})
    pub = psql("SELECT artifact_metadata->>'is_publishable' FROM artifacts WHERE type='video' AND artifact_metadata->>'render_mode' IS NOT NULL ORDER BY created_at DESC LIMIT 1")
    check("render manifest.is_publishable", pub == "true", f"is_publishable={pub}")
    art_id = out.get("artifact_id")
    if art_id:
        dl = c.get(f"/api/artifacts/{art_id}/download")
        check("下载 final mp4 (带 token)", dl.status_code == 200, f"{len(dl.content)}B")
        open("/tmp/e2e_final.mp4", "wb").write(dl.content)
        ff = subprocess.run([sys.executable, "-c", """
import subprocess, imageio_ffmpeg, sys
exe = imageio_ffmpeg.get_ffmpeg_exe()
p = subprocess.run([exe, "-i", "/tmp/e2e_final.mp4"], capture_output=True, text=True)
for line in p.stderr.splitlines():
    if "Duration" in line:
        print(line.strip())
"""], capture_output=True, text=True)
        print("  ffprobe:", ff.stdout.strip(), ff.stderr[:100])
        check("final mp4 时长可读", "Duration" in ff.stdout, ff.stdout.strip())
        anon = httpx.Client(base_url=BASE, timeout=10)
        check("无 token download → 401", anon.get(f"/api/artifacts/{art_id}/download").status_code == 401)
        sk = out.get("storage_key") or f"videos/{art_id}.mp4"
        check("无 token /storage → 401", anon.get(f"/storage/{sk}").status_code == 401)

    # manifest 含 artifact_selection（复审 0.8/P2-2：renderer 记录产物来源）
    mkey = psql("SELECT storage_key FROM artifacts WHERE artifact_metadata->>'kind'='render_manifest' ORDER BY created_at DESC LIMIT 1")
    manifest = {}
    if mkey:
        mart = psql(f"SELECT id FROM artifacts WHERE storage_key='{mkey}' LIMIT 1")
        mdl = c.get(f"/api/artifacts/{mart}/download")
        try:
            manifest = json.loads(mdl.content)
        except Exception:
            manifest = {}
    sels = [s.get("artifact_selection") for s in manifest.get("shots", [])]
    check("manifest 含 artifact_selection",
          len(sels) == SHOT_COUNT and all(s in {"selected", "latest", "placeholder"} for s in sels),
          f"selections={sels}")

    # ── 选定产物（Shot 级，复审 0.3/P2-2）──
    sh0 = all_shots[0]
    vids = [a for a in shot_artifacts[sh0["id"]] if (a.get("kind") or a.get("type")) == "video"]
    vid0 = vids[0].get("id") or vids[0].get("artifact_id")
    r = c.post(f"/api/series/artifacts/{vid0}/feedback",
               json={"action": "selected", "reason": "E2E 选定"})
    check("feedback selected（无 step_id）", r.status_code == 200, r.text[:120])
    sb4 = c.get(f"/api/series/projects/{pid}/storyboard").json()
    sh0_after = [s for sc in sb4[0]["scenes"] for s in sc["shots"] if s["id"] == sh0["id"]][0]
    check("Shot.selected_artifact_id 写入",
          sh0_after.get("selected_artifact_id") == vid0,
          str(sh0_after.get("selected_artifact_id"))[:40])

    # feedback 伪造他项目 step_id → 403（复审 0.3）
    r = c.post("/api/series/projects", json={"title": "第二项目", "project_type": "short_drama"})
    pid2 = r.json()["id"]
    r = c.post("/api/workflow/runs", json={"project_id": pid2, "goal": "伪造 step 源"})
    run_b = r.json()["id"]
    d_b = c.get(f"/api/workflow/runs/{run_b}").json()
    step_b = d_b["steps"][0]["id"]
    r = c.post(f"/api/series/artifacts/{vid0}/feedback",
               json={"action": "selected", "step_id": step_b})
    check("feedback 伪造他项目 step_id → 403", r.status_code == 403, str(r.status_code))

    # ── 第二用户 bob 越权 ──
    import bcrypt
    h = bcrypt.hashpw(b"bobpass12345678", bcrypt.gensalt()).decode()
    psql(f"INSERT INTO users (username, password_hash, is_admin) VALUES ('bob','{h}',false) ON CONFLICT (username) DO NOTHING")
    anon = httpx.Client(base_url=BASE, timeout=10)
    bt = anon.post("/api/auth/login", json={"username": "bob", "password": "bobpass12345678"}).json()["access_token"]
    bc = httpx.Client(base_url=BASE, timeout=10, headers={"Authorization": f"Bearer {bt}"})
    bob_id = psql("SELECT id FROM users WHERE username='bob'")
    r = bc.get(f"/api/series/projects/{pid}")
    check("bob 访问他人 project → 403/404", r.status_code in (403, 404), str(r.status_code))
    if art_id:
        r = bc.get(f"/api/artifacts/{art_id}/download")
        check("bob 下载他人 artifact → 403/404", r.status_code in (403, 404), str(r.status_code))
    r = bc.post("/api/series/approvals", json={
        "target_type": "storyboard", "target_id": ep_id,
        "target_revision": revision, "action": "approve", "project_id": pid})
    check("bob 伪造 project_id 审批 → 403/404", r.status_code in (403, 404), str(r.status_code))
    r = bc.get(f"/api/jobs?project_id={pid}")
    check("bob 访问他人 project jobs → 403/404", r.status_code in (403, 404), str(r.status_code))

    # ── 无主项目（复审 0.4）：普通用户 403 → admin claim → owner 可访问 ──
    psql("INSERT INTO series_projects (id, title, user_id) VALUES ('e2e-orphan-proj', '历史无主项目', NULL) ON CONFLICT (id) DO NOTHING")
    r = bc.get("/api/series/projects/e2e-orphan-proj")
    check("bob 访问无主项目 → 403/404", r.status_code in (403, 404), str(r.status_code))
    r = bc.post("/api/series/projects/e2e-orphan-proj/claim", json={"user_id": int(bob_id)})
    check("bob(非admin) claim → 403", r.status_code == 403, str(r.status_code))
    r = c.post("/api/series/projects/e2e-orphan-proj/claim", json={"user_id": int(bob_id)})
    check("admin claim 无主项目给 bob", r.status_code == 200, r.text[:120])
    r = bc.get("/api/series/projects/e2e-orphan-proj")
    check("claim 后 bob(owner) 可访问", r.status_code == 200, str(r.status_code))

    # ── 个人资产（P2-3）──
    r = bc.post("/api/assets/personal", json={"type": "character", "name": "bob的私有人设", "description": "E2E"})
    check("bob 创建个人资产", r.status_code in (200, 201), r.text[:120])
    personal = r.json() if r.status_code in (200, 201) else {}
    r = bc.get("/api/assets/personal")
    bob_list = r.json()
    check("bob 个人资产列表=1", r.status_code == 200 and len(bob_list) == 1, f"n={len(bob_list)}")
    r = c.get("/api/assets/personal")
    admin_list = r.json()
    check("admin 看不到 bob 的个人资产（隔离）",
          all(a["id"] != personal.get("id") for a in admin_list), f"admin n={len(admin_list)}")
    r = bc.post(f"/api/assets/personal/{personal.get('id')}/copy-to-project",
                json={"project_id": "e2e-orphan-proj"})
    copied = r.json() if r.status_code in (200, 201) else {}
    check("copy-to-project 到认领项目",
          r.status_code in (200, 201) and copied.get("project_id") == "e2e-orphan-proj",
          f"{r.status_code} {r.text[:100]}")

    # ── 重试闭环（复审 0.7）：failed job → retry → 新 job succeeded → step/run 恢复 ──
    victim = video_jobs[0]
    step_id_v = victim.get("workflow_step_id") or psql(f"SELECT workflow_step_id FROM generation_jobs WHERE id='{victim['id']}'")
    psql(f"UPDATE generation_jobs SET status='failed' WHERE id='{victim['id']}'")
    psql(f"UPDATE workflow_steps SET status='failed', error_code='job_failed' WHERE id='{step_id_v}'")
    psql(f"UPDATE workflow_runs SET status='failed' WHERE id='{run_id}'")
    r = c.post(f"/api/jobs/{victim['id']}/retry")
    check("failed job retry → 新 job", r.status_code in (200, 201), r.text[:150])
    new_job = r.json()
    nid = new_job.get("id")
    ro = psql(f"SELECT params->>'retry_of_job_id' FROM generation_jobs WHERE id='{nid}'")
    sb_by = psql(f"SELECT superseded_by FROM generation_jobs WHERE id='{victim['id']}'")
    old_st = psql(f"SELECT status FROM generation_jobs WHERE id='{victim['id']}'")
    check("新 job retry_of_job_id + 旧 job superseded_by/状态不变",
          ro == victim["id"] and sb_by == nid and old_st == "failed",
          f"retry_of={ro} superseded_by={sb_by} old={old_st}")
    nj = wait_job_terminal(c, nid, timeout=90)
    check("重试新 job succeeded", nj["status"] == "succeeded", nj["status"])
    time.sleep(2)  # 等 on_job_finished 聚合 + advance
    step_st = psql(f"SELECT status FROM workflow_steps WHERE id='{step_id_v}'")
    run_st = psql(f"SELECT status FROM workflow_runs WHERE id='{run_id}'")
    check("当前 attempt 聚合：step 恢复 succeeded（旧 failed 不拖累）",
          step_st == "succeeded", f"step={step_st}")
    check("job 级 retry 后 run 恢复 succeeded", run_st == "succeeded", f"run={run_st}")

    # ── pause: 新 run 暂停后不应展开 jobs ──
    r = c.post("/api/workflow/runs", json={"project_id": pid, "goal": "暂停测试"})
    run2 = r.json()["id"]
    c.post(f"/api/workflow/runs/{run2}/pause")
    n0 = int(psql(f"SELECT count(*) FROM generation_jobs j JOIN workflow_steps s ON j.workflow_step_id=s.id WHERE s.run_id='{run2}'") or 0)
    time.sleep(4)
    n1 = int(psql(f"SELECT count(*) FROM generation_jobs j JOIN workflow_steps s ON j.workflow_step_id=s.id WHERE s.run_id='{run2}'") or 0)
    st2 = psql(f"SELECT status FROM workflow_runs WHERE id='{run2}'")
    check("pause 后无新 job 展开/lease", n0 == n1 and st2 == "paused", f"jobs {n0}→{n1} run={st2}")

    # retry_step 恢复 failed run
    psql(f"UPDATE workflow_runs SET status='failed' WHERE id='{run2}'")
    step2 = psql(f"SELECT id FROM workflow_steps WHERE run_id='{run2}' AND node_type='outline.generate' LIMIT 1")
    psql(f"UPDATE workflow_steps SET status='failed' WHERE id='{step2}'")
    r = c.post(f"/api/workflow/steps/{step2}/retry")
    st2 = psql(f"SELECT status FROM workflow_runs WHERE id='{run2}'")
    check("retry_step 恢复 failed run", r.status_code == 200 and st2 != "failed", f"run={st2}")

    d = c.get(f"/api/workflow/runs/{run_id}").json()
    check("step 序列化含 selected_artifact_id+error",
          all("selected_artifact_id" in s and "error" in s for s in d["steps"]))

    print("\n===== SUMMARY =====")
    failed = [r for r in results if not r[1]]
    print(f"{len(results) - len(failed)}/{len(results)} passed")
    for name, ok, detail in failed:
        print("FAILED:", name, detail)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
