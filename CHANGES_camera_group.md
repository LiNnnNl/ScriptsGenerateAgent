# 镜头分组（camera_group）变更记录

同一组的点位可被同一镜头覆盖，AI 生成剧本时会约束同一镜头内的角色必须在同组点位。

---

## scenes_resource.json（两份同步修改）

新增太空站场景，36 个点位，分 9 组（A~H + X）。

每个点位加 `camera_group` 字段，场景顶层加 `camera_groups` 分组汇总：

```json
{
  "id": "SpaceStation",
  "camera_groups": [
    { "id": "A", "name": "主控室", "position_ids": ["Position 1", "Position 2", ...] },
    { "id": "B", "name": "主控室外围", "position_ids": ["Position 6", "Position 22", "Position 23"] }
  ],
  "valid_positions": [
    { "id": "Position 1", "camera_group": "A", "description": "中央指挥台", "is_sittable": false },
    { "id": "Position 6", "camera_group": "B", "description": "主控室走廊外侧", "is_sittable": false }
  ]
}
```

旧场景不含 `camera_groups` 字段，向后兼容。

---

## resource_loader.py（两份同步修改）

`Scene` 类新增字段加载和查询方法：

```python
self.camera_groups = data.get('camera_groups', [])

def get_group_for_position(self, position_id: str) -> str:
    for pos in self.valid_positions:
        if pos['id'] == position_id:
            return pos.get('camera_group', '')
    return ''
```

---

## director_ai.py（两份同步修改）

点位列表显示组标签，并在下方追加分组汇总：

```python
group_tag = f" [组{pos['camera_group']}]" if pos.get('camera_group') else ""
scene_info += f"- **{pos['id']}**{sittable}{group_tag}: {pos['description']}\n"

if scene.camera_groups:
    scene_info += "\n#### 镜头分组（同一镜头只能拍摄同组点位内的角色）:\n"
    for group in scene.camera_groups:
        scene_info += f"- **{group['id']}组 - {group['name']}**: {', '.join(group['position_ids'])}\n"
```

走位决策规则新增约束：

```
- 同一镜头中出现的所有角色，必须位于同一 camera_group 的点位内
- 如需展示不同组的角色，先用移动片段将角色集中到同组点位，再进行对白
- 对白/旁白片段中，所有角色的 current position 必须属于同一 camera_group
```

---

## backend/app.py

API 响应加入 `camera_groups`：

```python
'camera_groups': scene.camera_groups
```

---

## frontend/js/ui.js

场景信息面板先展示分组汇总，再展示带组标签的点位列表：

```javascript
if (scene.camera_groups && scene.camera_groups.length > 0) {
    html += '<div class="camera-groups-info"><strong>镜头分组：</strong><ul>';
    for (const group of scene.camera_groups) {
        html += `<li><strong>${group.id}组 - ${group.name}</strong>: ${group.position_ids.join(', ')}</li>`;
    }
    html += '</ul></div>';
}
html += scene.positions.map(pos => {
    const groupTag = pos.camera_group ? ` <span class="group-tag">[组${pos.camera_group}]</span>` : '';
    return `<p><strong>${pos.id}</strong>${groupTag}: ${pos.description}</p>`;
}).join('');
```
