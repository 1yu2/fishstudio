"""生成画布成片用的“AI 生成”角标 PNG（backend/app/assets/aigc_badge.png）。

文字用 Noto Sans SC Bold 渲染（SIL Open Font License 1.1，可自由使用其渲染出的图片）。
字体文件不进仓库，生成时通过参数传入；渲染时 ffmpeg 只做 scale + overlay，运行时不依赖中文字体。

用法（在 backend/ 下）：
    python scripts/make_aigc_badge.py /path/to/NotoSansSC-Bold.ttf

字体可从 https://fonts.google.com/noto/specimen/Noto+Sans+SC 获取。
成品宽高比固定 2.6:1（timeline_renderer 按此比例缩放），替换时保持该比例。
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "app" / "assets" / "aigc_badge.png"

WIDTH, HEIGHT = 260, 100
SCALE = 4  # 超采样后缩小抗锯齿
BG = (0, 0, 0, 140)  # 半透明深色底，alpha 约 55%
FG = (255, 255, 255, 235)  # 白字，alpha 约 92%
TEXT = "AI 生成"


def main(font_path: str) -> None:
    w, h = WIDTH * SCALE, HEIGHT * SCALE
    image = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=18 * SCALE, fill=BG)
    font = ImageFont.truetype(font_path, 56 * SCALE)
    left, top, right, bottom = draw.textbbox((0, 0), TEXT, font=font)
    x = (w - (right - left)) / 2 - left
    y = (h - (bottom - top)) / 2 - top
    draw.text((x, y), TEXT, font=font, fill=FG)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    image.resize((WIDTH, HEIGHT), Image.LANCZOS).save(OUT, optimize=True)
    print(f"已生成 {OUT}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("用法：python scripts/make_aigc_badge.py /path/to/NotoSansSC-Bold.ttf")
    main(sys.argv[1])
