#!/usr/bin/env python3
"""Draw a/b with the paper's style and pooled/directional c from the reviewed phase aggregation."""

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.text import Annotation
from matplotlib.transforms import Bbox
import numpy as np

FIGSIZE = (8, 6.5)
LW = 3
INK = "#222222"
COLORS = {"ntn": "#000080", "lte": "#B22222"}
STYLE = {"font.family": "DejaVu Sans", "font.size": 30, "axes.labelsize": 30, "legend.fontsize": 30,
         "xtick.labelsize": 30, "ytick.labelsize": 30, "axes.spines.top": True, "axes.spines.right": True,
         "axes.grid": False, "text.color": INK, "axes.edgecolor": INK, "axes.labelcolor": INK,
         "xtick.color": INK, "ytick.color": INK, "grid.color": "#e4e4e0", "grid.linewidth": 0.5,
         "pdf.fonttype": 42}
LEGEND = {"fontsize": 22, "handlelength": 1.5, "handletextpad": 0.4, "borderaxespad": 0.2}
DIRECTIONS = (("lte2ntn", "LTE→NTN", "++"), ("ntn2lte", "NTN→LTE", "//"))
PHASE_BAR_WIDTH = .8
PHASE_LABEL_ROTATION = 65
VERTICAL_LABEL_SIZE = 24
HORIZONTAL_LABEL_SIZE = 20
HORIZONTAL_VALUE_SIZE = 18
ACM_COLUMN_WIDTH = 3.3366
ACM_INSERT_HEIGHT = .8748
ACM_EXPORT_HEIGHT = 5.8
ACM_SUBFIGURE_FRACTION = .48
ACM_LEGEND_SIZE = 36
ACM_LEGEND_LINE_WIDTH = 5
ACM_VALUE_SIZE = 36
ACM_TOTAL_SIZE = 36
ACM_MEDIAN_SIZE = 30


def throughput(data, figsize=FIGSIZE, legend_above=False):
  values, events = np.array(data["hour_series_mbps"]), data["events"]
  rat = [events[0]["source"]] * 3600
  for row in events:
    for second in range(row["t_s"], 3600):
      rat[second] = None if second < row["t_s"] + row["gap_s"] else row["target"]
  start, end = 1800, 2400
  fig, ax = plt.subplots(figsize=figsize)
  for row in events:
    t, gap = row["t_s"], row["gap_s"]
    if gap and t < end and t + gap > start:
      ax.axvspan(max(t, start) - start, min(t + gap, end) - start, color="0.85", alpha=.4, lw=0)
  i = start
  while i < end:
    j = i
    while j < end and rat[j] == rat[i]:
      j += 1
    if rat[i] is not None:
      x = np.arange(i - 1 if i > start and rat[i - 1] is not None else i, j)
      ax.plot(x - start, values[x], color=COLORS[rat[i]], lw=LW)
    i = j
  ax.set_xlim(0, end - start)
  ax.set_xticks(np.arange(0, 601, 120))
  ax.set_ylim(-.5, 30)
  ax.set_xlabel("Time (s)")
  ax.set_ylabel("Throughput (Mbps)")
  ax.grid(True, axis="y", alpha=.3)
  legend_position = {"loc": "lower right", "bbox_to_anchor": (1, 1.02), "ncol": 3} if legend_above else {
    "loc": "upper right"}
  ax.legend(handles=[plt.Line2D([], [], color=COLORS["ntn"], lw=LW, label="On NTN"),
                     plt.Line2D([], [], color=COLORS["lte"], lw=LW, label="On LTE"),
                     Patch(color="0.85", alpha=.4, label="No coverage")], **legend_position, **LEGEND)
  return fig


