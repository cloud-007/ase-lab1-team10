"""Generate the self-contained team website from content/*.md profiles."""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parent
CONTENT_DIR = ROOT / "content"
IMAGES_DIR = CONTENT_DIR / "images"
OUTPUT_FILE = ROOT / "site" / "index.html"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".svg", ".webp"}

# The shared helper embeds the actual photo bytes into data URIs.
sys.path.insert(0, str(ROOT.parent))
from common.embed_images import embed_images  # noqa: E402


SECTION_LABELS = {
    "study background": "Study background",
    "interests": "Interests",
    "fun fact": "Fun fact",
    "what i think about ai in software engineering today": "AI today",
    "where software engineering will be in 5–10 years": "The next 5–10 years",
    "where software engineering will be in 5-10 years": "The next 5–10 years",
    "my hot take on ai": "Hot take",
}


def clean_markdown(value: str) -> str:
    """Remove comments and surrounding whitespace from a Markdown fragment."""
    value = re.sub(r"<!--[\s\S]*?-->", "", value)
    return value.strip()


def parse_profile(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8-sig")
    text = re.sub(r"<!--[\s\S]*?-->", "", text)
    headings = list(re.finditer(r"(?m)^(#{1,6})\s+(.+?)\s*#*\s*$", text))

    name = path.stem.replace("_", " ").replace("-", " ").title()
    sections: dict[str, str] = {}
    intro = ""
    if headings:
        first_title = headings[0].group(2).strip()
        if len(headings[0].group(1)) == 1 and not re.search(r"<[^>]+>", first_title):
            name = first_title
        intro = clean_markdown(text[: headings[0].start()])
        for index, heading in enumerate(headings):
            if len(heading.group(1)) != 2:
                continue
            end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
            key = re.sub(r"\s+", " ", heading.group(2).strip()).casefold()
            sections[key] = clean_markdown(text[heading.end() : end])

    # A name-like line after the title can be a preferred/full name; preserve it as a subtitle.
    intro_html = render_markdown(intro) if intro else ""
    image = find_picture(path, text)
    return {"name": name.strip(), "sections": sections, "intro": intro_html, "image": image}


def find_picture(profile_path: Path, text: str) -> Path | None:
    """Resolve a Markdown image reference, then try the profile's filename stem."""
    candidates: list[str] = []
    for match in re.finditer(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)", text):
        reference = match.group(1).split("#", 1)[0].split("?", 1)[0]
        candidates.append(Path(reference.replace("\\", "/")).name)

    for candidate in candidates:
        path = IMAGES_DIR / candidate
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
            return path

    for path in sorted(IMAGES_DIR.iterdir()) if IMAGES_DIR.is_dir() else []:
        if path.is_file() and path.stem.casefold() == profile_path.stem.casefold() and path.suffix.lower() in IMAGE_EXTENSIONS:
            return path
    return None


def render_markdown(fragment: str) -> str:
    """Render the small, safe Markdown subset used by the team profiles."""
    fragment = clean_markdown(fragment)
    if not fragment:
        return ""

    output: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []

    def inline(text: str) -> str:
        safe = html.escape(text.strip(), quote=True)
        safe = re.sub(
            r"\[([^\]]+)\]\((https?://[^\s)]+|mailto:[^\s)]+)\)",
            r'<a href="\2">\1</a>',
            safe,
        )
        safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)
        safe = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", safe)
        return safe

    def flush_paragraph() -> None:
        if paragraph:
            output.append("<p>" + " ".join(inline(line) for line in paragraph) + "</p>")
            paragraph.clear()

    def flush_list() -> None:
        if list_items:
            output.append("<ul>" + "".join(f"<li>{inline(item)}</li>" for item in list_items) + "</ul>")
            list_items.clear()

    for raw_line in fragment.splitlines():
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            flush_list()
        elif re.match(r"^(?:[-*+] |\d+[.)] )", line):
            flush_paragraph()
            list_items.append(re.sub(r"^(?:[-*+] |\d+[.)] )", "", line))
        else:
            flush_list()
            paragraph.append(line)
    flush_paragraph()
    flush_list()
    return "\n".join(output)


