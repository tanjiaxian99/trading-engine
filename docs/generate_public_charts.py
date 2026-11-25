"""Generates the latency SVG embedded in the public README.

Source numbers come from NOTES.private.md's measured runs (private, gitignored) —
this script and its output are the public-facing distillation of that data, so
keep the two in sync by hand when a number here is updated.

Run: python3 docs/generate_public_charts.py
"""

import os
from xml.sax.saxutils import escape as xml_escape

# Wire-to-wire tick-to-trade per change, normal-market profile, µs. NIC hardware
# timestamps on OCI BM.Optimized3.36, pooled over 3 runs (n=357, spin n=238).
# Pre-batch is timed to the second of its two order packets.
MILESTONES = [
    ("Baseline\n(JSON L2)", 148.8, 203.4),
    ("+ Batched\nwrites", 137.5, 161.1),
    ("+ Busy-spin\nloop", 19.3, 66.9),
    ("+ SBE L1\nmarket data", 17.6, 87.0),
    ("+ Cache\nwarming", 17.3, 33.0),
    ("+ Immediate\nTCP ACK", 15.9, 28.2),
    ("+ Hot-path\ncleanup", 13.2, 25.3),
    ("+ Kernel\nbypass", 6.4, 11.7),
]

P50_COLOR_LIGHT, P50_COLOR_DARK = "#2a78d6", "#3987e5"  # categorical slot 1, blue
P99_COLOR_LIGHT, P99_COLOR_DARK = "#eb6834", "#d95926"  # categorical slot 2, orange

# Shared chrome tokens (references/palette.md "Chart chrome & ink").
SURFACE_LIGHT, SURFACE_DARK = "#fcfcfb", "#1a1a19"
INK_LIGHT, INK_DARK = "#0b0b0b", "#ffffff"
SECONDARY_LIGHT, SECONDARY_DARK = "#52514e", "#c3c2b7"
MUTED = "#898781"
GRID_LIGHT, GRID_DARK = "#e1e0d9", "#2c2c2a"
BASELINE_LIGHT, BASELINE_DARK = "#c3c2b7", "#383835"

FONT = "system-ui, -apple-system, 'Segoe UI', sans-serif"


def _style_block():
    return f"""
  <style>
    text {{ font-family: {FONT}; }}
    .bg {{ fill: {SURFACE_LIGHT}; }}
    .ink {{ fill: {INK_LIGHT}; }}
    .secondary {{ fill: {SECONDARY_LIGHT}; }}
    .muted {{ fill: {MUTED}; }}
    .grid {{ stroke: {GRID_LIGHT}; }}
    .baseline {{ stroke: {BASELINE_LIGHT}; }}
    .p50 {{ fill: {P50_COLOR_LIGHT}; }}
    .p99 {{ fill: {P99_COLOR_LIGHT}; }}
    @media (prefers-color-scheme: dark) {{
      :root:not([data-theme="light"]) .bg {{ fill: {SURFACE_DARK}; }}
      :root:not([data-theme="light"]) .ink {{ fill: {INK_DARK}; }}
      :root:not([data-theme="light"]) .secondary {{ fill: {SECONDARY_DARK}; }}
      :root:not([data-theme="light"]) .grid {{ stroke: {GRID_DARK}; }}
      :root:not([data-theme="light"]) .baseline {{ stroke: {BASELINE_DARK}; }}
      :root:not([data-theme="light"]) .p50 {{ fill: {P50_COLOR_DARK}; }}
      :root:not([data-theme="light"]) .p99 {{ fill: {P99_COLOR_DARK}; }}
    }}
    [data-theme="dark"] .bg {{ fill: {SURFACE_DARK}; }}
    [data-theme="dark"] .ink {{ fill: {INK_DARK}; }}
    [data-theme="dark"] .secondary {{ fill: {SECONDARY_DARK}; }}
    [data-theme="dark"] .grid {{ stroke: {GRID_DARK}; }}
    [data-theme="dark"] .baseline {{ stroke: {BASELINE_DARK}; }}
    [data-theme="dark"] .p50 {{ fill: {P50_COLOR_DARK}; }}
    [data-theme="dark"] .p99 {{ fill: {P99_COLOR_DARK}; }}
  </style>"""


