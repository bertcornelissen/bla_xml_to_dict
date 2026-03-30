"""
Streamlit GUI for compare_iliad_xml — upload two ILIAD XML files and view
the message-by-message diff interactively.
"""
import html as _html
import re
from pathlib import Path

import pandas as pd
import streamlit as st
import xmltodict
from deepdiff import DeepDiff

# ── shared core (duplicated from compare_iliad_xml_cli.py) ──────────────────

TABLE_RECORD_NAME = "Table Record"


def transform_fields(fields) -> dict:
    if not isinstance(fields, list):
        fields = [fields]
    table_record_seq = 0
    result = {}
    for field in fields:
        name = field.get("FriendlyName", field["@ID"])
        if name == TABLE_RECORD_NAME:
            table_record_seq += 1
            key = f"{name} {table_record_seq}"
        else:
            key = name
        child_list = field.get("FieldList")
        children = child_list.get("Field") if isinstance(child_list, dict) else None
        if children:
            result[key] = transform_fields(children)
        else:
            result[key] = field.get("FieldViewable")
    return result


def apply_field_transforms(data):
    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            if key == "FieldList" and isinstance(value, dict) and "Field" in value:
                result[key] = transform_fields(value["Field"])
            elif isinstance(value, (dict, list)):
                result[key] = apply_field_transforms(value)
            else:
                result[key] = value
        return result
    elif isinstance(data, list):
        return [apply_field_transforms(item) for item in data]
    return data


def xml_to_dict_bytes(data: bytes) -> dict:
    return apply_field_transforms(xmltodict.parse(data, dict_constructor=dict))


def extract_all_msgs(data: dict) -> list[dict]:
    online_messages = data["EMVCoL3OnlineMessageFormat"]["OnlineMessageList"]["OnlineMessage"]
    if isinstance(online_messages, dict):
        online_messages = [online_messages]
    result = []
    for om in online_messages:
        label = f"{om.get('@Class', '?')} ({om.get('@Source', '?')} -> {om.get('@Destination', '?')})"
        field_list = om.get("FieldList") or {}
        msg = field_list.get("Message") or (field_list if field_list else None)
        if msg is not None:
            result.append({"label": label, "msg": msg})
    return result


def get_exclude_paths(exclude_fields: list[str]):
    return [re.compile(rf".*\['{re.escape(f)}'\]$") for f in exclude_fields]


def parse_path(path_str: str) -> list[str]:
    parts, current, in_bracket = [], "", False
    for c in path_str:
        if c == "[":
            in_bracket = True
            current = ""
        elif c == "]":
            in_bracket = False
            parts.append(current.strip("'"))
        elif in_bracket:
            current += c
    return parts


def insert_path(tree: dict, path: list[str], value):
    node = tree
    for part in path[:-1]:
        if part not in node:
            node[part] = {}
        node = node[part]
    node[path[-1]] = value


def build_diff_tree(diff) -> dict:
    diff_tree: dict = {}
    for change_type, changes in diff.items():
        if isinstance(changes, dict):
            for path_str, value in changes.items():
                path = parse_path(path_str)
                if path:
                    insert_path(diff_tree, path, {"type": change_type, "value": value})
        else:
            for path_str in changes:
                path = parse_path(str(path_str))
                if path:
                    insert_path(diff_tree, path, {"type": change_type, "value": str(path_str)})
    return diff_tree


# ── Streamlit rendering ──────────────────────────────────────────────────────

CHANGE_LABELS = {
    "values_changed":          "Changed",
    "dictionary_item_added":   "Added",
    "dictionary_item_removed": "Removed",
    "iterable_item_added":     "Added (list)",
    "iterable_item_removed":   "Removed (list)",
}

# Row background colours used by both the DataFrame styler and the legend
_green_soft  = 'background-color: rgba(46,204,113,0.72)'
_red_soft    = 'background-color: rgba(231,76,60,0.72)'
_orange_soft = 'background-color: rgba(243,156,18,0.72)'

_ROW_STYLE: dict[str, str] = {
    "Changed":        _orange_soft,
    "Added":          _green_soft,
    "Added (list)":   _green_soft,
    "Removed":        _red_soft,
    "Removed (list)": _red_soft,
}

def flatten_diff_tree(tree: dict, path_prefix: str = "") -> list[dict]:
    """Flatten the nested diff-tree into a list of table rows."""
    rows: list[dict] = []
    for key, value in tree.items():
        path = f"{path_prefix} › {key}" if path_prefix else key
        if isinstance(value, dict) and "type" in value:
            change_type = value["type"]
            change_value = value["value"]
            label = CHANGE_LABELS.get(change_type, change_type)
            if change_type == "values_changed" and isinstance(change_value, dict):
                rows.append({
                    "Field": path,
                    "Change": label,
                    "Old value": str(change_value.get("old_value", "")),
                    "New value": str(change_value.get("new_value", "")),
                })
            elif change_type in ("dictionary_item_removed", "iterable_item_removed"):
                old_val = "—" if isinstance(change_value, dict) else str(change_value)
                rows.append({"Field": path, "Change": label,
                             "Old value": old_val, "New value": "—"})
            else:
                new_val = "—" if isinstance(change_value, dict) else str(change_value)
                rows.append({"Field": path, "Change": label,
                             "Old value": "—", "New value": new_val})
        elif isinstance(value, dict):
            rows.extend(flatten_diff_tree(value, path))
    return rows


