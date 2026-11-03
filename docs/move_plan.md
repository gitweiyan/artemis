我建议把原来的 `move_to_target()` 升级成一个独立的 Visual Mouse Motion Controller（视觉鼠标运动控制器）。

这里最重要的变化不是把 `scale()` 写得更复杂，而是引入两个能力：

1. 闭环运动控制：每次移动一段距离，重新识别鼠标实际位置，根据实际误差决定下一步，而不是一次性把整段位移发送给 ESP32。
2. 轨迹感知观察（Trajectory-aware Observation）：移动过程中不再反复让 JEV 理解整张屏幕，而是根据运动方向预测鼠标的位置，只在预测轨迹附近寻找鼠标；只有跟踪失败时才回退到全屏识别。

我建议采用以下结构：

## Visual Mouse Motion Controller

Target Planner

JEV 输出目标 Bounding Box 和安全命中区域

Motion Planner

粗定位 → 接近 → 精定位，规划下一段相对位移

ESP32 HID Executor

将位移拆分成有节奏的 Relative Mouse Reports

Trajectory Observer

只观察运动走廊，追踪红色鼠标指针

更新实际位置，重新规划下一段运动

这里有一个关键设计原则：

JEV 负责理解 UI，传统计算机视觉负责跟踪鼠标，运动控制器负责计算位移。

不要让 JEV 在每一帧都重新理解整个 UI。这样既浪费计算量，也会让运动控制的反馈延迟变大。

## 一、先确定运动控制模型

原来代码的问题是：

```
return scale(dx, dy, 0.7)
```

这里的 `0.7` 没有考虑三个重要变量：

- 目标的实际大小。
- 当前离目标的距离。
- iOS 指针加速度导致的实际位移和输入位移之间的偏差。

应该改成基于目标尺寸的三阶段运动。

| 阶段        | 距离条件            | 本次运动比例 | 观察策略      |
| --------- | --------------- | ------ | --------- |
| Ballistic | 距离大于目标有效宽度的 4 倍 | 75%    | 低频观察轨迹    |
| Approach  | 目标有效宽度的 1.5～4 倍 | 55%    | 每段运动后观察   |
| Fine      | 目标有效宽度的 1.5 倍以内 | 30%    | 每段运动后精细观察 |

这里的运动比例，是指期望光标本次接近目标的屏幕距离比例，不是直接发送的 HID 位移比例。

例如：

```
当前光标 P0                    目标 T

● ---------------------------------- ◎
       Ballistic: 移动 75%

                   ● -------------- ◎
                         Approach: 55%

                              ● ---- ◎
                                  Fine: 30%
```

随着距离缩短，运动步长自动减小。

这里不使用固定的 100px、20px 阈值，而使用目标的有效宽度 `W`。

对于一个矩形目标，假设运动方向单位向量为 `(ux, uy)`，目标宽高为 `w, h`，沿运动方向的有效宽度可以近似为：

\\[ W=|u_x|w+|u_y|h \\]

这样，水平移动接近一个宽按钮和斜向移动接近一个窄按钮时，系统采用的精度策略会有所不同。

## 二、关键：把光标坐标和 HID 坐标分开

这是整个方案能否稳定工作的基础。

你有两种完全不同的坐标：

| 坐标              | 示例           | 含义            |
| --------------- | ------------ | ------------- |
| Screen Position | `(800, 400)` | 截图中的光标位置      |
| HID Delta       | `(60, 25)`   | ESP32 发送的相对位移 |

不能假设：

\\[ \Delta Screen = \Delta HID \\]

iOS 的指针加速度会使实际位移受到输入速度、报告频率和系统设置影响。

因此需要一个标定模型：

\\[ G_x=\frac{\Delta Screen_x}{\Delta HID_x} \\]

\\[ G_y=\frac{\Delta Screen_y}{\Delta HID_y} \\]

举例，假设某个运动档位下：

```
HID X = 50
屏幕 X = 150 px

Gx = 3 px / HID count
```

那么想让屏幕光标向右移动 300px，初始估算就是：

```
HID X = 300 / 3 = 100
```

