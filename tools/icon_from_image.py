"""把一张图片加工成 PWA 全套图标。

两种装框方式：
  默认        居中裁成正方形（画面主体本来就居中时用）
  --pad       宽幅图用这个：左右各裁掉一点，底部贴齐，上方纯白补底
  --crop=0.83 --pad 时保留的画幅比例（1.0 = 一点不裁，留白更多）

整条链路先把画面放大 2 倍再缩回：相当于一次平滑，能压掉 JPEG 块状噪点，
再用 UnsharpMask 把卡通描边找回来。全程不做 AI 重绘，避免改掉原画笔触。
"""
import sys

from PIL import Image, ImageDraw, ImageFilter

MASTER = 1024

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
FLAGS = {a for a in sys.argv[1:] if a.startswith("--")}
SRC = ARGS[0] if ARGS else None
CROP = next((float(f.split("=")[1]) for f in FLAGS if f.startswith("--crop=")), 0.83)


def _clean(im: Image.Image, w: int, h: int) -> Image.Image:
    """超采样降噪 + 锐化，输出 w×h。"""
    big = im.resize((w * 2, h * 2), Image.LANCZOS)
    big = big.filter(ImageFilter.MedianFilter(3))      # 去压缩噪点，保留描边
    small = big.resize((w, h), Image.LANCZOS)
    return small.filter(ImageFilter.UnsharpMask(radius=2.2, percent=130, threshold=3))


def enhance_square(src: str, size: int = MASTER) -> Image.Image:
    im = Image.open(src).convert("RGB")
    w, h = im.size
    if w != h:                                          # 居中裁方
        s = min(w, h)
        im = im.crop(((w - s) // 2, (h - s) // 2, (w + s) // 2, (h + s) // 2))
    return _clean(im, size, size)


def enhance_pad(src: str, size: int = MASTER, crop_ratio: float = 0.83,
                bg: tuple[int, int, int] = (255, 255, 255)) -> Image.Image:
    """宽幅图装进正方形：左右各裁掉一点，画面横向铺满、底部贴齐，
    上方用背景色补白。

    关键是不做任何拉伸——上图的边缘像素延展会把图形拉成竖条，看起来就是"变形"。
    补白只用纯色，宁可留白也不能改画。
    """
    im = Image.open(src)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        flat = Image.new("RGBA", im.size, (*bg, 255))
        im = Image.alpha_composite(flat, im)
    im = im.convert("RGB")

    w, h = im.size
    cw = max(1, int(w * crop_ratio))
    x0 = (w - cw) // 2
    band = im.crop((x0, 0, x0 + cw, h))

    nh = round(size * h / cw)
    canvas = Image.new("RGB", (size, size), bg)
    if nh > size:                                   # 裁得太窄会超出，改为按高度贴、左右补白
        nw = round(size * cw / h)
        canvas.paste(_clean(band, nw, size), ((size - nw) // 2, 0))
    else:                                           # 底部贴齐，上方补背景色
        canvas.paste(_clean(band, size, nh), (0, size - nh))
    return canvas


def rounded(img: Image.Image, ratio: float = 0.22) -> Image.Image:
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, img.size[0] - 1, img.size[1] - 1], radius=int(img.size[0] * ratio), fill=255
    )
    out = img.convert("RGBA")
    out.putalpha(mask)
    return out


def main() -> None:
    if not SRC:
        print(__doc__)
        return

    if "--pad" in FLAGS:
        master = enhance_pad(SRC, MASTER, CROP)
        print(f"模式：裁边补白（左右各裁 {1 - CROP:.0%}，纯白补底，不拉伸）")
    else:
        master = enhance_square(SRC, MASTER)
        print("模式：居中裁方")

    master.save("assets/icon-master.png")
    print(f"✓ assets/icon-master.png  {master.size[0]}x{master.size[1]}")

    jobs = [
        ("assets/icon-512.png", 512, "rounded"),
        ("assets/icon-192.png", 192, "rounded"),
        ("assets/apple-touch-icon.png", 180, "plain"),
        ("assets/icon-maskable-512.png", 512, "plain"),   # 母版已自带安全边距
    ]
    for path, size, mode in jobs:
        im = master.resize((size, size), Image.LANCZOS)
        out = rounded(im) if mode == "rounded" else im.convert("RGBA")
        out.save(path)
        print(f"✓ {path}  {size}x{size}")


if __name__ == "__main__":
    main()
