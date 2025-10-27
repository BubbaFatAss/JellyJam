import os
from PIL import Image, ImageDraw

# Constants
PIXEL_SIZE = 10  # Scale each pixel for visibility
CANVAS_SIZE = 16
colors = {
    '.': (0, 0, 0),        # Black background
    '#': (255, 105, 180),  # Hot pink blob
    'o': (0, 0, 0),        # Eyes
    '-': (0, 0, 0),        # Mouth
}

# Base blob shape (round, 8 rows tall)
blob_shape = [
    "......####......",
    ".....######.....",
    "....########....",
    "....##o##o##....",
    "....########....",
    "....###--###....",
    ".....######.....",
    "......####......",
]

# Final frame includes large "J J" text
final_frame = [
    "......####......",
    ".....######.....",
    "....########....",
    "....##o##o##....",
    "....########....",
    "....###--###....",
    ".....######.....",
    "......####......",
    "................",
    "................",
    "......#.....#...",
    "......#.....#...",
    "......#.....#...",
    "...#..#..#..#...",
    "....###...###...",
    "................",
]

# Function to shift blob vertically
def shift_blob(blob_lines, offset):
    empty = ["." * CANVAS_SIZE] * offset
    padded = empty + blob_lines
    padded += ["." * CANVAS_SIZE] * (CANVAS_SIZE - len(padded))
    return padded

# Define all 9 frames
frames = [
    shift_blob(blob_shape, 6),  # Frame 1 – Idle
    shift_blob(blob_shape, 5),  # Frame 1 – Idle
    shift_blob(blob_shape, 4),  # Frame 1 – Idle
    shift_blob(blob_shape, 3),  # Frame 1 – Idle
    shift_blob(blob_shape, 2),  # Frame 2 – Rise
    shift_blob(blob_shape, 1),  # Frame 3 – Peak
    shift_blob(blob_shape, 2),  # Frame 4 – Fall
    shift_blob(blob_shape, 3),  # Frame 5 – Idle
    shift_blob(blob_shape, 4),  # Frame 5 – Idle
    shift_blob(blob_shape, 5),  # Frame 5 – Idle
    shift_blob(blob_shape, 6),  # Frame 5 – Idle
    shift_blob(blob_shape, 5),  # Frame 6 – Rise again
    shift_blob(blob_shape, 4),  # Frame 7 – Peak again
    shift_blob(blob_shape, 3),  # Frame 8 – Fall again
    shift_blob(blob_shape, 2),  # Frame 8 – Fall again
    shift_blob(blob_shape, 1),  # Frame 8 – Fall again
    final_frame,                # Frame 9 – Idle + “J J”
]

# Render a frame to image
def render_frame(grid):
    img = Image.new("RGB", (CANVAS_SIZE * PIXEL_SIZE, CANVAS_SIZE * PIXEL_SIZE), colors['.'])
    draw = ImageDraw.Draw(img)
    for y, row in enumerate(grid):
        for x, char in enumerate(row):
            color = colors.get(char, colors['.'])
            draw.rectangle(
                [x * PIXEL_SIZE, y * PIXEL_SIZE, (x+1) * PIXEL_SIZE - 1, (y+1) * PIXEL_SIZE - 1],
                fill=color
            )
    return img

# Create images
images = [render_frame(frame) for frame in frames]

# Save as animated GIF
images[0].save(
    os.path.join(os.path.dirname(__file__), "..", "data", "animations", "startup.gif"),
    save_all=True,
    append_images=images[1:],
    duration=100,
    loop=0
)

print("Animated GIF saved as startup.gif")