这只是初始估算。执行之后必须重新观察实际位置，并根据测量结果修正 Gx、Gy。

### 如何标定

建议在屏幕中部、没有控件干扰的区域执行：

| 档位        | HID 测试位移    | 目的     |
| --------- | ----------- | ------ |
| Fine      | 10、20、30    | 精细移动增益 |
| Approach  | 40、60、80    | 中距离增益  |
| Ballistic | 100、150、200 | 快速移动增益 |

分别测试 X、Y 轴的正负方向，重复三次取中位数。

注意：这些是初始测试档位，不是所有 ESP32 固件都适用的固定参数。每个报告还要控制发送间隔，保证标定和实际运行时的报告节奏一致。

为了更好地应对 iOS 加速度曲线，我建议三个阶段分别保存一套增益，而不是整个系统只使用一个比例系数。

## 三、轨迹观察：不要再全屏搜索鼠标

这是我建议优先优化的部分。

假设现在：

```
P0 = (100, 100)
T  = (900, 600)
```

运动控制器决定本次执行到：

```
P1 = (700, 475)
```

那么下一次观察没有必要处理整个屏幕。

只需要建立：

```
                P1
                ●
              ／
            ／
          ／       ← 搜索走廊
        ／
      ●
      P0
```

用数学表示，轨迹就是线段：

\\[ L(t)=P_0+t(P_1-P_0),\quad 0\leq t\leq1 \\]

然后建立一个有宽度的走廊：

\\[ ROI=\\{p \mid distance(p,L)\<r\\} \\]

其中 `r` 是搜索半径。

我建议：

\\[ r=\max(24,\ 2.5R+3\sigma) \\]

- `R`：之前识别到的红色鼠标环半径。
- `σ`：运动预测误差。
- `24`：初始最小搜索半径，单位是截图像素。

例如：

```
P0 = (100,100)
P1 = (700,475)
R  = 10
σ  = 8

r = max(24, 25 + 24)
  = 49 px
```

此时视觉模型只需要在轨迹附近搜索。

### 观察应该分成两种模式

模式 A：Trajectory Tracking

正常路径

只处理起点与预测终点之间的局部图像，寻找红色鼠标指针。

