import copy
import json
from pathlib import Path

from .position_agent import PositionAgent


class PositionPlanningStage:
    STAGE_FILENAME = "director_stage2_position_planning.json"

    def __init__(
        self,
        script_json,
        scene_info_json,
        template_json,
        position_lib_json,
        api_key=None,
        api_url=None,
        model=None,
        output_dir=None,
        stage_output_dir=None,
        script_output_path=None,
    ):
        self.script_json = script_json
        self.scene_info_json = scene_info_json
        self.template_json = template_json
        self.position_lib_json = position_lib_json
        self.api_key = api_key
        self.api_url = api_url
        self.model = model
        self.output_dir = Path(output_dir) if output_dir else Path("Assets") / "Json"
        self.stage_output_dir = Path(stage_output_dir) if stage_output_dir else self.output_dir / "AgentStage"
        self.script_output_path = Path(script_output_path) if script_output_path else self.output_dir / "script_with_shot_description.json"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.stage_output_dir.mkdir(parents=True, exist_ok=True)
        self.agent = None
        self.result = None

    def run(self):
        self.agent = PositionAgent(
            script_json=self.script_json,
            scene_info_json=self.scene_info_json,
            template_json=self.template_json,
            position_lib_json=self.position_lib_json,
            api_key=self.api_key,
            api_url=self.api_url,
            model=self.model,
            output_dir=self.output_dir,
            stage_output_dir=self.stage_output_dir,
        )
        final_plan = self.agent.run()

        self.result = {
            "where": self.agent.where,
            "stage": "position",
            "description": "Director stage 2 position planning completed with the legacy three-step PositionAgent pipeline.",
            "input_script_path": str(self.script_output_path).replace("\\", "/"),
            "substage_sequence": [
                {
                    "name": "grouping",
                    "description": "Divide positions into interaction groups and singles.",
                    "stage_output_path": str(self.stage_output_dir / "position_stage1_grouping.json").replace("\\", "/"),
                    "result": copy.deepcopy(self.agent.stage1_result),
                },
                {
                    "name": "planning",
                    "description": "Assign region, layout, and lookat to each group or single.",
                    "stage_output_path": str(self.stage_output_dir / "position_stage2_planning.json").replace("\\", "/"),
                    "result": copy.deepcopy(self.agent.stage2_result),
                },
                {
                    "name": "compilation",
                    "description": "Compile the validated final position_plan.json.",
                    "stage_output_path": str(self.stage_output_dir / "position_stage3_compilation.json").replace("\\", "/"),
                    "result": copy.deepcopy(self.agent.final_plan),
                },
            ],
            "outputs": {
                "position_plan_root_path": str(Path("position_plan.json")).replace("\\", "/"),
                "position_plan_output_path": str(self.output_dir / "position_plan.json").replace("\\", "/"),
                "position_detail_root_path": str(Path("position_detail.json")).replace("\\", "/"),
                "position_detail_output_path": str(self.output_dir / "position_detail.json").replace("\\", "/"),
            },
            "position_plan": copy.deepcopy(final_plan),
        }
        self._write_json_file(self.stage_output_dir / self.STAGE_FILENAME, self.result)
        return copy.deepcopy(self.result)

    def _write_json_file(self, path, payload):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