def _style_row(row: pd.Series) -> list[str]:
    style = _ROW_STYLE.get(row["Change"], "")
    return [style] * len(row)


def render_diff_as_table(diff_tree: dict) -> None:
    rows = flatten_diff_tree(diff_tree)
    if not rows:
        return
    df = pd.DataFrame(rows, columns=["Field", "Change", "Old value", "New value"])
    styled = df.style.apply(_style_row, axis=1)
    st.dataframe(styled, width='stretch', hide_index=True)


def load_default_exclude_fields(file_path: str = "ignored_fields.txt") -> list[str]:
    try:
        with open(file_path) as f:
            return [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]
    except OSError:
        return []


def _diff_table_html(diff_tree: dict) -> str:
    """Return an HTML <table> string for the given diff tree (used in report)."""
    rows = flatten_diff_tree(diff_tree)
    if not rows:
        return "<p><em>No differences.</em></p>"
    th = 'style="padding:6px 10px;text-align:left;border-bottom:2px solid #ccc;font-weight:600"'
    td = 'style="padding:5px 10px;word-break:break-word"'
    out = ['<table style="width:100%;border-collapse:collapse;font-size:0.875em">']
    out.append(f'<thead><tr><th {th}>Field</th><th {th}>Change</th>'
               f'<th {th}>Old value</th><th {th}>New value</th></tr></thead><tbody>')
    for row in rows:
        bg = _ROW_STYLE.get(row["Change"], "")
        tr_style = f'style="{bg}"' if bg else ""
        out.append(f'<tr {tr_style}>')
        for col in ("Field", "Change", "Old value", "New value"):
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
            table = _diff_table_html(build_diff_tree(diff))
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
    COL = {"Field": 120, "Change": 16, "Old value": 30, "New value": 30}
    SEP = "-" * (sum(COL.values()) + len(COL) * 3 + 1)

    def _fmt_row(row: dict) -> str:
        return (
            f"| {row['Field']:<{COL['Field']}} "
            f"| {row['Change']:<{COL['Change']}} "
            f"| {row['Old value']:<{COL['Old value']}} "
            f"| {row['New value']:<{COL['New value']}} |"
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
            rows = flatten_diff_tree(build_diff_tree(diff))
            if rows:
                header = _fmt_row({"Field": "Field", "Change": "Change",
                                   "Old value": "Old value", "New value": "New value"})
                lines += [SEP, header, SEP]
                for row in rows:
                    # Truncate long values so columns stay readable
                    tr = {k: (v[:v_len - 1] + "…" if len(v) > v_len else v)
                          for (k, v_len), v in zip(COL.items(), [
                              row["Field"], row["Change"], row["Old value"], row["New value"]
                          ])}
                    lines.append(_fmt_row(tr))
                lines.append(SEP)
        lines.append("")
    return "\n".join(lines)


def _render_raw_tree(tree: dict, lines: list, prefix: str = "", is_last: bool = True) -> None:
    """Recursively render a diff tree as ASCII lines, matching the CLI Rich tree style."""
    entries = list(tree.items())
    for idx, (key, value) in enumerate(entries):
        connector = "└── " if idx == len(entries) - 1 else "├── "
        child_prefix = prefix + ("    " if idx == len(entries) - 1 else "│   ")
        if isinstance(value, dict) and "type" in value:
            change_type = value["type"]
            change_value = value["value"]
            _val = "" if isinstance(change_value, dict) else f": {change_value}"
            if change_type == "values_changed":
                tag = f"Changed {key}: {change_value}"
            elif change_type == "dictionary_item_added":
                tag = f"Added {key}{_val}"
            elif change_type == "dictionary_item_removed":
                tag = f"Removed {key}{_val}"
            elif change_type == "iterable_item_added":
                tag = f"Added (list) {key}{_val}"
            elif change_type == "iterable_item_removed":
                tag = f"Removed (list) {key}{_val}"
            else:
                tag = f"{change_type} {key}{_val}"
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

# ── Sidebar: settings ───────────────────────────────────────────────────────
with st.sidebar:
    st.header("Settings")

    ignore_order = st.checkbox("Ignore list order", value=False)

    default_excludes = load_default_exclude_fields("ignored_fields.txt")
    exclude_text = st.text_area(
        "Fields to ignore (one per line)",
        value="\n".join(default_excludes),
        height=160,
        help="Field names that will be excluded from the comparison.",
    )
    extra_excludes = [l.strip() for l in exclude_text.splitlines() if l.strip() and not l.strip().startswith("#")]



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
try:
    a_msgs = extract_all_msgs(xml_to_dict_bytes(upload_a.read()))
    b_msgs = extract_all_msgs(xml_to_dict_bytes(upload_b.read()))
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
        ignore_order=ignore_order,
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
for i, ((a_entry, b_entry), diff) in enumerate(zip(pairs, diffs)):
    header = f"Message {i + 1}: {a_entry['label']}"
    if not diff:
        with st.expander(f"✅ {header}", expanded=False):
            st.success("No differences.")
    else:
        n_changes = sum(
            len(v) if isinstance(v, dict) else len(list(v))
            for v in diff.values()
        )
        with st.expander(f"⚠️ {header}  —  **{n_changes} change(s)**", expanded=True):
            c1, c2 = st.columns(2)
            c1.caption(f"**A:** {a_entry['label']}")
            c2.caption(f"**B:** {b_entry['label']}")
            diff_tree = build_diff_tree(diff)
            render_diff_as_table(diff_tree)
