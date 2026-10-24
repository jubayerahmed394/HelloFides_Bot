"""
HTML/CSS + Playwright photocard renderer (v4+). Replaces the old plain-Pillow
designer.py output with real typography, gradients, shadows, and layered
photos. Three layout systems per mode (Personal 1080x1350, Company 1080x1080),
Poppins ExtraBold headlines + DM Sans body, HelloFides navy/white/accent.

Fonts and colors must stay in sync with config.yaml's brand section.
"""
import os

NAVY = "#0B1F3A"
WHITE = "#FFFFFF"
ACCENT = "#2E5BFF"

PROJECT = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(PROJECT, "assets", "fonts")


def file_uri(path):
    return "file:///" + path.replace("\\", "/")


POPPINS_EB = file_uri(os.path.join(FONT_DIR, "Poppins-ExtraBold.ttf"))
POPPINS_BOLD = file_uri(os.path.join(FONT_DIR, "Poppins-Bold.ttf"))
DMSANS = file_uri(os.path.join(FONT_DIR, "DMSans-Variable.ttf"))

FONT_FACE = f"""
@font-face {{ font-family: 'Poppins'; src: url('{POPPINS_EB}') format('truetype'); font-weight: 800; }}
@font-face {{ font-family: 'Poppins'; src: url('{POPPINS_BOLD}') format('truetype'); font-weight: 700; }}
@font-face {{ font-family: 'DM Sans'; src: url('{DMSANS}') format('truetype'); font-weight: 400 700; }}
"""

BASE_CSS = f"""
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:'DM Sans',sans-serif; }}
.tag {{
  display:inline-block; background:{ACCENT}; color:{NAVY}; font-family:'Poppins'; font-weight:700;
  font-size:26px; letter-spacing:1px; padding:12px 24px; border-radius:30px; text-transform:uppercase;
}}
.hook {{ font-family:'Poppins'; font-weight:800; line-height:1.18; }}
.support {{ font-family:'DM Sans'; font-weight:500; line-height:1.4; }}
.source {{ font-family:'DM Sans'; font-weight:500; }}
.photo-duotone {{ filter: grayscale(100%) contrast(1.08) brightness(0.95); }}
.overlay-navy {{ background:{NAVY}; mix-blend-mode:multiply; }}

/* --- Name block (byline) shared styles --- */
.nb-name {{ font-family:'Poppins'; font-weight:800; line-height:1.15; }}
.nb-title {{ font-family:'DM Sans'; font-weight:500; line-height:1.3; }}
.nb-title-merged {{ font-family:'DM Sans'; font-weight:500; line-height:1.3; }}
.nb-dot {{ opacity:0.6; margin:0 6px; }}
.nb-avatar {{ border-radius:50%; object-fit:cover; flex-shrink:0; }}
"""


# ---------------------------------------------------------------------------
# Name block variants. Each returns an HTML snippet. `align`: "center"|"left".
# `tone`: "light" (white text, for navy/photo backgrounds) or "dark" (navy
# text, for white backgrounds).
# ---------------------------------------------------------------------------

def _tone_colors(tone):
    if tone == "light":
        return WHITE, "rgba(255,255,255,0.78)"
    return NAVY, "#4A5A72"


def name_block_a(lines, align="center", tone="light"):
    """A: stacked, name bold+larger, titles lighter+smaller, thin accent line above."""
    text_color, muted = _tone_colors(tone)
    items = "".join(f'<div class="nb-title" style="font-size:24px;color:{muted};">{l}</div>' for l in lines[1:])
    items_margin = "margin-top:2px;"
    align_items = "center" if align == "center" else "flex-start"
    text_align = "center" if align == "center" else "left"
    return f"""
    <div style="display:flex;flex-direction:column;align-items:{align_items};text-align:{text_align};">
      <div style="width:70px;height:4px;background:{ACCENT};border-radius:2px;margin-bottom:14px;"></div>
      <div class="nb-name" style="font-size:32px;color:{text_color};">{lines[0]}</div>
      <div style="{items_margin}">{items}</div>
    </div>"""


