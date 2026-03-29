import argparse
import sys
import xmltodict
from deepdiff import DeepDiff
from pathlib import Path
import re


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
    """Walk the parsed XML dict and replace every FieldList that contains
    Field elements with the output of transform_fields."""
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


def extract_all_msgs(data: dict) -> list[dict]:
    """Return a list of (label, msg_dict) for every OnlineMessage that contains a MSG field."""
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

def get_exclude_paths(exclude_fields):
    """
    Convert field names to regex patterns for DeepDiff exclude_paths.
    This creates patterns that match the field at any level in the nested structure.
    """
    patterns = []
    for field in exclude_fields:
        # Match the field at any level: root['msg']['msg_body']['field_name']
        # Using regex to match any path ending with the field name
        pattern = re.compile(rf".*\['{field}'\]$")
        patterns.append(pattern)
    return patterns

def insert_path(tree, path, value):
    node = tree
    for part in path[:-1]:
        if part not in node:
            node[part] = {}
        node = node[part]
    node[path[-1]] = value

def parse_path(path_str):
    # Remove root and split by ['...
    parts = []
    current = ''
    in_bracket = False
    for c in path_str:
        if c == '[':
            in_bracket = True
            current = ''
        elif c == ']':
            in_bracket = False
            parts.append(current.strip("'"))
        elif in_bracket:
            current += c
    return parts

def render_diff_tree(tree, rich_tree):
    for key, value in tree.items():
        if isinstance(value, dict) and "type" in value:
            change_type = value['type']
            change_value = value['value']
            
            # Format based on change type
            if change_type == 'values_changed':
                label = f"[yellow]Changed[/yellow] {key}: {change_value}"
            elif change_type == 'dictionary_item_added':
                label = f"[green]Added[/green] {key}: {change_value}"
            elif change_type == 'dictionary_item_removed':
                label = f"[red]Removed[/red] {key}: {change_value}"
            elif change_type == 'iterable_item_added':
                label = f"[green]Added (list)[/green] {key}: {change_value}"
            elif change_type == 'iterable_item_removed':
                label = f"[red]Removed (list)[/red] {key}: {change_value}"
            else:
                label = f"[{change_type}] {key}: {change_value}"
            
            rich_tree.add(label)
        elif isinstance(value, dict):
            branch = rich_tree.add(str(key))
            render_diff_tree(value, branch)
        else:
            rich_tree.add(f"{key}: {value}")

def tree_by_path_output(diff, output_file=None):
    from rich.tree import Tree
    from rich.console import Console
    diff_tree = {}
    
    for change_type, changes in diff.items():
        # With verbose_level=2, all change types are dicts
        if isinstance(changes, dict):
            for path_str, value in changes.items():
                path = parse_path(path_str)
                if path:
                    insert_path(diff_tree, path, {"type": change_type, "value": value})
        else:
            # Fallback for non-dict types (shouldn't happen with verbose_level=2)
            for path_str in changes:
                path = parse_path(str(path_str))
                if path:
                    insert_path(diff_tree, path, {"type": change_type, "value": str(path_str)})
    
    rich_tree = Tree("root")
    render_diff_tree(diff_tree, rich_tree)
    console = Console()
    console.print(rich_tree)
    
    # Save to file if output_file is specified
    if output_file:
        with open(output_file, 'w') as f:
            # Use force_terminal=True and legacy_windows=False to ensure colors are preserved
            file_console = Console(file=f, width=120, force_terminal=True, legacy_windows=False)
            file_console.print(rich_tree)

def load_default_exclude_fields(file_path="ignored_fields.txt"):
    try:
        with open(file_path, "r") as f:
            return [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
    except Exception as e:
        print(f"Warning: Could not read {file_path}: {e}")
        return []

def main():
    parser = argparse.ArgumentParser(
        description="Compare two ILIAD XML files message by message and show differences.",
    )
    parser.add_argument("file_a", type=Path, metavar="FILE_A", help="First XML file (reference)")
    parser.add_argument("file_b", type=Path, metavar="FILE_B", help="Second XML file (comparison)")
    parser.add_argument(
        "--ignore", "-i",
        metavar="FIELD",
        action="append",
        default=[],
        dest="extra_ignore",
        help="Field name to ignore during comparison (may be repeated)",
    )
    parser.add_argument(
        "--ignore-file",
        metavar="FILE",
        default="ignored_fields.txt",
        help="File with field names to ignore, one per line (default: ignored_fields.txt)",
    )
    parser.add_argument(
        "--output-dir",
        metavar="DIR",
        type=Path,
        default=Path("."),
        help="Directory for diff_tree_msgN.txt output files (default: current directory)",
    )
    parser.add_argument(
        "--ignore-order",
        action="store_true",
        default=False,
        help="Ignore order differences in lists",
    )
    args = parser.parse_args()

    for p in (args.file_a, args.file_b):
        if not p.exists():
            print(f"Error: file not found: {p}", file=sys.stderr)
            sys.exit(1)

    a_msgs = extract_all_msgs(xml_to_dict(args.file_a))
    b_msgs = extract_all_msgs(xml_to_dict(args.file_b))

    exclude_fields = load_default_exclude_fields(args.ignore_file) + args.extra_ignore
    exclude_paths = get_exclude_paths(exclude_fields)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    pairs = list(zip(a_msgs, b_msgs))
    if not pairs:
        print("No comparable messages found.")
        return

    for i, (a_entry, b_entry) in enumerate(pairs):
        print(f"\n{'='*60}")
        print(f"Message {i+1}:")
        print(f"  A: {a_entry['label']}")
        print(f"  B: {b_entry['label']}")
        print('='*60)

        diff = DeepDiff(
            a_entry["msg"], b_entry["msg"],
            verbose_level=2,
            exclude_regex_paths=exclude_paths,
            ignore_order=args.ignore_order,
        )
        if not diff:
            print("  (no differences)")
        else:
            print("\nDiff tree:")
            output_file = args.output_dir / f"diff_tree_msg{i+1}.txt"
            tree_by_path_output(diff, output_file=str(output_file))

    if len(a_msgs) != len(b_msgs):
        for e in a_msgs[len(b_msgs):]:
            print(f"\n[A only] {e['label']}")
        for e in b_msgs[len(a_msgs):]:
            print(f"\n[B only] {e['label']}")


if __name__ == "__main__":
    main()
