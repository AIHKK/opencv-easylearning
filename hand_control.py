# -*- coding: utf-8 -*-
"""
============================================================
 第一课 v2：四指捏合距离 -> 四路舵机（OpenCV + MediaPipe Tasks）
============================================================
 链路：
   笔记本摄像头
     -> MediaPipe HandLandmarker 识别 21 个手部关键点
     -> 拇指与四根手指的距离：最近的手指被"选中"
     -> 捏合距离换算 0~180°（含平滑/死区/节流）
     -> WebSocket 发送 "通道:角度" -> ESP32-S3 -> PCA9685 -> 舵机

 操作方式：
   拇指+食指 捏合距离  -> 舵机0
   拇指+中指           -> 舵机1
   拇指+无名指         -> 舵机2
   拇指+小指           -> 舵机3
   手离开画面          -> 四路全部回中 90°
   按 q / ESC          -> 退出

 学习点：
   - 捏合距离要除以手掌大小做归一化，手远近才不影响读数
   - 每根手指的"能张开的最大距离"不同，所以各有各的 PINCH_MAX
   - 选中逻辑 = 距离最小者（argmin），一帧只控制一路，互不干扰
"""

import csv
import math
import os
import queue
import threading
import time

import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_tasks
from mediapipe.tasks.python import vision
from websockets.sync.client import connect

# ==================== 配置区（改这里） ====================
ESP32_WS_URL = "ws://192.168.3.105/ws"   # 板子的 WebSocket 地址
CAM_INDEX    = 0        # 摄像头编号，外接USB摄像头可能是 1
SMOOTH       = 0.30     # 平滑系数 0~1：越小越稳、越"迟钝"
DEADBAND     = 1        # 死区：角度变化小于几度就不发，防抖
SEND_MIN_GAP = 0.03     # 两次发送最小间隔（秒）
PINCH_MIN    = 0.20     # 捏拢比例：小于它按 0° 处理
# 每根手指"完全张开"的比例上限（食指/中指/无名指/小指）。
# 小指短，天然够不到食指的张开距离，所以上限更小。
# 校准方法：看 HUD 上显示的 pinch 数值，把你尽量张开时的读数填进来。
PINCH_MAXS   = [1.40, 1.30, 1.10, 0.90]
CENTER_ANGLE = 90       # 手离开画面时的回中角度
PRINT_INTERVAL = 0.5    # 遥测打印/记录间隔（秒）
TELEMETRY_CSV  = True   # 把数据写进 telemetry.csv（Excel/pandas 可直接分析）
# =========================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(BASE_DIR, "models", "hand_landmarker.task")

# 学习点：手部就是 21 个编号点(0=手腕,4拇指尖,8食指尖...)
HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),          # 拇指
    (0, 5), (5, 6), (6, 7), (7, 8),          # 食指
    (5, 9), (9, 10), (10, 11), (11, 12),     # 中指
    (9, 13), (13, 14), (14, 15), (15, 16),   # 无名指
    (13, 17), (17, 18), (18, 19), (19, 20),  # 小指
    (0, 17),                                 # 手掌下缘
]
THUMB_TIP = 4
# (指尖编号, 舵机通道)：食指->舵机0  中指->舵机1  无名指->舵机2  小指->舵机3
FINGERS = [(8, 0), (12, 1), (16, 2), (20, 3)]
FINGER_NAMES = ["index", "middle", "ring", "pinky"]
CHANNELS = [ch for _, ch in FINGERS]


def dist(a, b):
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def to_dict(lm):
    return {"x": lm.x, "y": lm.y}


# ---------------- WebSocket 发送线程（断线自动重连） ----------------
def ws_worker(cmd_q, state):
    ws = None
    while True:
        item = cmd_q.get()
        if item is None:
            break
        ch, ang = item
        if ws is None:
            try:
                ws = connect(ESP32_WS_URL, open_timeout=3)
                state["ok"] = True
                print("[WS] 已连接", ESP32_WS_URL)
            except Exception:
                state["ok"] = False
                time.sleep(1.0)
                continue
        try:
            ws.send(f"{ch}:{int(ang)}")
        except Exception:
            state["ok"] = False
            try:
                ws.close()
            except Exception:
                pass
            ws = None
            print("[WS] 连接断开，正在重连...")
    if ws is not None:
        try:
            ws.close()
        except Exception:
            pass


