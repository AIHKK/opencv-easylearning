# 第一课：手势控制舵机（hand_control.py）

## 运行步骤

1. 确保 ESP32 板子已上电并连上 WiFi（浏览器能打开 `http://192.168.3.105` 就说明就绪）
2. VSCode 打开 `cv-learning` 文件夹 → 打开 `hand_control.py` → 右上角"运行 Python 文件"
   （或终端：`.venv\Scripts\python hand_control.py`）
3. 弹出摄像头窗口后：
   - **拇指+食指** 捏合距离 → 舵机0（捏得越拢角度越小，张得越开角度越大）
   - **拇指+中指 / 无名指 / 小指** → 分别控制舵机1 / 舵机2 / 舵机3
   - 选中规则：拇指离哪根手指最近就控制哪路，画面上该指尖红圈高亮并显示距离比例
   - **手离开画面** → 四路全部自动回中 90°
   - **q / ESC** → 退出

## 文件结构

```
cv-learning/
├── hand_control.py              主程序（本课全部代码）
├── models/hand_landmarker.task  MediaPipe 手部关键点模型（已下载）
├── requirements.txt             依赖列表
└── .venv/                       虚拟环境
```

## 代码地图（学习重点）

| 位置 | 内容 |
|---|---|
| 配置区（文件头） | 所有可调参数：IP、平滑、死区、PINCH_MIN、各手指的 PINCH_MAXS |
| `HAND_CONNECTIONS` | 21 个关键点的骨架连线——手部拓扑 |
| `ws_worker()` | WebSocket 后台线程，断线自动重连（网络与视觉解耦） |
| `main()` 循环 | 读帧 → 识别 → 映射 → 平滑+死区+节流 → 发送 → 画HUD |

## 课后练习（按顺序做）

1. 把 `SMOOTH` 改成 `0.05` 和 `0.8` 各试一次，体会"稳"与"跟手"的权衡
2. 看 HUD 上的 `pinch` 数值：小指尽量张开时如果到不了 0.90，把 `PINCH_MAXS` 里小指的上限改成你的实测值（这就是标定）
3. 捏合方向反过来（捏拢=180°）：把映射公式的分子改成 `(PINCH_MAXS[k] - r)`
4. 在画面上把四根手指的距离比例全部显示出来（不只是选中的那根）
5. 进阶：按某键开始/停止记录某根手指 5 秒轨迹，然后自动回放给对应舵机（路径录制与回放）

## 看数据 & 调参（遥测 telemetry）

程序运行时会同时输出三路数据：

1. **窗口 HUD**：第 4 行实时显示四根手指的距离比例（I/M/R/P）
2. **终端**：每 0.5 秒打印一行 `[tele]`（比例、选中手指、目标角度）
3. **telemetry.csv**（同目录）：每 0.5 秒写一行完整数据，用 Excel 或 pandas 事后分析

调参对照表：

| 看到什么现象 | 调哪里 |
|---|---|
| 某根手指张到最大，比例仍到不了 PINCH_MAXS | 把该手指的 PINCH_MAXS 改成实测最大值 |
| 手指不动，角度仍小幅跳动 | 调大 SMOOTH（如 0.2）或 DEADBAND（如 2） |
| 跟手太慢、延迟感强 | 调大 SMOOTH（如 0.5） |
| 想事后画曲线分析 | pandas 读 telemetry.csv |

## 常见问题

- **摄像头黑屏/打不开**：`CAM_INDEX` 改成 `1`
- **WS 显示 NOT-CONNECTED**：板子 IP 变了（看串口打印的 IP），改 `ESP32_WS_URL`
- **舵机动、网页滑块不动**：正常，脚本和网页是两个独立客户端
- **骨架抖动明显**：调大 `SMOOTH`（更稳）或调高 `min_hand_detection_confidence`
- **注意**：你装的 MediaPipe 是 1.0 新版，**网上老教程的 `mp.solutions.hands` 写法已失效**，
  本脚本用的是新的 Tasks API（`HandLandmarker` + `hand_landmarker.task` 模型文件）
