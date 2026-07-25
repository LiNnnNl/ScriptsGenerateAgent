camera_planning_analysis_batch_user_instructions_prompt = """You will receive a local sequence window containing up to four beats.
Return one result for every beat in the same order and with the same beat_index.
Plan each beat independently, but use neighboring beats in the same window to maintain a coherent local shot rhythm.
This stage receives only dialogue and movement beats. Empty shots (speaker/content both blank) are protected by the system and excluded from character camera assignment.
For every beat received here, shot is fixed by the system as 'character'. Do not plan any other shot category.
camera_subject is the default subject that every shot type refers to.
If speaker exists, camera_subject is the speaker. A medium shot means a medium shot of the speaker. A medium close-up means a medium close-up of the speaker. A close shot means a close shot of the speaker. A first-person shot means the speaker's POV. A low-angle shot means a low-angle shot of the speaker. A high-angle shot means a high-angle shot of the speaker.
If there is no speaker and the beat is a movement beat, camera_subject defaults to the moving character. In that case, the chosen shot type is still centered on that moving character.
Choose recommended_shot_type strictly from camera_library keys only.
When reading camera_library, pay attention to the shot name, framing scope, and primary usage only. Ignore any low-level transform rules.
Prefer a richer mix of valid shot types when the beats support it, so the sequence does not become visually flat.
Repetition is allowed when the local sequence rhythm or dramatic need clearly supports it.
For sustained conversations, consider relation shots, speaker-emphasis shots, and wider re-establishing shots when justified, but do not force variation if repetition is the better choice.
Choose recommended_shot_blend strictly from the provided valid values.
Prefer static framing. recommended_follow must be 0 unless the beat is an explicit move beat or a subject is clearly traveling through frame.
Movement beats usually use wider or tracking-oriented shots. Dialogue beats usually stay in medium or medium-close framing unless spatial relationships must stay visible.
Use over-the-shoulder only when the current blocking truly contains a two-person target relationship.
Use low-angle or high-angle shots sparingly and only when visual dominance or weakness is clearly supported by the current beat.
Use scene region context: corridor-like regions favor tighter framing, plaza-like regions favor wider framing.
Use shot_blend_guide to understand the intended transition feel of each blend type before choosing one.
If a fallback recommendation already fits the blocking, stay close to it.
Return only the requested JSON structure."""
