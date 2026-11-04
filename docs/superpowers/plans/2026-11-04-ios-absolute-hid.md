# iOS 绝对坐标 HID 落地（2026-11-04）

## 背景

2026-11-04 的 spike 证明：在 boot mouse 接口上追加一个绝对 pointer collection（Report ID 2），
iOS 18 接受；`amove` 归一化 0..1000，实测中位误差 1.6 px（最大 3.1 px），`aclick` 在绝对位置点击生效；
相对鼠标与键盘路径不受影响。FKA 方案暂缓（见 `2026-11-03-ios-keyboard-focus-scheme-design.md` 顶部的状态说明）。

## 任务

1. **固件**：保留 `amove`/`aclick`；`hid_mouse_abs_move` 加"报告未就绪重发"（≤5 次、每次 5 ms），
   避免单条报文丢失；`idf.py -B build-ios build` 通过。
2. **控制层**：`Esp32HidControl.mouse_move_abs(nx, ny)`（0..1000 校验）、`mouse_click_abs()`；
   单测用现有 Recorder 断言命令行与越界报错。
3. **会话**：`PointerSession.move_abs(x, y)`（帧像素 → 归一化，更新信仰）、`click_abs()`；
   `SessionTapController` 在控制层支持绝对时优先走一步绝对定位，第一次观测兼作能力探测
   （误差 > max(3×tolerance, 24) 则本会话标记不支持并继续用相对分段）；指标 `phases` 记 `absolute`。
4. **验证**：单测覆盖绝对路径、探测失败回退、相对路径不回归；真机用 move bench 复测
   （目标 224,400，10 次），并确认相对路径仍可用。

## 验收

- 单测全绿（`tests/unit` 保持 14 个既有失败）。
- 真机：绝对路径单次点击 ≤1.5 s（含一次抓帧验证）、误差 ≤8 px；探测失败时自动回退相对且仍能命中。
