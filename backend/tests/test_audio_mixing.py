import sys
import types
import unittest

from pathlib import Path

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

from app.tools.audio_mixing import (
    AudioSegment,
    BASE_DIR,
    BGM_DIR,
    configure_pydub_ffmpeg,
    load_audio_segment,
)


class AudioMixingTests(unittest.TestCase):
    def test_configures_pydub_to_use_imageio_ffmpeg(self):
        ffmpeg_path = configure_pydub_ffmpeg()

        self.assertTrue(ffmpeg_path)
        self.assertEqual(AudioSegment.converter, ffmpeg_path)
        self.assertEqual(AudioSegment.ffprobe, ffmpeg_path)
        self.assertTrue(Path(ffmpeg_path).exists())
        self.assertTrue((BASE_DIR / ".bin" / "ffmpeg").exists())
        self.assertFalse((BASE_DIR / ".bin" / "ffprobe").exists())

    def test_load_audio_segment_reads_mp3_without_ffprobe_binary(self):
        bgm_files = list(BGM_DIR.glob("*.mp3"))
        if not bgm_files:
            self.skipTest("No BGM mp3 files available")

        audio = load_audio_segment(bgm_files[0])

        self.assertGreater(len(audio), 0)


if __name__ == "__main__":
    unittest.main()
