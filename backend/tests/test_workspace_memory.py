import sys
from types import SimpleNamespace
from unittest.mock import patch

if "langchain_core" in sys.modules and not hasattr(sys.modules["langchain_core"], "__path__"):
    sys.modules.pop("langchain_core", None)
    sys.modules.pop("langchain_core.tools", None)

from app.services import agent_service, prompt


def test_full_prompt_includes_workspace_context():
    full_prompt = prompt.get_full_prompt(
        tools_list_text="- example_tool: does work",
        workspace_context="<工作空间>\n## USER.md（用户画像）\n喜欢写实风格\n</工作空间>",
    )

    assert "喜欢写实风格" in full_prompt
    assert "<工作空间>" in full_prompt


def test_agent_registers_write_memory_tool():
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
        "write_memory": SimpleNamespace(name="write_memory", description="write workspace memory"),
    }

    patches = [patch.object(agent_service, attr, tool) for attr, tool in fake_tools.items()]
    with patch.object(agent_service, "create_llm", return_value=object()), patch.object(
        agent_service.workspace_service,
        "get_workspace_context",
        return_value="<工作空间>\n## USER.md（用户画像）\n喜欢写实风格\n</工作空间>",
    ), patch.object(agent_service, "create_react_agent", side_effect=fake_create_react_agent):
        for p in patches:
            p.start()
        try:
            agent_service.create_agent()
        finally:
            for p in reversed(patches):
                p.stop()

    tool_names = [tool.name for tool in captured["tools"]]
    assert "write_memory" in tool_names

    prompt_text = captured["prompt"]
    assert "<工作空间>" in prompt_text or "工作空间" in prompt_text
