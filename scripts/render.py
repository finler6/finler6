"""Render the SVG cards for the finler6 profile README.

    GH_TOKEN=... python3 scripts/render.py path/to/cascadia/ttf/static

Writes assets/status-{light,dark}.svg (static text) and
assets/stats-{light,dark}.svg (public GitHub activity, read via the GraphQL API).
The GitHub Actions workflow in .github/workflows/stats.yml runs this daily.

Text is converted to outlines with fontTools, so the cards look the same no
matter which fonts the viewer has. Font: Cascadia Mono 2407.24,
SIL Open Font License 1.1 (https://github.com/microsoft/cascadia-code).
"""

import datetime as dt
import json
import math
import os
import sys
import urllib.request
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

ASSETS = Path(__file__).resolve().parent.parent / "assets"
FONT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/cascadia/x/ttf/static")
LOGIN = os.environ.get("PROFILE_LOGIN", "finler6")


def num(value: float) -> str:
    """Short number formatting for SVG attributes and path data."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


class Face:
    """A static font file that can turn a string into SVG path data."""

    def __init__(self, filename: str):
        font = TTFont(FONT_DIR / filename)
        self.glyphs = font.getGlyphSet()
        self.cmap = font.getBestCmap()
        self.upm = font["head"].unitsPerEm
        self.cap_height = font["OS/2"].sCapHeight
        self.x_height = font["OS/2"].sxHeight

    def width(self, text, size):
        scale = size / self.upm
        return sum(self.glyphs[self.cmap[ord(ch)]].width * scale for ch in text)

    def cap(self, size):
        return self.cap_height * size / self.upm

    def path(self, text, size, x, baseline):
        scale = size / self.upm
        pen = SVGPathPen(self.glyphs, ntos=num)
        for ch in text:
            glyph = self.glyphs[self.cmap[ord(ch)]]
            glyph.draw(TransformPen(pen, (scale, 0, 0, -scale, x, baseline)))
            x += glyph.width * scale
        return pen.getCommands()


BOLD = Face("CascadiaMono-Bold.ttf")
REGULAR = Face("CascadiaMono-Regular.ttf")

THEMES = {
    "light": dict(bg="#f6f8fa", border="#d0d7de", text="#1f2328", muted="#656d76", green="#1a7f37"),
    "dark": dict(bg="#161b22", border="#30363d", text="#e6edf3", muted="#7d8590", green="#3fb950"),
}


def svg(width, height, body, label, css=""):
    style = f"<style>{css}</style>" if css else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{num(width)}" height="{num(height)}" '
        f'viewBox="0 0 {num(width)} {num(height)}" role="img" aria-label="{label}">'
        f"<title>{label}</title>{style}{body}</svg>\n"
    )


def card(width, height, c):
    return (
        f'<rect x=".5" y=".5" width="{num(width - 1)}" height="{num(height - 1)}" rx="8" '
        f'fill="{c["bg"]}" stroke="{c["border"]}"/>'
    )


def text(face, value, size, x, baseline, colour):
    return f'<path d="{face.path(value, size, x, baseline)}" fill="{colour}"/>'


# --- status card: `systemctl status` with the profile owner as a service -------

STATUS_SIZE, STATUS_LINE, PAD_X, PAD_Y = 13, 21, 18, 16

# (text, colour key, bold). "DOT" is the status bullet, drawn as a circle so it
# can breathe like the indicator of a running unit.
STATUS_LINES = [
    [("$ ", "muted", False), ("systemctl status finler6", "text", False)],
    [("DOT", "green", False), ("finler6.service", "text", True), (" - Gleb", "text", False)],
    [("     Loaded: ", "muted", False), ("loaded (/etc/systemd/system/finler6.service; enabled)", "text", False)],
    [("     Active: ", "muted", False), ("active (running)", "green", True)],
    [("      Tasks: ", "muted", False), ("3 (C, C#, Python)", "text", False)],
]

STATUS_LABEL = (
    "Terminal output of systemctl status finler6: finler6.service - Gleb, "
    "active (running), tasks: C, C#, Python"
)

DOT_CSS = (
    ".dot{animation:dot 2.4s ease-in-out infinite}"
    "@keyframes dot{50%{opacity:.35}}"
    "@media (prefers-reduced-motion:reduce){.dot{animation:none}}"
)


def cell():
    return REGULAR.width(" ", STATUS_SIZE)


def card_width():
    longest = max(sum(2 if t == "DOT" else len(t) for t, _, _ in line) for line in STATUS_LINES)
    return PAD_X * 2 + longest * cell()


def status_card(theme):
    c = THEMES[theme]
    width = card_width()
    cap = REGULAR.cap(STATUS_SIZE)
    height = PAD_Y * 2 + STATUS_LINE * (len(STATUS_LINES) - 1) + cap + 4
    parts = [card(width, height, c)]
    for row, line in enumerate(STATUS_LINES):
        baseline = PAD_Y + cap + row * STATUS_LINE
        x = PAD_X
        for value, colour, bold in line:
            if value == "DOT":
                cy = baseline - REGULAR.x_height * STATUS_SIZE / REGULAR.upm / 2
                parts.append(
                    f'<circle class="dot" cx="{num(x + cell() / 2)}" cy="{num(cy)}" '
                    f'r="{num(STATUS_SIZE * 0.3)}" fill="{c[colour]}"/>'
                )
                x += 2 * cell()
                continue
            parts.append(text(BOLD if bold else REGULAR, value, STATUS_SIZE, x, baseline, c[colour]))
            x += len(value) * cell()
    return svg(width, height, "".join(parts), STATUS_LABEL, DOT_CSS)


# --- stats card: radar of public activity plus the raw numbers ---------------

# (radar label, table label, key)
AXES = [
    ("commits", "commits", "commits"),
    ("repos", "repositories", "repos"),
    ("PRs", "pull requests", "prs"),
    ("reviews", "code reviews", "reviews"),
    ("issues", "issues", "issues"),
]


def graphql(query, **variables):
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("GH_TOKEN or GITHUB_TOKEN is required to read GitHub stats")
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": f"{LOGIN}-profile-stats",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if payload.get("errors"):
        sys.exit(f"GraphQL error: {payload['errors']}")
    return payload["data"]


def fetch_stats(login):
    user = graphql(
        "query($login: String!) { user(login: $login) { createdAt "
        "repositories(ownerAffiliations: OWNER, privacy: PUBLIC) { totalCount } } }",
        login=login,
    )["user"]
    stats = dict(commits=0, prs=0, reviews=0, issues=0, repos=user["repositories"]["totalCount"])
    first_year = int(user["createdAt"][:4])
    # The API only returns up to one year per request, so walk calendar years.
    for year in range(first_year, dt.date.today().year + 1):
        collection = graphql(
            "query($login: String!, $from: DateTime!, $to: DateTime!) { user(login: $login) { "
            "contributionsCollection(from: $from, to: $to) { totalCommitContributions "
            "totalPullRequestContributions totalPullRequestReviewContributions totalIssueContributions } } }",
            login=login,
            **{"from": f"{year}-01-01T00:00:00Z", "to": f"{year}-12-31T23:59:59Z"},
        )["user"]["contributionsCollection"]
        stats["commits"] += collection["totalCommitContributions"]
        stats["prs"] += collection["totalPullRequestContributions"]
        stats["reviews"] += collection["totalPullRequestReviewContributions"]
        stats["issues"] += collection["totalIssueContributions"]
    return stats


def spoke(value):
    """Log scale so a few PRs still show up next to hundreds of commits."""
    return max(0.04, min(1.0, math.log10(value + 1) / math.log10(1001)))


def stats_card(theme, stats, as_of):
    c = THEMES[theme]
    width, height = card_width(), 204
    cx, cy, radius = 140, 104, 66
    parts = [card(width, height, c)]

    def point(index, r):
        angle = math.radians(-90 + 72 * index)
        return cx + r * math.cos(angle), cy + r * math.sin(angle)

    def polygon(points, attrs):
        coords = " ".join(f"{num(x)},{num(y)}" for x, y in points)
        return f'<polygon points="{coords}" {attrs}/>'

    grid = f'fill="none" stroke="{c["muted"]}" stroke-opacity=".35"'
    for level in (1 / 3, 2 / 3, 1):
        parts.append(polygon([point(i, radius * level) for i in range(5)], grid))
    for i in range(5):
        x, y = point(i, radius)
        parts.append(
            f'<line x1="{cx}" y1="{cy}" x2="{num(x)}" y2="{num(y)}" stroke="{c["muted"]}" stroke-opacity=".35"/>'
        )

    shape = [point(i, radius * spoke(stats[key])) for i, (_, _, key) in enumerate(AXES)]
    parts.append(
        polygon(
            shape,
            f'fill="{c["green"]}" fill-opacity=".18" stroke="{c["green"]}" '
            'stroke-width="1.5" stroke-linejoin="round"',
        )
    )
    for x, y in shape:
        parts.append(f'<circle cx="{num(x)}" cy="{num(y)}" r="2.5" fill="{c["green"]}"/>')

    label_size = 11
    label_cap = REGULAR.cap(label_size)
    for i, (label, _, _) in enumerate(AXES):
        x, y = point(i, radius + 12)
        w = REGULAR.width(label, label_size)
        if i == 0:
            parts.append(text(REGULAR, label, label_size, x - w / 2, y, c["muted"]))
        elif i in (1, 2):
            parts.append(text(REGULAR, label, label_size, x, y + label_cap / 2, c["muted"]))
        else:
            parts.append(text(REGULAR, label, label_size, x - w, y + label_cap / 2, c["muted"]))

    size, row_height = 12, 22
    left, right = 300, width - PAD_X
    first = cy - (len(AXES) - 1) * row_height / 2 + REGULAR.cap(size) / 2
    for row, (_, label, key) in enumerate(AXES):
        baseline = first + row * row_height
        value = f"{stats[key]:,}"
        value_w = BOLD.width(value, size)
        parts.append(text(REGULAR, label, size, left, baseline, c["muted"]))
        parts.append(text(BOLD, value, size, right - value_w, baseline, c["text"]))
        x1 = left + REGULAR.width(label, size) + 8
        x2 = right - value_w - 8
        parts.append(
            f'<line x1="{num(x1)}" y1="{num(baseline - 1)}" x2="{num(x2)}" y2="{num(baseline - 1)}" '
            f'stroke="{c["muted"]}" stroke-opacity=".6" stroke-dasharray="1 4" stroke-linecap="round"/>'
        )
    note = f"public activity · {as_of}"
    parts.append(text(REGULAR, note, 10, left, height - PAD_Y - 2, c["muted"]))

    label = (
        f"GitHub activity of {LOGIN}: {stats['commits']} commits, {stats['repos']} public repositories, "
        f"{stats['prs']} pull requests, {stats['reviews']} code reviews, {stats['issues']} issues"
    )
    return svg(width, height, "".join(parts), label)


def write(name, content):
    ASSETS.mkdir(exist_ok=True)
    (ASSETS / name).write_text(content, encoding="utf-8", newline="\n")
    print(f"wrote assets/{name}")


if __name__ == "__main__":
    stats = fetch_stats(LOGIN)
    print("stats:", stats)
    as_of = dt.date.today().strftime("%b %Y")
    for theme in THEMES:
        write(f"status-{theme}.svg", status_card(theme))
        write(f"stats-{theme}.svg", stats_card(theme, stats, as_of))
