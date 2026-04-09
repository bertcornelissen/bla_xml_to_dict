"""
Shared core logic for ILIAD XML comparison — used by both the CLI and GUI.
"""
import re
from pathlib import Path

import xmltodict
from deepdiff import DeepDiff

# ── Constants ────────────────────────────────────────────────────────────────

TABLE_RECORD_NAME = "Table Record"

CHANGE_LABELS: dict[str, str] = {
    "values_changed":          "Changed",
    "dictionary_item_added":   "Added",
    "dictionary_item_removed": "Removed",
    "iterable_item_added":     "Added (list)",
    "iterable_item_removed":   "Removed (list)",
}

# ── XML parsing ──────────────────────────────────────────────────────────────

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


def xml_to_dict(path: Path) -> dict:
    with path.open("rb") as f:
        data = xmltodict.parse(f, dict_constructor=dict)
    return apply_field_transforms(data)


def xml_to_dict_bytes(data: bytes) -> dict:
    return apply_field_transforms(xmltodict.parse(data, dict_constructor=dict))


def _extract_tags_recursive(node: object, tag_map: dict) -> None:
    if isinstance(node, dict):
        if "@ID" in node:
            tag = node["@ID"]
            name = node.get("FriendlyName", tag)
            tag_map[name] = tag
        for value in node.values():
            _extract_tags_recursive(value, tag_map)
    elif isinstance(node, list):
        for item in node:
            _extract_tags_recursive(item, tag_map)


def xml_to_tag_map(data: bytes) -> dict:
    """Return a flat {FriendlyName: @ID} mapping for every Field in the XML."""
    raw = xmltodict.parse(data, dict_constructor=dict)
    tag_map: dict = {}
    _extract_tags_recursive(raw, tag_map)
    return tag_map


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

# ── DeepDiff helpers ─────────────────────────────────────────────────────────

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


def insert_path(tree: dict, path: list[str], value) -> None:
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


def flatten_diff_tree(tree: dict, path_prefix: str = "", tag_map: dict | None = None) -> list[dict]:
    """Flatten the nested diff-tree into a list of table rows."""
    rows: list[dict] = []
    for key, value in tree.items():
        path = f"{path_prefix} › {key}" if path_prefix else key
        if isinstance(value, dict) and "type" in value:
            change_type = value["type"]
            change_value = value["value"]
            label = CHANGE_LABELS.get(change_type, change_type)
            raw_tag = tag_map.get(key, "") if tag_map else ""
            tag = raw_tag.rsplit(".", 1)[-1] if raw_tag else ""
            if change_type == "values_changed" and isinstance(change_value, dict):
                rows.append({
                    "Field": path,
                    "Tag": tag,
                    "Change": label,
                    "Old value": str(change_value.get("old_value", "")),
                    "New value": str(change_value.get("new_value", "")),
                })
            elif change_type in ("dictionary_item_removed", "iterable_item_removed"):
                old_val = "—" if isinstance(change_value, dict) else str(change_value)
                rows.append({"Field": path, "Tag": tag, "Change": label,
                             "Old value": old_val, "New value": "—"})
            else:
                new_val = "—" if isinstance(change_value, dict) else str(change_value)
                rows.append({"Field": path, "Tag": tag, "Change": label,
                             "Old value": "—", "New value": new_val})
        elif isinstance(value, dict):
            rows.extend(flatten_diff_tree(value, path, tag_map))
    return rows


def format_change_label(change_type: str, key: str, change_value) -> str:
    """Return a plain-text label for a single diff node (used by CLI and raw report)."""
    _val = "" if isinstance(change_value, dict) else f": {change_value}"
    if change_type == "values_changed":
        return f"Changed {key}: {change_value}"
    if change_type == "dictionary_item_added":
        return f"Added {key}{_val}"
    if change_type == "dictionary_item_removed":
        return f"Removed {key}{_val}"
    if change_type == "iterable_item_added":
        return f"Added (list) {key}{_val}"
    if change_type == "iterable_item_removed":
        return f"Removed (list) {key}{_val}"
    return f"{change_type} {key}{_val}"


def load_default_exclude_fields(file_path: str = "ignored_fields.txt") -> list[str]:
    try:
        with open(file_path) as f:
            return [line.strip() for line in f
                    if line.strip() and not line.strip().startswith("#")]
    except OSError:
        return []
