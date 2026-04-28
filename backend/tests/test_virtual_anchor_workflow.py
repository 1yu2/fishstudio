import unittest
from pathlib import Path
import sys
import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if "langchain_core.tools" not in sys.modules:
    langchain_core = types.ModuleType("langchain_core")
    tools_module = types.ModuleType("langchain_core.tools")

    def tool_stub(*args, **kwargs):
        if args and callable(args[0]):
            return args[0]
        return lambda wrapped: wrapped

    tools_module.tool = tool_stub
    sys.modules["langchain_core"] = langchain_core
    sys.modules["langchain_core.tools"] = tools_module

from app.tools.virtual_anchor_generation import (
    build_drop_first_frame_command,
    build_comfyui_extra_data,
    configure_infinitetalk_workflow,
    comfyui_video_download_candidates,
    extract_comfyui_execution_error,
    resolve_comfyui_workflow_path,
    safe_comfyui_upload_filename,
    normalize_comfyui_workflow,
)


class VirtualAnchorWorkflowTests(unittest.TestCase):
    def test_normalizes_ui_workflow_to_api_prompt(self):
        ui_workflow = {
            "nodes": [
                {
                    "id": 284,
                    "type": "LoadImage",
                    "inputs": [
                        {"name": "image"},
                    ],
                    "widgets_values": ["woman.png", "image"],
                },
                {
                    "id": 125,
                    "type": "LoadAudio",
                    "inputs": [
                        {"name": "audio"},
                        {"name": "audioUI"},
                        {"name": "upload"},
                    ],
                    "widgets_values": ["hush.mp3", None, None],
                },
            ],
            "links": [],
        }

        prompt = normalize_comfyui_workflow(ui_workflow)

        self.assertEqual(prompt["284"]["class_type"], "LoadImage")
        self.assertEqual(prompt["284"]["inputs"]["image"], "woman.png")
        self.assertEqual(prompt["125"]["class_type"], "LoadAudio")
        self.assertEqual(prompt["125"]["inputs"]["audio"], "hush.mp3")

    def test_normalizes_known_infinitetalk_widget_nodes(self):
        ui_workflow = {
            "nodes": [
                {
                    "id": 241,
                    "type": "WanVideoTextEncodeCached",
                    "inputs": [{"name": "extender_args", "link": None}],
                    "widgets_values": [
                        "umt5-xxl-enc-bf16.safetensors",
                        "bf16",
                        "a woman is singing",
                        "low quality",
                        "disabled",
                        False,
                        "gpu",
                    ],
                },
                {
                    "id": 194,
                    "type": "MultiTalkWav2VecEmbeds",
                    "inputs": [
                        {"name": "wav2vec_model", "link": 334},
                        {"name": "audio_1", "link": 544},
                    ],
                    "widgets_values": [True, 400, 25, 1, 1, "para"],
                },
            ],
            "links": [
                [334, 137, 0, 194, 0, "WAV2VECMODEL"],
                [544, 302, 0, 194, 1, "AUDIO"],
            ],
        }

        prompt = normalize_comfyui_workflow(ui_workflow)

        self.assertEqual(prompt["241"]["inputs"]["model_name"], "umt5-xxl-enc-bf16.safetensors")
        self.assertEqual(prompt["241"]["inputs"]["positive_prompt"], "a woman is singing")
        self.assertEqual(prompt["241"]["inputs"]["negative_prompt"], "low quality")
        self.assertEqual(prompt["194"]["inputs"]["wav2vec_model"], ["137", 0])
        self.assertEqual(prompt["194"]["inputs"]["audio_1"], ["302", 0])
        self.assertEqual(prompt["194"]["inputs"]["num_frames"], 400)
        self.assertEqual(prompt["194"]["inputs"]["fps"], 25)

    def test_normalizes_model_paths_to_forward_slashes(self):
        ui_workflow = {
            "nodes": [
                {
                    "id": 122,
                    "type": "WanVideoModelLoader",
                    "inputs": [],
                    "widgets_values": [
                        "WanVideo\\wan2.1-i2v-14b-480p-Q8_0.gguf",
                        "fp16_fast",
                        "disabled",
                        "offload_device",
                        "sageattn",
                    ],
                },
            ],
            "links": [],
        }

        prompt = normalize_comfyui_workflow(ui_workflow)

        self.assertEqual(
            prompt["122"]["inputs"]["model"],
            "WanVideo/wan2.1-i2v-14b-480p-Q8_0.gguf",
        )

    def test_normalizes_ui_set_get_nodes_by_rewiring_links(self):
        ui_workflow = {
            "nodes": [
                {
                    "id": 129,
                    "type": "WanVideoVAELoader",
                    "inputs": [],
                    "outputs": [{"name": "vae", "type": "WANVAE", "links": [1]}],
                    "widgets_values": ["wanvae.safetensors", "bf16"],
                },
                {
                    "id": 240,
                    "type": "SetNode",
                    "inputs": [{"name": "WANVAE", "type": "WANVAE", "link": 1}],
                    "outputs": [{"name": "*", "type": "*", "links": None}],
                    "widgets_values": ["VAE"],
                },
                {
                    "id": 244,
                    "type": "GetNode",
                    "inputs": [],
                    "outputs": [{"name": "WANVAE", "type": "WANVAE", "links": [2]}],
                    "widgets_values": ["VAE"],
                },
                {
                    "id": 192,
                    "type": "WanVideoImageToVideoMultiTalk",
                    "inputs": [{"name": "vae", "type": "WANVAE", "link": 2}],
                    "widgets_values": [],
                },
            ],
            "links": [
                [1, 129, 0, 240, 0, "*"],
                [2, 244, 0, 192, 0, "WANVAE"],
            ],
        }

        prompt = normalize_comfyui_workflow(ui_workflow)

        self.assertNotIn("240", prompt)
        self.assertNotIn("244", prompt)
        self.assertEqual(prompt["192"]["inputs"]["vae"], ["129", 0])

    def test_configures_infinitetalk_node_inputs(self):
        workflow = {
            "284": {"class_type": "LoadImage", "inputs": {"image": "old.png"}},
            "125": {"class_type": "LoadAudio", "inputs": {"audio": "old.wav"}},
            "241": {
                "class_type": "WanVideoTextEncodeCached",
                "inputs": {"positive_prompt": "old", "negative_prompt": "old"},
            },
            "128": {"class_type": "WanVideoSampler", "inputs": {"seed": 1}},
            "194": {
                "class_type": "MultiTalkWav2VecEmbeds",
                "inputs": {"num_frames": 100, "fps": 25},
            },
            "131": {
                "class_type": "VHS_VideoCombine",
                "inputs": {"frame_rate": 25, "save_output": False},
            },
        }

        configure_infinitetalk_workflow(
            workflow,
            uploaded_image="portrait.png",
            uploaded_audio="voice.wav",
            prompt_text="hello",
            negative_prompt="blur",
            seed=42,
            num_frames=400,
            fps=24,
        )

        self.assertEqual(workflow["284"]["inputs"]["image"], "portrait.png")
        self.assertEqual(workflow["125"]["inputs"]["audio"], "voice.wav")
        self.assertEqual(workflow["241"]["inputs"]["positive_prompt"], "hello")
        self.assertEqual(workflow["241"]["inputs"]["negative_prompt"], "blur")
        self.assertEqual(workflow["128"]["inputs"]["seed"], 42)
        self.assertEqual(workflow["194"]["inputs"]["num_frames"], 400)
        self.assertEqual(workflow["194"]["inputs"]["fps"], 24)
        self.assertEqual(workflow["131"]["inputs"]["frame_rate"], 24)
        self.assertTrue(workflow["131"]["inputs"]["save_output"])

    def test_safe_comfyui_upload_filename_removes_non_ascii(self):
        filename = safe_comfyui_upload_filename(
            Path("voice_design_20260427_190112_f444c1f2.wav"),
            prefix="audio",
        )

        self.assertTrue(filename.startswith("audio_voice_design_20260427_190112_f444c1f2"))
        self.assertTrue(filename.endswith(".wav"))
        self.assertTrue(filename.isascii())

    def test_build_drop_first_frame_command_uses_one_frame_offset(self):
        command = build_drop_first_frame_command(
            ffmpeg_path="/usr/bin/ffmpeg",
            source_path=Path("/tmp/input.mp4"),
            output_path=Path("/tmp/output.mp4"),
            fps=25,
        )

        self.assertEqual(command[0], "/usr/bin/ffmpeg")
        self.assertIn("-ss", command)
        self.assertEqual(command[command.index("-ss") + 1], "0.040000")
        self.assertIn("-avoid_negative_ts", command)
        self.assertEqual(command[-1], "/tmp/output.mp4")

    def test_build_comfyui_extra_data_enables_cloudberry_mode(self):
        raw_workflow = {"nodes": [], "extra": {"existing": True}}

        extra_data = build_comfyui_extra_data(raw_workflow, cloudberry_mode=True)

        workflow = extra_data["extra_pnginfo"]["workflow"]
        self.assertTrue(workflow["extra"]["existing"])
        self.assertTrue(workflow["extra"]["cloudberry_mode"])

    def test_extracts_cloudberry_resource_not_found_error_from_history(self):
        history_item = {
            "status": {
                "status_str": "error",
                "messages": [
                    [
                        "execution_error",
                        {
                            "node_id": 122,
                            "node_type": "WanVideoModelLoader",
                            "exception_message": (
                                "Resource not found, the requested resource does not exist at "
                                "the specified location, URL: "
                                "https://ms.sc4.ai:10443/models/siliconflow/awesome-comfyui-models/"
                                "resolve/master/diffusion_models/WanVideo/"
                                "wan2.1-i2v-14b-480p-Q8_0.gguf"
                            ),
                        },
                    ]
                ],
            }
        }

        error = extract_comfyui_execution_error(history_item)

        self.assertIsNotNone(error)
        self.assertEqual(error["node_id"], 122)
        self.assertEqual(error["class_type"], "WanVideoModelLoader")
        self.assertIn("wan2.1-i2v-14b-480p-Q8_0.gguf", error["message"])
        self.assertEqual(error["reason"], "cloud_model_resource_not_found")

    def test_extracts_cloudberry_missing_local_output_error_from_history(self):
        history_item = {
            "status": {
                "messages": [
                    [
                        "execution_error",
                        {
                            "node_id": "cloudberry_wrapper",
                            "node_type": "CloudBerrySubgraphNode",
                            "exception_message": (
                                "[Errno 2] No such file or directory: "
                                "'/Users/fishyuu/idea_project/ComfyUI-master/output/outputs/"
                                "WanVideo2_1_InfiniteTalk_00001-audio.mp4'"
                            ),
                        },
                    ]
                ],
            }
        }

        error = extract_comfyui_execution_error(history_item)

        self.assertIsNotNone(error)
        self.assertEqual(error["reason"], "cloudberry_local_output_missing")

    def test_invalid_tool_workflow_path_falls_back_to_default_workflow(self):
        default_path = Path(__file__).resolve()

        workflow_path, used_fallback = resolve_comfyui_workflow_path(
            "virtual_anchor_basic.json",
            str(default_path),
        )

        self.assertEqual(workflow_path, default_path)
        self.assertTrue(used_fallback)

    def test_video_download_candidates_try_cloudberry_outputs_subfolder(self):
        candidates = comfyui_video_download_candidates("video.mp4", "")

        self.assertEqual(
            candidates,
            [
                ("video.mp4", "", "output"),
                ("video.mp4", "outputs", "output"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
