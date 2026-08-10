# Sit / Stand / Empty Shot Test Case

这个测试包用于验证剧本 JSON 中以下能力：

- 空镜：`speaker` 和 `content` 均为空字符串，`duration` 默认为 `5s`
- 坐下动作：`Sit Down`
- 坐姿对白：坐下后下一条对白不再额外触发动作
- 站起动作：`Stand Up`
- 站立说话动作：`Standing Talking 2`

主测试文件：

- `script_sit_stand_empty_shot_test.json`

注意：`Sit Down` / `Stand Up` 已加入动作库，但 Unity 侧仍需要存在对应 FBX：

- `ImportedAnimations\\Sit Down.fbx`
- `ImportedAnimations\\Stand Up.fbx`
