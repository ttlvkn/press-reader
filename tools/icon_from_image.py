"""把一张图片加工成 PWA 全套图标。

流程：源图 → 2 倍超采样降噪 → 回落到 1024 母版并锐化 → 生成各尺寸。
不做 AI 重绘，避免改掉原画的神态和细节。
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

SRC = sys.argv[1] if len(sys.argv) > 1 else None
MASTER = 1024


def enhance(src: str) -> Image.Image:
    im = Image.open(src).convert("RGB")
    w, h = im.size
    # 居中裁成正方形（源图不是方图时）
    if w != h:
        s = min(w, h)
        im = im.crop(((w - s) // 2, (h - s) // 2, (w + s) // 2, (h + s) // 2))

    # 先放大 2 倍再缩回：相当于一次平滑，能明显压掉 JPEG 块状噪点
    big = im.resize((MASTER * 2, MASTER * 2), Image.LANCZOS)
    big = big.filter(ImageFilter.MedianFilter(3))          # 去压缩噪点，保留描边
    small = big.resize((MASTER, MASTER), Image.LANCZOS)

    # 锐化：卡通线稿靠这一步找回清晰度
    return small.filter(ImageFilter.UnsharpMask(radius=2.2, percent=130, threshold=3))


def rounded(img: Image.Image, ratio: float = 0.22) -> Image.Image:
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, img.size[0] - 1, img.size[1] - 1], radius=int(img.size[0] * ratio), fill=255
    )
    out = img.convert("RGBA")
    out.putalpha(mask)
    return out


def maskable(img: Image.Image, size: int, content_ratio: float = 0.72) -> Image.Image:
    """安卓会按圆形/胶囊裁剪，主体必须缩进安全区。

    四周不用模糊底图（会露出"画中画"的边界），而是把最外圈像素向外延展，
    木纹/纯色背景这样铺最自然。
    """
    inner = int(size * content_ratio)
    off = (size - inner) // 2
    canvas = Image.new("RGBA", (size, size))
    fg = img.resize((inner, inner), Image.LANCZOS).convert("RGBA")
    canvas.paste(fg, (off, off))

    # 左右：用最外一列像素横向拉伸出边框
    left = fg.crop((0, 0, 1, inner)).resize((off, inner))
    right = fg.crop((inner - 1, 0, inner, inner)).resize((size - off - inner, inner))
    canvas.paste(left, (0, off))
    canvas.paste(right, (off + inner, off))

    # 上下：用最外一行像素纵向拉伸，覆盖到四角
    top = canvas.crop((0, off, size, off + 1)).resize((size, off))
    bottom = canvas.crop((0, off + inner - 1, size, off + inner)).resize((size, size - off - inner))
    canvas.paste(top, (0, 0))
    canvas.paste(bottom, (0, off + inner))
    return canvas


def main() -> None:
    if not SRC:
        print("用法: python tools/icon_from_image.py <图片路径>")
        return
    master = enhance(SRC)
    master.save("assets/icon-master.png")
    print("✓ assets/icon-master.png  1024x1024")

    jobs = [
        ("assets/icon-512.png", 512, "rounded"),
        ("assets/icon-192.png", 192, "rounded"),
        ("assets/apple-touch-icon.png", 180, "plain"),
        ("assets/icon-maskable-512.png", 512, "maskable"),
    ]
    for path, size, mode in jobs:
        if mode == "maskable":
            out = maskable(master, size)
        else:
            im = master.resize((size, size), Image.LANCZOS)
            out = rounded(im) if mode == "rounded" else im.convert("RGBA")
        out.save(path)
        print(f"✓ {path}  {size}x{size}  ({mode})")


if __name__ == "__main__":
    main()