def name_block_b(lines, align="center", tone="light"):
    """B: name on line 1, titles merged onto one quiet line separated by a dot."""
    text_color, muted = _tone_colors(tone)
    title_line = f'<span class="nb-dot">&middot;</span>'.join(f"<span>{l}</span>" for l in lines[1:])
    align_items = "center" if align == "center" else "flex-start"
    text_align = "center" if align == "center" else "left"
    return f"""
    <div style="display:flex;flex-direction:column;align-items:{align_items};text-align:{text_align};">
      <div class="nb-name" style="font-size:30px;color:{text_color};">{lines[0]}</div>
      <div class="nb-title-merged" style="font-size:22px;color:{muted};margin-top:4px;">{title_line}</div>
    </div>"""


def name_block_c(lines, align="left", tone="light", avatar_path=None, bar_width="auto"):
    """C: framed footer strip (solid bar) containing the text (+ optional avatar)."""
    text_color, muted = _tone_colors(tone)
    bar_bg = NAVY if tone == "light" else WHITE
    # the bar itself always carries light-on-dark or dark-on-light internally,
    # independent of the card background behind it, since it's an opaque strip
    bar_text_color = WHITE if tone == "light" else NAVY
    bar_muted = "rgba(255,255,255,0.78)" if tone == "light" else "#4A5A72"
    title_line = f'<span class="nb-dot">&middot;</span>'.join(f"<span>{l}</span>" for l in lines[1:])
    avatar_html = ""
    if avatar_path:
        avatar_html = f'<img class="nb-avatar" src="{file_uri(avatar_path)}" style="width:64px;height:64px;margin-right:18px;">'
    width_css = "width:100%;" if bar_width == "full" else "display:inline-flex;"
    return f"""
    <div style="{width_css}align-items:center;background:{bar_bg};border-radius:16px;padding:16px 26px;
                box-shadow:0 10px 24px rgba(11,31,58,0.25);">
      {avatar_html}
      <div style="display:flex;flex-direction:column;">
        <div class="nb-name" style="font-size:28px;color:{bar_text_color};">{lines[0]}</div>
        <div class="nb-title-merged" style="font-size:20px;color:{bar_muted};margin-top:2px;">{title_line}</div>
      </div>
    </div>"""


NAME_BLOCK_VARIANTS = {"A": name_block_a, "B": name_block_b, "C": name_block_c}


# ---------------------------------------------------------------------------
# Personal layouts (1080x1350). Each takes the photo path + a pre-rendered
# name_block_html string, so any variant can be dropped in.
# ---------------------------------------------------------------------------

