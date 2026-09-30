"""Render the SVG cards for the finler6 profile README.

    GH_TOKEN=... python3 scripts/render.py path/to/cascadia/ttf/static

Writes assets/status-{light,dark}.svg (a `systemctl status` card) and
assets/stats-{light,dark}.svg (public GitHub activity), both from the GraphQL API.
The GitHub Actions workflow in .github/workflows/stats.yml runs this daily, so
the uptime on the status card and the numbers on the stats card stay current.

Text is converted to outlines with fontTools, so the cards look the same no
matter which fonts the viewer has. Font: Cascadia Mono 2407.24,
SIL Open Font License 1.1 (https://github.com/microsoft/cascadia-code).
"""

import datetime as dt
import html
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
    label = html.escape(label)
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

# Both cards share one width, counted in terminal columns. The Active line is the
# longest one and grows with the uptime; 74 columns fit "10 years 11 months ago".
COLUMNS = 74

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

DOT_CSS = (
    ".dot{animation:dot 2.4s ease-in-out infinite}"
    "@keyframes dot{50%{opacity:.35}}"
    "@media (prefers-reduced-motion:reduce){.dot{animation:none}}"
)


def ago(start, now):
    """Elapsed time the way systemd prints it, e.g. "6 years 8 months ago"."""
    # systemd's USEC_PER_YEAR, USEC_PER_MONTH and USEC_PER_DAY, in seconds.
    year, month, day = 31_557_600, 2_629_800, 86_400
    seconds = int((now - start).total_seconds())

    def unit(value, name):
        return f"{value} {name}{'' if value == 1 else 's'}"

    if seconds >= year:
        return f"{unit(seconds // year, 'year')} {unit(seconds % year // month, 'month')} ago"
    return f"{unit(seconds // month, 'month')} {unit(seconds % month // day, 'day')} ago"


def status_lines(profile, now):
    """Card text: one list of (text, colour key, bold) per line.

    "DOT" is the status bullet, drawn as a circle so it can breathe like the
    indicator of a running unit. The service "started" when the account was
    created, and the PID is the account's numeric GitHub ID.
    """
    started = profile["created"]
    since = f"since {WEEKDAYS[started.weekday()]} {started:%Y-%m-%d}; {ago(started, now)}"
    return [
        [("$ ", "muted", False), (f"systemctl status {LOGIN}", "text", False)],
        [("DOT", "green", False), (f"{LOGIN}.service", "text", True), (" - Gleb", "text", False)],
        [("     Loaded: ", "muted", False), (f"loaded (/etc/systemd/system/{LOGIN}.service; enabled)", "text", False)],
        [("     Active: ", "muted", False), ("active (running)", "green", True), (f" {since}", "text", False)],
        [("   Main PID: ", "muted", False), (f"{profile['pid']} ({LOGIN})", "text", False)],
        [("      Tasks: ", "muted", False), ("3 (C, C#, Python)", "text", False)],
    ]


def cell():
    return REGULAR.width(" ", STATUS_SIZE)


def columns(line):
    return sum(2 if value == "DOT" else len(value) for value, _, _ in line)


def card_width(lines):
    return PAD_X * 2 + max(COLUMNS, *(columns(line) for line in lines)) * cell()


def status_card(theme, lines, width):
    c = THEMES[theme]
    cap = REGULAR.cap(STATUS_SIZE)
    height = PAD_Y * 2 + STATUS_LINE * (len(lines) - 1) + cap + 4
    parts = [card(width, height, c)]
    for row, line in enumerate(lines):
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
    label = ". ".join(" ".join("".join(v for v, _, _ in line if v != "DOT").split()) for line in lines)
    return svg(width, height, "".join(parts), f"Terminal output: {label}", DOT_CSS)


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


def fetch_profile(login, now):
    user = graphql(
        "query($login: String!) { user(login: $login) { databaseId createdAt "
        "repositories(ownerAffiliations: OWNER, privacy: PUBLIC) { totalCount } } }",
        login=login,
    )["user"]
    # fromisoformat() only accepts a trailing "Z" from Python 3.11 on.
    created = dt.datetime.fromisoformat(user["createdAt"].replace("Z", "+00:00"))
    stats = dict(commits=0, prs=0, reviews=0, issues=0, repos=user["repositories"]["totalCount"])
    # The API only returns up to one year per request, so walk calendar years.
    for year in range(created.year, now.year + 1):
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
    return dict(pid=user["databaseId"], created=created, stats=stats)


def spoke(value):
    """Log scale so a few PRs still show up next to hundreds of commits."""
    return max(0.04, min(1.0, math.log10(value + 1) / math.log10(1001)))


def stats_card(theme, stats, as_of, width):
    c = THEMES[theme]
    height = 204
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
    now = dt.datetime.now(dt.timezone.utc)
    profile = fetch_profile(LOGIN, now)
    print("stats:", profile["stats"])
    print("uptime:", ago(profile["created"], now))
    lines = status_lines(profile, now)
    width = card_width(lines)
    as_of = now.strftime("%b %Y")
    for theme in THEMES:
        write(f"status-{theme}.svg", status_card(theme, lines, width))
        write(f"stats-{theme}.svg", stats_card(theme, profile["stats"], as_of, width))