def main():
    # 1) 加载手部关键点模型
    if not os.path.exists(MODEL_PATH):
        raise SystemExit(f"找不到模型文件: {MODEL_PATH}")
    options = vision.HandLandmarkerOptions(
        base_options=mp_tasks.BaseOptions(model_asset_path=MODEL_PATH),
        running_mode=vision.RunningMode.VIDEO,
        num_hands=1,
        min_hand_detection_confidence=0.6,
        min_tracking_confidence=0.5,
    )
    landmarker = vision.HandLandmarker.create_from_options(options)
    print("[CV] 手部模型加载完成")

    # 2) 打开摄像头
    cap = cv2.VideoCapture(CAM_INDEX)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    if not cap.isOpened():
        raise SystemExit("打不开摄像头，试试把 CAM_INDEX 改成 1")
    print("[CV] 摄像头已打开，按 q / ESC 退出")

    # 3) 启动 WebSocket 发送线程
    cmd_q = queue.Queue()
    state = {"ok": False}
    threading.Thread(target=ws_worker, args=(cmd_q, state), daemon=True).start()

    smooth = {ch: float(CENTER_ANGLE) for ch in CHANNELS}   # 平滑后的当前角度
    sent = {ch: None for ch in CHANNELS}                    # 上次实际发送的角度
    last_send_t = 0.0
    centered_latch = False   # 手消失后只发一次回中
    t_prev = time.monotonic()
    fps = 0.0
    hud_active = "--"
    hud_pinch = 0.0
    last_ratios = [0.0, 0.0, 0.0, 0.0]   # 四指距离比例（遥测用）
    last_target = None                    # 当前目标角度（遥测用）
    last_print_t = 0.0
    tele_f = tele_w = None
    if TELEMETRY_CSV:
        tele_f = open(os.path.join(BASE_DIR, "telemetry.csv"), "w",
                      newline="", encoding="utf-8")
        tele_w = csv.writer(tele_f)
        tele_w.writerow(["t", "index", "middle", "ring", "pinky",
                         "active", "active_ratio", "target",
                         "s0", "s1", "s2", "s3"])
        print("[tele] 数据记录到 telemetry.csv（每次运行重新开始）")

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frame = cv2.flip(frame, 1)  # 镜像显示，符合直觉
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w = frame.shape[:2]

            mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            t_ms = int(time.monotonic() * 1000)
            result = landmarker.detect_for_video(mp_img, t_ms)

            targets = {}
            active_ch = None

            if result.hand_landmarks:
                centered_latch = False
                lms = [to_dict(p) for p in result.hand_landmarks[0]]

                # 画骨架
                for a, b in HAND_CONNECTIONS:
                    pa = (int(lms[a]["x"] * w), int(lms[a]["y"] * h))
                    pb = (int(lms[b]["x"] * w), int(lms[b]["y"] * h))
                    cv2.line(frame, pa, pb, (0, 200, 255), 2)
                for i, p in enumerate(lms):
                    c = (255, 0, 255) if i == THUMB_TIP else (0, 255, 0)
                    cv2.circle(frame, (int(p["x"] * w), int(p["y"] * h)), 4, c, -1)

                # 归一化：捏合距离 / 手掌大小(手腕0到中指根9)
                hand_size = max(dist(lms[0], lms[9]), 1e-6)
                ratios = []
                for tip, ch in FINGERS:
                    ratios.append(dist(lms[THUMB_TIP], lms[tip]) / hand_size)

                # 选中逻辑：与拇指距离最近的手指
                k = ratios.index(min(ratios))
                tip, ch = FINGERS[k]
                active_ch = ch
                hud_active = FINGER_NAMES[k]
                hud_pinch = ratios[k]

                # 捏合比例 -> 0~180°（按各手指自己的张开上限归一化）
                r = max(PINCH_MIN, min(PINCH_MAXS[k], ratios[k]))
                targets[ch] = (r - PINCH_MIN) / (PINCH_MAXS[k] - PINCH_MIN) * 180.0

                # 遥测：记住四指比例和目标角度
                last_ratios = ratios[:]
                last_target = targets.get(ch)

                # 高亮选中的手指，并画出拇指-指尖连线
                tp = (int(lms[tip]["x"] * w), int(lms[tip]["y"] * h))
                th = (int(lms[THUMB_TIP]["x"] * w), int(lms[THUMB_TIP]["y"] * h))
                cv2.line(frame, th, tp, (0, 0, 255), 3)
                cv2.circle(frame, tp, 8, (0, 0, 255), 2)
                mid = ((th[0] + tp[0]) // 2, (th[1] + tp[1]) // 2)
                cv2.putText(frame, f"{ratios[k]:.2f}", mid,
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
            else:
                # 手不见了 -> 四路全部回中（只发一次）
                hud_active = "hand lost"
                hud_pinch = 0.0
                last_target = None
                if not centered_latch:
                    centered_latch = True
                    for ch in CHANNELS:
                        cmd_q.put((ch, CENTER_ANGLE))
                        smooth[ch] = float(CENTER_ANGLE)
                        sent[ch] = CENTER_ANGLE
                    print("[CV] 手离开画面，四路回中")

            # 平滑 + 死区 + 节流，然后交给发送线程
            now = time.monotonic()
            if targets and now - last_send_t >= SEND_MIN_GAP:
                for ch, tgt in targets.items():
                    smooth[ch] += (tgt - smooth[ch]) * SMOOTH
                    ang = int(round(smooth[ch]))
                    if sent[ch] is None or abs(ang - sent[ch]) >= DEADBAND:
                        cmd_q.put((ch, ang))
                        sent[ch] = ang
                last_send_t = now

            # 遥测：定时打印 + 写 CSV（调参看这里）
            if now - last_print_t >= PRINT_INTERVAL:
                last_print_t = now
                print(f"[tele] I:{last_ratios[0]:.2f} M:{last_ratios[1]:.2f} "
                      f"R:{last_ratios[2]:.2f} P:{last_ratios[3]:.2f} "
                      f"active:{hud_active} ratio:{hud_pinch:.2f} "
                      f"target:{last_target:.0f} "
                      if last_target is not None else
                      f"[tele] I:{last_ratios[0]:.2f} M:{last_ratios[1]:.2f} "
                      f"R:{last_ratios[2]:.2f} P:{last_ratios[3]:.2f} "
                      f"active:{hud_active} (no hand)")
                if tele_w is not None:
                    tele_w.writerow([f"{now:.2f}",
                                     *[f"{r:.3f}" for r in last_ratios],
                                     hud_active, f"{hud_pinch:.3f}",
                                     f"{last_target:.0f}" if last_target is not None else "",
                                     *[f"{smooth[c]:.0f}" for c in CHANNELS]])
                    tele_f.flush()

            # HUD
            fps = 0.9 * fps + 0.1 * (1.0 / max(now - t_prev, 1e-6))
            t_prev = now
            ws_txt = "OK" if state["ok"] else "NOT-CONNECTED"
            cv2.putText(frame, f"WS: {ws_txt}", (10, 28),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                        (0, 255, 0) if state["ok"] else (0, 0, 255), 2)
            cv2.putText(frame,
                        " ".join(f"s{ch}:{int(smooth[ch]):3d}" for ch in CHANNELS),
                        (10, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            cv2.putText(frame, f"active:{hud_active}  pinch:{hud_pinch:.2f}",
                        (10, 86), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
            cv2.putText(frame, f"FPS:{fps:.0f}",
                        (10, 114), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            cv2.putText(frame,
                        f"I:{last_ratios[0]:.2f} M:{last_ratios[1]:.2f} "
                        f"R:{last_ratios[2]:.2f} P:{last_ratios[3]:.2f}",
                        (10, 142), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)

            cv2.imshow("fingers -> servos (q=quit)", frame)
            if cv2.waitKey(1) & 0xFF in (27, ord("q")):
                break
    finally:
        cmd_q.put(None)
        if tele_f is not None:
            tele_f.close()
        cap.release()
        cv2.destroyAllWindows()
        landmarker.close()
        print("[exit] 已退出")


if __name__ == "__main__":
    main()
