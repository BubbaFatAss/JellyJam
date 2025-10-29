from PIL import Image, ImageDraw, ImageFont
import math

# === SETTINGS ===
FRAMES = 20
FPS = 5
WIDTH, HEIGHT = 64, 64
BOUNCE_HEIGHT = 4
TEXT_Y = 50   # Moved slightly up to fit larger text
OUTPUT = "jellyjam_64x64_bounce_box_bigtext.gif"

# === LOAD BASE IMAGE ===
base = Image.open("jelly.png").convert("RGBA")
base = base.resize((36, 36), Image.Resampling.BOX)

frames = []

# === FONT (larger, clearer) ===
try:
    font = ImageFont.truetype("DejaVuSans-Bold.ttf", 14)
except:
    font = ImageFont.load_default()

# === GENERATE FRAMES ===
for i in range(FRAMES):
    frame = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(frame)

    # === Bounce motion ===
    phase = (i / FRAMES) * 2 * math.pi
    bounce_offset = int(math.sin(phase) * -BOUNCE_HEIGHT)

    # === Squash & stretch ===
    scale_y = 1.0 - 0.1 * math.sin(phase)
    scale_x = 1.0 + 0.1 * math.sin(phase)

    jelly_scaled = base.resize(
        (max(1, int(base.width * scale_x)), max(1, int(base.height * scale_y))),
        Image.Resampling.BOX
    )

    jelly_x = (WIDTH - jelly_scaled.width) // 2
    jelly_y = 8 + bounce_offset + (base.height - jelly_scaled.height)
    frame.alpha_composite(jelly_scaled, (jelly_x, jelly_y))

    # === Text animation ===
    jelly_text = "Jelly"
    jam_text = "Jam"

    jelly_w = draw.textlength(jelly_text, font=font)
    jam_w = draw.textlength(jam_text, font=font)
    total_w = jelly_w + jam_w

    jelly_target_x = (WIDTH - total_w) // 2
    jam_target_x = jelly_target_x + jelly_w

    t = min(1.0, i / (FRAMES / 2))
    ease_in = 1 - math.cos(t * math.pi / 2)

    jelly_start_x = -jelly_w
    jam_start_x = WIDTH

    jelly_x_text = int(round(jelly_start_x * (1 - ease_in) + jelly_target_x * ease_in))
    jam_x_text = int(round(jam_start_x * (1 - ease_in) + jam_target_x * ease_in))

    # === Draw text with soft outline for clarity ===
    def draw_text_with_outline(draw, pos, text, font, fill, outline=(255, 255, 255, 160)):
        x, y = pos
        for ox, oy in [(-1,0), (1,0), (0,-1), (0,1)]:
            draw.text((x+ox, y+oy), text, font=font, fill=outline)
        draw.text(pos, text, font=font, fill=fill)

    draw_text_with_outline(draw, (jelly_x_text, TEXT_Y), jelly_text, font, (255, 120, 160, 255))
    draw_text_with_outline(draw, (jam_x_text, TEXT_Y), jam_text, font, (200, 160, 255, 255))

    frames.append(frame)

# === SAVE GIF ===
frames[0].save(
    OUTPUT,
    save_all=True,
    append_images=frames[1:],
    duration=int(1000 / FPS),
    loop=0,
    disposal=2,
    transparency=0
)

print(f"✅ JellyJam startup animation (larger, outlined text) saved as {OUTPUT}")
