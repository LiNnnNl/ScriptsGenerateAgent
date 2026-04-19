import argparse
import copy
import json
from pathlib import Path


class PositionDetailConverter:
    def __init__(self, position_plan_json, output_path=None):
        self.position_plan = self._load_json_like(position_plan_json)
        self.output_path = Path(output_path) if output_path else Path("Assets") / "Json" / "position_detail.json"

    def convert(self):
        where = self._require_non_empty_string(self.position_plan.get("where"), "position_plan.where")
        groups = self.position_plan.get("groups", [])
        singles = self.position_plan.get("singles", [])

        if not isinstance(groups, list):
            raise ValueError("position_plan.groups must be a list.")
        if not isinstance(singles, list):
            raise ValueError("position_plan.singles must be a list.")

        detail_groups = []
        detail_signals = []
        used_position_ids = set()

        for group in groups:
            detail_groups.extend(self._convert_group(group, used_position_ids))

        for single in singles:
            detail_signals.append(self._convert_single(single, used_position_ids))

        result = {
            "where": where,
            "groups": detail_groups,
            "signals": detail_signals,
        }
        return result

    def run(self):
        result = self.convert()
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return copy.deepcopy(result)

    def _convert_group(self, group, used_position_ids):
        if not isinstance(group, dict):
            raise ValueError("Each group in position_plan.groups must be an object.")

        group_id = self._require_non_empty_string(group.get("group_id"), "group.group_id")
        region = self._require_non_empty_string(group.get("region"), f"group {group_id!r}.region")
        layout = self._require_non_empty_string(group.get("layout"), f"group {group_id!r}.layout")
        positions = group.get("positions", [])
        lookat = group.get("lookat")

        if not isinstance(positions, list) or len(positions) < 2:
            raise ValueError(f"group {group_id!r} must contain at least two positions.")
        if not isinstance(lookat, dict):
            raise ValueError(f"group {group_id!r}.lookat must be an object.")

        normalized_positions = []
        character_to_position_id = {}
        for index, item in enumerate(positions, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"group {group_id!r}.positions[{index}] must be an object.")
            position_id = self._require_non_empty_string(item.get("position_id"), f"group {group_id!r}.positions[{index}].position_id")
            character = self._require_non_empty_string(item.get("character"), f"group {group_id!r}.positions[{index}].character")

            if position_id in used_position_ids:
                raise ValueError(f"position_id {position_id!r} appears more than once in position_plan.")
            if position_id in character_to_position_id.values():
                raise ValueError(f"Duplicate position_id {position_id!r} inside group {group_id!r}.")
            if character in character_to_position_id:
                raise ValueError(f"Duplicate character {character!r} inside group {group_id!r}.")

            used_position_ids.add(position_id)
            character_to_position_id[character] = position_id
            normalized_positions.append({"position_id": position_id, "character": character})

        mode = self._require_non_empty_string(lookat.get("mode"), f"group {group_id!r}.lookat.mode")
        if mode == "center":
            return [
                {
                    "position_id": item["position_id"],
                    "group_id": group_id,
                    "region": region,
                    "character": item["character"],
                    "layout": layout,
                    "lookat": "center",
                }
                for item in normalized_positions
            ]

        if mode == "target":
            target_character = lookat.get("target_character")
            if isinstance(target_character, str) and target_character.strip():
                target_character = target_character.strip()
                if target_character not in character_to_position_id:
                    raise ValueError(
                        f"group {group_id!r}.lookat.target_character {target_character!r} "
                        "must match one character inside the same group."
                    )
                target_position_id = character_to_position_id[target_character]
                detail_items = []
                for item in normalized_positions:
                    detail_items.append(
                        {
                            "position_id": item["position_id"],
                            "group_id": group_id,
                            "region": region,
                            "character": item["character"],
                            "layout": layout,
                            "lookat": "center" if item["character"] == target_character else target_position_id,
                        }
                    )
                return detail_items

            target_object = lookat.get("target_object")
            target_object = self._require_non_empty_string(
                target_object,
                f"group {group_id!r}.lookat.target_object",
            )
            return [
                {
                    "position_id": item["position_id"],
                    "group_id": group_id,
                    "region": region,
                    "character": item["character"],
                    "layout": layout,
                    "lookat": target_object,
                }
                for item in normalized_positions
            ]

        raise ValueError(f"group {group_id!r}.lookat.mode must be 'center' or 'target'.")

    def _convert_single(self, single, used_position_ids):
        if not isinstance(single, dict):
            raise ValueError("Each single in position_plan.singles must be an object.")

        position_id = self._require_non_empty_string(single.get("position_id"), "single.position_id")
        character = self._require_non_empty_string(single.get("character"), "single.character")
        region = self._require_non_empty_string(single.get("region"), "single.region")
        neartarget = self._require_non_empty_string(single.get("neartarget"), "single.neartarget")
        lookat = self._require_non_empty_string(single.get("lookat"), "single.lookat")

        if position_id in used_position_ids:
            raise ValueError(f"position_id {position_id!r} appears more than once in position_plan.")
        used_position_ids.add(position_id)

        return {
            "position_id": position_id,
            "character": character,
            "region": region,
            "neartarget": neartarget,
            "lookat": lookat,
        }

    def _load_json_like(self, value):
        if isinstance(value, dict):
            return copy.deepcopy(value)
        if isinstance(value, Path):
            return self._read_json_file(value)
        if isinstance(value, str):
            possible_path = Path(value)
            if possible_path.exists():
                return self._read_json_file(possible_path)
            return json.loads(value)
        raise TypeError(f"Unsupported input type: {type(value).__name__}")

    def _read_json_file(self, path):
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))

    def _require_non_empty_string(self, value, label):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} must be a non-empty string.")
        return value.strip()


def _default_input_path():
    for candidate in (
        Path("Assets") / "Json" / "position_plan.json",
        Path("position_plan.json"),
    ):
        if candidate.exists():
            return str(candidate)
    return str(Path("Assets") / "Json" / "position_plan.json")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Convert position_plan.json into position_detail.json.")
    parser.add_argument(
        "--input",
        default=_default_input_path(),
        help="Path to position_plan.json.",
    )
    parser.add_argument(
        "--output",
        default=str(Path("Assets") / "Json" / "position_detail.json"),
        help="Path to output position_detail.json.",
    )
    args = parser.parse_args(argv)

    converter = PositionDetailConverter(args.input, args.output)
    converter.run()
    print(f"[PositionDetailConverter] Completed.", flush=True)
    print(f"[PositionDetailConverter] output: {Path(args.output)}", flush=True)


if __name__ == "__main__":
    main()
