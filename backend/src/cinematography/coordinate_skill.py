"""
Coordinate Skill — placeholder for character position coordinate calculation.

TODO: Replace the placeholder offset logic with actual spatial computation:
  - Layout-specific formation geometry (two_person: face-to-face along Z;
    triangle: 120° spread at radius; line: collinear with spacing; etc.)
  - NavMesh clearance / collision avoidance
  - Orientation-aware placement (face toward lookat target)
"""

from typing import Any, Dict, List


class CoordinateSkill:
    """
    Computes x/y/z character placement coordinates from scene_info anchors.

    Inputs
    ------
    region_name     : scene region to place characters in
    anchor_name     : anchor or scene_marker name within that region
    layout          : layout type from LayoutLib (e.g. "two_person", "triangle")
    layout_lib      : parsed LayoutLib.json dict
    scene_info      : parsed scene_info JSON for the scene
    group_positions : ordered list of position_ids to assign coordinates to

    Output
    ------
    {position_id: {"x": float, "y": float, "z": float}}
    """

    def calculate(
        self,
        region_name: str,
        anchor_name: str,
        layout: str,
        layout_lib: Dict[str, Any],
        scene_info: Dict[str, Any],
        group_positions: List[str],
    ) -> Dict[str, Dict[str, float]]:
        anchor_pos = self._find_anchor_position(region_name, anchor_name, scene_info)
        spacing = self._get_layout_spacing(layout, layout_lib)
        result = {}
        count = len(group_positions)
        for i, position_id in enumerate(group_positions):
            offset = self._compute_offset(i, count, spacing)
            result[position_id] = {
                "x": round(anchor_pos.get("x", 0.0) + offset["x"], 4),
                "y": round(anchor_pos.get("y", 0.0), 4),
                "z": round(anchor_pos.get("z", 0.0) + offset["z"], 4),
            }
        return result

    # ──────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────

    def _find_anchor_position(
        self, region_name: str, anchor_name: str, scene_info: Dict
    ) -> Dict[str, float]:
        """Locate anchor/marker coordinates in scene_info; fall back to first anchor."""
        for region in scene_info.get("regions", []):
            if region.get("name") != region_name:
                continue
            for anchor in region.get("anchors", []):
                if anchor.get("name") == anchor_name:
                    return anchor.get("position", {"x": 0.0, "y": 0.0, "z": 0.0})
            for marker in region.get("scene_markers", []):
                if marker.get("name") == anchor_name:
                    return marker.get("position", {"x": 0.0, "y": 0.0, "z": 0.0})
            anchors = region.get("anchors", [])
            if anchors:
                return anchors[0].get("position", {"x": 0.0, "y": 0.0, "z": 0.0})
        return {"x": 0.0, "y": 0.0, "z": 0.0}

    def _get_layout_spacing(self, layout: str, layout_lib: Dict) -> float:
        for entry in layout_lib.get("layout_library", []):
            if entry.get("layout") == layout:
                return float(entry.get("parameters", {}).get("spacing", 1.5))
        return 1.5

    def _compute_offset(self, index: int, total: int, spacing: float) -> Dict[str, float]:
        # TODO: implement layout-specific formation geometry
        # Current placeholder: evenly space along X axis, centered at anchor
        if total <= 1:
            return {"x": 0.0, "z": 0.0}
        half = (total - 1) / 2.0
        return {"x": round((index - half) * spacing, 4), "z": 0.0}
