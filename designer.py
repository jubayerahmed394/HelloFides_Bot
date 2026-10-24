"""
Photocard image generation for HelloFides Bot, using Pillow only (no paid
design API). Three layout variations per mode, auto-fitting text so it
never overflows or clips, minimum readable size ~28px.
"""
import os

from PIL import Image, ImageDraw, ImageFont

FONTS = {
    "extrabold": "Poppins-ExtraBold.ttf",
    "bold": "Poppins-Bold.ttf",
    "semibold": "Poppins-SemiBold.ttf",
    "medium": "Poppins-Medium.ttf",
    "regular": "Poppins-Regular.ttf",
}

PILLAR_TAG = {
    # Company/Facebook mode (unchanged)
    "strategy": "STRATEGY",
    "stats": "FACTS & STATS",
    "howto": "HOW-TO TIP",
    "lesson": "LESSON LEARNED",
    # Personal/LinkedIn mode post types (per the Personal Mode Update)
    "fact": "FACT",
    "mystery": "MYSTERY",
    "myth_reality": "MYTH VS REALITY",
    "mistake": "MISTAKE",
    "tip": "TIP",
    "checklist": "CHECKLIST",
    "hot_take": "HOT TAKE",
    "behind_scenes": "BEHIND THE SCENES",
    "personal_story": "PERSONAL STORY",
    "service_offer": "SERVICE",
    "question": "QUESTION",
    "concept_sample": "CONCEPT SAMPLE",
}

_FONT_CACHE = {}


def _hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


def get_font(fonts_dir, weight, size):
    key = (weight, size)
    if key not in _FONT_CACHE:
        path = os.path.join(fonts_dir, FONTS[weight])
        _FONT_CACHE[key] = ImageFont.truetype(path, size)
    return _FONT_CACHE[key]


def wrap_text(draw, text, font, max_width):
    words = text.split()
    lines, current = [], ""
    for word in words:
        trial = (current + " " + word).strip()
        width = draw.textlength(trial, font=font)
        if width <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def fit_text_block(draw, fonts_dir, weight, text, max_width, max_height,
                    start_size=66, min_size=28, line_spacing=1.3, step=2):
    size = start_size
    chosen = None
    while size >= min_size:
        font = get_font(fonts_dir, weight, size)
        lines = wrap_text(draw, text, font, max_width)
        ascent, descent = font.getmetrics()
        line_height = (ascent + descent) * line_spacing
        total_height = line_height * len(lines)
        max_line_width = max((draw.textlength(line, font=font) for line in lines), default=0)
        if total_height <= max_height and max_line_width <= max_width:
            chosen = (font, lines, line_height)
            break
        size -= step
    if chosen is None:
        font = get_font(fonts_dir, weight, min_size)
        lines = wrap_text(draw, text, font, max_width)
        ascent, descent = font.getmetrics()
        line_height = (ascent + descent) * line_spacing
        chosen = (font, lines, line_height)
    return chosen


def draw_centered_lines(draw, lines, font, line_height, center_x, top_y, fill, align="center", left_x=None):
    y = top_y
    for line in lines:
        width = draw.textlength(line, font=font)
        if align == "center":
            x = center_x - width / 2
        else:
            x = left_x
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height
    return y


def _load_logo(config):
    logo_path = os.path.join(os.path.dirname(__file__), config["brand"]["logo_path"])
    if not os.path.exists(logo_path):
        return None
    return Image.open(logo_path).convert("RGBA")


def _recolor(logo_img, rgb):
    alpha = logo_img.split()[-1]
    solid = Image.new("RGBA", logo_img.size, rgb + (255,))
    solid.putalpha(alpha)
    return solid


