#!/usr/bin/env python3
"""
build_dashboard.py - generate a single self-contained HTML dashboard for a run.

Why a separate file instead of extra tabs inside `report.html`: that report is
produced by `pytest-html`, which has no public hook for adding tabs. Fighting it
would mean monkey-patching someone else's HTML. Producing our own page next to it
is simpler, survives a `pytest-html` upgrade, and lets the layout show the plan
and the risks as first-class content rather than as attachments.

The page is **self-contained on purpose**: CSS and JavaScript are inlined and no
CDN is referenced. The evidence has to be readable from an artifact downloaded
offline, from a Release, or attached to an e-mail, with no network access.

Inputs (all optional - a missing file is shown as "not available", never as an
empty section that pretends to be data):

    --cases      cases.json          scenarios from the unified plan
    --risks      risks.json          risks with consequence and mitigation
    --coverage   coverage.json       the model's coverage estimate
    --manifest   manifest.json       provenance: model, prompt hash, tokens
    --junit      junit.xml           per-test result
    --suggestions ai_fix_suggestions.md
    --iflow      the iFlow name, for the header
    --output     where to write the page

Exit codes:
    0  page written
    2  usage error (no output path, or unwritable)
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


class DashboardError(Exception):
    """The page could not be produced."""


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _load_json(path: Path | None):
    """Return the parsed document, or None when the file is absent or invalid."""
    if path is None or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _load_text(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def load_junit(path: Path | None) -> dict | None:
    """Per-test results, or None when there is no JUnit file."""
    if path is None or not path.is_file():
        return None
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return None

    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    cases = []
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "time": 0.0}

    for suite in suites:
        for key in ("tests", "failures", "errors", "skipped"):
            totals[key] += int(suite.get(key, 0) or 0)
        try:
            totals["time"] += float(suite.get("time", 0) or 0)
        except ValueError:
            pass

        for case in suite.iter("testcase"):
            status = "passed"
            detail = ""
            node = case.find("failure")
            if node is None:
                node = case.find("error")
            if node is not None:
                status = "failed" if case.find("failure") is not None else "error"
                message = node.get("message") or ""
                body = (node.text or "").strip()
                # The `message` attribute is a one-liner; the body carries the
                # assertion and the traceback, which is what a reviewer reads.
                detail = "\n".join(part for part in (message, body) if part)[:2000]
            elif case.find("skipped") is not None:
                status = "skipped"

            cases.append(
                {
                    "name": case.get("name", "?"),
                    "classname": case.get("classname", ""),
                    "status": status,
                    "message": detail,
                }
            )

    return {**totals, "cases": cases}


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _esc(value) -> str:
    return html.escape(str(value)) if value is not None else ""


def _kv_rows(pairs: list[tuple[str, object]]) -> str:
    rows = []
    for label, value in pairs:
        if value is None or value == "":
            continue
        rows.append(
            f'<div class="kv"><span class="k">{_esc(label)}</span>'
            f'<span class="v">{_esc(value)}</span></div>'
        )
    return "".join(rows) or '<p class="muted">Not available.</p>'


def _render_scenarios(cases) -> str:
    if not isinstance(cases, list) or not cases:
        return '<p class="muted">No scenarios recorded in cases.json.</p>'

    cards = []
    for index, item in enumerate(cases, start=1):
        if not isinstance(item, dict):
            cards.append(f'<article class="card"><p>{_esc(item)}</p></article>')
            continue

        identifier = item.get("id") or item.get("ID") or f"#{index}"
        name = item.get("name") or item.get("title") or "(unnamed scenario)"
        fields = [
            ("Trigger", item.get("trigger")),
            ("Expected outcome", item.get("expected_outcome")),
            ("Validates", item.get("validates")),
        ]
        body = "".join(
            f'<div class="field"><span class="k">{_esc(label)}</span>'
            f'<p>{_esc(value)}</p></div>'
            for label, value in fields
            if value
        )
        cards.append(
            f'<article class="card"><header><span class="badge">{_esc(identifier)}</span>'
            f"<h3>{_esc(name)}</h3></header>{body}</article>"
        )

    return f'<div class="grid">{"".join(cards)}</div>'


def _render_risks(risks) -> str:
    if not isinstance(risks, list) or not risks:
        return '<p class="muted">No risks recorded in risks.json.</p>'

    rows = []
    for item in risks:
        if not isinstance(item, dict):
            rows.append(f"<tr><td colspan='3'>{_esc(item)}</td></tr>")
            continue
        risk = item.get("risk") or item.get("risco") or ""
        consequence = item.get("consequence") or item.get("consequencia") or item.get("impact") or ""
        mitigation = item.get("mitigation") or item.get("mitigacao") or ""
        rows.append(
            f"<tr><td class='strong'>{_esc(risk)}</td>"
            f"<td>{_esc(consequence)}</td><td>{_esc(mitigation)}</td></tr>"
        )

    return (
        "<table class='table'><thead><tr><th>Risk</th><th>Consequence</th>"
        f"<th>Mitigation</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


def _render_coverage(coverage) -> str:
    if not isinstance(coverage, dict) or not coverage:
        return '<p class="muted">No coverage analysis recorded.</p>'

    target = coverage.get("target_coverage_percent")
    by_component = coverage.get("coverage_by_component") or {}
    gaps = coverage.get("uncovered_paths") or []
    recommendations = coverage.get("coverage_recommendations") or []

    bars = []
    if isinstance(by_component, dict):
        for name, value in by_component.items():
            try:
                percent = float(str(value).replace("%", "").strip())
            except (TypeError, ValueError):
                percent = 0.0
            percent = max(0.0, min(100.0, percent))
            bars.append(
                f'<div class="bar-row"><span class="bar-label">{_esc(name)}</span>'
                f'<span class="bar"><i style="width:{percent:.1f}%"></i></span>'
                f'<span class="bar-value">{percent:.0f}%</span></div>'
            )

    def _list(title: str, items) -> str:
        if not items:
            return ""
        entries = "".join(f"<li>{_esc(entry)}</li>" for entry in items)
        return f"<h4>{_esc(title)}</h4><ul>{entries}</ul>"

    return (
        f'<div class="callout">Target coverage from the model: '
        f"<strong>{_esc(target) if target is not None else 'not stated'}%</strong>. "
        "This is an estimate produced by the language model, not a measured value; "
        "measured coverage would come from <code>pytest-cov</code>.</div>"
        f'<div class="bars">{"".join(bars) or "<p class=\'muted\'>No per-component breakdown.</p>"}</div>'
        f"{_list('Uncovered paths', gaps)}"
        f"{_list('Recommendations', recommendations)}"
    )


def _render_suite(title: str, junit: dict | None, note: str) -> str:
    """One suite: its counters and its cases."""
    if not junit or not junit["tests"]:
        return (
            f"<h4>{_esc(title)}</h4>"
            f'<p class="muted">No report for this suite in the run.</p>'
        )

    passed = junit["tests"] - junit["failures"] - junit["errors"] - junit["skipped"]
    rows = []
    for case in junit["cases"]:
        status = case["status"]
        detail = f'<div class="failure">{_esc(case["message"])}</div>' if case["message"] else ""
        label = f"{case['classname']}::{case['name']}" if case["classname"] else case["name"]
        rows.append(
            f"<tr class='{status}'><td><span class='dot {status}'></span>{_esc(label)}</td>"
            f"<td class='status'>{_esc(status)}</td></tr>"
            f"<tr class='detail'><td colspan='2'>{detail}</td></tr>"
        )

    return (
        f"<h4>{_esc(title)}</h4>"
        f'<p class="sub">{_esc(note)}</p>'
        f'<div class="stats">'
        f'<div class="stat"><span>{junit["tests"]}</span><label>tests</label></div>'
        f'<div class="stat ok"><span>{passed}</span><label>passed</label></div>'
        f'<div class="stat bad"><span>{junit["failures"]}</span><label>failed</label></div>'
        f'<div class="stat bad"><span>{junit["errors"]}</span><label>errors</label></div>'
        f'<div class="stat warn"><span>{junit["skipped"]}</span><label>skipped</label></div>'
        f'<div class="stat"><span>{junit["time"]:.2f}s</span><label>time</label></div>'
        f"</div>"
        f"<table class='table tests'><tbody>{''.join(rows)}</tbody></table>"
    )


def _render_tests(junit: dict | None, groovy_junit: dict | None = None) -> str:
    """
    Both suites, shown separately.

    They are separate verdicts and must not be merged into one number. The generated
    suite simulates the flow in Python and can be green while the harness - which
    compiles and executes the real scripts - is red. Showing only the first is how a
    broken script reads as a passing run.
    """
    if not junit and not groovy_junit:
        return '<p class="muted">No test report available for this run.</p>'

    return _render_suite(
        "Generated suite (pytest)",
        junit,
        "Simulates the flow's routing and mapping in Python, with HTTP mocked. "
        "It is AI-generated code and can itself be wrong.",
    ) + _render_suite(
        "Groovy harness (real scripts)",
        groovy_junit,
        "Compiles and executes the real .groovy and .gsh scripts of the artifact. "
        "This is the authoritative verdict on a script; a green suite above does not "
        "clear a red result here.",
    )


def _render_provenance(manifest) -> str:
    if not isinstance(manifest, dict) or not manifest:
        return '<p class="muted">No manifest available: generation was skipped or did not run.</p>'

    llm = manifest.get("llm") or {}
    artifacts = manifest.get("artifacts") or {}
    prompt = manifest.get("prompt") or {}
    environment = manifest.get("environment") or {}

    pairs = [
        ("iFlow", manifest.get("iflow")),
        ("Commit", (environment.get("github_sha") or "")[:12] or None),
        ("Run", environment.get("github_run_id")),
        ("Model", llm.get("model")),
        ("Prompt tokens", llm.get("prompt_tokens")),
        ("Completion tokens", llm.get("completion_tokens")),
        ("Total tokens", llm.get("total_tokens")),
        ("Attempts", manifest.get("attempts")),
        ("Duration", f"{manifest.get('duration_seconds')}s" if manifest.get("duration_seconds") else None),
        ("Scenario count", artifacts.get("scenarios")),
        ("Risk count", artifacts.get("risks")),
        ("Testdata files", artifacts.get("testdata_files")),
        ("prompt sha256", (prompt.get("sha256") or "")[:16] or None),
        ("iflow sha256", (manifest.get("iflow_sha256") or "")[:16] or None),
        ("Generated at", manifest.get("generated_at")),
    ]

    truncated = manifest.get("truncated") or []
    warning = ""
    if truncated:
        warning = (
            '<div class="callout warn"><strong>Content truncated before submission.</strong> '
            f"The analysis did not see the whole artifact: {_esc(', '.join(truncated))}. "
            "Coverage may have gaps.</div>"
        )

    rejected = artifacts.get("rejected") or []
    rejected_html = ""
    if rejected:
        items = "".join(f"<li>{_esc(entry)}</li>" for entry in rejected)
        rejected_html = f'<h4>Discarded fixtures</h4><ul>{items}</ul>'

    return warning + _kv_rows(pairs) + rejected_html


# The sections the model is asked to close its answer with. Each gets its own heading, so
# the guidance is not buried in the tail of a long document.
_SUGGESTION_SECTIONS = (
    (
        "fix risks",
        "fix-risks",
        "Fix Risks",
        "How to fix or mitigate each risk listed in the Risks tab.",
    ),
    (
        "fix coverage",
        "fix-coverage",
        "Fix Coverage",
        "How to cover every item below 100% and every uncovered path in the Coverage tab.",
    ),
)


def _render_suggestions(text: str | None) -> str:
    if not text or not text.strip():
        return (
            '<p class="muted">No suggested fixes for this run. The analysis runs on every '
            "run, so an empty file means the model call failed.</p>"
        )

    body = text.strip()

    # Split on any of the section headings, keeping each heading with its content. Matched
    # case-insensitively: the model is asked for the exact titles but may vary the casing,
    # and a missed match would leave a section unstyled and unfindable.
    pieces = re.split(
        r"^\s*##\s+(fix risks|fix coverage)\s*$",
        body,
        flags=re.IGNORECASE | re.MULTILINE,
    )

    analysis = (pieces[0] or "").rstrip()
    # The file is markdown produced by a model, rendered as preformatted text on purpose:
    # no markdown engine, no raw HTML injection into the page.
    html = f'<pre class="prose">{_esc(analysis)}</pre>' if analysis else ""

    found: list[str] = []
    index = 1
    while index < len(pieces):
        title = (pieces[index] or "").strip().lower()
        content = (pieces[index + 1] if index + 1 < len(pieces) else "").strip()
        index += 2

        match = next((item for item in _SUGGESTION_SECTIONS if item[0] == title), None)
        if match is None:
            continue

        _, ident, label, description = match
        found.append(label)

        html += f'<h4 id="{ident}">{_esc(label)}</h4>'
        html += (
            f'<p class="sub">{_esc(description)} '
            "Advisory: verify against the artifact before changing anything.</p>"
        )
        if content:
            html += f'<pre class="prose">{_esc(content)}</pre>'
        else:
            html += (
                '<p class="muted">The model opened the section but wrote no guidance.</p>'
            )

    missing = [label for _, _, label, _ in _SUGGESTION_SECTIONS if label not in found]
    if missing:
        nomes = " and ".join(f"<code>{_esc(name)}</code>" for name in missing)
        html += (
            f'<p class="muted">This answer carries no {nomes} section, though the model '
            "is asked for it; the absence is worth reporting.</p>"
        )

    return html



# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

STYLE = """
:root{--bg:#0f1216;--panel:#171b21;--panel2:#1e232b;--line:#2a313b;--fg:#e6e9ee;
--muted:#98a2b3;--accent:#4c8dff;--ok:#2fbf71;--bad:#ef4d5a;--warn:#e8a33d}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
header.top{padding:28px 32px 20px;border-bottom:1px solid var(--line);background:linear-gradient(180deg,#1a1f27,#12161b)}
header.top h1{margin:0;font-size:22px;font-weight:650;letter-spacing:-.01em}
header.top p{margin:6px 0 0;color:var(--muted);font-size:13px}
.wrap{max-width:1120px;margin:0 auto;padding:0 24px 64px}
nav.tabs{display:flex;gap:4px;flex-wrap:wrap;padding:14px 32px 0;border-bottom:1px solid var(--line);
position:sticky;top:0;background:rgba(15,18,22,.96);backdrop-filter:blur(8px);z-index:5}
nav.tabs button{appearance:none;border:1px solid transparent;border-bottom:none;background:transparent;
color:var(--muted);padding:10px 16px;border-radius:9px 9px 0 0;cursor:pointer;font-size:14px;font-weight:550}
nav.tabs button:hover{color:var(--fg);background:#1b2029}
nav.tabs button[aria-selected=true]{color:var(--fg);background:var(--panel);border-color:var(--line);position:relative;top:1px}
nav.tabs button .count{margin-left:7px;font-size:11px;color:var(--muted);background:#232a34;padding:2px 7px;border-radius:999px}
section[role=tabpanel]{display:none;padding-top:24px}
section[role=tabpanel].active{display:block}
h2{margin:0 0 4px;font-size:18px;font-weight:650;letter-spacing:-.01em}
h3{margin:0;font-size:15px;font-weight:600}
h4{margin:22px 0 8px;font-size:13px;font-weight:650;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.sub{color:var(--muted);font-size:13px;margin:0 0 20px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px}
.card header{display:flex;gap:10px;align-items:baseline;margin-bottom:10px}
.badge{font:600 11px/1 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--accent);
background:#16233a;border:1px solid #24395c;padding:5px 8px;border-radius:6px;white-space:nowrap}
.field{margin-top:10px}
.field .k{display:block;font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin-bottom:3px}
.field p{margin:0;font-size:14px}
.kv{display:flex;justify-content:space-between;gap:18px;padding:9px 2px;border-bottom:1px solid #222833}
.kv .k{color:var(--muted);font-size:13px}
.kv .v{font:500 13px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;text-align:right;word-break:break-word}
.table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.table th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);
padding:11px 14px;background:#1b2029;border-bottom:1px solid var(--line)}
.table td{padding:12px 14px;border-bottom:1px solid #222833;vertical-align:top;font-size:14px}
.table tr:last-child td{border-bottom:none}
.table td.strong{font-weight:600}
.table.tests tr.detail td{padding:0 14px 10px;border-bottom:1px solid #222833}
.table.tests tr.detail:last-child td{border-bottom:none}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:9px;background:var(--muted)}
.dot.passed{background:var(--ok)}.dot.failed,.dot.error{background:var(--bad)}.dot.skipped{background:var(--warn)}
td.status{text-align:right;color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.failure{font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;color:#ffb4ba;background:#2a1619;
border:1px solid #42232a;border-radius:7px;padding:9px 11px;white-space:pre-wrap;margin:6px 0}
.stats{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:22px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:11px;padding:12px 18px;min-width:96px}
.stat span{display:block;font-size:21px;font-weight:650}
.stat label{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
.stat.ok span{color:var(--ok)}.stat.bad span{color:var(--bad)}.stat.warn span{color:var(--warn)}
.callout{background:#16233a;border:1px solid #24395c;border-radius:10px;padding:13px 15px;margin-bottom:20px;font-size:14px}
.callout.warn{background:#2a2113;border-color:#4d3a17;color:#ffdca8}
.bars{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px}
.bar-row{display:grid;grid-template-columns:180px 1fr 52px;gap:12px;align-items:center;padding:6px 0}
.bar-label{font-size:13px;color:var(--muted);overflow:hidden;text-overflow:ellipsis}
.bar{background:#232a34;height:9px;border-radius:999px;overflow:hidden}
.bar i{display:block;height:100%;background:linear-gradient(90deg,#3f7ae0,#4c8dff)}
.bar-value{font:500 13px ui-monospace,SFMono-Regular,Menlo,monospace;text-align:right}
.prose{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px;
white-space:pre-wrap;word-break:break-word;font:13px/1.65 ui-monospace,SFMono-Regular,Menlo,monospace;margin:0}
.muted{color:var(--muted)}
code{background:#232a34;padding:2px 6px;border-radius:5px;font:12px ui-monospace,SFMono-Regular,Menlo,monospace}
ul{margin:8px 0 0;padding-left:20px}li{margin:5px 0}
@media print{body{background:#fff;color:#000}nav.tabs{display:none}
section[role=tabpanel]{display:block!important;page-break-inside:avoid}
.card,.table,.bars,.prose{background:#fff;border-color:#ccc}}
"""

SCRIPT = """
(function(){
  var tabs = Array.prototype.slice.call(document.querySelectorAll('nav.tabs button'));
  var panels = Array.prototype.slice.call(document.querySelectorAll('section[role=tabpanel]'));
  function select(id, push){
    tabs.forEach(function(t){ t.setAttribute('aria-selected', String(t.dataset.target === id)); });
    panels.forEach(function(p){ p.classList.toggle('active', p.id === id); });
    if (push && history.replaceState) { history.replaceState(null, '', '#' + id); }
  }
  tabs.forEach(function(t){
    t.addEventListener('click', function(){ select(t.dataset.target, true); });
  });
  var initial = (location.hash || '').replace('#','');
  var known = panels.some(function(p){ return p.id === initial; });
  select(known ? initial : (panels[0] && panels[0].id), false);
})();
"""


def build_page(
    *,
    iflow: str,
    scenarios,
    risks,
    coverage,
    manifest,
    junit,
    suggestions: str | None,
    groovy_junit=None,
) -> str:
    """Assemble the whole document."""
    counts = {
        "scenarios": len(scenarios) if isinstance(scenarios, list) else 0,
        "risks": len(risks) if isinstance(risks, list) else 0,
        "tests": (junit["tests"] if junit else 0) + (groovy_junit["tests"] if groovy_junit else 0),
    }

    def tab(target: str, label: str, count: int | None = None) -> str:
        badge = f'<span class="count">{count}</span>' if count is not None else ""
        return (
            f'<button role="tab" data-target="{target}" aria-selected="false">'
            f"{_esc(label)}{badge}</button>"
        )

    # Order matters: it is the reading order. The plan comes before the results, because
    # the scenarios say what the run was meant to demonstrate; coverage follows the
    # results, and closing with the risks and then the suggested fixes leaves the
    # reviewer with what still needs action.
    panels = [
        ("tab-overview", "Overview", f"""
            <h2>Overview</h2>
            <p class="sub">Provenance of this run and how the suite performed.</p>
            {_render_provenance(manifest)}
        """),
        ("tab-scenarios", "Scenarios", _render_scenarios(scenarios)),
        ("tab-tests", "Tests", _render_tests(junit, groovy_junit)),
        ("tab-coverage", "Coverage", _render_coverage(coverage)),
        ("tab-risks", "Risks", _render_risks(risks)),
        ("tab-fixes", "Suggested fixes", _render_suggestions(suggestions)),
    ]

    nav = "".join(
        [
            tab("tab-overview", "Overview"),
            tab("tab-scenarios", "Scenarios", counts["scenarios"]),
            tab("tab-tests", "Tests", counts["tests"]),
            tab("tab-coverage", "Coverage"),
            tab("tab-risks", "Risks", counts["risks"]),
            tab("tab-fixes", "Suggested fixes"),
        ]
    )

    body = "".join(
        f'<section id="{ident}" role="tabpanel" aria-label="{_esc(label)}">{inner}</section>'
        for ident, label, inner in panels
    )

    # The verdict is the worst of the two suites: a green generated suite with a red
    # harness is a failed run, which is the whole reason both are shown.
    verdict = "unknown"
    suites = [s for s in (junit, groovy_junit) if s and s["tests"]]
    if suites:
        broken = any(s["failures"] or s["errors"] for s in suites)
        verdict = "failed" if broken else "passed"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Test evidence — {_esc(iflow)}</title>
<style>{STYLE}</style>
</head>
<body>
<header class="top">
  <h1>Test evidence — {_esc(iflow)}</h1>
  <p>Generated by the SAP Integration Suite test pipeline. Self-contained: no external
  resources, readable offline. Verdict: <strong>{_esc(verdict)}</strong>.</p>
</header>
<nav class="tabs" role="tablist">{nav}</nav>
<div class="wrap">{body}</div>
<script>{SCRIPT}</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a single self-contained HTML dashboard from the artifacts of a run."
    )
    parser.add_argument("--iflow", default="iFlow", help="iFlow name, shown in the header.")
    parser.add_argument("--cases", default=None, help="Path to cases.json.")
    parser.add_argument("--risks", default=None, help="Path to risks.json.")
    parser.add_argument("--coverage", default=None, help="Path to coverage.json.")
    parser.add_argument("--manifest", default=None, help="Path to manifest.json.")
    parser.add_argument("--junit", default=None, help="Path to the pytest junit.xml.")
    parser.add_argument(
        "--junit-groovy",
        default=None,
        help="Path to the Groovy harness junit-groovy.xml. Shown as its own suite.",
    )
    parser.add_argument("--suggestions", default=None, help="Path to ai_fix_suggestions.md.")
    parser.add_argument(
        "--output",
        required=True,
        help="Where to write the dashboard (for example CI-CD/reports/<iFlow>/dashboard.html).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    page = build_page(
        iflow=args.iflow,
        scenarios=_load_json(Path(args.cases) if args.cases else None),
        risks=_load_json(Path(args.risks) if args.risks else None),
        coverage=_load_json(Path(args.coverage) if args.coverage else None),
        manifest=_load_json(Path(args.manifest) if args.manifest else None),
        junit=load_junit(Path(args.junit) if args.junit else None),
        groovy_junit=load_junit(Path(args.junit_groovy) if args.junit_groovy else None),
        suggestions=_load_text(Path(args.suggestions) if args.suggestions else None),
    )

    destination = Path(args.output)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(page, encoding="utf-8")
    except OSError as exc:
        print(f"[ERROR] could not write {destination}: {exc}", file=sys.stderr)
        return 2

    print(f"Dashboard written: {destination} ({len(page)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
