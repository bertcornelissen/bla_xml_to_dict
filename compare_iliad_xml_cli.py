import argparse
import sys
from pathlib import Path

from deepdiff import DeepDiff

from iliad_core import (
    build_diff_tree,
    extract_all_msgs,
    get_exclude_paths,
    load_default_exclude_fields,
    xml_to_dict,
)

def render_diff_tree(tree, rich_tree):
    for key, value in tree.items():
        if isinstance(value, dict) and "type" in value:
            change_type = value['type']
            change_value = value['value']
            
            # Format based on change type
            _val = "" if isinstance(change_value, dict) else f": {change_value}"
            if change_type == 'values_changed':
                label = f"[yellow]Changed[/yellow] {key}: {change_value}"
            elif change_type == 'dictionary_item_added':
                label = f"[green]Added[/green] {key}{_val}"
            elif change_type == 'dictionary_item_removed':
                label = f"[red]Removed[/red] {key}{_val}"
            elif change_type == 'iterable_item_added':
                label = f"[green]Added (list)[/green] {key}{_val}"
            elif change_type == 'iterable_item_removed':
                label = f"[red]Removed (list)[/red] {key}{_val}"
            else:
                label = f"[{change_type}] {key}{_val}"
            
            rich_tree.add(label)
        elif isinstance(value, dict):
            branch = rich_tree.add(str(key))
            render_diff_tree(value, branch)
        else:
            rich_tree.add(f"{key}: {value}")

def tree_by_path_output(diff, output_file=None):
    from rich.tree import Tree
    from rich.console import Console

    diff_tree = build_diff_tree(diff)
    
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