def fallback_avatar(name: str) -> str:
    """Return a tiny generated SVG avatar for a profile with no uploaded image."""
    initials = "".join(word[0] for word in name.split()[:2] if word).upper() or "?"
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 400">'
        '<rect width="400" height="400" fill="#f7b267"/>'
        '<circle cx="200" cy="150" r="72" fill="#fff8e7"/>'
        '<path d="M65 390c10-100 58-150 135-150s125 50 135 150" fill="#e4007c"/>'
        f'<text x="200" y="355" text-anchor="middle" font-family="sans-serif" '
        f'font-size="48" font-weight="700" fill="#1d1d1f">{html.escape(initials)}</text></svg>'
    )
    return "data:image/svg+xml," + quote(svg, safe="/:;=,+")


def member_card(profile: dict[str, object], index: int) -> str:
    name = str(profile["name"])
    sections = profile["sections"]
    assert isinstance(sections, dict)
    picture = profile["image"]
    if isinstance(picture, Path):
        image_src = f'{{{{image:{picture.name}}}}}'
    else:
        image_src = fallback_avatar(name)

    details = []
    for key, label in SECTION_LABELS.items():
        # Both spellings of the date heading map to the same display section.
        content = sections.get(key)
        if not content:
            continue
        rendered = render_markdown(str(content))
        if not rendered:
            continue
        css_class = ' class="hot-take"' if label == "Hot take" else ""
        details.append(
            f'<section{css_class}><h3>{html.escape(label)}</h3>{rendered}</section>'
        )

    intro = str(profile["intro"])
    return f'''<article class="member-card" style="--card-order: {index}">
      <div class="member-top">
        <img class="portrait" src="{image_src}" alt="Portrait of {html.escape(name, quote=True)}" loading="lazy">
        <div><p class="eyebrow">Teammate {index + 1:02d}</p><h2>{html.escape(name)}</h2>{intro}</div>
      </div>
      <div class="member-details">{"".join(details) if details else '<p class="quiet">Profile sections coming soon — currently compiling personality.</p>'}</div>
    </article>'''


