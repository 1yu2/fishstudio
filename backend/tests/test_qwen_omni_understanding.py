import importlib
import json
import sys
from types import SimpleNamespace
from unittest.mock import patch

if "langchain_core" in sys.modules and not hasattr(sys.modules["langchain_core"], "__path__"):
    sys.modules.pop("langchain_core", None)
    sys.modules.pop("langchain_core.tools", None)

from app.services import agent_service, prompt


def test_qwen_omni_tool_returns_clear_error_without_api_key(monkeypatch):
    module = importlib.import_module("app.tools.qwen_omni_understanding")
    monkeypatch.setattr(module, "DASHSCOPE_API_KEY", "")

    result = module.qwen_omni_understand_tool.invoke({
        "media_path": "/storage/images/missing.png",
        "question": "分析这张图",
        "output_audio": False,
    })

    assert "DASHSCOPE_API_KEY" in result
    assert "未配置" in result


def test_full_prompt_includes_multimodal_understanding_guidance():
    full_prompt = prompt.get_full_prompt("- qwen_omni_understand: analyze media")

    assert "qwen_omni_understand" in full_prompt
    assert "多模态内容理解" in full_prompt
    assert "/storage/images/" in full_prompt
    assert "/storage/audios/" in full_prompt
    assert "/storage/videos/" in full_prompt


def test_agent_registers_qwen_omni_tool():
    captured = {}

    def fake_create_react_agent(**kwargs):
        captured.update(kwargs)
        return object()

    fake_tools = {
        "generate_volcano_image_tool": SimpleNamespace(name="generate_volcano_image", description="generate image"),
        "edit_volcano_image_tool": SimpleNamespace(name="edit_volcano_image", description="edit image"),
        "generate_3d_model_tool": SimpleNamespace(name="generate_3d_model", description="generate model"),
        "generate_volcano_video_tool": SimpleNamespace(name="generate_volcano_video", description="generate video"),
        "concatenate_videos_tool": SimpleNamespace(name="concatenate_videos", description="concat videos"),
        "detect_face_tool": SimpleNamespace(name="detect_face", description="detect face"),
        "generate_virtual_anchor_tool": SimpleNamespace(name="generate_virtual_anchor", description="virtual anchor"),
        "qwen_voice_design_tool": SimpleNamespace(name="qwen_voice_design", description="voice design"),
        "qwen_voice_cloning_tool": SimpleNamespace(name="qwen_voice_cloning", description="voice clone"),
        "concatenate_audio_tool": SimpleNamespace(name="concatenate_audio", description="concat audio"),
        "select_bgm_tool": SimpleNamespace(name="select_bgm", description="select bgm"),
        "mix_audio_with_bgm_tool": SimpleNamespace(name="mix_audio_with_bgm", description="mix audio"),
        "qwen_omni_understand_tool": SimpleNamespace(name="qwen_omni_understand", description="understand media"),
        "write_memory": SimpleNamespace(name="write_memory", description="write workspace memory"),
        "read_skill_file_tool": SimpleNamespace(name="read_skill_file", description="read skill"),
        "list_skill_dir_tool": SimpleNamespace(name="list_skill_dir", description="list skill dir"),
        "init_skill_tool": SimpleNamespace(name="init_skill", description="init skill"),
        "write_skill_file_tool": SimpleNamespace(name="write_skill_file", description="write skill file"),
        "delete_skill_file_tool": SimpleNamespace(name="delete_skill_file", description="delete skill file"),
    }

    patches = [patch.object(agent_service, attr, tool) for attr, tool in fake_tools.items()]
    with patch.object(agent_service, "create_llm", return_value=object()), patch.object(
        agent_service.workspace_service, "get_workspace_context", return_value=""
    ), patch.object(
        agent_service.skill_service, "get_skills_context", return_value=""
    ), patch.object(agent_service, "create_react_agent", side_effect=fake_create_react_agent):
        for p in patches:
            p.start()
        try:
            agent_service.create_agent()
        finally:
            for p in reversed(patches):
                p.stop()

    tool_names = [tool.name for tool in captured["tools"]]
    assert "qwen_omni_understand" in tool_names
