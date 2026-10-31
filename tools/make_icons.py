"""生成 PWA 图标位图。

安卓安装 Web App 时要求 192/512 的 PNG（WebP/PNG 才会被系统接受），
iOS 的 apple-touch-icon 也只认 PNG。SVG 只作为补充。
"""
from PIL import Image, ImageDraw

C1 = (91, 140, 255)
C2 = (47, 79, 216)
R = 512


def gradient(size: int) -> Image.Image:
    img = Image.new("RGB", (size, size))
    d = ImageDraw.Draw(img)
    for y in range(size):
        t = y / max(1, size - 1)
        d.line(
            [(0, y), (size, y)],
            fill=tuple(int(C1[i] + (C2[i] - C1[i]) * t) for i in range(3)),
        )
    return img


def draw_mark(canvas: Image.Image, scale: float, offset: int = 0) -> None:
    """在 canvas 上画两页书 + 六条文字线，scale=1 时正好铺满 512。"""
    s = scale
    o = offset + R * (1 - s) / 2

    def sx(v):
        return int(o + v * s)

    d = ImageDraw.Draw(canvas, "RGBA")
    r = max(2, int(18 * s))
    # 左页（实心）右页（半透明）
    d.rounded_rectangle([sx(96), sx(118), sx(246), sx(394)], radius=r, fill=(255, 255, 255, 235))
    d.rounded_rectangle([sx(266), sx(118), sx(416), sx(394)], radius=r, fill=(255, 255, 255, 158))
    # 文字线
    lw = max(2, int(13 * s))
    blue = (47, 79, 216, 215)
    for i, (x1, x2) in enumerate([(128, 214), (128, 214), (128, 186)]):
        y = 170 + i * 42
        d.line([(sx(x1), sx(y)), (sx(x2), sx(y))], fill=blue, width=lw)
    for i, (x1, x2) in enumerate([(298, 384), (298, 384), (298, 356)]):
        y = 170 + i * 42
        d.line([(sx(x1), sx(y)), (sx(x2), sx(y))], fill=blue, width=lw)


def rounded(img: Image.Image, radius_ratio: float = 0.22) -> Image.Image:
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, img.size[0] - 1, img.size[1] - 1], radius=int(img.size[0] * radius_ratio), fill=255
    )
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def make(size: int, maskable: bool = False) -> Image.Image:
    canvas = gradient(size).convert("RGBA")
    if maskable:
        # maskable 图标会被系统裁切，内容必须落在中间 80% 的安全区内
        draw_mark(canvas, scale=0.62)
        return canvas
    draw_mark(canvas, scale=1.0)
    return rounded(canvas)


if __name__ == "__main__":
    targets = [
        ("assets/icon-192.png", 192, False),
        ("assets/icon-512.png", 512, False),
        ("assets/icon-maskable-512.png", 512, True),
        ("assets/apple-touch-icon.png", 180, False),
    ]
    for path, size, maskable in targets:
        img = make(size, maskable)
        img.save(path)
        print(f"✓ {path}  {img.size[0]}x{img.size[1]}")