def build_html(profiles: list[dict[str, object]]) -> str:
    rendered_cards = "\n".join(member_card(profile, i) for i, profile in enumerate(profiles))
    count = len(profiles)
    roster_line = (
        f"{count} minds, one repository, and a surprisingly high number of opinions about semicolons."
        if count
        else "A team is compiling. Please add a profile to content/ to populate this page."
    )
    return f'''<!doctype html>
<html lang="en" data-theme="day">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#fff8e7">
  <title>Team Byte-Sized Optimists | Agentic SE Lab</title>
  <style>
    :root {{ color-scheme: light; --page: #fff8e7; --ink: #1d1d1f; --surface: #ffffff; --muted: #625d57; --accent: #e4007c; --line: #e8dfd0; --glow: #ffe0ef; }}
    [data-theme="night"] {{ color-scheme: dark; --page: #14142b; --ink: #f4f1de; --surface: #20203c; --muted: #c7c3d8; --accent: #ff70b5; --line: #3c3a5a; --glow: #3c2342; }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{ margin: 0; background: var(--page); color: var(--ink); font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.65; transition: background-color .25s ease, color .25s ease; }}
    a {{ color: var(--accent); text-decoration-thickness: .12em; text-underline-offset: .18em; }}
    .wrap {{ width: min(72rem, calc(100% - 2.5rem)); margin-inline: auto; }}
    .topbar {{ display: flex; justify-content: space-between; align-items: center; gap: 1rem; padding-block: 1.2rem; }}
    .brand {{ font-weight: 850; letter-spacing: -.04em; text-decoration: none; color: var(--ink); }}
    button {{ font: inherit; cursor: pointer; color: var(--ink); background: var(--surface); border: 1px solid var(--line); border-radius: 999px; padding: .65rem 1rem; }}
    button:hover {{ border-color: var(--accent); }}
    .hero {{ padding: clamp(3.5rem, 9vw, 7.5rem) 0 4rem; position: relative; overflow: hidden; }}
    .hero::after {{ content: "</>"; position: absolute; z-index: -1; right: 3%; top: 12%; font-size: clamp(7rem, 22vw, 17rem); line-height: 1; color: var(--glow); font-weight: 900; transform: rotate(-8deg); }}
    .eyebrow {{ color: var(--accent); font-size: .78rem; font-weight: 800; letter-spacing: .13em; text-transform: uppercase; margin: 0 0 .5rem; }}
    h1 {{ max-width: 12ch; margin: 0; font-size: clamp(3rem, 8vw, 6.5rem); letter-spacing: -.075em; line-height: .98; }}
    .hero-copy {{ max-width: 42rem; color: var(--muted); font-size: clamp(1.05rem, 2vw, 1.25rem); margin: 1.4rem 0 0; }}
    .hero-copy strong {{ color: var(--ink); }}
    .section-head {{ display: flex; justify-content: space-between; align-items: end; gap: 1rem; margin: 2rem 0 1.25rem; }}
    h2 {{ margin: 0; font-size: clamp(1.65rem, 4vw, 2.5rem); letter-spacing: -.05em; line-height: 1.1; }}
    .section-head p {{ color: var(--muted); margin: 0; }}
    .members {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1.1rem; padding-bottom: 5rem; }}
    .member-card {{ background: var(--surface); border: 1px solid var(--line); border-radius: 1.4rem; padding: clamp(1rem, 2.5vw, 1.6rem); box-shadow: 0 .7rem 2rem rgb(30 20 50 / .05); }}
    .member-top {{ display: grid; grid-template-columns: 6.5rem 1fr; align-items: center; gap: 1rem; padding-bottom: 1.1rem; border-bottom: 1px solid var(--line); }}
    .portrait {{ display: block; width: 6.5rem; aspect-ratio: 1; object-fit: cover; border-radius: 1rem; background: var(--glow); }}
    .member-top h2 {{ overflow-wrap: anywhere; }}
    .member-top .eyebrow {{ font-size: .68rem; }}
    .member-top p:not(.eyebrow) {{ color: var(--muted); margin: .5rem 0 0; }}
    .member-details {{ display: grid; gap: .85rem; padding-top: 1rem; }}
    .member-details section {{ min-width: 0; }}
    .member-details h3 {{ font-size: .76rem; color: var(--accent); text-transform: uppercase; letter-spacing: .1em; margin: 0 0 .25rem; }}
    .member-details p, .member-details ul {{ margin: 0; color: var(--muted); }}
    .member-details ul {{ padding-left: 1.2rem; }}
    .hot-take {{ border-left: .2rem solid var(--accent); padding-left: .8rem; }}
    .hot-take p {{ color: var(--ink); font-weight: 650; }}
    .quiet {{ color: var(--muted); }}
    .future {{ padding: 4rem 0 5rem; border-top: 1px solid var(--line); }}
    .future-grid {{ display: grid; grid-template-columns: 1fr 1.2fr; gap: 2rem; align-items: start; }}
    .future h2 {{ max-width: 12ch; }}
    .manifesto {{ background: var(--surface); border: 1px solid var(--line); border-radius: 1.4rem; padding: clamp(1.2rem, 3vw, 2rem); }}
    .manifesto p {{ margin-top: 0; }}
    .manifesto p:last-child {{ margin-bottom: 0; }}
    .punchline {{ font-weight: 800; color: var(--accent); }}
    footer {{ border-top: 1px solid var(--line); padding: 1.5rem 0 2.5rem; color: var(--muted); font-size: .9rem; }}
    footer p {{ margin: .2rem 0; }}
    @media (max-width: 700px) {{
      .wrap {{ width: min(100% - 1.25rem, 40rem); }}
      .hero {{ padding-top: 3.2rem; }}
      .members {{ grid-template-columns: 1fr; gap: .8rem; }}
      .section-head {{ align-items: start; flex-direction: column; }}
      .future-grid {{ grid-template-columns: 1fr; gap: 1rem; }}
      .member-top {{ grid-template-columns: 5.25rem 1fr; }}
      .portrait {{ width: 5.25rem; }}
    }}
    @media (prefers-reduced-motion: reduce) {{ *, *::before, *::after {{ scroll-behavior: auto !important; transition-duration: .01ms !important; }} }}
  </style>
</head>
<body>
  <header class="wrap topbar">
    <a class="brand" href="#top" aria-label="Back to top">BYTE-SIZED / BIG IDEAS</a>
    <button id="theme-toggle" type="button" aria-label="Switch to night mode" aria-pressed="false">☾ Night mode</button>
  </header>
  <main id="top">
    <section class="wrap hero" aria-labelledby="hero-title">
      <p class="eyebrow">A small team with a very large backlog</p>
      <h1 id="hero-title">We make software. And snacks.</h1>
      <p class="hero-copy">We are the people behind the pull requests: curious engineers learning to build useful things, ship them together, and only occasionally blame the cache. <strong>{html.escape(roster_line)}</strong></p>
    </section>
    <section class="wrap" id="team" aria-labelledby="team-title">
      <div class="section-head"><div><p class="eyebrow">Meet the humans</p><h2 id="team-title">The team, unminified</h2></div><p>All our best features are still in beta.</p></div>
      <div class="members">{rendered_cards}</div>
    </section>
    <section class="future" id="future-of-se" aria-labelledby="future-title">
      <div class="wrap future-grid">
        <div><p class="eyebrow">Our shared signal</p><h2 id="future-title">The future is collaborative (and probably linted).</h2></div>
        <div class="manifesto">
          <p>AI is already a useful accelerator for boilerplate, setup, and the first draft. It is not a substitute for understanding what the software should do—or noticing when a confident answer has quietly introduced a security bug.</p>
          <p>In five to ten years, we expect more of the work to shift from typing every line toward designing systems, setting clear constraints, and validating what our tools produce. The fundamentals will matter even more: good engineers need to judge reliability, security, and the human consequences of what gets shipped.</p>
          <p class="punchline">Our forecast: fewer boilerplate keystrokes, more thoughtful decisions, and the same number of “works on my machine” mysteries.</p>
        </div>
      </div>
    </section>
  </main>
  <footer><div class="wrap"><p>Built by an agent, supervised by humans.</p><p>Powered by curiosity, teamwork, and the occasional emergency coffee.</p></div></footer>
  <script>
    (() => {{
      const button = document.getElementById('theme-toggle');
      const root = document.documentElement;
      const saved = localStorage.getItem('upb-theme');
      const initial = saved === 'night' ? 'night' : 'day';
      const applyTheme = (theme) => {{
        root.dataset.theme = theme;
        button.setAttribute('aria-pressed', String(theme === 'night'));
        button.setAttribute('aria-label', theme === 'night' ? 'Switch to day mode' : 'Switch to night mode');
        button.textContent = theme === 'night' ? '☀ Day mode' : '☾ Night mode';
        localStorage.setItem('upb-theme', theme);
      }};
      applyTheme(initial);
      button.addEventListener('click', () => applyTheme(root.dataset.theme === 'night' ? 'day' : 'night'));
    }})();
  </script>
</body>
</html>
'''


def main() -> None:
    profiles = [parse_profile(path) for path in sorted(CONTENT_DIR.glob("*.md")) if not path.name.startswith("_")]
    html_text = embed_images(build_html(profiles), IMAGES_DIR)
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(html_text, encoding="utf-8")
    print(f"Built {OUTPUT_FILE.relative_to(ROOT)} with {len(profiles)} profile(s) ({OUTPUT_FILE.stat().st_size / 1024:.0f} KB).")


if __name__ == "__main__":
    main()