def make_optimization_chart():
    w, h = 820, 420
    margin_left, margin_right, margin_top, margin_bottom = 52, 20, 84, 62
    plot_w = w - margin_left - margin_right
    plot_h = h - margin_top - margin_bottom

    y_max = 220
    y_ticks = [0, 50, 100, 150, 200]

    n = len(MILESTONES)
    band = plot_w / n
    pair_w = band * 0.7
    gap = pair_w * 0.06
    bar_w = (pair_w - gap) / 2

    def y_for(v):
        return margin_top + plot_h - (v / y_max) * plot_h

    svg = []
    svg.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" role="img" '
        f'aria-label="Wire-to-wire tick-to-trade dropped from 148.8 microseconds p50 and '
        f'203.4 microseconds p99 to 6.4 and 11.7 across seven changes">'
    )
    svg.append(_style_block())
    svg.append(f'<rect class="bg" x="0" y="0" width="{w}" height="{h}"/>')

    svg.append(
        f'<text class="ink" x="{margin_left}" y="26" font-size="16" '
        f'font-weight="600">Wire-to-wire tick-to-trade, by change</text>'
    )
    svg.append(
        f'<text class="secondary" x="{margin_left}" y="46" font-size="12.5">'
        f'Market data in &#8594; order out, NIC hardware timestamps &#8226; '
        f'normal-market profile, n=357 per change (busy-spin n=238)</text>'
    )

    for li, (name, cls) in enumerate((("p50", "p50"), ("p99", "p99"))):
        lx = margin_left + li * 70
        svg.append(f'<rect class="{cls}" x="{lx}" y="58" width="12" height="12" rx="2"/>')
        svg.append(f'<text class="secondary" x="{lx + 18}" y="68" font-size="11.5">{name}</text>')

    for yt in y_ticks:
        y = y_for(yt)
        svg.append(
            f'<line class="grid" x1="{margin_left}" y1="{y:.1f}" '
            f'x2="{w - margin_right}" y2="{y:.1f}" stroke-width="1"/>'
        )
        svg.append(
            f'<text class="muted" x="{margin_left - 10}" y="{y + 4:.1f}" '
            f'font-size="11" text-anchor="end">{yt}</text>'
        )
    svg.append(
        f'<text class="muted" x="{margin_left - 10}" y="{margin_top - 12}" '
        f'font-size="11" text-anchor="end">&#956;s</text>'
    )
    svg.append(
        f'<line class="baseline" x1="{margin_left}" y1="{margin_top + plot_h}" '
        f'x2="{w - margin_right}" y2="{margin_top + plot_h}" stroke-width="1.5"/>'
    )

    for i, (label, p50, p99) in enumerate(MILESTONES):
        cx = margin_left + band * i + band / 2
        flat = label.replace("\n", " ")
        for cls, x, v in (("p50", cx - gap / 2 - bar_w, p50), ("p99", cx + gap / 2, p99)):
            y = y_for(v)
            title = xml_escape(f"{flat}: {v} µs {cls}")
            svg.append(
                f'<rect class="{cls}" x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" '
                f'height="{margin_top + plot_h - y:.1f}" rx="3"><title>{title}</title></rect>'
            )
            svg.append(
                f'<text class="ink" x="{x + bar_w / 2:.1f}" y="{y - 6:.1f}" font-size="10.5" '
                f'font-weight="600" text-anchor="middle">{v:.1f}</text>'
            )
        for li, line in enumerate(label.split("\n")):
            svg.append(
                f'<text class="secondary" x="{cx:.1f}" '
                f'y="{margin_top + plot_h + 20 + li * 14:.1f}" font-size="11.5" '
                f'text-anchor="middle">{xml_escape(line)}</text>'
            )

    svg.append("</svg>")
    return "\n".join(svg)


if __name__ == "__main__":
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "latency_optimization.svg")
    with open(path, "w") as f:
        f.write(make_optimization_chart())
    print(f"wrote {path}")
