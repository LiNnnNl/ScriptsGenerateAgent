shot_planning_description_user_instructions_prompt = """Generate one or two sentences describing only visible composition.
Use previous_line and next_line as the immediate neighboring beats.
Use context_window_before and context_window_after only as continuity hints beyond those immediate neighbors.
The shot_description must still describe the current beat's frame, not summarize the whole local sequence.
You MUST include all present characters.
You MUST clearly separate interacting characters from observers or background characters.
You MUST include spatial arrangement such as left, right, center, foreground, midground, or background.
You MUST reflect the hierarchy between primary, secondary, observer, and inactive characters.
If transition_type is not none, reflect that change spatially with words such as approaching, shifting, leaving, or regrouping.
Do not explain story meaning, emotion, or plot logic.
Do not group all characters unless the interaction analysis says they are all active participants.
Observers and background characters must not be described as part of the main interaction composition.
Output only the requested JSON structure."""