def cdf(data, figsize=FIGSIZE):
  fig, ax = plt.subplots(figsize=figsize)
  ax.axhline(.5, color=".85", lw=1.5, zorder=0)
  for values, color, label, y in ((data["intervals_s"], "#00468B", "Handover interval", .04),
                                 (data["durations_s"], "#ED0000", "Handover duration", .6)):
    if not values or any(value is None for value in values):
      raise ValueError("CDF input contains missing or censored samples")
    x = np.sort(values)
    ax.step(np.r_[x[0], x], np.arange(len(x) + 1) / len(x), where="post", color=color, lw=LW,
            label=label, clip_on=False)
    median = np.median(values)
    ax.axvline(median, color=color, lw=2, ls=":")
    ax.text(median + 4, y, f"median {median:.1f} s" if median < 10 else f"median {median:.0f} s",
            color=color, fontsize=26)
  ax.set_xlim(0, 240)
  ax.set_xticks(np.arange(0, 241, 60))
  ax.set_ylim(0, 1)
  ax.set_xlabel("Time (s)")
  ax.set_ylabel("CDF")
  ax.grid(True, axis="y", alpha=.3)
  ax.legend(loc="lower right", bbox_to_anchor=(1, .12), **LEGEND)
  return fig


def phase_axes(ax, phases):
  ax.set_xticks(np.arange(len(phases)), [phase.get("vertical_label", phase["label"]) for phase in phases],
                rotation=PHASE_LABEL_ROTATION, ha="right", rotation_mode="anchor", fontsize=VERTICAL_LABEL_SIZE)
  ax.set_xlim(-.65, len(phases) - .35)
  ax.set_ylabel("Mean duration (s)", y=.47)
  ax.grid(True, axis="y", alpha=.3)
  ax.set_axisbelow(True)


def breakdown(data):
  """Means are additive; the table carries per-run values, counts and missing-boundary policy."""
  if data["statistic"] != "mean":
    raise ValueError("Phase values must be means, not sums of independent medians")
  phases, groups = data["phases"], data["groups"]
  fig, ax = plt.subplots(figsize=FIGSIZE)
  group = next(group for group in groups if group["id"] == "pooled")
  heights = [group["phase_mean_s"][phase["id"]] for phase in phases]
  x = np.arange(len(phases))
  ax.bar(x, heights, width=PHASE_BAR_WIDTH, color=[phase["color"] for phase in phases], edgecolor=INK, linewidth=LW)
  for position, height in zip(x, heights):
    ax.annotate(f"{height:.2f}", (position, height), xytext=(0, 5), textcoords="offset points",
                ha="center", va="bottom", fontsize=26)
  phase_axes(ax, phases)
  ax.set_ylim(0, max(heights) * 1.35)
  ax.text(.98, .96, f"Total {group['mean_total_s']:.2f} s", ha="right", va="top", transform=ax.transAxes, fontsize=26)
  return fig


def breakdown_directions(data):
  """Split each phase's width equally between the two measured handover directions."""
  if data["statistic"] != "mean":
    raise ValueError("Directional phase bars require the measured per-direction means")
  phases = data["phases"]
  groups = {group["id"]: group for group in data["groups"]}
  fig, ax = plt.subplots(figsize=FIGSIZE)
  x = np.arange(len(phases))
  width = PHASE_BAR_WIDTH / 2
  maximum = 0
  handles = []
  for column, (direction, label, hatch) in enumerate(DIRECTIONS):
    group = groups[direction]
    heights = [group["phase_mean_s"][phase["id"]] for phase in phases]
    positions = x + (column - .5) * width
    ax.bar(positions, heights, width=width, color=[phase["color"] for phase in phases],
           hatch=hatch, edgecolor=INK, linewidth=LW)
    for position, height in zip(positions, heights):
      # Upright columns of text fit the half-width bars without covering their neighbours.
      ax.annotate(f"{height:.3f}", (position, height), xytext=(0, 6), textcoords="offset points",
                  ha="center", va="bottom", fontsize=26, rotation=90)
    maximum = max(maximum, max(heights))
    handles.append(Patch(facecolor="white", edgecolor=INK, linewidth=LW, hatch=hatch, label=label))
  phase_axes(ax, phases)
  ax.set_ylim(0, maximum * 2.1)
  ax.legend(handles=handles, loc="upper right", ncol=2, columnspacing=.8, **LEGEND)
  return fig


