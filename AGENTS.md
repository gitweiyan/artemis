# AGENTS.md — 进入本仓库先读这些

## 动 iOS 机台（UVC / ESP32 HID / 指针 / 点击）之前

1. 读 **[`docs/ios-rig-playbook.md`](docs/ios-rig-playbook.md)**（唯一事实源，含触发条件索引与排障顺序）。
2. 跑 `ARTEMIS_ESP32_PORT=<port> artemis ios-smoke --target <看得见的目标>`，四段全绿再动手。
3. 点击无效时的第一反应是 **按 Esc**（Spotlight/弹层会吞掉点击和 Cmd+H），不是盲点。

## 全局约定

- 测试优先：`uv run pytest tests/unit -q`；改动要有单测；既有失败基线见
  `docs/ios-artemis-fusion-status.md`（admin_console 2 个来自本地 deepseek 配置，
  explorer/video_analyzer 12 个为既有问题）。
- 计划与台账：`docs/superpowers/plans/`，`.superpowers/sdd/<plan>/progress.md`。
- 复盘：《writing-run-retrospectives》技能；新教训按 playbook §7 的**沉淀规则**落成
  代码/检查阶段/断言，做不到就标 `prose-only`。