def _paste_logo_and_footer(img, draw, config, fonts_dir, mode, footer_color, logo_rgb, center_x, bottom_margin,
                            source_text=None):
    brand = config["brand"]
    footer_text = brand["personal_footer"] if mode == "personal" else brand["company_footer"]
    footer_font = get_font(fonts_dir, "medium", 26)
    footer_w = draw.textlength(footer_text, font=footer_font)

    source_font = get_font(fonts_dir, "medium", 20)
    source_h = 0
    source_w = 0
    if source_text:
        source_w = draw.textlength(source_text, font=source_font)
        s_ascent, s_descent = source_font.getmetrics()
        source_h = s_ascent + s_descent + 10

    logo = _load_logo(config)
    logo_h = 0
    if logo is not None:
        target_h = 60
        ratio = target_h / logo.height
        logo = logo.resize((max(1, int(logo.width * ratio)), target_h))
        logo = _recolor(logo, logo_rgb)
        logo_h = logo.height

    footer_y = img.height - bottom_margin
    source_y = footer_y - source_h if source_text else footer_y
    logo_y = source_y - logo_h - 16 if logo_h else source_y

    if logo is not None:
        img.paste(logo, (int(center_x - logo.width / 2), int(logo_y)), logo)

    if source_text:
        draw.text((center_x - source_w / 2, source_y), source_text, font=source_font, fill=footer_color)

    draw.text((center_x - footer_w / 2, footer_y), footer_text, font=footer_font, fill=footer_color)


def _draw_pill(draw, fonts_dir, text, x, y, fill, text_color):
    font = get_font(fonts_dir, "semibold", 26)
    pad_x, pad_y = 22, 12
    w = draw.textlength(text, font=font)
    ascent, descent = font.getmetrics()
    h = ascent + descent
    box = [x, y, x + w + pad_x * 2, y + h + pad_y * 2]
    draw.rounded_rectangle(box, radius=(h + pad_y * 2) / 2, fill=fill)
    draw.text((x + pad_x, y + pad_y), text, font=font, fill=text_color)
    return box


def _layout_bold_center(img, draw, config, entry, fonts_dir):
    W, H = img.size
    brand = config["brand"]
    navy, white, accent = _hex_to_rgb(brand["navy"]), _hex_to_rgb(brand["white"]), _hex_to_rgb(brand["accent"])
    margin = 90

    draw.rectangle([0, 0, W, H], fill=navy)
    draw.rectangle([margin, 80, W - margin, 86], fill=accent)

    _draw_pill(draw, fonts_dir, PILLAR_TAG[entry["pillar"]], margin, 120, accent, navy)

    text_top = int(H * 0.38)
    text_height = int(H * 0.40)
    font, lines, line_h = fit_text_block(
        draw, fonts_dir, "extrabold", entry["photocard_text"],
        max_width=W - margin * 2, max_height=text_height,
        start_size=72, min_size=config["image"]["min_font_size"],
    )
    block_height = line_h * len(lines)
    start_y = text_top + (text_height - block_height) / 2
    draw_centered_lines(draw, lines, font, line_h, W / 2, start_y, white)

    source_text = f"Source: {entry['source']}" if entry.get("source") else None
    _paste_logo_and_footer(img, draw, config, fonts_dir, entry["mode"],
                            footer_color=white, logo_rgb=white, center_x=W / 2, bottom_margin=90,
                            source_text=source_text)


def _layout_side_accent(img, draw, config, entry, fonts_dir):
    W, H = img.size
    brand = config["brand"]
    navy, white, accent = _hex_to_rgb(brand["navy"]), _hex_to_rgb(brand["white"]), _hex_to_rgb(brand["accent"])
    margin = 100
    bar_w = 26

    draw.rectangle([0, 0, W, H], fill=white)
    draw.rectangle([0, 0, bar_w, H], fill=accent)

    left_x = margin + bar_w
    _draw_pill(draw, fonts_dir, PILLAR_TAG[entry["pillar"]], left_x, 90, navy, white)

    content_width = W - left_x - margin
    text_top = int(H * 0.30)
    text_height = int(H * 0.45)
    font, lines, line_h = fit_text_block(
        draw, fonts_dir, "extrabold", entry["photocard_text"],
        max_width=content_width, max_height=text_height,
        start_size=64, min_size=config["image"]["min_font_size"],
    )
    y = text_top
    for line in lines:
        draw.text((left_x, y), line, font=font, fill=navy)
        y += line_h

    footer_text = brand["personal_footer"] if entry["mode"] == "personal" else brand["company_footer"]
    footer_font = get_font(fonts_dir, "medium", 26)
    footer_w = draw.textlength(footer_text, font=footer_font)

    source_text = f"Source: {entry['source']}" if entry.get("source") else None
    source_font = get_font(fonts_dir, "medium", 20)
    source_h = 0
    source_w = 0
    if source_text:
        source_w = draw.textlength(source_text, font=source_font)
        s_ascent, s_descent = source_font.getmetrics()
        source_h = s_ascent + s_descent + 10

    logo = _load_logo(config)
    bottom_margin = 90
    footer_y = H - bottom_margin
    source_y = footer_y - source_h if source_text else footer_y
    logo_h = 0
    if logo is not None:
        target_h = 56
        ratio = target_h / logo.height
        logo = logo.resize((max(1, int(logo.width * ratio)), target_h))
        logo_h = logo.height
        img.paste(logo, (int(W - margin - logo.width), int(source_y - logo_h - 14)), logo)
    if source_text:
        draw.text((W - margin - source_w, source_y), source_text, font=source_font, fill=navy)
    draw.text((W - margin - footer_w, footer_y), footer_text, font=footer_font, fill=navy)


