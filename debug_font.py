from PIL import Image, ImageDraw, ImageFont
img = Image.new("RGB", (800, 480))
draw = ImageDraw.Draw(img)
font_path = "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"
for size in [12, 15, 28]:
    f = ImageFont.truetype(font_path, size)
    bb = draw.textbbox((0, 0), "test", font=f)
    print(f"size={size} bbox={bb} height={bb[3]-bb[1]}")