def breakdown_horizontal(data, compare=False, figsize=FIGSIZE, label_size=HORIZONTAL_LABEL_SIZE):
  """Keep phases in timeline order, with optional half-height bars for each direction."""
  if data["statistic"] != "mean":
    raise ValueError("Horizontal phase bars require the measured phase means")
  phases = data["phases"]
  groups = {group["id"]: group for group in data["groups"]}
  fig, ax = plt.subplots(figsize=figsize)
  y = np.arange(len(phases))
  height = PHASE_BAR_WIDTH / 2 if compare else PHASE_BAR_WIDTH
  series = DIRECTIONS if compare else (("pooled", "All direct changes", None),)
  maximum = 0
  handles = []
  for row, (direction, label, hatch) in enumerate(series):
    group = groups[direction]
    widths = [group["phase_mean_s"][phase["id"]] for phase in phases]
    positions = y + (row - .5) * height if compare else y
    ax.barh(positions, widths, height=height, color=[phase["color"] for phase in phases],
            hatch=hatch, edgecolor=INK, linewidth=LW)
    for position, width in zip(positions, widths):
      ax.annotate(f"{width:.3f}" if compare else f"{width:.2f}", (width, position),
                  xytext=(6, 0), textcoords="offset points", ha="left", va="center",
                  fontsize=HORIZONTAL_VALUE_SIZE)
    maximum = max(maximum, max(widths))
    if compare:
      handles.append(Patch(facecolor="white", edgecolor=INK, linewidth=LW, hatch=hatch, label=label))
  ax.set_yticks(y, [" ".join(phase["label"].split()) for phase in phases], fontsize=label_size)
  ax.set_ylim(len(phases) - .35, -1.45)
  ax.set_xlim(0, maximum * 1.4)
  ax.set_xticks(np.arange(0, maximum * 1.4, 1 if maximum * 1.4 > 2.5 else .5))
  ax.set_xlabel("Mean duration (s)")
  ax.grid(True, axis="x", alpha=.3)
  ax.set_axisbelow(True)
  if compare:
    ax.legend(handles=handles, loc="upper right", ncol=2, columnspacing=.8, **LEGEND)
  else:
    ax.text(.98, .96, f"Total {groups['pooled']['mean_total_s']:.2f} s", ha="right", va="top",
            transform=ax.transAxes, fontsize=26)
  return fig


def acm_figures(data, phases):
  """Reuse the measured plots on fixed-size pages for one ACM column and two half-column panels."""
  width = ACM_COLUMN_WIDTH * ACM_EXPORT_HEIGHT / ACM_INSERT_HEIGHT
  full_size = (width, ACM_EXPORT_HEIGHT)
  panel_size = (width * ACM_SUBFIGURE_FRACTION, ACM_EXPORT_HEIGHT)
  figures = {
    "sec2-thpt": throughput(data, figsize=full_size, legend_above=True),
    "sec2-cdf": cdf(data, figsize=panel_size),
    "sec2-breakdown": breakdown_horizontal(phases, figsize=panel_size,
                                           label_size=STYLE["ytick.labelsize"]),
  }
  thpt_ax = figures["sec2-thpt"].axes[0]
  # Preserve the original ticks when reserving more height for the larger legend.
  thpt_ax.set_yticks(np.arange(0, 31, 10))
  previous_legend = thpt_ax.get_legend()
  handles = [*previous_legend.get_lines(), *previous_legend.get_patches()]
  thpt_legend = thpt_ax.legend(handles=handles, loc="lower right", bbox_to_anchor=(1, 1.02), ncol=3,
                               fontsize=ACM_LEGEND_SIZE, handlelength=1.3, handletextpad=.3,
                               columnspacing=.8, borderpad=.25, borderaxespad=.1, framealpha=1)
  for patch in thpt_legend.get_patches():
    patch.set_alpha(1)
    patch.set_edgecolor(".6")
    patch.set_linewidth(1.5)

  cdf_ax = figures["sec2-cdf"].axes[0]
  cdf_legend = cdf_ax.legend(loc="lower right", bbox_to_anchor=(.99, .015), ncol=1,
                             fontsize=ACM_LEGEND_SIZE, handlelength=.9, handletextpad=.25,
                             borderpad=.2, labelspacing=.12, borderaxespad=0, framealpha=1)
  for label in cdf_ax.texts:
    if label.get_text().startswith("median "):
      label.set_fontsize(ACM_MEDIAN_SIZE)
    if label.get_color() == "#00468B":
      label.set_position((235, .53))
      label.set_horizontalalignment("right")
  for legend in (thpt_legend, cdf_legend):
    # These are legend proxies; the measured curves retain their original line widths.
    for line in legend.get_lines():
      line.set_linewidth(ACM_LEGEND_LINE_WIDTH)
    legend.get_frame().set_linewidth(1.5)

  for label in figures["sec2-breakdown"].axes[0].texts:
    if isinstance(label, Annotation):
      label.set_fontsize(ACM_VALUE_SIZE)
      label.set_position((8, 0))
    elif label.get_text().startswith("Total "):
      label.set_fontsize(ACM_TOTAL_SIZE)
      label.set_position((.98, .98))
  for fig in figures.values():
    ax = fig.axes[0]
    ax.tick_params(axis="y", pad=8)
    ax.xaxis.labelpad = 8
    # Fit decorations inside the final page; a tight export crop would change its aspect ratio.
    fig.tight_layout(pad=.45)
  # Align outer spines after layout, using the final uncropped page coordinates.
  cdf_bounds = figures["sec2-cdf"].axes[0].get_position()
  breakdown_bounds = figures["sec2-breakdown"].axes[0].get_position()
  left = ACM_SUBFIGURE_FRACTION * cdf_bounds.x0
  right = 1 - ACM_SUBFIGURE_FRACTION + ACM_SUBFIGURE_FRACTION * breakdown_bounds.x1
  bounds = thpt_ax.get_position()
  thpt_ax.set_position((left, bounds.y0, right - left, bounds.height))
  # Keep the terminal tick inside the page now that the right margin is smaller.
  thpt_ax.get_xticklabels()[-1].set_horizontalalignment("right")
  return figures


