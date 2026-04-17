"""
Streamlit GUI for compare_iliad_xml — upload two ILIAD XML files and view
the message-by-message diff interactively.
"""
import html as _html

import pandas as pd
import streamlit as st
from deepdiff import DeepDiff

from iliad_core import (
    CHANGE_LABELS,
    build_diff_tree,
    extract_all_msgs,
    flatten_diff_tree,
    format_change_label,
    get_exclude_paths,
    load_default_exclude_fields,
    xml_to_dict_bytes,
    xml_to_raw_dict_bytes,
)

# ── Streamlit rendering ──────────────────────────────────────────────────────

# Row background colours used by both the DataFrame styler and the legend
_green_soft  = 'background-color: rgba(46,204,113,0.72)'
_red_soft    = 'background-color: rgba(231,76,60,0.72)'
_orange_soft = 'background-color: rgba(243,156,18,0.72)'

_ROW_STYLE: dict[str, str] = {
    "Differs":        _orange_soft,
    "Added":          _green_soft,
    "Added (list)":   _green_soft,
    "Removed":        _red_soft,
    "Removed (list)": _red_soft,
}

def _style_row(row: pd.Series) -> list[str]:
    style = _ROW_STYLE.get(row["Change"], "")
    return [style] * len(row)


def render_diff_as_table(diff_tree: dict, tag_map: dict | None = None) -> None:
    rows = flatten_diff_tree(diff_tree, tag_map=tag_map)
    if not rows:
        return
    cols = ["Field", "Field ID", "Tag", "Change", "File 1", "File 2"]
    df = pd.DataFrame(rows, columns=cols)
    styled = df.style.apply(_style_row, axis=1)
    st.dataframe(styled, width='stretch', hide_index=True,
                 height=38 + 35 * len(rows),
                 column_order=["Field", "Tag", "Change", "File 1", "File 2"])


def _diff_table_html(diff_tree: dict, tag_map: dict | None = None) -> str:
    """Return an HTML <table> string for the given diff tree (used in report)."""
    rows = flatten_diff_tree(diff_tree, tag_map=tag_map)
    if not rows:
        return "<p><em>No differences.</em></p>"
    th = 'style="padding:6px 10px;text-align:left;border-bottom:2px solid #ccc;font-weight:600"'
    td = 'style="padding:5px 10px;word-break:break-word"'
    out = ['<table style="width:100%;border-collapse:collapse;font-size:0.875em">']
    out.append(f'<thead><tr><th {th}>Field</th><th {th}>Tag</th><th {th}>Change</th>'
               f'<th {th}>File 1</th><th {th}>File 2</th></tr></thead><tbody>')
    for row in rows:
        bg = _ROW_STYLE.get(row["Change"], "")
        tr_style = f'style="{bg}"' if bg else ""
        out.append(f'<tr {tr_style}>')
        for col in ("Field", "Tag", "Change", "File 1", "File 2"):
            out.append(f'<td {td}>{_html.escape(str(row.get(col, "")))}</td>')
        out.append("</tr>")
    out.append("</tbody></table>")
    return "\n".join(out)


def _build_html_report(name_a: str, name_b: str, pairs: list, diffs: list) -> str:
    """Build a self-contained HTML report of all diffs."""
    from datetime import date
    n_changed = sum(1 for d in diffs if d)
    n_identical = len(pairs) - n_changed
    sections = []
    for i, ((a_entry, b_entry), diff) in enumerate(zip(pairs, diffs)):
        header = _html.escape(f"Message {i + 1}: {a_entry['label']}")
        if not diff:
            sections.append(f'<h3>{header}</h3><p style="color:green">✅ No differences.</p>')
        else:
            n_changes = sum(
                len(v) if isinstance(v, dict) else len(list(v)) for v in diff.values()
            )
            msg_tag_map = {**a_entry.get("tag_map", {}), **b_entry.get("tag_map", {})}
            table = _diff_table_html(build_diff_tree(diff), msg_tag_map)
            sections.append(
                f'<h3>{header} — {n_changes} change(s)</h3>'
                f'<p style="font-size:0.85em;color:#555">'
                f'A: {_html.escape(a_entry["label"])} &nbsp;|&nbsp; '
                f'B: {_html.escape(b_entry["label"])}</p>'
                + table
            )
    body = "\n<hr>\n".join(sections)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>ILIAD XML Diff Report</title>