def layout_personal_hero(card, photo_path, name_block_html):
    """L1: full-bleed photo, bottom gradient scrim, hook in scrim."""
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{FONT_FACE}{BASE_CSS}
  body {{ width:1080px; height:1350px; position:relative; overflow:hidden; background:{NAVY}; }}
  .photo {{ position:absolute; inset:0; width:100%; height:100%; object-fit:cover; }}
  .photo-overlay {{ position:absolute; inset:0; }}
  .scrim {{ position:absolute; left:0; right:0; bottom:0; height:62%;
    background:linear-gradient(to top, rgba(11,31,58,0.97) 10%, rgba(11,31,58,0.75) 45%, rgba(11,31,58,0) 100%); }}
  .tag-wrap {{ position:absolute; top:70px; left:70px; }}
  .content {{ position:absolute; left:70px; right:70px; bottom:230px; }}
  .hook {{ color:{WHITE}; font-size:68px; margin-bottom:22px; }}
  .support {{ color:rgba(255,255,255,0.85); font-size:32px; }}
  .footer {{ position:absolute; left:0; right:0; bottom:60px; display:flex; justify-content:center; }}
  </style></head><body>
  <img class="photo photo-duotone" src="{file_uri(photo_path)}">
  <div class="photo-overlay overlay-navy" style="opacity:0.35;"></div>
  <div class="scrim"></div>
  <div class="tag-wrap"><span class="tag">{card['tag']}</span></div>
  <div class="content">
    <div class="hook">{card['hook']}</div>
    <div class="support">{card['support']}</div>
  </div>
  <div class="footer">{name_block_html}</div>
  </body></html>"""


def layout_personal_split(card, photo_path, name_block_html):
    """L2: top photo panel, bottom solid navy panel."""
    source_html = (f'<div class="source" style="color:rgba(255,255,255,0.65);font-size:22px;margin:10px 0 18px;">'
                    f'Source: {card["source"]}</div>') if card.get("source") else '<div style="height:14px;"></div>'
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{FONT_FACE}{BASE_CSS}
  body {{ width:1080px; height:1350px; position:relative; overflow:hidden; background:{NAVY}; }}
  .photo-panel {{ position:absolute; top:0; left:0; right:0; height:55%; overflow:hidden; }}
  .photo {{ width:100%; height:100%; object-fit:cover; }}
  .photo-overlay {{ position:absolute; inset:0; }}
  .seam {{ position:absolute; top:55%; left:0; right:0; height:6px; background:{ACCENT}; }}
  .panel {{ position:absolute; top:55%; left:0; right:0; bottom:0; padding:60px 70px; }}
  .tag-wrap {{ position:absolute; top:-30px; left:0; }}
  .hook {{ color:{WHITE}; font-size:58px; margin:36px 0 20px; }}
  .support {{ color:rgba(255,255,255,0.85); font-size:30px; }}
  .footer {{ position:absolute; left:70px; right:70px; bottom:50px; }}
  </style></head><body>
  <div class="photo-panel">
    <img class="photo photo-duotone" src="{file_uri(photo_path)}">
    <div class="photo-overlay overlay-navy" style="opacity:0.3;"></div>
  </div>
  <div class="seam"></div>
  <div class="panel">
    <div class="tag-wrap"><span class="tag">{card['tag']}</span></div>
    <div class="hook">{card['hook']}</div>
    <div class="support">{card['support']}</div>
    {source_html}
    <div class="footer">{name_block_html}</div>
  </div>
  </body></html>"""