def save_acm(data, output, phases):
  output = Path(output)
  output.mkdir(parents=True, exist_ok=True)
  with plt.rc_context({**STYLE, "savefig.bbox": None}):
    for name, fig in acm_figures(data, phases).items():
      for extension in ("pdf", "png"):
        fig.savefig(output / f"{name}.{extension}", bbox_inches=None, dpi=300,
                    facecolor="white", transparent=False)
      plt.close(fig)


def save_all(data, output, phases=None, prefix="udp", only=None):
  output = Path(output)
  output.mkdir(parents=True, exist_ok=True)
  with plt.rc_context(STYLE):
    figures = [("a-thpt", throughput(data)), ("b-cdf", cdf(data))]
    if phases is not None:
      figures.append(("c-breakdown", breakdown(phases)))
      figures.append(("c-directions", breakdown_directions(phases)))
      figures.append(("c-breakdown-horizontal", breakdown_horizontal(phases)))
      figures.append(("c-directions-horizontal", breakdown_horizontal(phases, compare=True)))
    selected = {name for name, _ in figures} if only is None else set(only)
    if selected - {name for name, _ in figures}:
      raise ValueError("Requested figure needs unavailable phase data")
    # A/b define the established export size; phase labels fit inside the same crop.
    for name, fig in figures:
      fig.tight_layout(rect=(0, 0, .98, 1) if name.startswith("c-") else None)
      fig.canvas.draw()
    boxes = [fig.get_tightbbox(fig.canvas.get_renderer()).padded(.1) for _, fig in figures]
    crop = Bbox.union(boxes[:2])
    for name, fig in figures:
      if name not in selected:
        plt.close(fig)
        continue
      for extension in ("pdf", "png"):
        fig.savefig(output / f"{prefix}-{name}.{extension}", bbox_inches=crop, dpi=150)
      plt.close(fig)


if __name__ == "__main__":
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("campaign", type=Path)
  parser.add_argument("--output", type=Path, help="Optional figure output directory")
  layout = parser.add_mutually_exclusive_group()
  layout.add_argument("--acm-layout", action="store_true",
                      help="Export sec2-thpt, sec2-cdf and sec2-breakdown for the ACM single-column layout")
  layout.add_argument("--figures", nargs="+", choices=("a-thpt", "b-cdf", "c-breakdown", "c-directions",
                                                     "c-breakdown-horizontal", "c-directions-horizontal"),
                      help="Only replace the selected figures; default: all available figures")
  args = parser.parse_args()
  analysis = json.loads((args.campaign / "tables/analysis.json").read_text())
  phases = json.loads((args.campaign / "tables/breakdown.json").read_text())
  if args.acm_layout:
    save_acm(analysis, args.output or args.campaign / "figures", phases)
  else:
    save_all(analysis, args.output or args.campaign / "figures", phases, only=args.figures)
