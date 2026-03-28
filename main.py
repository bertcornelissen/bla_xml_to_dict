import xmltodict
from deepdiff import DeepDiff
from pprint import pprint
from pathlib import Path


IPH_FILE = Path("IPH - CTAP10 PAR TERM C10KA1 - 1 BRAND_AMEX.emvco")
WLPFO_FILE = Path("WLPFO - CTAP10 PAR TERM FC10M111 - 1 BRAND_AMEX - WLPFO.emvco")


def transform_fields(fields) -> dict:
    if not isinstance(fields, list):
        fields = [fields]
    result = {}
    for field in fields:
        name = field.get("FriendlyName", field["@ID"])
        child_list = field.get("FieldList")
        children = child_list.get("Field") if isinstance(child_list, dict) else None
        if children:
            result[name] = transform_fields(children)
        else:
            result[name] = field.get("FieldViewable")
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
        data = xmltodict.parse(f)
    return apply_field_transforms(data)


def extract_msg(data: dict) -> dict:
    """Navigate to the NET.cp.DE.MSG sub-dict inside the first OnlineMessage's FieldList."""
    online_message = data["EMVCoL3OnlineMessageFormat"]["OnlineMessageList"]["OnlineMessage"]
    if isinstance(online_message, list):
        online_message = online_message[0]
    return online_message["FieldList"]["Message"]

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


def main():
    iph = extract_msg(xml_to_dict(IPH_FILE))
    wlpfo = extract_msg(xml_to_dict(WLPFO_FILE))

    diff = DeepDiff(iph, wlpfo, verbose_level=2)
    if not diff:
        print("The messages are equal.")
    else:
        print("The messages are not equal.")
        print("Differences:")
        print(diff.pretty())

        print("\nDifferences as tree by path:")
        tree_by_path_output(diff, output_file="diff_tree_output.txt")


if __name__ == "__main__":
    main()
