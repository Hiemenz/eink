from PIL import Image, ImageDraw

from display import display_color_image

# Define the 7 colors (RGB)
colors = [
    (0, 0, 0),       # Black
    (255, 255, 255), # White
    (255, 0, 0),     # Red
    (0, 255, 0),     # Green
    (0, 0, 255),     # Blue
    (255, 255, 0),   # Yellow
    (255, 128, 0)    # Orange
]

# Set final image dimensions
width = 800
height = 480

# Compute stripe height (using integer division)
stripe_height = height // len(colors)  # This gives 68

# Create a new RGB image
img = Image.new("RGB", (width, height))
draw = ImageDraw.Draw(img)

# Draw each stripe
for i, color in enumerate(colors):
    top = i * stripe_height
    # For the last stripe, fill to the full image height to cover any remainder.
    bottom = height if i == len(colors) - 1 else (i + 1) * stripe_height
    draw.rectangle([0, top, width, bottom], fill=color)

# Save and show the image
img.save("test_striped_image.bmp")

display_color_image("test_striped_image.bmp")
