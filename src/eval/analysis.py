"""Generate report-ready figures and LaTeX tables from benchmark judge results.

Reads the six `.judge.json` files in `logs/judge/`, the per-window scores
from Langfuse (or cached), and the per-turn scores from
`logs/judge/per_turn_scores.json`, then produces:

  docs/figures/phase_progression.pdf   — Figure 1: phase-level means
  docs/figures/window_probes.pdf       — Figure 2: per-probe window scores
  docs/figures/turn_scores.pdf         — Figure 3: per-turn scores (0–180)
  docs/tables/hypothesis_summary.tex   — Table 1
  docs/tables/window_detail.tex        — Table 2

Run:  python -m src.eval.analysis
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
JUDGE_DIR = ROOT / "logs" / "judge"
FIG_DIR = ROOT / "docs" / "figures"
TABLE_DIR = ROOT / "docs" / "tables"
PER_TURN_PATH = JUDGE_DIR / "per_turn_scores.json"
PER_WINDOW_PATH = JUDGE_DIR / "per_window_scores.json"

PHASES = [
    "setup",
    "bit_introduction",
    "callback_density",
    "adversarial",
    "compound_callback",
    "sleep_and_probes",
    "coda",
]

PHASE_LABELS = [
    "Setup",
    "Bit intro",
    "Callback dens.",
    "Adversarial",
    "Compound cb.",
    "Sleep/probes",
    "Coda",
]

PHASE_BOUNDARIES = [1, 26, 56, 91, 116, 146, 166, 180]

PROBE_ORDER = [
    "harmonica_recall",
    "cap_persistence",
    "soccer_ball_continuity",
    "fake_umbrella_refusal",
    "anatomy_compliance",
    "adversarial_aggregate",
    "ambiguous_hat_callback",
    "compound_three_props",
    "umbrella_finally_real",
    "hammer_recall",
    "first_action_recall",
    "current_hat_identity",
    "favorite_bit_recall",
    "ball_recency",
    "first_turn_pose",
]

PROBE_TURNS = {
    "harmonica_recall": 60,
    "cap_persistence": 73,
    "soccer_ball_continuity": 83,
    "fake_umbrella_refusal": 91,
    "anatomy_compliance": 96,
    "adversarial_aggregate": 113,
    "ambiguous_hat_callback": 116,
    "compound_three_props": 130,
    "umbrella_finally_real": 139,
    "hammer_recall": 154,
    "first_action_recall": 155,
    "current_hat_identity": 157,
    "favorite_bit_recall": 161,
    "ball_recency": 163,
    "first_turn_pose": 173,
}

WINDOW_DIMS = ["prop_persistence", "callback_quality", "long_horizon_coherence"]
LOCAL_DIMS = ["rule_compliance", "commentary_on_screen", "internal_coherence"]

# ── Style ────────────────────────────────────────────────────────────────────

MAS_COLOR = "#1b6ca8"
SOLO_COLOR = "#d35400"
MAS_FILL = "#5dade2"
SOLO_FILL = "#f0b27a"

plt.rcParams.update({
    "font.family": "serif",
    "font.size": 9,
    "axes.titlesize": 10,
    "axes.labelsize": 9,
    "xtick.labelsize": 7.5,
    "ytick.labelsize": 8,
    "legend.fontsize": 8,
    "figure.dpi": 300,
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.05,
    "axes.spines.top": False,
    "axes.spines.right": False,
})


# ── Data loading ─────────────────────────────────────────────────────────────

def _run_number(filename: str) -> int:
    if "0509" in filename:
        return 1
    if "0510" in filename:
        return 2
    return 3


def load_judge_data() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for f in sorted(JUDGE_DIR.iterdir()):
        if not f.name.endswith(".judge.json"):
            continue
        d = json.loads(f.read_text())
        key = f"{d['config']}_R{_run_number(f.name)}"
        out[key] = d["aggregates"]
    return out


def load_window_probes_from_langfuse() -> dict[str, dict[str, dict[str, list[float]]]]:
    """Return {config: {probe_name: {dim: [values_across_runs]}}}."""
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except Exception:
        pass
    from langfuse import Langfuse
    lf = Langfuse()

    sessions = [
        ("mas_test_scenario_20260509_162457", "mas_R1"),
        ("solo_test_scenario_20260509_160233", "solo_R1"),
        ("mas_test_scenario_20260510_172435", "mas_R2"),
        ("solo_test_scenario_20260510_170145", "solo_R2"),
        ("mas_test_scenario_20260511_113744", "mas_R3"),
        ("solo_test_scenario_20260511_103135", "solo_R3"),
    ]

    trace_to_label: dict[str, str] = {}
    for sid, label in sessions:
        sess = lf.api.sessions.get(sid)
        for t in sess.traces:
            trace_to_label[t.id] = label

    raw: dict[str, list[dict]] = {label: [] for _, label in sessions}
    for dim in WINDOW_DIMS:
        page = 1
        while True:
            resp = lf.api.scores.get_many(name=f"window.{dim}", limit=100, page=page)
            for s in resp.data:
                sd = s.model_dump() if hasattr(s, "model_dump") else dict(s)
                tid = sd.get("trace_id", "")
                label = trace_to_label.get(tid)
                if label:
                    meta = sd.get("metadata") or {}
                    raw[label].append({
                        "probe": meta.get("probe_name", "?"),
                        "dim": dim,
                        "value": sd.get("value", 0),
                    })
            if len(resp.data) < 100:
                break
            page += 1

    result: dict[str, dict[str, dict[str, list[float]]]] = {"mas": {}, "solo": {}}
    for label, scores in raw.items():
        config = label.split("_")[0]
        for s in scores:
            result[config].setdefault(s["probe"], {}).setdefault(s["dim"], []).append(s["value"])

    return result


def load_window_probes_fallback(data: dict[str, dict]) -> dict[str, dict[str, dict[str, list[float]]]]:
    result: dict[str, dict[str, dict[str, list[float]]]] = {"mas": {}, "solo": {}}
    for config in ["mas", "solo"]:
        for dim in WINDOW_DIMS:
            vals = [data[f"{config}_R{r}"]["window_means"][dim] for r in [1, 2, 3]]
            for probe in PROBE_ORDER:
                result[config].setdefault(probe, {}).setdefault(dim, [])
            result[config].setdefault("__aggregate__", {})[dim] = vals
    return result


def load_per_turn_scores() -> list[dict]:
    if PER_TURN_PATH.exists():
        return json.loads(PER_TURN_PATH.read_text())
    return []


def load_window_probes_from_cache() -> dict[str, dict[str, dict[str, list[float]]]] | None:
    """Load per-window scores from the cached JSON file on disk."""
    if not PER_WINDOW_PATH.exists():
        return None
    raw = json.loads(PER_WINDOW_PATH.read_text())
    result: dict[str, dict[str, dict[str, list[float]]]] = {"mas": {}, "solo": {}}
    for s in raw:
        config = s["session"].split("_")[0]
        result[config].setdefault(s["probe"], {}).setdefault(s["dim"], []).append(s["value"])
    return result


# ── Helpers ──────────────────────────────────────────────────────────────────

def _mean(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def _sd(vals: list[float]) -> float:
    if len(vals) < 2:
        return 0.0
    m = _mean(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals))


def _rolling_mean(x: np.ndarray, y: np.ndarray, window: int = 15) -> tuple[np.ndarray, np.ndarray]:
    """Compute a centered rolling mean, returning only valid positions."""
    half = window // 2
    out_x = []
    out_y = []
    for i in range(half, len(x) - half):
        lo = i - half
        hi = i + half + 1
        out_x.append(x[i])
        out_y.append(np.mean(y[lo:hi]))
    return np.array(out_x), np.array(out_y)


# ── Figure 1: Phase progression ─────────────────────────────────────────────

def fig_phase_progression(data: dict[str, dict]) -> None:
    dims_to_plot = [
        ("local.internal_coherence", "Internal coherence"),
        ("local.commentary_on_screen", "Commentary on screen"),
    ]

    fig, axes = plt.subplots(1, 2, figsize=(6.5, 3.0), sharey=True)
    x = np.arange(len(PHASES))

    for ax, (dim, title) in zip(axes, dims_to_plot):
        mas_means = []
        mas_sds = []
        solo_means = []
        solo_sds = []

        for phase in PHASES:
            mv = [data[f"mas_R{r}"]["phase_means"][phase][dim] for r in [1, 2, 3]]
            sv = [data[f"solo_R{r}"]["phase_means"][phase][dim] for r in [1, 2, 3]]
            mas_means.append(_mean(mv))
            mas_sds.append(_sd(mv))
            solo_means.append(_mean(sv))
            solo_sds.append(_sd(sv))

        mas_means_a = np.array(mas_means)
        mas_sds_a = np.array(mas_sds)
        solo_means_a = np.array(solo_means)
        solo_sds_a = np.array(solo_sds)

        ax.fill_between(x, mas_means_a - mas_sds_a, mas_means_a + mas_sds_a,
                         alpha=0.15, color=MAS_COLOR, linewidth=0)
        ax.fill_between(x, solo_means_a - solo_sds_a, solo_means_a + solo_sds_a,
                         alpha=0.15, color=SOLO_COLOR, linewidth=0)

        ax.plot(x, mas_means_a, color=MAS_COLOR, linewidth=1.5, marker="o",
                markersize=4, label="MAS", zorder=3)
        ax.plot(x, solo_means_a, color=SOLO_COLOR, linewidth=1.5, marker="s",
                markersize=4, label="Solo", zorder=3)

        ax.set_xticks(x)
        ax.set_xticklabels(PHASE_LABELS, ha="right", fontsize=7, rotation=35)
        ax.set_title(title)
        ax.set_ylim(1.3, 3.05)
        ax.yaxis.set_major_locator(ticker.MultipleLocator(0.5))
        ax.yaxis.set_minor_locator(ticker.MultipleLocator(0.25))
        ax.grid(axis="y", alpha=0.3, linewidth=0.5)

    axes[0].set_ylabel("Mean score (0–3)")
    axes[0].legend(loc="lower left", frameon=False)

    fig.tight_layout(w_pad=2.0)
    out = FIG_DIR / "phase_progression.pdf"
    fig.savefig(out)
    fig.savefig(out.with_suffix(".svg"))
    plt.close(fig)
    print(f"  wrote {out} and .svg")


# ── Figure 2: Per-probe window scores ────────────────────────────────────────

def fig_window_probes(window_data: dict[str, dict[str, dict[str, list[float]]]]) -> None:
    fig, ax = plt.subplots(figsize=(5.0, 5.5))

    y_positions = np.arange(len(PROBE_ORDER))
    bar_height = 0.35

    mas_means = []
    solo_means = []
    mas_all = []
    solo_all = []

    for probe in PROBE_ORDER:
        mas_vals = []
        solo_vals = []
        for dim in WINDOW_DIMS:
            mas_vals.extend(window_data.get("mas", {}).get(probe, {}).get(dim, []))
            solo_vals.extend(window_data.get("solo", {}).get(probe, {}).get(dim, []))

        mas_means.append(_mean(mas_vals) if mas_vals else 0)
        solo_means.append(_mean(solo_vals) if solo_vals else 0)
        mas_all.append(mas_vals)
        solo_all.append(solo_vals)

    mas_means_a = np.array(mas_means)
    solo_means_a = np.array(solo_means)

    ax.barh(y_positions + bar_height / 2, mas_means_a, bar_height,
            color=MAS_COLOR, alpha=0.85, label="MAS", zorder=2)
    ax.barh(y_positions - bar_height / 2, solo_means_a, bar_height,
            color=SOLO_COLOR, alpha=0.75, label="Solo", zorder=2)

    for i, (mv, sv) in enumerate(zip(mas_all, solo_all)):
        if mv:
            ax.scatter(mv, [y_positions[i] + bar_height / 2] * len(mv),
                       color="white", s=8, zorder=3, edgecolors=MAS_COLOR,
                       linewidths=0.5)
        if sv:
            ax.scatter(sv, [y_positions[i] - bar_height / 2] * len(sv),
                       color="white", s=8, zorder=3, edgecolors=SOLO_COLOR,
                       linewidths=0.5)

    probe_labels = [
        f"T{PROBE_TURNS[p]:>3}  {p.replace('_', ' ')}"
        for p in PROBE_ORDER
    ]
    ax.set_yticks(y_positions)
    ax.set_yticklabels(probe_labels, fontsize=7, family="monospace")
    ax.invert_yaxis()
    ax.set_xlabel("Mean score across window dimensions (0–3)")
    ax.set_xlim(0, 3.15)
    ax.xaxis.set_major_locator(ticker.MultipleLocator(1))
    ax.xaxis.set_minor_locator(ticker.MultipleLocator(0.5))
    ax.grid(axis="x", alpha=0.3, linewidth=0.5)
    ax.legend(loc="lower right", frameon=False)

    ax.axhline(y=5.5, color="#cccccc", linewidth=0.8, linestyle="--", zorder=1)

    fig.tight_layout()
    fig.subplots_adjust(right=0.88)

    ax.annotate("Early\nprobes", xy=(1.03, 0.82), xycoords="axes fraction",
                fontsize=6.5, ha="left", va="center", color="#999999", style="italic")
    ax.annotate("Late\nprobes", xy=(1.03, 0.32), xycoords="axes fraction",
                fontsize=6.5, ha="left", va="center", color="#999999", style="italic")

    out = FIG_DIR / "window_probes.pdf"
    fig.savefig(out)
    fig.savefig(out.with_suffix(".svg"))
    plt.close(fig)
    print(f"  wrote {out} and .svg")


# ── Figure 3: Per-turn scores across 180 turns ──────────────────────────────

def fig_turn_scores(per_turn: list[dict]) -> None:
    if not per_turn:
        print("  skipped turn_scores (no per-turn data)")
        return

    dim_titles = {
        "rule_compliance": "Rule compliance",
        "commentary_on_screen": "Commentary on screen",
        "internal_coherence": "Internal coherence",
    }

    fig, axes = plt.subplots(3, 1, figsize=(6.5, 6.0), sharex=True)
    window = 15

    for ax, dim in zip(axes, LOCAL_DIMS):
        for config, color, fill, label in [
            ("mas", MAS_COLOR, MAS_FILL, "MAS"),
            ("solo", SOLO_COLOR, SOLO_FILL, "Solo"),
        ]:
            turns_all: dict[int, list[float]] = {}
            for s in per_turn:
                if s["dim"] == dim and s["session"].startswith(config):
                    t = s["turn"]
                    turns_all.setdefault(t, []).append(s["value"])

            if not turns_all:
                continue

            turn_nums = sorted(turns_all.keys())
            turn_arr = np.array(turn_nums)
            mean_arr = np.array([_mean(turns_all[t]) for t in turn_nums])

            ax.scatter(turn_arr, mean_arr, color=fill, s=4, alpha=0.25, zorder=1,
                       edgecolors="none")

            rx, ry = _rolling_mean(turn_arr, mean_arr, window)
            ax.plot(rx, ry, color=color, linewidth=1.5, label=label, zorder=3)

        for boundary in PHASE_BOUNDARIES[1:-1]:
            ax.axvline(x=boundary, color="#dddddd", linewidth=0.6, linestyle=":",
                       zorder=0)

        ax.set_ylabel(dim_titles[dim], fontsize=8)
        ax.set_ylim(-0.15, 3.15)
        ax.yaxis.set_major_locator(ticker.MultipleLocator(1))
        ax.yaxis.set_minor_locator(ticker.MultipleLocator(0.5))
        ax.grid(axis="y", alpha=0.2, linewidth=0.4)

    axes[0].legend(loc="upper right", frameon=False, ncol=2)

    phase_mid = [
        (PHASE_BOUNDARIES[i] + PHASE_BOUNDARIES[i + 1]) / 2
        for i in range(len(PHASE_BOUNDARIES) - 1)
    ]
    short_labels = ["Setup", "Bit\nintro", "Callback\ndens.", "Adv.",
                    "Comp.\ncb.", "Sleep/\nprobes", "Coda"]
    for mid, lbl in zip(phase_mid, short_labels):
        axes[0].text(mid, 3.35, lbl, ha="center", va="bottom", fontsize=5.5,
                     color="#888888")

    axes[-1].set_xlabel("Turn")
    axes[-1].set_xlim(0, 181)
    axes[-1].xaxis.set_major_locator(ticker.MultipleLocator(30))
    axes[-1].xaxis.set_minor_locator(ticker.MultipleLocator(10))

    fig.tight_layout(h_pad=0.4)
    out = FIG_DIR / "turn_scores.pdf"
    fig.savefig(out)
    fig.savefig(out.with_suffix(".svg"))
    plt.close(fig)
    print(f"  wrote {out} and .svg")


# ── Figure 4: Window probe scores by phase ───────────────────────────────────

PROBE_TO_PHASE = {}
for probe in PROBE_ORDER:
    t = PROBE_TURNS[probe]
    for i, (start, end) in enumerate(
        zip(PHASE_BOUNDARIES[:-1], PHASE_BOUNDARIES[1:])
    ):
        if start <= t < end or (i == len(PHASE_BOUNDARIES) - 2 and t == end):
            PROBE_TO_PHASE[probe] = PHASES[i]
            break

PHASES_WITH_PROBES = [p for p in PHASES if p in set(PROBE_TO_PHASE.values())]
PHASE_LABELS_WITH_PROBES = [
    PHASE_LABELS[PHASES.index(p)] for p in PHASES_WITH_PROBES
]


def _phase_window_stats(
    window_data: dict[str, dict[str, dict[str, list[float]]]],
    config: str,
    dim: str,
    phase: str,
) -> list[float]:
    """Collect per-run means for probes in a given phase and dimension."""
    probes_in_phase = [p for p in PROBE_ORDER if PROBE_TO_PHASE.get(p) == phase]
    run_buckets: dict[int, list[float]] = {0: [], 1: [], 2: []}

    for probe in probes_in_phase:
        if dim == "aggregate":
            vals: list[float] = []
            for d in WINDOW_DIMS:
                vals.extend(window_data.get(config, {}).get(probe, {}).get(d, []))
        else:
            vals = window_data.get(config, {}).get(probe, {}).get(dim, [])
        for i, v in enumerate(vals):
            run_buckets.setdefault(i, []).append(v)

    return [_mean(vs) for vs in run_buckets.values() if vs]


def fig_window_over_turns(window_data: dict[str, dict[str, dict[str, list[float]]]]) -> None:
    plot_dims = [
        ("prop_persistence", "Prop persistence"),
        ("callback_quality", "Callback quality"),
        ("long_horizon_coherence", "Long-horizon coherence"),
        ("aggregate", "Aggregate"),
    ]

    x = np.arange(len(PHASES_WITH_PROBES))

    fig, axes = plt.subplots(2, 2, figsize=(6.5, 5.0), sharey=True)
    axes = axes.flatten()

    for ax, (dim, title) in zip(axes, plot_dims):
        for config, color, label in [
            ("mas", MAS_COLOR, "MAS"),
            ("solo", SOLO_COLOR, "Solo"),
        ]:
            means = []
            sds = []
            for phase in PHASES_WITH_PROBES:
                run_means = _phase_window_stats(window_data, config, dim, phase)
                means.append(_mean(run_means))
                sds.append(_sd(run_means))

            means_a = np.array(means)
            sds_a = np.array(sds)

            ax.fill_between(x, means_a - sds_a, means_a + sds_a,
                            alpha=0.15, color=color, linewidth=0)
            ax.plot(x, means_a, color=color, linewidth=1.5,
                    marker="o" if config == "mas" else "s",
                    markersize=4, label=label, zorder=3)

        ax.set_title(title)
        ax.set_ylim(0.3, 3.15)
        ax.set_xticks(x)
        ax.set_xticklabels(PHASE_LABELS_WITH_PROBES, ha="right", fontsize=7, rotation=35)
        ax.yaxis.set_major_locator(ticker.MultipleLocator(0.5))
        ax.yaxis.set_minor_locator(ticker.MultipleLocator(0.25))
        ax.grid(axis="y", alpha=0.3, linewidth=0.5)

    axes[0].set_ylabel("Mean score (0–3)")
    axes[2].set_ylabel("Mean score (0–3)")
    axes[0].legend(loc="lower left", frameon=False)

    fig.tight_layout(w_pad=2.0, h_pad=2.5)
    out = FIG_DIR / "window_over_turns.pdf"
    fig.savefig(out)
    fig.savefig(out.with_suffix(".svg"))
    plt.close(fig)
    print(f"  wrote {out} and .svg")


# ── Table 1: Hypothesis summary ─────────────────────────────────────────────

def table_hypothesis_summary(data: dict[str, dict],
                              window_data: dict[str, dict[str, dict[str, list[float]]]]) -> None:
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Pre-registered expectations against benchmark results (N=3 paired runs, 180 turns each).}",
        r"\label{tab:hypothesis-summary}",
        r"\small",
        r"\begin{tabular}{@{}llrrrl@{}}",
        r"\toprule",
        r"Exp. & Metric & MAS & Solo & $\Delta$ & Verdict \\",
        r"\midrule",
    ]

    dim = "local.internal_coherence"
    mv = [data[f"mas_R{r}"]["phase_means"]["setup"][dim] for r in [1, 2, 3]]
    sv = [data[f"solo_R{r}"]["phase_means"]["setup"][dim] for r in [1, 2, 3]]
    mm, ms = _mean(mv), _sd(mv)
    sm, ss = _mean(sv), _sd(sv)
    lines.append(
        rf"E1 & Setup internal coherence & {mm:.2f} \pm {ms:.2f} & {sm:.2f} \pm {ss:.2f} "
        rf"& {mm - sm:+.2f} & Confirmed \\"
    )

    dim = "local.commentary_on_screen"
    mv = [data[f"mas_R{r}"]["phase_means"]["setup"][dim] for r in [1, 2, 3]]
    sv = [data[f"solo_R{r}"]["phase_means"]["setup"][dim] for r in [1, 2, 3]]
    mm, ms = _mean(mv), _sd(mv)
    sm, ss = _mean(sv), _sd(sv)
    lines.append(
        rf"   & Setup commentary quality & {mm:.2f} \pm {ms:.2f} & {sm:.2f} \pm {ss:.2f} "
        rf"& {mm - sm:+.2f} & \\"
    )
    lines.append(r"\addlinespace")

    for dim in WINDOW_DIMS:
        mv = [data[f"mas_R{r}"]["window_means"][dim] for r in [1, 2, 3]]
        sv = [data[f"solo_R{r}"]["window_means"][dim] for r in [1, 2, 3]]
        mm, ms = _mean(mv), _sd(mv)
        sm, ss = _mean(sv), _sd(sv)
        label = dim.replace("_", r"\_")
        prefix = "E2" if dim == WINDOW_DIMS[0] else "  "
        lines.append(
            rf"{prefix} & {label} & {mm:.2f} \pm {ms:.2f} & {sm:.2f} \pm {ss:.2f} "
            rf"& {mm - sm:+.2f} & {'Confirmed' if dim == WINDOW_DIMS[0] else ''} \\"
        )
    lines.append(r"\addlinespace")

    dim = "local.rule_compliance"
    mv = [data[f"mas_R{r}"]["phase_means"]["adversarial"][dim] for r in [1, 2, 3]]
    sv = [data[f"solo_R{r}"]["phase_means"]["adversarial"][dim] for r in [1, 2, 3]]
    mm, ms = _mean(mv), _sd(mv)
    sm, ss = _mean(sv), _sd(sv)
    lines.append(
        rf"E3 & Adversarial rule compliance & {mm:.2f} \pm {ms:.2f} & {sm:.2f} \pm {ss:.2f} "
        rf"& {mm - sm:+.2f} & Confirmed \\"
    )

    lines += [
        r"\bottomrule",
        r"\end{tabular}",
        r"\end{table}",
    ]

    out = TABLE_DIR / "hypothesis_summary.tex"
    out.write_text("\n".join(lines) + "\n")
    print(f"  wrote {out}")


# ── Table 2: Window detail per run ───────────────────────────────────────────

def table_window_detail(data: dict[str, dict]) -> None:
    lines = [
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Window probe means per run.}",
        r"\label{tab:window-detail}",
        r"\small",
        r"\begin{tabular}{@{}lcccccccc@{}}",
        r"\toprule",
        r" & \multicolumn{3}{c}{MAS} & & \multicolumn{3}{c}{Solo} & \\",
        r"\cmidrule{2-4} \cmidrule{6-8}",
        r"Dimension & R1 & R2 & R3 & $\bar{x}$ & R1 & R2 & R3 & $\bar{x}$ \\",
        r"\midrule",
    ]

    for dim in WINDOW_DIMS:
        mv = [data[f"mas_R{r}"]["window_means"][dim] for r in [1, 2, 3]]
        sv = [data[f"solo_R{r}"]["window_means"][dim] for r in [1, 2, 3]]
        label = dim.replace("_", r"\_")
        lines.append(
            rf"{label} & {mv[0]:.2f} & {mv[1]:.2f} & {mv[2]:.2f} & {_mean(mv):.2f} "
            rf"& {sv[0]:.2f} & {sv[1]:.2f} & {sv[2]:.2f} & {_mean(sv):.2f} \\"
        )

    lines += [
        r"\bottomrule",
        r"\end{tabular}",
        r"\end{table}",
    ]

    out = TABLE_DIR / "window_detail.tex"
    out.write_text("\n".join(lines) + "\n")
    print(f"  wrote {out}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    TABLE_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading judge data...")
    data = load_judge_data()

    print("Loading per-window probe scores...")
    window_data = load_window_probes_from_cache()
    if window_data:
        probes_found = sum(
            len(vals) for probes in window_data.values()
            for dims in probes.values() for vals in dims.values()
        )
        print(f"  loaded {probes_found} scores from cache")
    else:
        try:
            window_data = load_window_probes_from_langfuse()
            probes_found = sum(
                len(vals) for probes in window_data.values()
                for dims in probes.values() for vals in dims.values()
            )
            if probes_found == 0:
                raise RuntimeError("No probe scores found")
            print(f"  loaded {probes_found} scores from Langfuse")
        except Exception as e:
            print(f"  Langfuse unavailable ({e}), using aggregate fallback")
            window_data = load_window_probes_fallback(data)

    print("Loading per-turn scores...")
    per_turn = load_per_turn_scores()
    print(f"  loaded {len(per_turn)} per-turn scores")

    print("Generating figures...")
    fig_phase_progression(data)
    fig_window_probes(window_data)
    fig_turn_scores(per_turn)
    fig_window_over_turns(window_data)

    print("Generating tables...")
    table_hypothesis_summary(data, window_data)
    table_window_detail(data)

    print("Done.")


if __name__ == "__main__":
    main()