<style>
  body {{ font-family: sans-serif; margin: 2em; color: #222; }}
  h1   {{ font-size: 1.4em; }}
  h3   {{ font-size: 1.05em; margin-top: 1.5em; }}
  hr   {{ border: none; border-top: 1px solid #ddd; margin: 1.5em 0; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.875em; margin-bottom: 1em; }}
  th, td {{ padding: 5px 10px; text-align: left; }}
  thead th {{ border-bottom: 2px solid #ccc; font-weight: 600; }}
</style>
</head>
<body>
<h1>ILIAD XML Diff Report</h1>
<p>
  <strong>A:</strong> {_html.escape(name_a)}<br>
  <strong>B:</strong> {_html.escape(name_b)}<br>
  <strong>Date:</strong> {date.today()}<br>
  Messages compared: {len(pairs)} &nbsp;|&nbsp;
  With differences: {n_changed} &nbsp;|&nbsp;
  Identical: {n_identical}
</p>
<hr>
{body}
</body>
</html>"""


def _build_txt_report(name_a: str, name_b: str, pairs: list, diffs: list) -> str:
    """Build a plain-text report of all diffs."""
    from datetime import date
    COL = {"Field": 120, "Tag": 20, "Change": 16, "File 1": 30, "File 2": 30}
    SEP = "-" * (sum(COL.values()) + len(COL) * 3 + 1)

    def _fmt_row(row: dict) -> str:
        return (
            f"| {row['Field']:<{COL['Field']}} "
            f"| {row['Tag']:<{COL['Tag']}} "
            f"| {row['Change']:<{COL['Change']}} "
            f"| {row['File 1']:<{COL['File 1']}} "
            f"| {row['File 2']:<{COL['File 2']}} |"
        )

    n_changed = sum(1 for d in diffs if d)
    n_identical = len(pairs) - n_changed
    lines = [
        "ILIAD XML Diff Report",
        "=" * 60,
        f"A    : {name_a}",
        f"B    : {name_b}",
        f"Date : {date.today()}",
        f"Messages compared: {len(pairs)}  |  With differences: {n_changed}  |  Identical: {n_identical}",
        "",
    ]
    for i, ((a_entry, b_entry), diff) in enumerate(zip(pairs, diffs)):
        lines.append("=" * 60)
        lines.append(f"Message {i + 1}: {a_entry['label']}")
        if not diff:
            lines.append("  No differences.")
        else:
            n_changes = sum(
                len(v) if isinstance(v, dict) else len(list(v)) for v in diff.values()
            )
            lines.append(f"  {n_changes} change(s)")
            msg_tag_map = {**a_entry.get("tag_map", {}), **b_entry.get("tag_map", {})}
            rows = flatten_diff_tree(build_diff_tree(diff), tag_map=msg_tag_map)
            if rows:
                header = _fmt_row({"Field": "Field", "Tag": "Tag", "Change": "Change",
                                   "File 1": "File 1", "File 2": "File 2"})
                lines += [SEP, header, SEP]
                for row in rows:
                    # Truncate long values so columns stay readable
                    tr = {k: (v[:v_len - 1] + "…" if len(v) > v_len else v)
                          for (k, v_len), v in zip(COL.items(), [
                              row["Field"], row["Tag"], row["Change"], row["File 1"], row["File 2"]
                          ])}
                    lines.append(_fmt_row(tr))
                lines.append(SEP)
        lines.append("")
    return "\n".join(lines)


def _render_raw_tree(tree: dict, lines: list, prefix: str = "") -> None:
    """Recursively render a diff tree as ASCII lines, matching the CLI Rich tree style."""
    entries = list(tree.items())
    for idx, (key, value) in enumerate(entries):
        connector = "└── " if idx == len(entries) - 1 else "├── "
        child_prefix = prefix + ("    " if idx == len(entries) - 1 else "│   ")
        if isinstance(value, dict) and "type" in value:
            change_type = value["type"]
            change_value = value["value"]
            if change_type in ("dictionary_item_removed", "iterable_item_removed") and isinstance(change_value, dict):
                lines.append(f"{prefix}{connector}{key} [Removed]")
                _render_raw_tree({k: {"type": change_type, "value": v} for k, v in change_value.items()}, lines, child_prefix)
            elif change_type in ("dictionary_item_added", "iterable_item_added") and isinstance(change_value, dict):
                lines.append(f"{prefix}{connector}{key} [Added]")
                _render_raw_tree({k: {"type": change_type, "value": v} for k, v in change_value.items()}, lines, child_prefix)
            else:
                tag = format_change_label(change_type, key, change_value)
                lines.append(f"{prefix}{connector}{tag}")
        elif isinstance(value, dict):
            lines.append(f"{prefix}{connector}{key}")
            _render_raw_tree(value, lines, child_prefix)
        else:
            lines.append(f"{prefix}{connector}{key}: {value}")


def _build_raw_report(name_a: str, name_b: str, pairs: list, diffs: list) -> str:
    """Build a plain-text tree report matching the CLI output style."""
    from datetime import date
    n_changed = sum(1 for d in diffs if d)
    n_identical = len(pairs) - n_changed
    lines = [
        "ILIAD XML Diff Report (raw)",
        "=" * 60,
        f"A    : {name_a}",
        f"B    : {name_b}",
        f"Date : {date.today()}",
        f"Messages compared: {len(pairs)}  |  With differences: {n_changed}  |  Identical: {n_identical}",
        "",
    ]
    for i, ((a_entry, b_entry), diff) in enumerate(zip(pairs, diffs)):
        lines.append("=" * 60)
        lines.append(f"Message {i + 1}: {a_entry['label']}")
        if not diff:
            lines.append("  No differences.")
        else:
            diff_tree = build_diff_tree(diff)
            lines.append("root")
            _render_raw_tree(diff_tree, lines)
        lines.append("")
    return "\n".join(lines)


# ── UI ───────────────────────────────────────────────────────────────────────

st.set_page_config(page_title="ILIAD XML Compare", page_icon="🔍", layout="wide")
st.title("🔍 ILIAD XML Compare")

st.markdown(
    """<style>
    div[data-testid='stButton'] > button {
        justify-content: flex-start !important;
    }
    div[data-testid='stButton'] > button > div {
        justify-content: flex-start !important;
    }
    div[data-testid='stButton'] > button p {
        text-align: left !important;
    }
    </style>""",
    unsafe_allow_html=True,
)

# ── Sidebar: settings ───────────────────────────────────────────────────────
if "ignored_fields_text" not in st.session_state:
    st.session_state["ignored_fields_text"] = "\n".join(load_default_exclude_fields("ignored_fields.txt"))

with st.sidebar:
    st.header("Settings")

    st.subheader("Fields to ignore")
    col_refresh, col_save = st.columns(2)
    with col_refresh:
        if st.button("↺ Refresh", help="Reload ignored fields from ignored_fields.txt"):
            st.session_state["ignored_fields_text"] = "\n".join(load_default_exclude_fields("ignored_fields.txt"))
    with col_save:
        if st.button("💾 Save", help="Write current fields back to ignored_fields.txt"):
            lines = [l.strip() for l in st.session_state["ignored_fields_text"].splitlines() if l.strip()]
            with open("ignored_fields.txt", "w", encoding="utf-8") as _f:
                _f.write("\n".join(lines) + "\n")
            st.toast("Saved to ignored_fields.txt", icon="💾")
    use_ignored = st.checkbox("Apply ignored fields", value=True,
                               help="Uncheck to include all fields in the comparison.")
    st.text_area(
        "One field per line",
        key="ignored_fields_text",
        height=160,
        help="Field names that will be excluded from the comparison.",
        disabled=not use_ignored,
    )
    extra_excludes = (
        [l.strip() for l in st.session_state["ignored_fields_text"].splitlines()
         if l.strip() and not l.strip().startswith("#")]
        if use_ignored else []
    )




# ── File upload ──────────────────────────────────────────────────────────────
col_a, col_b = st.columns(2)
with col_a:
    upload_a = st.file_uploader("File A (reference)", type=["xml", "emvco"], key="file_a")
    if upload_a:
        st.caption(f"📄 {upload_a.name}  ·  {upload_a.size:,} bytes")
with col_b:
    upload_b = st.file_uploader("File B (comparison)", type=["xml", "emvco"], key="file_b")
    if upload_b:
        st.caption(f"📄 {upload_b.name}  ·  {upload_b.size:,} bytes")

if not upload_a or not upload_b:
    st.info("Upload both files to start the comparison.")
    st.stop()

# ── Parse ────────────────────────────────────────────────────────────────────
bytes_a = upload_a.getvalue()
bytes_b = upload_b.getvalue()
try:
    a_msgs = extract_all_msgs(xml_to_dict_bytes(bytes_a), raw_data=xml_to_raw_dict_bytes(bytes_a))
    b_msgs = extract_all_msgs(xml_to_dict_bytes(bytes_b), raw_data=xml_to_raw_dict_bytes(bytes_b))
except Exception as exc:
    st.error(f"Failed to parse files: {exc}")
    st.stop()

exclude_paths = get_exclude_paths(extra_excludes)

pairs = list(zip(a_msgs, b_msgs))
if not pairs:
    st.warning("No comparable messages found.")
    st.stop()

# ── Summary bar ──────────────────────────────────────────────────────────────
diffs = []
for a_entry, b_entry in pairs:
    d = DeepDiff(
        a_entry["msg"], b_entry["msg"],
        verbose_level=2,
        exclude_regex_paths=exclude_paths,
    )
    diffs.append(d)

n_changed = sum(1 for d in diffs if d)
n_identical = len(pairs) - n_changed

st.divider()
m1, m2, m3 = st.columns(3)
m1.metric("Messages compared", len(pairs))
m2.metric("With differences", n_changed)
m3.metric("Identical", n_identical)

tab_html, tab_txt, tab_raw = st.tabs(["⬇ HTML report", "⬇ TXT report", "⬇ Raw report"])
with tab_html:
    rdata = _build_html_report(upload_a.name, upload_b.name, pairs, diffs).encode("utf-8")
    st.download_button(
        label="Download HTML report",
        data=rdata,
        file_name="diff_report.html",
        mime="text/html",
    )
with tab_txt:
    rdata = _build_txt_report(upload_a.name, upload_b.name, pairs, diffs).encode("utf-8")
    st.download_button(
        label="Download TXT report",
        data=rdata,
        file_name="diff_report.txt",
        mime="text/plain",
    )
with tab_raw:
    rdata = _build_raw_report(upload_a.name, upload_b.name, pairs, diffs).encode("utf-8")
    st.download_button(
        label="Download Raw report",
        data=rdata,
        file_name="diff_report_raw.txt",
        mime="text/plain",
    )
st.divider()

# ── Extra messages ───────────────────────────────────────────────────────────
if len(a_msgs) != len(b_msgs):
    for e in a_msgs[len(b_msgs):]:
        st.warning(f"[A only] {e['label']}")
    for e in b_msgs[len(a_msgs):]:
        st.warning(f"[B only] {e['label']}")

# ── Per-message diff ─────────────────────────────────────────────────────────
if "msg_expanded" not in st.session_state:
    st.session_state["msg_expanded"] = {}

for i, ((a_entry, b_entry), diff) in enumerate(zip(pairs, diffs)):
    if i not in st.session_state["msg_expanded"]:
        st.session_state["msg_expanded"][i] = False
    is_open = st.session_state["msg_expanded"][i]
    header = f"Message {i + 1}: {a_entry['label']}"
    if not diff:
        suffix = "✅ No differences"
    else:
        n_changes = sum(
            len(v) if isinstance(v, dict) else len(list(v))
            for v in diff.values()
        )
        suffix = f"⚠️  {n_changes} change(s)"
    arrow = "▼" if is_open else "▶"
    if st.button(f"{arrow}  {header}  —  {suffix}", key=f"btn_{i}", use_container_width=True):
        st.session_state["msg_expanded"][i] = not is_open
        st.rerun()
    if st.session_state["msg_expanded"][i]:
        with st.container(border=True):
            if not diff:
                st.success("No differences.")
            else:
                c1, c2 = st.columns(2)
                c1.caption(f"**A:** {a_entry['label']}")
                c2.caption(f"**B:** {b_entry['label']}")
                diff_tree = build_diff_tree(diff)
                msg_tag_map = {**a_entry.get("tag_map", {}), **b_entry.get("tag_map", {})}
                render_diff_as_table(diff_tree, msg_tag_map)
