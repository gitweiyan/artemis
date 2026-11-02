# 鼠标指针识别：错误案例复盘与优化决策

Date: 2026-11-02  
Scope: iOS UVC 指针识别（`RedPointerDetector` / `RingShapeDetector` / nudge diff）

## 1. 结论

采用 **Hybrid A**：

```text
自适应 HSV 圆环
+ 空心/圆度/尺寸过滤
+ 上一位置 ROI
+ nudge 前后帧 diff 兜底
+ 点击前 ≤8px 预检
```

暂不采用完整 TrackNet。  
原因不是 TrackNet 思路错，而是当前 UVC 是 **按需单帧、约 0.9–1.5s/帧**；三帧时序模型会把一次定位拉到 3–5s，指针可能已淡出。

TrackNet 留作 **V2 候选**，前提是先有 ≥10fps 的稳定采集。

## 2. 已确认的错误案例

### 2.1 非红色指针：颜色硬编码导致全漏

证据：

| 图片 | 指针颜色 | RedPointerDetector | HSV 圆环最佳误差 |
|---|---|---|---:|
| `captures/pointer_green.png` | 绿色 | `None` | 23.1px |
| `captures/pointer_orange.png` | 橙色 | `None` | 14.6px |
| `captures/pointer_blue.png` | 蓝色 | `None` | 16.2px |
| `captures/pointer_white.png` | 白色 | `None` | 133.2px |
| `captures/20261019_pointer_not_seen_1408.jpg` | 深灰 | `None` | — |

结论：

```text
RedPointerDetector 只适用于红色指针。
换色后不是“精度下降”，而是直接失效。
```

### 2.2 同色 UI 误检：黄色案例

九张黄色图中：

```text
正确 5/9：yellow1, yellow2, yellow7, yellow8, yellow9
误检 4/9：yellow3, yellow4, yellow5, yellow6
```

误检对象：

- yellow3/4/5：橙色“电影演出”猫图标；
- yellow6：“首页”图标。

区分特征：

```text
真指针 hole ratio：0.34–0.42
误检图标 hole ratio：0.00–0.07
```

结论：

```text
仅靠 HSV 颜色 + 面积/圆度不够；
必须要求“空心环”或更强的形状先验。
```

### 2.3 形状检测器会抓错 UI 圆形元素

`RingShapeDetector` 在多个非红图上返回同一个无关圆：

```text
(0.904, 0.744) 附近
```

它的问题：

- Hough 对图标、徽章、头像都响应；
- `confidence` 固定 0.65，无法区分真假；
- 没有颜色/尺寸/ROI/时序约束。

结论：

```text
形状检测只能作为兜底，不能单独决定点击。
```

### 2.4 贴边指针

绿色/蓝色指针位于 `(20,20)`、`(20,87)`，被 crop 裁掉一部分：

- 面积/宽高过滤不稳定；
- 圆环中心偏移；
- HSV 候选中心误差 16–23px。

结论：

```text
指针必须尽量位于安全区；
贴边时使用 border 专用逻辑，不能套完整圆环阈值。
```

### 2.5 单帧静态歧义

红色淘宝页面里存在：

- 红色 tab；
- 红色优惠券；
- 红色价格/图标。

单帧 HSV 很容易把“红色 UI”当成指针。  
之前 830 案例就是把红色 pointer 当成广告 X，坐标判断反了；这不是检测器的错，而是**目标语义和指针语义混在一起**。

结论：

```text
指针检测必须独立于目标检测；
目标 bbox 和 pointer bbox 不能共用同一个红块。
```

## 3. 当前方案能力边界

| 能力 | 现状 |
|---|---|
| 红色指针 | 较好，HSV + hollow + border |
| 非红色指针 | 基本失效 |
| 同色 UI 干扰 | 黄色 4/9 误检 |
| 形状兜底 | 不可靠，误抓图标 |
| 贴边 | 不稳定 |
| 指针淡出 | 有 wake/nudge 逻辑，但仍可能 `pointer_missing` |
| 运动先验 | `PointerTracker.locate` 已有两帧 diff，但非默认主路径 |
| 点击安全 | ≤8px + 后置 diff，设计正确 |

## 4. 方案比较

| 方案 | 收益 | 成本 | 结论 |
|---|---|---|---|
| 改 HSV 阈值 | 低 | 低 | 只能解决单色，不通用 |
| 通用 HSV 圆环 + hole ratio | 中高 | 低 | **先做** |
| 上一位置 ROI | 高 | 低 | **先做** |
| nudge 两帧 diff | 高 | 中（多一次抓帧） | **低置信度默认启用** |
| 单帧 Tiny Heatmap | 高 | 中高（数据/训练） | V1.5 |
| 三帧 TrackNet | 高（若有视频） | 高 | 暂缓，受 1fps 限制 |
| BCI absolute | 低 | 高 | **放弃**（实测只小范围/焦点吸附） |

## 5. 决策

### 采用 Hybrid A

```text
1. 颜色可配置 HSV（蓝/青/洋红优先，红为兼容）
2. 必须满足空心环：hole ratio ≥ 0.2
3. 尺寸/圆度/面积过滤
4. 以上一位置为中心 80–120px ROI
5. 单帧失败 → nudge 两帧 diff
6. 仍失败 → pointer_missing，拒绝点击
```

### 暂不采用

```text
- 完整 TrackNet 三帧
- YOLO bbox 作为主方案
- BCI absolute 作为指针控制
```

### TrackNet 重启条件

```text
UVC 稳定 ≥10fps
且 Hybrid A 的 P95 误差仍 >5px
且有 5k+ 带中心标签的连续帧
```

## 6. 优化路线

### P1：失败样本与指标

- 建立 `pointer_cases/` 标注集；
- 每张记录：颜色、背景、位置、是否贴边、真值中心；
- 指标：检测率、Median/P95 像素误差、误检率、`pointer_missing` 率。

### P2：通用 HSV 圆环

- HSV 范围由指针颜色配置；
- 增加 `hole_ratio`、圆度、面积、bbox；
- 保留贴边专用分支；
- ROI 限制在上一位置附近。

验收：

```text
yellow3–6 不再误检；
5 个真指针仍命中；
非红颜色至少达到 70% 检测率。
```

### P3：nudge diff 默认兜底

- 单帧低置信度/失败时，发送一次已知小位移；
- 抓前后两帧做 diff；
- 用质心定位，不依赖颜色；
- 记录 `method="diff"`。

验收：

```text
颜色伪装/复杂背景下，漏检率下降；
点击前仍必须 ≤8px。
```

### P4：单帧 Tiny Heatmap（可选）

只有 P2+P3 仍不足时再做：

```text
256×256 ROI
→ Tiny U-Net
→ 64×64 heatmap
→ soft-argmax
```

### P5：TrackNet

只有在帧率达到条件后评估，不作为当前主路径。

## 7. 当前推荐配置

```text
指针颜色：蓝/青/洋红 > 黄 > 白 > 红/橙
采集：1080p 单帧
检测：通用 HSV + hole + ROI
兜底：nudge diff
点击：≤8px + 后置 diff
```

## 8. 最终判断

当前瓶颈不是“模型不够大”，而是：

```text
颜色硬编码
+ 同色 UI 干扰
+ 单帧歧义
+ 贴边/淡出
```

因此优先做 **通用 HSV 圆环 + hole ratio + ROI + nudge diff**，  
TrackNet 是后续增强，不是当前解。
