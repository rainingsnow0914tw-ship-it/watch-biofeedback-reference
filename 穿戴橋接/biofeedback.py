"""
生理回饋閉環 —— 用身體訊號驅動裝置。

這是 使用者 想要的那個東西：
    「讓手錶偵測到我很想要的時候，驅動玩具開始動，而且跟程度成正比。」

    關鍵在於用一個「意識控制不了」的訊號當輸入。
    心率可以靠深呼吸壓下來，呼吸可以憋，但皮膚電反應（EDA/GSR）不行 ——
    汗腺歸交感神經直接管，意識碰不到。

    所以這個迴路一旦跑起來，妳沒辦法用意志力騙它。

輸入來源（三種，可以隨時切換）：
    mock   —— 模擬訊號，不需要任何硬體，現在就能測
    serial —— 從 Arduino / ESP32 讀 GSR 模組（硬體到了接這個）
    manual —— 手動輸入數值，用來校準跟測試映射曲線

兩種閉環模式（差別很大，值得都試）：
    正回饋 escalate —— 訊號越高震越強，會自我強化直到飽和。
                       有臨界點：低於它會自己滑回去，高於它就回不來。
    負回饋 hold     —— 把訊號「維持」在目標區間。
                       高了就減弱、低了就加強，用 PID 控制器做。
                       這個難做得多，但它會像一個活的東西在跟妳協商。

用法：
    python biofeedback.py --mock --mode escalate     # 模擬，正回饋
    python biofeedback.py --mock --mode hold --target 60
    python biofeedback.py --serial COM3 --mode hold --target 55 --play
                                                     # 真硬體 + 真裝置
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
import time

# ---------------------------------------------------------------------------
# 訊號來源
# ---------------------------------------------------------------------------

class MockSource:
    """
    模擬 GSR 訊號 —— 沒有硬體也能開發跟測試整條控制鏈。

    刻意做出真實 GSR 的兩個特徵：
      · 慢速漂移（基線會隨體溫、環境濕度緩慢移動）
      · 對刺激的快速反應（1-3 秒內上升，然後緩慢回落）

    第二點很重要 —— GSR 的價值就在於「反應快」，
    如果模擬訊號沒有這個特性，做出來的控制器調參會完全不對。
    """

    def __init__(self) -> None:
        self.t = 0.0
        self.baseline = 40.0
        self.response = 0.0

    def read(self, stimulus: float = 0.0) -> float:
        self.t += 0.1

        # 基線緩慢漂移
        self.baseline += math.sin(self.t / 40) * 0.06

        # 對刺激的反應：快速上升（時間常數約 2 秒）、緩慢回落（約 8 秒）
        target = stimulus * 45
        if target > self.response:
            self.response += (target - self.response) * 0.05
        else:
            self.response += (target - self.response) * 0.012

        noise = math.sin(self.t * 3.7) * 1.2 + math.sin(self.t * 11.3) * 0.5
        return max(0.0, min(100.0, self.baseline + self.response + noise))


class SerialSource:
    """
    從 Arduino / ESP32 讀 GSR 模組。

    韌體那端只要一行：Serial.println(analogRead(A0));
    也就是每行送一個數字。刻意不定義複雜協議 ——
    協議越簡單，硬體那端越不容易出錯，而出錯的時候也一眼看得出來。
    """

    def __init__(self, port: str, baud: int = 115200) -> None:
        try:
            import serial
        except ImportError:
            raise SystemExit("需要 pyserial：pip install pyserial")

        self.ser = serial.Serial(port, baud, timeout=1)
        self.last = 0.0
        print(f"已開啟序列埠 {port} @ {baud}")

    def read(self, stimulus: float = 0.0) -> float:
        """
        讀一行。讀不到或格式錯就回傳上一次的值 ——
        訊號中斷時保持現狀，比突然掉到 0 安全（掉到 0 會讓控制器暴衝補償）。
        """
        try:
            line = self.ser.readline().decode(errors="ignore").strip()
            if line:
                # Arduino 的 analogRead 是 0-1023，正規化到 0-100
                self.last = max(0.0, min(100.0, float(line) / 1023 * 100))
        except (ValueError, UnicodeDecodeError):
            pass
        return self.last


# ---------------------------------------------------------------------------
# 控制器
# ---------------------------------------------------------------------------

class EscalateController:
    """
    正回饋：訊號越高，輸出越強。

    數學上就是一個線性映射，但因為輸出會反過來推高訊號，
    整個系統變成自我強化的迴路 —— 有臨界點、有雙穩態。
    （這個現象是 2026-08-08 跑心率 demo 時意外發現的，見 heartrate.py）
    """

    def __init__(self, low: float = 35.0, high: float = 75.0) -> None:
        self.low, self.high = low, high

    def step(self, signal: float, dt: float) -> float:
        span = max(1e-6, self.high - self.low)
        return max(0.0, min(1.0, (signal - self.low) / span))

    def describe(self) -> str:
        return f"正回饋：訊號 {self.low}→{self.high} 映射到強度 0%→100%"


class HoldController:
    """
    負回饋：把訊號「維持」在目標值附近，用 PID 控制器。

    為什麼要 PID 而不是簡單的「高了就減、低了就加」：
        簡單的做法會震盪 —— 因為身體的反應有延遲。
        等妳看到訊號降下來的時候，其實已經降過頭了，
        然後系統又猛加，又衝過頭，來回擺盪永遠停不下來。

        PID 的三個項各自對付一種毛病：
          P（比例）  —— 離目標多遠就出多少力。單獨用會有穩態誤差
          I（積分）  —— 累積長期偏差，消除「永遠差一點」的問題
          D（微分）  —— 看變化率，提前煞車，這項專門用來抑制超調

        D 項是這裡的關鍵。沒有它，控制器會跟身體的延遲打架。
    """

    def __init__(self, target: float, kp: float = 0.02,
                 ki: float = 0.002, kd: float = 0.05) -> None:
        self.target = target
        self.kp, self.ki, self.kd = kp, ki, kd
        self.integral = 0.0
        self.prev_error: float | None = None
        self.output = 0.3          # 從中低強度起步，不要一開始就衝

    def step(self, signal: float, dt: float) -> float:
        error = self.target - signal

        # 積分項要夾住，否則長時間偏離會累積成巨大的值，
        # 等訊號回來時控制器會瘋狂反向補償（叫 integral windup）
        self.integral = max(-50.0, min(50.0, self.integral + error * dt))

        derivative = 0.0 if self.prev_error is None else (error - self.prev_error) / max(dt, 1e-6)
        self.prev_error = error

        delta = self.kp * error + self.ki * self.integral + self.kd * derivative
        self.output = max(0.0, min(1.0, self.output + delta * dt))
        return self.output

    def describe(self) -> str:
        return f"負回饋：把訊號維持在 {self.target} 附近（PID 控制）"


# ---------------------------------------------------------------------------
# 主迴圈
# ---------------------------------------------------------------------------

async def run(source, controller, duration: int, play: bool,
              server: str = "ws://127.0.0.1:12345") -> None:
    bridge = None
    devs = []

    if play:
        # 跨專案依賴：2026-08-11 手錶線與玩具線分家後，
        # toy_bridge 留在玩具線那邊。這裡只有 --play（真的要驅動裝置）
        # 才需要它，所以延遲 import 並臨時加路徑，
        # 不讓「跑生理回饋模擬」這件事被另一條線的存在綁住。
        import sys
        from pathlib import Path

        toy_line = (Path(__file__).resolve().parent.parent.parent
                    / "成人玩具AI逆向工程" / "玩具控制橋接")
        if toy_line.exists() and str(toy_line) not in sys.path:
            sys.path.insert(0, str(toy_line))

        try:
            from toy_bridge import ToyBridge
        except ImportError:
            print("✗ 找不到玩具線的 toy_bridge.py")
            print(f"  預期位置：{toy_line}")
            print("  （只影響 --play，不加這個參數照樣能跑模擬）")
            return

        bridge = ToyBridge(server=server)
        print(await bridge.connect())
        toys = await bridge.scan(6)
        if not toys:
            print("✗ 沒找到裝置，改成只顯示不驅動\n")
            await bridge.disconnect()
            bridge = None
        else:
            print(f"已連上：{toys[0].name}\n")
            devs = bridge._devices()

    print("=" * 74)
    print(f"  {controller.describe()}")
    print(f"  {'驅動真實裝置' if bridge else '僅顯示（未連裝置）'}"
          f"　　執行 {duration} 秒　　Ctrl+C 中止")
    print("=" * 74)
    print(f"  {'時間':>5}  {'訊號':>6}  {'強度':>6}   訊號 ▁ / 輸出 █")
    print("-" * 74)

    intensity = 0.0
    t0 = time.time()
    last = t0

    try:
        while time.time() - t0 < duration:
            now = time.time()
            dt = now - last
            last = now

            # 把上一輪的輸出當成這一輪的刺激餵回訊號源 —— 這就是「閉環」
            signal = source.read(intensity)
            intensity = controller.step(signal, dt)

            if devs:
                for dev in devs:
                    for act in dev.actuators:
                        await act.command(intensity)

            sig_bar = "▁" * int(signal / 100 * 30)
            out_bar = "█" * int(intensity * 30)
            print(f"  {now-t0:5.1f}s  {signal:6.1f}  {intensity:6.1%}   "
                  f"{sig_bar:<30}|{out_bar}")

            await asyncio.sleep(0.25)

    except KeyboardInterrupt:
        print("\n使用者中止")
    finally:
        if bridge:
            print("\n" + await bridge.stop_all())
            await bridge.disconnect()
        print("\n結束")


def main() -> int:
    p = argparse.ArgumentParser(description="生理回饋閉環控制")
    p.add_argument("--mock", action="store_true", help="用模擬訊號（不需硬體）")
    p.add_argument("--serial", metavar="PORT", help="從序列埠讀 GSR（如 COM3）")
    p.add_argument("--mode", choices=["escalate", "hold"], default="escalate")
    p.add_argument("--target", type=float, default=60.0, help="hold 模式的目標值")
    p.add_argument("--duration", type=int, default=60)
    p.add_argument("--play", action="store_true", help="真的驅動裝置")
    args = p.parse_args()

    if args.serial:
        source = SerialSource(args.serial)
    elif args.mock:
        source = MockSource()
    else:
        print("要指定訊號來源：--mock（模擬）或 --serial COM3（真硬體）")
        return 1

    controller = (HoldController(args.target) if args.mode == "hold"
                  else EscalateController())

    asyncio.run(run(source, controller, args.duration, args.play))
    return 0


if __name__ == "__main__":
    sys.exit(main())
