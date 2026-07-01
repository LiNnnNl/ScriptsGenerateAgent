position_agent_stage2_prompt = """## Stage 2 — 规划阶段 (stage2_planning)

### 3.1 指令
1. 为每个组和单人规划区域、布局和 lookat 值。
2. 只使用 scene_info_json 中的区域和目标。
3. 只使用 position_lib_json 中的布局。
4. 当 shot_description 和对话上下文描述镜头构图或空间焦点时，将它们作为区域选择提示。
5. 如果 scene_info_json.regions[*].spatial_relations 存在，将其作为区域邻接性、连通性和移动距离的首要事实来源。
6. 区域选择优先级为：1) move_position_links 和 spatial_relations，2) 跨连接节拍的地理连续性，3) anchor_count / 调度容量，4) region.description 作为语义参考，5) shot_description 和对话焦点作为辅助构图提示。
7. 如果另一个相同/相邻/近/中等距离的区域在空间上更合理地符合当前移动和调度连续性，不要仅因为某个区域的描述在语义上听起来完美就选择它。
8. 在空间合理性、连续性和 anchor_count 满足后使用 region.description，但在 shot_description 和对话语义之前。
9. 在规划区域时，你必须考虑每个区域的 anchor_count。锚点更多的区域可以支持更多的调度灵活性；只有一个锚点的区域应更保守地使用，除非剧本强烈要求。
10. 你必须考虑 scene_info_json 中描述的区域之间的关系，spatial_relations 优先于 region.description 中的任何自然语言线索。
11. 如果源区域和目标区域之间的直接 spatial_relations 标签为 far（远），则任何移动关联的源/目标对都是无效的。
12. 对于任何移动转换，源位置和目标位置必须映射到同一区域，或仅映射到相邻/近/中等/直接连接的区域。
13. 你绝不能选择在 scene_info_json 中被标记为相距很远的源区域和目标区域作为移动的两端。
14. connected 不意味着 adjacent 或 near。如果源区域和目标区域之间的直接 spatial_relations 标签为 far，即使 connected=true，该移动也是无效的。
15. move_position_links 列出了从 script_json 移动节拍中解析的精确 source_position_id 和 destination_position_id 对。在选择区域时，将每对视为硬性规划约束。
16. move_group_links 列出了 stage1 中接收移动目标位置的任何组或单人。对于任何移动关联的组，你必须考虑该组内每个位置的移动前后变化。如果任何移动成员的源区域到候选目标区域为 far，则该候选区域对整个拥有者无效。
17. 例如，如果 G2 包含来自 G1 的移动目标，则 G1 区域和 G2 区域不能是 far。移动关联的组永远不能使用 far 的目标区域。
18. 在检查移动合法性时，仅使用移动位置的直接源区域 -> 候选目标区域关系。不要通过引用恰好相邻或在语义上合适的第三个区域来证明移动目标的合理性。
19. 最终的 region 字段和最终的 reason 必须完全匹配。在返回之前，重新检查 region 字段是否为实际选定的合法区域。
20. 在修改推理后，不要在 region 字段中留下被拒绝的草稿候选。
21. reason 必须只证明最终选定区域的合理性。不要叙述一连串被拒绝的候选（如 A 无效，所以选 B）同时仍返回 region=A。
22. 如果你在推理过程中修改了候选，请更新 region 字段、layout 字段和 reason，使它们都指向同一个最终选择。
23. 如果两个位置在移动前后属于同一角色，在选择它们的区域时保持地理连续性。
24. 然而，如果移动目标变成一个明确离开场景、退出主要交互或仅作为远处背景观察者的孤立单人，你可以选择一个更适合该退出/撤退构图的相邻或中等距离区域。
25. 对于类似退出的单人，优先选择合理可达的隐蔽、边缘、藏身处或背景支持区域，而非强制将他们放回同一交互区域，但仍保持目标在相邻或中等距离范围内。永远不要为该移动使用相距很远的区域对。
26. 如果多个移动位置因为共同离开、分散、撤退或一起转换而保持分组，当 shot_description 和对话连续性支持时，你也可以为整个组选择一个相邻或中等距离的撤退区域。
27. 当剧本明确描述了协调离场或远离重组的移动时，不要强制同区域连续性。相反，从 scene_info_json 中选择空间上最合理的相邻或中等距离区域，但永远不要使用 far 区域。
28. 组的 lookat.mode 必须是 'center' 或 'target'。
29. 当组的 lookat.mode 为 'target' 时，恰好提供以下之一：target_character 或 target_object。
30. 如果使用 target_character，它必须是该组的角色之一。
31. 如果使用 target_object，它必须是来自 scene_info_json 的目标字符串。
32. 单人的 lookat 必须是来自 scene_info_json 的目标字符串。
33. stage1 中的每个 group_id 和单人 position_id 必须恰好出现一次。

### 3.2 区域规划规则
1. 将 scene_info_json.regions[*].spatial_relations 作为判断两个区域是相邻、近、中等还是远的最强信号。
2. 将 move_position_links 作为来自 script_json 的精确移动对约束。每个列出的 source_position_id -> destination_position_id 对必须避免相距很远的区域分配。
3. 使用 move_group_links 来强制执行组级别的移动连续性。如果一个组包含移动目标位置，则该组的候选区域相对于每个关联源位置和源拥有者必须是非 far 的。
4. 首先满足移动距离合理性和 spatial_relations，然后是连续性，再是 anchor_count。这些满足后，在 shot_description 和对话之前使用 region.description 作为下一个语义适配检查。
5. 如果某个区域在空间上不如另一个相同/相邻/近/中等选项合理，不要仅因其描述匹配场景语义就选择它。
6. 对于较大的组或可能需要多种不同调度选项的场景，优先选择锚点更多的区域。
7. 如果移动创建了新的调度节拍，目标区域仍应首先基于 spatial_relations 在空间上相对于源区域合理，然后是描述。
8. 不要将任何移动映射到直接 spatial_relations 标签为 far 的两个区域。
9. connected 不能覆盖 far。如果直接对为 far，即使区域在更大的场景图中仍然连通，也将其视为 far。
10. 对于移动关联的拥有者，允许同区域、相邻、近或中等距离的目标规划；禁止 far 目标规划。
11. 不要通过引用与某个第三区域的邻接关系来证明移动目标的合理性。只有移动位置的直接源区域 -> 候选区域关系对移动合法性有影响。
12. 返回的 region 字段必须是最终接受的区域，而不是更早被拒绝的草稿。
13. reason 必须只解释返回的区域。不要包含以与 region 字段中存储的区域不同的区域结尾的冗长自我修正。
14. 在 spatial_relations、连续性和 anchor_count 之后，使用 region.description 来区分多个已经合理的候选；然后才使用 shot_description 和对话作为最终的辅助构图检查。
15. 如果目标变成一个孤立单人，且 shot_description 有退出场景、远处背景、被动观察者或主要交互之外的线索，则相邻或中等距离的撤退区域是可以接受的，可能比留在同一区域更好。
16. 如果多个移动位置形成一个协调退出或撤退的组，当相邻或中等距离的撤退区域比原始交互区域更符合 shot_description 时可以接受，但不允许 far 区域。"""
