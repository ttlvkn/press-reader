"""把一张图片加工成 PWA 全套图标。

两种装框方式：
  默认        居中裁成正方形（画面主体本来就居中时用）
  --fit       整张图缩进正方形，上下用边缘像素延展补齐
              （宽幅插画用这个，能看到全部内容，不会被方框切掉腿脚）
  --top=0.14  --fit 时上方留白的比例

整条链路先把画面放大 2 倍再缩回：相当于一次平滑，能压掉 JPEG 块状噪点，
再用 UnsharpMask 把卡通描边找回来。全程不做 AI 重绘，避免改掉原画笔触。
"""
import sys

from PIL import Image, ImageDraw, ImageFilter

MASTER = 1024

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
FLAGS = {a for a in sys.argv[1:] if a.startswith("--")}
SRC = ARGS[0] if ARGS else None
TOP = next((float(f.split("=")[1]) for f in FLAGS if f.startswith("--top=")), 0.14)


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


def enhance_fit(src: str, size: int = MASTER, top_ratio: float = 0.14) -> Image.Image:
    """整张图缩进正方形：横向铺满，纵向按 top_ratio 定位，
    上下空白用最外一行像素拉伸补齐，纯色/横纹背景看不出接缝。"""
    im = Image.open(src).convert("RGB")
    w, h = im.size
    nh = max(1, round(size * h / w))
    band = _clean(im, size, nh)

    y0 = int(size * top_ratio)
    if y0 + nh > size:                                  # 图太扁时退化为居中
        y0 = max(0, (size - nh) // 2)

    canvas = Image.new("RGB", (size, size), band.getpixel((0, 0)))
    canvas.paste(band, (0, y0))
    if y0:
        canvas.paste(band.crop((0, 0, size, 1)).resize((size, y0)), (0, 0))
    gap = size - y0 - nh
    if gap > 0:
        canvas.paste(band.crop((0, nh - 1, size, nh)).resize((size, gap)), (0, y0 + nh))
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

    if "--fit" in FLAGS:
        master = enhance_fit(SRC, MASTER, TOP)
        print(f"模式：整图适配（上方留白 {TOP:.0%}）")
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
