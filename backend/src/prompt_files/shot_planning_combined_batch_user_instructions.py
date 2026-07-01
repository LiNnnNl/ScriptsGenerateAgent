shot_planning_combined_batch_user_instructions_prompt = """You will receive a local sequence window containing up to four beats.
Return one result for every beat in the same order and with the same beat_index.
For each beat, first infer the full interaction state for the current script line.
Then generate a one-or-two-sentence shot_description that strictly follows that analysis.
Use previous_line and next_line as the immediate neighboring beats for that beat.
Use context_window_before and context_window_after as short continuity summaries covering up to three additional beats on each side beyond the immediate neighbors.
Current_line is still the primary source of truth for each beat. The short context windows are only auxiliary evidence for continuity, transition, and upcoming participation.
Use focus_character = speaker when a speaker exists. If there is no speaker, infer focus from move or actions.
Choose interaction_type from: dialogue, monologue, confrontation, presentation, observation, movement, idle.
Assume all current_position characters are physically present unless strongly implied otherwise.
For each present character assign exactly one visibility: foreground, midground, background, offscreen.
For each present character assign exactly one participation: primary, secondary, observer, inactive.
Only primary and secondary characters belong to the main interaction.
Observer and background characters must stay outside the main interaction composition.
A character is secondary only if there is direct interaction evidence in the current beat.
Direct interaction evidence includes: being explicitly addressed, being directly responded to, taking part in the same action exchange, being on the current dialogue axis, or being the immediate movement target in a movement beat.
A character is observer if they are only present, only visible, only approaching, only waiting, or only reacting weakly without direct exchange in the current beat.
Physical presence alone is never enough to classify a character as secondary.
If a character will speak in the next few beats but is not yet engaged in the current beat, classify them as observer and put them in upcoming_active_characters instead of secondary.
Do not promote a character to secondary only because context_window_after shows that they become active later.
For dialogue or confrontation, do not label multiple secondary characters unless the line clearly addresses multiple people at once.
Use interaction_evidence and observer_evidence strings to justify each non-primary classification.
For each present character assign one spatial_role from: leader, responder, co_actor, observer, background, target.
Choose group_structure from: one_to_one, one_to_many, many_equal, one_to_one_plus_observer, isolated.
Analyze temporal continuity using move, previous line, and next line.
Use context_window_before and context_window_after to decide whether the current beat is continuing a local exchange, transitioning into one, or breaking away from one.
Choose transition_type from: none, enter, exit, regroup, approach, disperse.
If a character is about to join interaction, keep them out of the main interaction but mark them in upcoming_active_characters.
The shot_description must include all present characters.
The shot_description must clearly separate interacting characters from observers or background characters.
The shot_description must include visible spatial arrangement such as left, right, center, foreground, midground, or background.
The shot_description must reflect the hierarchy between primary, secondary, observer, and inactive characters.
If transition_type is not none, reflect that change spatially with words such as approaching, shifting, leaving, or regrouping.
Do not explain story meaning, emotion, or plot logic.
Do not group all characters unless the interaction analysis says they are all active participants.
Observers and background characters must not be described as part of the main interaction composition.
Output only the requested JSON structure."""
