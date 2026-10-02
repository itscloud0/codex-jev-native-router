#!/usr/bin/env python3
"""Render the public aggregate-only Shadow evidence SVG."""
import argparse
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "assets" / "shadow-evidence.json"
OUTPUT = ROOT / "assets" / "shadow-evidence.svg"
W, H = 1200, 580


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate(data):
    sample = data["sample"]
    proposals = sample["jev_backed_proposals"]
    counts = [sample[key] for key in ("shadow_decisions", "jev_backed_proposals",
              "cache_held_luna_proposals", "linked_usage_records", "cache_detail_records",
              "validated_full_turn_records")]
    for group in ("model_proposals", "effort_vs_actual", "proposed_efforts", "native_turn_scope"):
        counts.extend(sample[group].values())
    require(all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in counts), "nonnegative integer counts")
    require(0 < proposals <= sample["shadow_decisions"], "proposal range")
    require(sample["linked_usage_records"] <= sample["shadow_decisions"], "linked usage range")
    require(sum(sample["model_proposals"].values()) == proposals, "model totals")
    require(sum(sample["effort_vs_actual"].values()) == proposals, "effort comparison totals")
    require(sum(sample["proposed_efforts"].values()) == proposals, "proposed effort totals")
    require(sample["cache_held_luna_proposals"] <= sample["model_proposals"]["Luna"], "held Luna count")
    require(sample["cache_detail_records"] <= sample["linked_usage_records"], "cache coverage")
    require(sample["validated_full_turn_records"] <= sample["linked_usage_records"], "full-turn coverage")
    require(sum(sample["native_turn_scope"].values()) == sample["linked_usage_records"], "scope totals")


def bar(label, value, total, y, color, label_at, bar_at):
    width = round(250 * value / total, 1)
    return f'''<text x="{label_at}" y="{y + 20}" class="label">{html.escape(label)}</text>
<rect x="{bar_at}" y="{y}" width="250" height="28" rx="7" class="track"/>
<rect x="{bar_at}" y="{y}" width="{width}" height="28" rx="7" fill="{color}"/>
<text x="{bar_at + 264}" y="{y + 20}" class="value">{value} <tspan class="muted">/ {total}</tspan></text>'''


def render(data):
    validate(data)
    source, sample = data["source"], data["sample"]
    proposed = sample["jev_backed_proposals"]
    models = "\n".join(bar(name, count, proposed, 253 + i * 47, color, 82, 186)
                       for i, (name, count, color) in enumerate((("Sol", sample["model_proposals"]["Sol"], "#6ee7d8"), ("Luna", sample["model_proposals"]["Luna"], "#b9a8ff"))))
    efforts = "\n".join(bar(name, count, proposed, 253 + i * 47, color, 640, 744)
                        for i, (name, count, color) in enumerate((("Lower", sample["effort_vs_actual"]["Lower"], "#6ee7d8"), ("Same", sample["effort_vs_actual"]["Same"], "#8a9dbb"), ("Higher", sample["effort_vs_actual"]["Higher"], "#b9a8ff"))))
    limits = data["limitations"]
    snapshot = source["observed_at"][:16].replace("T", " ") + " UTC"
    models_count = sample["model_proposals"]
    efforts_count = sample["effort_vs_actual"]
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-labelledby="title desc">
<title id="title">Effortlane Shadow proposal evidence</title>
<desc id="desc">A {sample["shadow_decisions"]}-decision Shadow observation from {source["decision_period"]}. Of {proposed} Jev-backed proposals, Sol was proposed {models_count["Sol"]} times and Luna {models_count["Luna"]} times. Proposed effort was lower {efforts_count["Lower"]} times, the same {efforts_count["Same"]} times, and higher {efforts_count["Higher"]} times than actual selection. Alternatives were not executed, so savings and quality are not demonstrated.</desc>
<defs><linearGradient id="bg" x2="1" y2="1"><stop stop-color="#081525"/><stop offset="1" stop-color="#111e39"/></linearGradient></defs>
<rect width="1200" height="580" rx="28" fill="url(#bg)"/><rect x="36" y="32" width="1128" height="516" rx="20" fill="#0d1b31" stroke="#24405b"/>
<style>.title{{font-size:34px;font-weight:700;font-family:Arial,Helvetica,sans-serif;fill:#f4f7fb}}.sub{{font-size:16px;font-weight:500;font-family:Arial,Helvetica,sans-serif;fill:#a8b7cc}}.kicker{{font-size:13px;font-weight:700;font-family:Arial,Helvetica,sans-serif;letter-spacing:1.4px;fill:#6ee7d8}}.panel{{fill:#10233d;stroke:#294765}}.heading{{font-size:20px;font-weight:700;font-family:Arial,Helvetica,sans-serif;fill:#f4f7fb}}.label{{font-size:16px;font-weight:600;font-family:Arial,Helvetica,sans-serif;fill:#d9e4f2}}.value{{font-size:17px;font-weight:700;font-family:Arial,Helvetica,sans-serif;fill:#f4f7fb}}.muted{{font-weight:500;fill:#9bb0c8}}.track{{fill:#203853}}.note{{font-size:14px;font-weight:500;font-family:Arial,Helvetica,sans-serif;fill:#a8b7cc}}</style>
<text x="58" y="74" class="kicker">EFFORTLANE · PRODUCT EVIDENCE</text><text x="58" y="114" class="title">What Effortlane Shadow recommended</text>
<text x="58" y="142" class="sub">Observed {source["decision_period"]} · snapshot {snapshot} · one Mac</text>
<rect x="58" y="164" width="526" height="205" rx="14" class="panel"/><text x="82" y="207" class="heading">Model proposals</text><text x="82" y="232" class="sub">{proposed} Jev-backed proposals · {sample["cache_held_luna_proposals"]} Luna held by cache guard</text>{models}
<rect x="616" y="164" width="526" height="252" rx="14" class="panel"/><text x="640" y="207" class="heading">Effort vs. selected actual baseline</text><text x="640" y="232" class="sub">Each bar starts at zero · {proposed} proposals</text>{efforts}
<path d="M58 448H1142" stroke="#294765"/><text x="58" y="480" class="sub">{sample["shadow_decisions"]} Shadow decisions · {sample["linked_usage_records"]} linked usage records · {sample["cache_detail_records"]} with cached-detail records · median Jev {sample["median_jev_latency_ms"]} ms</text>
<text x="58" y="514" class="note">{html.escape(limits[0])} {html.escape(limits[1])}</text>
<text x="58" y="535" class="note">{html.escape(limits[2])}</text></svg>\n'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if SVG is not current")
    args = parser.parse_args()
    rendered = render(json.loads(DATA.read_text()))
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text() != rendered:
            raise SystemExit("shadow-evidence.svg is out of date; run scripts/render_evidence.py")
        return
    OUTPUT.write_text(rendered)


if __name__ == "__main__":
    main()
