# opencv-easylearning

用摄像头识别手势，控制真实的舵机——OpenCV + MediaPipe + WebSocket + ESP32 的入门实战课。
每一课都是完整可跑的代码，从零打通"视觉识别 → 网络传输 → 嵌入式执行"整条链路。

```
笔记本摄像头 -> MediaPipe 手部关键点 -> 角度换算 -> WebSocket -> ESP32-S3 -> PCA9685 -> 舵机
```

## 课程目录

- [x] **第一课：手势控制四路舵机** —— 拇指与四根手指的捏合距离分别控制 4 路舵机（0~180°），手离开画面自动回中

## 仓库结构

```
├── hand_control.py    # PC 端视觉程序（第一课）
├── firmware/          # ESP32 固件侧（Aily Blockly 工程快照 + 通信协议说明）
└── requirements.txt   # PC 端依赖
```

## 快速开始

### 0. 硬件（可选）

没有硬件也能跑：视觉识别、HUD、遥测数据全部正常工作，只是舵机不会动（WS 显示 NOT-CONNECTED）。

要控制真实舵机需要：

- ESP32-S3 开发板（固件用 [Aily Blockly](https://yiyu.pro) 积木编程生成）
- PCA9685 16 路 PWM 舵机驱动板
- 舵机 ×4（如 SG90），独立 5V 供电（≥2A），与 ESP32 共地
- 接线：ESP32 `3V3→VCC`、`GPIO8→SDA`、`GPIO9→SCL`、`GND→GND`；舵机电源接 PCA9685 绿色端子 `V+/GND`

### 1. 安装环境（Python 3.10+）

```powershell
git clone https://github.com/AIHKK/opencv-easylearning.git
cd opencv-easylearning
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 下载手部模型（必需，约 7.8MB，仓库不收录大文件）

```powershell
mkdir models
curl.exe -L -o models\hand_landmarker.task https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task
```

### 3. 配置板子地址

打开 `hand_control.py`，把配置区的 `ESP32_WS_URL` 改成你板子的实际地址
（固件串口启动时会打印 IP，默认值是 `ws://192.168.3.105/ws`）。

### 4. 运行

```powershell
python hand_control.py
```

操作方式：

| 手势 | 效果 |
|---|---|
| 拇指 + 食指/中指/无名指/小指 捏合 | 分别控制舵机 0/1/2/3（捏拢角度变小，张开变大） |
| 拇指离哪根手指最近 | 选中哪路（画面红圈高亮并显示距离比例） |
| 手离开画面 | 四路全部自动回中 90° |
| 按 q / ESC | 退出 |

## 看数据 & 调参（遥测 telemetry）

运行时同时输出三路数据：

1. **窗口 HUD**：四根手指的距离比例（I/M/R/P）+ 当前角度 + FPS
2. **终端**：每 0.5 秒一行 `[tele]`（比例、选中手指、目标角度）
3. **telemetry.csv**：每 0.5 秒一行完整数据（已被 gitignore，不会提交）

| 看到什么现象 | 调哪里 |
|---|---|
| 某根手指张到最大，比例仍到不了 PINCH_MAXS | 把该手指的 PINCH_MAXS 改成实测最大值 |
| 手指不动，角度仍小幅跳动 | 调大 SMOOTH（如 0.2）或 DEADBAND（如 2） |
| 跟手太慢、延迟感强 | 调大 SMOOTH（如 0.5） |

## 常见问题

- **摄像头打不开**：`CAM_INDEX` 改成 `1`
- **WS 显示 NOT-CONNECTED**：板子 IP 变了，改 `ESP32_WS_URL`
- **舵机动、网页滑块不动**：正常，本脚本和 Aily Blockly 生成的网页是两个独立客户端
- **注意**：本项目基于 **MediaPipe 1.0 新版 Tasks API**（`HandLandmarker` + `hand_landmarker.task` 模型文件）。
  网上老教程的 `mp.solutions.hands` 写法在新版已删除，直接搬会报错

## License

MIT
