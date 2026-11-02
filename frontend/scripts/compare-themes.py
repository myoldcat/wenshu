"""Assemble local browser captures for desktop material-theme review."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

design = Path(__file__).resolve().parents[1] / "design"
font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 32)
small = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 19)

def sheet(items, name, crop=None):
    images = [Image.open(design / filename).convert("RGB").resize((1440, 1024)) for filename, _, _ in items]
    if crop:
        images = [image.crop(crop) for image in images]
    width, height = images[0].size
    canvas = Image.new("RGB", (width * len(images), height + 96), "#f5f6f4")
    draw = ImageDraw.Draw(canvas)
    for index, (image, (_, title, subtitle)) in enumerate(zip(images, items)):
        x = width * index
        canvas.paste(image, (x, 96))
        draw.text((x + 26, 12), title, fill="#263f35", font=font)
        draw.text((x + 26, 56), subtitle, fill="#64736b", font=small)
    canvas.save(design / name)

themes = [
    ("jade-final.png", "01  青玉", "温润文艺 · 白玉肌理 · 青玉光泽"),
    ("glass-final.png", "02  流动玻璃", "通透科技 · 悬浮层次 · 流动环境光"),
]
sheet(themes, "theme-comparison.png")
baseline = ("library-final.png", "原版文艺风", "保持资料、选材与写作的原有动线")
sheet([baseline] + themes, "theme-reference-comparison.png")
sheet([baseline] + themes, "theme-detail-comparison.png", (260, 75, 1090, 585))
print("Created three desktop comparison sheets.")

if (design / "civic-final.png").exists():
    civic = ("civic-final.png", "03  丹朱鎏金", "庄重政务 · 红色缎带 · 金箔细节")
    sheet(themes + [civic], "themes-three.png")
    sheet([baseline, civic], "civic-reference-comparison.png")
    sheet([baseline, civic], "civic-detail-comparison.png", (260, 75, 1090, 585))
    print("Created civic-theme comparison sheets.")
