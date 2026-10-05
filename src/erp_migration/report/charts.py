"""Two small inline SVG charts for the static report (no JavaScript).

Colours are CSS custom properties defined in the page, so light and dark mode
swap in one place. Hover text uses SVG <title>, which browsers show natively.
"""

from __future__ import annotations

from html import escape


def _bar_path(x: float, y: float, w: float, h: float, r: float = 4.0) -> str:
    """Bar growing right from x, square at the baseline, rounded at the data end."""
    if w <= r:
        return f"M{x:.1f},{y:.1f}h{w:.1f}v{h:.1f}h{-w:.1f}z"
    return (
        f"M{x:.1f},{y:.1f}h{w - r:.1f}a{r},{r} 0 0 1 {r},{r}v{h - 2 * r:.1f}"
        f"a{r},{r} 0 0 1 {-r},{r}h{-(w - r):.1f}z"
    )


def bar_chart(rows: list[tuple[str, int]], label: str) -> str:
    """Horizontal bars, one series, value at the tip."""
    if not rows:
        return ""
    width, left, right, row_h, bar_h = 640, 190, 56, 26, 14
    height = row_h * len(rows) + 8
    top = max(v for _, v in rows) or 1
    scale = (width - left - right) / top
    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{escape(label)}">'
    ]
    for i, (name, value) in enumerate(rows):
        y = 4 + i * row_h
        w = max(value * scale, 1.0)
        parts.append(
            f"<g><title>{escape(name)}: {value:,}</title>"
            f'<text class="axis-label" x="{left - 8}" y="{y + bar_h - 2}" text-anchor="end">'
            f"{escape(name)}</text>"
            f'<path class="bar" d="{_bar_path(left, y, w, bar_h)}"/>'
            f'<text class="value" x="{left + w + 6:.1f}" y="{y + bar_h - 2}">{value:,}</text></g>'
        )
    parts.append(f'<line class="baseline" x1="{left}" y1="0" x2="{left}" y2="{height}"/>')
    parts.append("</svg>")
    return "".join(parts)


def sweep_chart(points: list[tuple[int, float, float]], chosen: int, label: str) -> str:
    """Precision and recall against the auto-merge threshold, one shared 0-1 axis."""
    width, height = 640, 300
    left, right, top, bottom = 44, 92, 34, 36
    xs = [p[0] for p in points]
    x0, x1 = min(xs), max(xs)

    def px(t: float) -> float:
        return left + (t - x0) / (x1 - x0) * (width - left - right)

    def py(v: float) -> float:
        return top + (1 - v) * (height - top - bottom)

    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{escape(label)}">'
    ]
    for tick in (0, 0.25, 0.5, 0.75, 1.0):
        parts.append(
            f'<line class="grid" x1="{left}" x2="{width - right}" '
            f'y1="{py(tick):.1f}" y2="{py(tick):.1f}"/>'
            f'<text class="axis-label" x="{left - 6}" y="{py(tick) + 4:.1f}" '
            f'text-anchor="end">{tick:.2f}</text>'
        )
    for t in range(x0, x1 + 1, 5):
        parts.append(
            f'<text class="axis-label" x="{px(t):.1f}" y="{height - bottom + 18}" '
            f'text-anchor="middle">{t}</text>'
        )
    parts.append(
        f'<text class="axis-label" x="{(left + width - right) / 2:.1f}" '
        f'y="{height - 4}" text-anchor="middle">auto-merge threshold</text>'
    )
    parts.append(
        f'<line class="marker" x1="{px(chosen):.1f}" x2="{px(chosen):.1f}" '
        f'y1="{top - 6}" y2="{height - bottom}"/>'
        f'<text class="value" x="{px(chosen) + 5:.1f}" y="{top - 10}">'
        f"chosen: {chosen}</text>"
    )
    for idx, (cls, name) in enumerate((("s1", "Precision"), ("s2", "Recall"))):
        coords = " ".join(f"{px(p[0]):.1f},{py(p[idx + 1]):.1f}" for p in points)
        last = points[-1]
        lx, ly = px(last[0]), py(last[idx + 1])
        parts.append(f'<polyline class="line {cls}" points="{coords}"/>')
        parts.append(f'<circle class="dot {cls}" cx="{lx:.1f}" cy="{ly:.1f}" r="4"/>')
        offset = -8 if idx == 0 and abs(py(last[1]) - py(last[2])) < 14 else 4
        parts.append(f'<text class="value" x="{lx + 10:.1f}" y="{ly + offset:.1f}">{name}</text>')
    step = (width - left - right) / (x1 - x0)
    for t, p, r in points:
        parts.append(
            f'<rect class="hit" x="{px(t) - step / 2:.1f}" y="{top}" width="{step:.1f}" '
            f'height="{height - top - bottom}"><title>threshold {t}: precision {p:.3f}, '
            f"recall {r:.3f}</title></rect>"
        )
    parts.append("</svg>")
    return "".join(parts)
