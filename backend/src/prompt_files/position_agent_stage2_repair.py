position_agent_stage2_repair_prompt = """Repair the provided stage2 planning JSON so that every region choice obeys move constraints and every reason matches the final selected region field.
Do not rewrite valid items unnecessarily. Only fix invalid or inconsistent groups/singles.
Any move-linked source/destination pair is invalid if the direct spatial_relations label is far.
Any move-linked group is invalid if any moved member's source region is far from the group's candidate region.
connected does not override far.
The final region field and the final reason must match exactly.
The reason must justify only the selected region. Do not include rejected candidates or self-correction chains.
Return only the corrected final JSON object with groups and singles."""
