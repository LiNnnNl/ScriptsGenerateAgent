camera_planning_analysis_user_instructions_prompt = """shot is fixed by the system as 'character'. Do not plan any other shot category.
camera_subject is the default subject that every shot type refers to.
If speaker exists, camera_subject is the speaker. For example: 中景 means a medium shot of the speaker; 中近景 means a medium close-up of the speaker; 近景 means a close shot of the speaker; 第一人称镜头 means the speaker's POV; 仰拍镜头 means a low-angle shot of the speaker; 俯拍镜头 means a high-angle shot of the speaker.
If there is no speaker and the beat is a movement beat, camera_subject defaults to the moving character. In that case, the chosen shot type is still centered on that moving character.
Choose recommended_shot_type strictly from camera_library keys only.
When reading camera_library, pay attention to the shot name, framing scope, and primary usage only. Ignore any low-level transform rules.
Prefer a richer mix of valid shot types when the beat supports it, so the sequence does not become visually flat.
Use recent_camera_history as soft reference only. Repeating the same shot type is allowed when the scene rhythm or dramatic need clearly supports it.
For sustained conversations, consider relation shots, speaker-emphasis shots, and wider re-establishing shots when justified, but do not force variation if repetition is the better choice.
Choose recommended_shot_blend strictly from the provided valid values.
Prefer static framing. recommended_follow must be 0 unless the beat is an explicit move beat or a subject is clearly traveling through frame.
Movement beats usually use 全景 or 侧跟镜头. Dialogue beats usually stay in 中景 or 中近景 unless spatial relationships must stay visible.
Use 肩后镜头 only when the current blocking truly contains a two-person target relationship.
Use 仰拍镜头 or 俯拍镜头 sparingly and only when visual dominance or weakness is clearly supported by the current beat.
Use scene region context: corridor-like regions favor tighter framing, plaza-like regions favor wider framing.
Use shot_blend_guide to understand the intended transition feel of each blend type before choosing one.
If the fallback recommendation already fits the blocking, stay close to it.
Return only the requested JSON structure."""
