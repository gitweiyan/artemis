# LocateAnything 蓝色指针基线

Date: 2026-11-02  
Model: `andai-labs/LocateAnything-3B-MLX`  
Prompt: `Point to: the blue circular mouse pointer.`  
Mode: fast, max_tokens=64

## 结论

**没有全部识别到。**

```text
输出点：13/14
正确：6/14
错误/NONE：8/14
```

LocateAnything 有输出覆盖率，但精度不足以单独做指针定位。

## 逐图结果

| 图 | LocateAnything | 实际 | 结果 |
|---|---|---|---|
| taobao_1 | (111,38) | 真指针约 (177,308) | 错 |
| taobao_2 | (231,580) | 真指针约 (237,576) | ✅ |
| alipay_1 | (222,365) | 绿色“菜鸟”图标，非指针 | 错 |
| alipay_2 | (421,596) | 真指针约 (426,384) | 错 |
| weibo_1 | (172,316) | 主屏，不是微博 | 无效 |
| weibo_2 | None | 主屏，不是微博 | 无效 |
| pinduoduo_1 | (426,384) | 真指针约 (428,385) | ✅ |
| pinduoduo_2 | (253,579) | 真指针约 (252,298) | 错 |
| wechat_1 | (225,377) | 真指针约 (226,378) | ✅ |
| wechat_2 | (297,595) | 真指针约 (298,596) | ✅ |
| hema_1 | (240,500) | 真指针约 (245,498) | ✅ |
| hema_2 | (265,500) | 蓝色登录按钮，非指针 | 错 |
| meituan_1 | (208,487) | 真指针约 (209,486) | ✅ |
| meituan_2 | (222,949) | 真指针约 (261,308) | 错 |

有效 App 帧（排除微博）中正确约 6/12。

## 错误模式

1. **蓝色 Logo / 图标**
   - alipay_1 绿色“菜鸟”图标；
   - hema_2 蓝色登录按钮；
   - 模型没有 hole/空心环约束。

2. **位置偏移**
   - alipay_2、pinduoduo_2、meituan_2 都指向页面其他蓝色元素；
   - 说明 prompt 层面的“蓝色圆环”不足以排除同色 UI。

3. **漏检**
   - weibo_2 输出 `None`。

## 与 HSV 对比

```text
BluePointerDetector: 10/12 有效 App 帧正确，0 误检
LocateAnything:       6/12 有效 App 帧正确
```

当前基线：

```text
HSV 空心环 > LocateAnything
```

LocateAnything 可以作为**候选生成器**，但不能单独决定指针位置。

## 建议

- 保留 HSV + hole 作为主检测器；
- LocateAnything 只在 HSV 无候选时提供 coarse candidate；
- 对 LocateAnything 候选加 hole/圆度/ROI 验证；
- 使用更适合“指针”的 prompt 或 ROI crop 再测；
- 不应把 13/14 的输出覆盖率误当成召回率。

## 产物

- `captures/pointer_baseline_blue/locateanything_baseline.png`
- `captures/pointer_baseline_blue/locateanything_crops.png`

## Prompt 2 对照

Prompt:

```text
Point to the center of the small blue outline ring, not a solid circle.
```

结果更差：

```text
正确：2/14
```

仅 `alipay_2`、`meituan_1` 接近真指针；其余多数输出为右侧大框或错误坐标。
模型把长句拆成大量重复 ref，并尝试框住“not a solid circle”的语义，而不是定位空心环。

结论：

```text
简短外观 prompt 优于长否定句
LocateAnything 对句式高度敏感
不能用“not a solid circle”来替代 hole 几何过滤
```

图：`captures/pointer_baseline_blue/locateanything_baseline2.png`