def layout_personal_framed(card, photo_path, name_block_html):
    """L3: white bg, inset framed photo card near top, hook below on white."""
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{FONT_FACE}{BASE_CSS}
  body {{ width:1080px; height:1350px; position:relative; overflow:hidden; background:{WHITE}; }}
  .frame {{ position:absolute; top:60px; left:60px; right:60px; height:600px; border-radius:24px;
    overflow:hidden; box-shadow:0 24px 48px rgba(11,31,58,0.25); }}
  .photo {{ width:100%; height:100%; object-fit:cover; }}
  .photo-overlay {{ position:absolute; inset:0; }}
  .tag-wrap {{ position:absolute; top:636px; left:60px; }}
  .content {{ position:absolute; top:730px; left:70px; right:70px; }}
  .hook {{ color:{NAVY}; font-size:60px; margin-bottom:22px; }}
  .support {{ color:#4A5A72; font-size:30px; }}
  .footer {{ position:absolute; left:0; right:0; bottom:60px; display:flex; justify-content:center; }}
  </style></head><body>
  <div class="frame">
    <img class="photo photo-duotone" src="{file_uri(photo_path)}">
    <div class="photo-overlay overlay-navy" style="opacity:0.4;"></div>
  </div>
  <div class="tag-wrap"><span class="tag">{card['tag']}</span></div>
  <div class="content">
    <div class="hook">{card['hook']}</div>
    <div class="support">{card['support']}</div>
  </div>
  <div class="footer">{name_block_html}</div>
  </body></html>"""


PERSONAL_LAYOUTS = {
    "L1": (layout_personal_hero, "center", "light"),
    "L2": (layout_personal_split, "left", "light"),
    "L3": (layout_personal_framed, "center", "dark"),
}


# ---------------------------------------------------------------------------
# Company layouts (1080x1080). Footer stays the simple unchanged
# "HelloFides | hellofides.com" two-line footer - no name-block variants here.
# ---------------------------------------------------------------------------

def layout_company_hero(card, photo_path, footer_name, footer_sub):
    """C1: full-bleed square, center-bottom scrim, CTA chip footer."""
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{FONT_FACE}{BASE_CSS}
  body {{ width:1080px; height:1080px; position:relative; overflow:hidden; background:{NAVY}; }}
  .photo {{ position:absolute; inset:0; width:100%; height:100%; object-fit:cover; }}
  .photo-overlay {{ position:absolute; inset:0; }}
  .scrim {{ position:absolute; left:0; right:0; bottom:0; height:68%;
    background:linear-gradient(to top, rgba(11,31,58,0.97) 15%, rgba(11,31,58,0.7) 50%, rgba(11,31,58,0) 100%); }}
  .tag-wrap {{ position:absolute; top:60px; left:60px; }}
  .content {{ position:absolute; left:60px; right:60px; bottom:150px; }}
  .hook {{ color:{WHITE}; font-size:58px; margin-bottom:18px; }}
  .support {{ color:rgba(255,255,255,0.85); font-size:28px; }}
  .footer {{ position:absolute; left:0; right:0; bottom:48px; text-align:center; }}
  .footer-name {{ color:{WHITE}; font-family:'Poppins'; font-weight:800; font-size:30px; }}
  .cta-chip {{ display:inline-block; background:{ACCENT}; color:{NAVY}; font-family:'DM Sans'; font-weight:700;
    font-size:22px; padding:8px 20px; border-radius:20px; margin-top:10px; }}
  </style></head><body>
  <img class="photo photo-duotone" src="{file_uri(photo_path)}">
  <div class="photo-overlay overlay-navy" style="opacity:0.35;"></div>
  <div class="scrim"></div>
  <div class="tag-wrap"><span class="tag">{card['tag']}</span></div>
  <div class="content">
    <div class="hook">{card['hook']}</div>
    <div class="support">{card['support']}</div>
  </div>
  <div class="footer">
    <div class="footer-name">{footer_name}</div>
    <div class="cta-chip">{footer_sub}</div>
  </div>
  </body></html>"""


def layout_company_split(card, photo_path, footer_name, footer_sub):
    """C2: left photo panel, right navy panel."""
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{FONT_FACE}{BASE_CSS}
  body {{ width:1080px; height:1080px; position:relative; overflow:hidden; background:{NAVY}; }}
  .photo-panel {{ position:absolute; top:0; left:0; bottom:0; width:44%; overflow:hidden; }}
  .photo {{ width:100%; height:100%; object-fit:cover; }}
  .photo-overlay {{ position:absolute; inset:0; }}
  .seam {{ position:absolute; top:0; bottom:0; left:44%; width:6px; background:{ACCENT}; }}
  .panel {{ position:absolute; top:0; right:0; bottom:0; left:44%; padding:70px 56px; }}
  .hook {{ color:{WHITE}; font-size:48px; margin:30px 0 18px; }}
  .support {{ color:rgba(255,255,255,0.85); font-size:26px; }}
  .footer {{ position:absolute; left:56px; right:56px; bottom:50px; }}
  .footer-name {{ color:{WHITE}; font-family:'Poppins'; font-weight:800; font-size:28px; }}
  .footer-sub {{ color:rgba(255,255,255,0.8); font-family:'DM Sans'; font-weight:500; font-size:20px; margin-top:4px; }}
  </style></head><body>
  <div class="photo-panel">
    <img class="photo photo-duotone" src="{file_uri(photo_path)}">
    <div class="photo-overlay overlay-navy" style="opacity:0.3;"></div>
  </div>
  <div class="seam"></div>
  <div class="panel">
    <span class="tag">{card['tag']}</span>
    <div class="hook">{card['hook']}</div>
    <div class="support">{card['support']}</div>
    <div class="footer">
      <div class="footer-name">{footer_name}</div>
      <div class="footer-sub">{footer_sub}</div>
    </div>
  </div>
  </body></html>"""


def layout_company_card(card, photo_path, footer_name, footer_sub):
    """C3: photo top, white rounded card overlapping bottom third."""
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{FONT_FACE}{BASE_CSS}
  body {{ width:1080px; height:1080px; position:relative; overflow:hidden; background:{NAVY}; }}
  .photo {{ position:absolute; top:0; left:0; right:0; height:68%; object-fit:cover; width:100%; }}
  .photo-overlay {{ position:absolute; top:0; left:0; right:0; height:68%; }}
  .tag-wrap {{ position:absolute; top:50px; left:50px; }}
  .card {{ position:absolute; left:40px; right:40px; bottom:40px; top:640px; background:{WHITE};
    border-radius:28px; box-shadow:0 -20px 40px rgba(0,0,0,0.25); padding:48px 50px; }}
  .hook {{ color:{NAVY}; font-size:48px; margin-bottom:16px; }}
  .support {{ color:#4A5A72; font-size:26px; margin-bottom:20px; }}
  .footer-row {{ display:flex; justify-content:space-between; align-items:center; position:absolute; bottom:40px; left:50px; right:50px; }}
  .footer-name {{ color:{NAVY}; font-family:'Poppins'; font-weight:800; font-size:28px; }}
  .cta-chip {{ display:inline-block; background:{NAVY}; color:{WHITE}; font-family:'DM Sans'; font-weight:700;
    font-size:20px; padding:8px 18px; border-radius:18px; }}
  </style></head><body>
  <img class="photo photo-duotone" src="{file_uri(photo_path)}">
  <div class="photo-overlay overlay-navy" style="opacity:0.35;"></div>
  <div class="tag-wrap"><span class="tag">{card['tag']}</span></div>
  <div class="card">
    <div class="hook">{card['hook']}</div>
    <div class="support">{card['support']}</div>
    <div class="footer-row">
      <span class="footer-name">{footer_name}</span>
      <span class="cta-chip">{footer_sub}</span>
    </div>
  </div>
  </body></html>"""


COMPANY_LAYOUTS = {
    "C1": layout_company_hero,
    "C2": layout_company_split,
    "C3": layout_company_card,
}


def render_html(out_path, html):
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)


def screenshot(playwright_browser, html_path, size, out_png):
    W, H = size
    page = playwright_browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
    page.goto(file_uri(html_path))
    page.wait_for_timeout(200)
    page.screenshot(path=out_png)
    page.close()


def render_card_sync(mode, layout_key, card, photo_path, html_dir, out_png, config,
                      name_block_variant="A"):
    """One-shot render: builds HTML, launches a throwaway Chromium, screenshots,
    closes. Fine for bot usage (1-6 cards per invocation, not a hot path)."""
    from playwright.sync_api import sync_playwright

    os.makedirs(html_dir, exist_ok=True)
    brand = config["brand"]

    if mode == "personal":
        layout_fn, align, tone = PERSONAL_LAYOUTS[layout_key]
        avatar_path = os.path.join(PROJECT, brand["avatar_path"])
        if not os.path.exists(avatar_path):
            avatar_path = None
        nb_html = NAME_BLOCK_VARIANTS[name_block_variant](
            brand["personal_name_lines"], align=align, tone=tone, avatar_path=avatar_path
        ) if name_block_variant == "C" else NAME_BLOCK_VARIANTS[name_block_variant](
            brand["personal_name_lines"], align=align, tone=tone
        )
        html = layout_fn(card, photo_path, nb_html)
        size = (1080, 1350)
    else:
        layout_fn = COMPANY_LAYOUTS[layout_key]
        footer_name, footer_sub = brand["company_footer"].split(" | ")
        html = layout_fn(card, photo_path, footer_name, footer_sub)
        size = (1080, 1080)

    html_path = os.path.join(html_dir, f"_render_{mode}_{layout_key}.html")
    render_html(html_path, html)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        screenshot(browser, html_path, size, out_png)
        browser.close()

    return out_png