[Pedestrian Tracker C++ Demo — OpenVINO™  documentation](https://images.openai.com/static-rsc-4/nhcjoV6bcDnEiBqMBC8v7sPfLK5T7pT31zFBlVlqdZBP2swMhk53-ma2hDkf0TPtH6PgT-HVglUKHFi_iqmbL1tlsb6iI4PtXISSN5mDi-r76ZJsIcV8uxf3x6AEVzaaomkq0CmBw-7qKAs8KaV0k6d606w4f8ATp_lXoIEIAdQ?purpose=inline)

[docs.openvino.ai](https://docs.openvino.ai/2023.3/omz_demos_pedestrian_tracker_demo_cpp.html?utm_source=chatgpt.com)

模式 B：Global Recovery

异常路径

ROI 内没有检测到鼠标，扩大搜索范围，必要时恢复全屏识别。

[Ross Goroshin](https://images.openai.com/static-rsc-4/3OWgShTAWV1OFiQnJLb6TnSpTtD5XTFO_jhT6Nki8ODJq3DQBdNP3n42z1z3vkyJu1qV_vkf1FK1jsqKjvo1UB814ZlNMkTZ4mRmMtvRvaIyIfbL0wY32dRUD6V3F6Lo6s1BHo_6-ZJN6vTCbUeSDnuJaN_wHjhO0519a6Tx16Y?purpose=inline)

[goroshin.github.io](https://goroshin.github.io/?utm_source=chatgpt.com)

这里有一个很重要的性能优化：

鼠标指针跟踪不一定需要 JEV。

你的鼠标是一个红色圆环，可以先用 OpenCV 的 HSV 红色分割、圆形度和环形特征检测。

JEV 只需要在开始时识别目标控件，移动过程中用 OpenCV 追踪鼠标。OpenCV 识别失败时再使用 JEV 对局部 ROI 做一次补充识别。

这样可以把昂贵的语义识别从运动主循环中移出去。

## 四、可直接实现的 Python 核心代码

下面这版是一个可以独立集成的 MVP。它不假设你的 WebSocket、UVC 或 ESP32 通信协议，提供了一个很薄的接口，你只需要把现有通信代码接进去。

依赖：

```
pip install numpy opencv-python
```

### 1. 数据结构和目标区域

```
from __future__ import annotationsimport asyncioimport mathfrom dataclasses import dataclass, fieldfrom typing import Optional, Protocolimport cv2import numpy as np@dataclass(frozen=True)class Point:    x: float    y: float@dataclass(frozen=True)class Rect:    x1: float    y1: float    x2: float    y2: float    @property    def width(self):        return max(0.0, self.x2 - self.x1)    @property    def height(self):        return max(0.0, self.y2 - self.y1)    @property    def center(self):        return Point(
```

这里的 `Target` 直接接收 JEV 输出的 Bounding Box。

例如：

```
target = Target(    bbox=Rect(800, 400, 920, 460))
```

不需要每次移动都重新计算目标位置。

### 2. 指针视觉检测器

这里采用你之前研究的红色圆环鼠标方案。

检测时不只看红色，还结合：

- 圆形度。
- 圆环区域的红色占比。
- 圆环内部非红色的程度。
- 上一次检测的半径。
- 预期位置和运动轨迹。

```
def point_to_segment_distance(p, a, b):    vx = b.x - a.x    vy = b.y - a.y    length2 = vx * vx + vy * vy    if length2 < 1e-6:        return math.hypot(p.x - a.x, p.y - a.y)    t = (        (p.x - a.x) * vx        + (p.y - a.y) * vy    ) / length2    t = max(0.0, min(1.0, t))    qx = a.x + t * vx    qy = a.y + t * vy    return math.hypot(p.x - qx, p.y - qy)class RedRingDetector:    def __init__(self, min_confidence=0.40):        self.min_confidence = min_confidence    def detect(        self,        frame,        roi=None,        expected=None,        segment=None,        corridor_width=None,        expected_radius=None,
```

这版代码的定位不是取代所有视觉定位模型，而是让鼠标运动中的指针跟踪足够便宜。

实际部署时，需要根据你红色圆环的实际 HSV、大小和截图缩放比例，调整阈值。置信度也应该用你采集的真实样本校准，而不是把 0.40 当成通用标准。

### 3. 标定模型、HID 报告拆分和轨迹 ROI

下面解决三个问题：

- 分阶段维护 X/Y 位移增益。
- 将相对位移拆成合法的 HID 报告，避免单条报告超出有符号 8-bit 范围。
- 根据起点和预测终点动态建立 ROI。

```
@dataclassclass MotionCalibration:    # 单位：截图像素 / HID count    px_per_count_x: float    px_per_count_y: float    ema: float = 0.25    # 各阶段分别维护增益    phase_gains: dict = field(default_factory=dict)    def gain(self, phase):        return self.phase_gains.get(            phase,            (self.px_per_count_x, self.px_per_count_y)        )    def to_hid(self, phase, screen_dx, screen_dy):        gx, gy = self.gain(phase)        if abs(gx) < 0.01 or abs(gy) < 0.01:            raise ValueError("Invalid HID calibration gain")        return (            round(screen_dx / gx),            round(screen_dy / gy)        )    def update(        self,        phase,        hid_dx,        hid_dy,        actual_dx,
```

这里有两个值得强调的地方。

第一，分阶段增益。

因为不同运动速度下 iOS 可能产生不同的光标增益，所以：

```
phase_gains = {    "ballistic": (gx1, gy1),    "approach":  (gx2, gy2),    "fine":      (gx3, gy3)}
```

比只设置一个 `px_per_count` 更合理。

第二，不要让一个运动段太长。

我建议每段最多 6 条 HID 报告，每条 X/Y 最大 80 counts。也就是每段最多 48ms 的报告时间，然后重新观察。

这是一个可调起点，不是 iOS 的硬性要求。实际最优的段长需要结合截图延迟和指针加速度实测。

### 4. 核心：闭环 move_to_target()

现在把运动规划、指针检测、ROI 构造、实际误差反馈连接起来。

```
@dataclassclass MotionConfig:    max_segment_px: float = 260    max_reports_per_segment: int = 6    max_report_delta: int = 80    report_interval: float = 0.008    min_roi_pad: float = 24    initial_sigma: float = 12    max_segments: int = 40    # 圆环中心到实际点击热点的偏移    hotspot_offset: Point = Point(0, 0)@dataclassclass MoveResult:    position: Point    segments: int    history: listclass TrackingLost(Exception):    passdef choose_phase(position, target):    dx = target.point.x - position.x    dy = target.point.y - position.y    distance = math.hypot(dx, dy)
```

这里的执行顺序是：

```
获得当前指针位置
       ↓
计算目标距离和目标有效宽度
       ↓
确定 Ballistic / Approach / Fine
       ↓
计算期望屏幕位移
       ↓
使用增益模型转换为 HID Delta
       ↓
发送不超过 6 个 HID Reports
       ↓
计算预测终点
       ↓
构造轨迹 ROI
       ↓
截图 + ROI 红环检测
       ↓
计算预测误差
       ↓
更新增益和不确定性
       ↓
继续下一段
```

这就是一个完整的闭环运动控制器。

## 五、怎么初始化和接入你现有的 ESP32 项目

### 1. 先做自动增益标定

可以把下面这个函数加进去：

```
async def measure_gain(io, detector, dx, dy):    """    在安全区域测量一次 HID -> 屏幕坐标增益。    dx/dy 是 HID 总位移。    返回有符号的 px_per_count_x/y。    """    before_frame = await io.screenshot()    before = detector.detect(before_frame)    if before is None:        raise RuntimeError("Cannot detect pointer before calibration")    await send_stroke(io, dx, dy)    await asyncio.sleep(0.08)    after_frame = await io.screenshot()    after = detector.detect(        after_frame,        expected=Point(            before.point.x + dx,            before.point.y + dy        ),        expected_radius=before.radius    )    if after is None:        raise RuntimeError("Cannot detect pointer after calibration")    delta_x = after.point.x - before.point.x    delta_y = after.point.y - before.point.y
```

实际标定应该在屏幕中部的空白区域完成，并且每个档位正负方向各测三次，使用中位数。

初始化示意：

```
calibration = MotionCalibration(    px_per_count_x=measured_gx,    px_per_count_y=measured_gy)calibration.phase_gains = {    "ballistic": (gx_ballistic, gy_ballistic),    "approach": (gx_approach, gy_approach),    "fine": (gx_fine, gy_fine)}
```

这里的增益必须来自真实测量，不能直接把示例数字当成 iPhone 的参数。

### 2. 接入现有通信层

只需要实现两个接口：

```
class ESP32MouseIO:    async def mouse_report(self, dx, dy):        # 调用你现有的 ESP32 USB HID 通信接口        await self.esp32.send_mouse(dx, dy)    async def screenshot(self):        # 调用现有 UVC / 截图接收接口        return await self.camera.capture()
```

真正运行：

```
target = Target(    bbox=Rect(800, 400, 920, 460))result = await move_to_target(    io=io,    target=target,    calibration=calibration)print(result.position)print(result.segments)print(result.history)
```

`target.bbox` 和鼠标位置必须使用同一张完整截图的像素坐标。如果 JEV 使用了缩放图片或局部图片，需要先把 bbox 转换回完整截图坐标。

## 六、进一步优化：让观察真正跟随移动进度

上面的 MVP 采用的是：

```
移动 4～8 个报告
       ↓
截图
       ↓
检查轨迹 ROI
       ↓
规划下一段
```

这已经能避免全屏反复搜索。

但你的设备还有一个更值得利用的能力：如果 UVC 已经可以连续输出视频帧，就不必等整个 stroke 结束才观察。

可以升级为时间同步的轨迹观察。

假设：

```
P0 = (100, 100)
P1 = (500, 350)

运动预计耗时 = 80ms
```

运动过程中每个时间点都有一个预测位置：

\\[ P(t)=P_0+\frac{t}{T}(P_1-P_0) \\]

例如：

| 时间   | 预测位置       |
| ---- | ---------- |
| 0ms  | (100, 100) |
| 20ms | (200, 163) |
| 40ms | (300, 225) |
| 60ms | (400, 288) |
| 80ms | (500, 350) |

此时不需要每一帧都搜索整条轨迹，只需要在当前预测位置周围构建一个局部 ROI。

连续观察窗口示意

蓝色框是局部搜索区域，绿色虚线是预测运动方向。示意图不代表真实屏幕比例。

这一层可以采用以下伪代码：

```
async def observe_during_stroke(    video_stream,    start,    predicted_end,    expected_duration,    detector):    start_time = monotonic()    while stroke_is_running:        frame = await video_stream.next_frame()        elapsed = monotonic() - start_time        t = min(            1.0,            elapsed / expected_duration        )        expected = Point(            start.x + (predicted_end.x - start.x) * t,            start.y + (predicted_end.y - start.y) * t        )        roi = centered_roi(            expected,            width=100,            height=100        )        pointer = detector.detect(            frame,            roi=roi,            expected=expected        )
```

这是第二阶段的优化。第一阶段先用前面完整的闭环版本，避免一开始就引入视频流并发同步的复杂度。

另外还有一个重要细节：上面示例中的 `expected_duration` 应该是实际运动持续时间，不能仅仅取 `8ms × 报告数量` 就认为完全准确。它需要考虑 ESP32 发送延迟、iOS 指针响应延迟和采集帧时间戳。最好用实测的运动开始时间和运动结束时间校准。

## 七、我建议的最终执行频率

不同运动阶段采用不同的观察策略：

| 参数          | Ballistic | Approach | Fine    |
| ----------- | --------- | -------- | ------- |
| 运动比例        | 75%       | 55%      | 30%     |
| 每段 HID 报告上限 | 6         | 4～6      | 1～3     |
| 观察方式        | 轨迹 ROI    | 轨迹 ROI   | 预测点 ROI |
| 失败处理        | 扩大 ROI    | 全局重捕获    | 全局重捕获   |
| JEV 使用      | 一般不使用     | 一般不使用    | 必要时使用   |

这些是初始配置，尤其是 Fine 阶段的报告数量，需要根据实际增益调整。

### 日志和评估

建议保留 `history`，每段记录：

```
{
  "segment": 3,
  "phase": "approach",
  "hid": [35, 12],
  "start": [520, 300],
  "predicted": [590, 324],
  "actual": [602, 330],
  "prediction_error": 13.4,
  "confidence": 0.87,
  "roi": [470, 250, 660, 380],
  "global_recovery": false
}
```

这样你可以重点分析：

- 运动预测误差是否逐渐降低？
- Ballistic 阶段是否经常过冲？
- ROI 的面积占全屏比例是多少？
- 有多少次使用了全屏重捕获？
- 从开始移动到进入安全区域总共耗时多少？
- 一次成功定位平均需要多少次观察？

## 八、最终实施路线

我建议分三个版本推进，而不是一次性实现所有功能。

V1：Closed-loop Motion

优先实现

实现三阶段运动、HID 增益标定、每段运动后截图、实际位移误差修正。

V2：Trajectory ROI Tracking

实现红色圆环 OpenCV 检测、动态走廊 ROI、失败回退全屏。记录 ROI 面积与识别耗时。

V3：Streaming Prediction

接入连续 UVC 帧，以运动时间预测光标位置，动态调整观察窗口，实现运动和视觉跟踪并行。

我最想强调的是一个架构决策：将 Target Recognition 和 Pointer Tracking 彻底分离。

```
JEV:
理解 UI → 找到目标 → 输出 Target BBox

Motion Controller:
Target BBox → 规划路径 → HID Relative Delta

OpenCV Tracker:
预测位置 → 局部 ROI → 找到实际鼠标位置

Feedback:
预测位置 vs 实际位置 → 修正运动模型
```

这套架构和你现在的 JEV + ESP32 + iPhone 实验非常容易衔接。

第一阶段最值得验证的指标是：在保持目标命中率不下降的前提下，能否把每次目标定位所需的截图次数和识别耗时显著降低。 这比单纯追求更大的鼠标移动速度更有意义。