def _layout_framed(img, draw, config, entry, fonts_dir):
    W, H = img.size
    brand = config["brand"]
    navy, white, accent = _hex_to_rgb(brand["navy"]), _hex_to_rgb(brand["white"]), _hex_to_rgb(brand["accent"])
    margin = 70

    draw.rectangle([0, 0, W, H], fill=navy)

    card_top = int(H * 0.16)
    card_bottom = int(H * 0.74)
    card_box = [margin, card_top, W - margin, card_bottom]
    draw.rounded_rectangle(card_box, radius=28, fill=white)

    pad = 50
    _draw_pill(draw, fonts_dir, PILLAR_TAG[entry["pillar"]], margin + pad, card_top + pad, accent, navy)

    inner_top = card_top + pad + 95
    inner_height = card_bottom - inner_top - pad
    font, lines, line_h = fit_text_block(
        draw, fonts_dir, "extrabold", entry["photocard_text"],
        max_width=(W - margin * 2) - pad * 2, max_height=inner_height,
        start_size=58, min_size=config["image"]["min_font_size"],
    )
    block_height = line_h * len(lines)
    start_y = inner_top + (inner_height - block_height) / 2
    draw_centered_lines(draw, lines, font, line_h, W / 2, start_y, navy)

    source_text = f"Source: {entry['source']}" if entry.get("source") else None
    _paste_logo_and_footer(img, draw, config, fonts_dir, entry["mode"],
                            footer_color=white, logo_rgb=white, center_x=W / 2, bottom_margin=70,
                            source_text=source_text)


LAYOUTS = [_layout_bold_center, _layout_side_accent, _layout_framed]


def render_photocard(config, entry, variant=None):
    mode = entry["mode"]
    size_cfg = config["image"]["personal"] if mode == "personal" else config["image"]["company"]
    W, H = size_cfg["width"], size_cfg["height"]
    fonts_dir = os.path.join(os.path.dirname(__file__), config["image"]["fonts_dir"])

    if variant is None:
        variant = int(entry["id"], 16) % len(LAYOUTS)
    variant = variant % len(LAYOUTS)

    img = Image.new("RGB", (W, H), "#FFFFFF")
    draw = ImageDraw.Draw(img)
    LAYOUTS[variant](img, draw, config, entry, fonts_dir)
    return img, variant


def ensure_placeholder_logo(config):
    """Creates a simple placeholder HelloFides wordmark logo if assets/logo.png
    does not exist yet. Replace assets/logo.png with the real brand logo any time -
    it will be picked up automatically on the next run."""
    logo_path = os.path.join(os.path.dirname(__file__), config["brand"]["logo_path"])
    if os.path.exists(logo_path):
        return
    os.makedirs(os.path.dirname(logo_path), exist_ok=True)
    fonts_dir = os.path.join(os.path.dirname(__file__), config["image"]["fonts_dir"])
    navy = _hex_to_rgb(config["brand"]["navy"])

    font = get_font(fonts_dir, "extrabold", 90)
    tmp_img = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    tmp_draw = ImageDraw.Draw(tmp_img)
    text = "HelloFides"
    bbox = tmp_draw.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0] + 20, bbox[3] - bbox[1] + 20
    logo = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(logo)
    d.text((10 - bbox[0], 10 - bbox[1]), text, font=font, fill=navy + (255,))
    logo.save(logo_path)
