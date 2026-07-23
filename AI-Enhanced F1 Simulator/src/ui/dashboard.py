# dashboard.py
# what the dashboard would be looking like  python -m ui.dashboard
# python3 dashboard.py to show the dashboard with fake data if TORCS isn't running

import json
import math
import time
import tkinter as tk
from tkinter import messagebox
from pathlib import Path

try:
    from data_pipeline.cache import cache, GameStatus
    USING_REAL_CACHE = True
except ImportError:
    print("cache.py not found - running the dashboard with fake data instead")
    USING_REAL_CACHE = False

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
COACHING_FILE = PROJECT_ROOT / "data" / "latest_coaching.json"

TRACK_LENGTH_M = 6283.0


def get_fake_telemetry():
    t = time.time()
    return {
        "speed_kmh": 180 + 40 * math.sin(t * 0.3),
        "gear": int(3 + 2 * abs(math.sin(t * 0.2))),
        "rpm": 8000 + 4000 * abs(math.sin(t * 0.4)),
        "throttle": max(0, math.sin(t * 0.5)),
        "brake": max(0, -math.sin(t * 0.5) * 0.8),
        "fuel": max(0, 100 - (t % 100)),
        "lap_time": t % 90,
        "lap_distance": (t * 50) % TRACK_LENGTH_M,
        "track_pos": math.sin(t * 0.1) * 0.6,
        "angle": math.sin(t * 0.2) * 0.15,
        "wheel_spin": 40 + 40 * abs(math.sin(t * 0.6)),
        "race_pos": 3,
    }


BG = "#0d0d0d"
PANEL_BG = "#1a1a1a"
BORDER = "#2a2a2a"
WHITE = "#f0f0f0"
GREY = "#888888"
RED = "#e03c3c"
GREEN = "#3ce070"
BLUE = "#3ca0e0"
YELLOW = "#e0c43c"
ORANGE = "#e07a3c"
PURPLE = "#9b59b6"


def format_lap_time(seconds):
    if seconds is None or seconds <= 0:
        return "--:--.---"
    mins = int(seconds // 60)
    secs = seconds % 60
    return f"{mins}:{secs:06.3f}"


class RPMGauge(tk.Canvas):

    MAX_RPM = 18000

    def __init__(self, parent, size=140, **kwargs):
        super().__init__(parent, width=size, height=size, bg=PANEL_BG, highlightthickness=0, **kwargs)
        self.size = size
        self._draw(0)

    def _draw(self, rpm):
        self.delete("all")
        cx = cy = self.size / 2
        r = self.size / 2 - 10

        # background track for the arc
        self.create_arc(cx - r, cy - r, cx + r, cy + r, start=220, extent=-260,
                         style="arc", outline=BORDER, width=8)

        fraction = min(rpm / self.MAX_RPM, 1.0)
        if fraction < 0.6:
            colour = GREEN
        elif fraction < 0.85:
            colour = YELLOW
        else:
            colour = RED

        if fraction > 0:
            self.create_arc(cx - r, cy - r, cx + r, cy + r, start=220,
                             extent=-fraction * 260, style="arc", outline=colour, width=8)

        self.create_text(cx, cy - 8, text=f"{int(rpm):,}", fill=WHITE, font=("Courier", 13, "bold"))
        self.create_text(cx, cy + 12, text="RPM", fill=GREY, font=("Courier", 9))

    def update_rpm(self, rpm):
        self._draw(rpm)


class BarWidget(tk.Canvas):
    """just a rectangle that fills up based on a 0-1 value, used for throttle/brake/fuel/wheelspin"""

    def __init__(self, parent, colour, width=180, height=18, **kwargs):
        super().__init__(parent, width=width, height=height, bg=PANEL_BG, highlightthickness=0, **kwargs)
        self.w = width
        self.h = height
        self.colour = colour
        self._draw(0)

    def _draw(self, fraction):
        self.delete("all")
        self.create_rectangle(0, 0, self.w, self.h, fill=BORDER, outline="")
        fill_w = int(fraction * self.w)
        if fill_w > 0:
            self.create_rectangle(0, 0, fill_w, self.h, fill=self.colour, outline="")

    def set_value(self, fraction, colour=None):
        if colour and colour != self.colour:
            self.colour = colour
        self._draw(max(0.0, min(1.0, fraction)))


class TrackPositionBar(tk.Canvas):
    # track_pos is -1 to 1, -1 = left edge, +1 = right edge, 0 = dead center

    def __init__(self, parent, width=180, height=22, **kwargs):
        super().__init__(parent, width=width, height=height, bg=PANEL_BG, highlightthickness=0, **kwargs)
        self.w = width
        self.h = height
        self._draw(0.0)

    def _draw(self, pos):
        self.delete("all")
        self.create_rectangle(0, 0, self.w, self.h, fill=BORDER, outline="")
        self.create_line(self.w / 2, 0, self.w / 2, self.h, fill=GREY)

        pos = max(-1.2, min(1.2, pos))
        marker_x = (pos + 1.2) / 2.4 * self.w
        colour = RED if abs(pos) > 1.0 else GREEN
        self.create_oval(marker_x - 5, self.h / 2 - 5, marker_x + 5, self.h / 2 + 5, fill=colour, outline="")

    def set_position(self, pos):
        self._draw(pos)


class TelemetryDashboard:

    REFRESH_MS = 50          
    COACH_REFRESH_MS = 1000  

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Telemetry Dashboard - Olethros Road 1")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)

        self.best_lap = None
        self.prev_lap = None
        self._last_lap_time_seen = 0.0
        self._last_coach_text = None
        self._shown_connection_popup = False

        self._build_ui()
        self.root.after(self.REFRESH_MS, self._update)
        self.root.after(self.COACH_REFRESH_MS, self._update_coach_panel)

    # building the window 

    def _build_ui(self):
        title_frame = tk.Frame(self.root, bg=BG, pady=6)
        title_frame.pack(fill="x", padx=10)

        tk.Label(title_frame, text="Live Telemetry Dashboard", fg=BLUE, bg=BG,
                 font=("Courier", 18, "bold")).pack(side="left")

        self.lbl_session = tk.Label(title_frame, text="Olethros Road 1", fg=GREY, bg=BG, font=("Courier", 11))
        self.lbl_session.pack(side="left", padx=20)

        tk.Frame(self.root, bg=BORDER, height=1).pack(fill="x", padx=10)

        content = tk.Frame(self.root, bg=BG)
        content.pack(fill="both", expand=True, padx=10, pady=8)

        self._build_left_panel(content)
        self._build_center_panel(content)
        self._build_right_panel(content)

        tk.Frame(self.root, bg=BORDER, height=1).pack(fill="x", padx=10)
        self._build_bottom_row()

    def _panel(self, parent, title, col):
        outer = tk.Frame(parent, bg=PANEL_BG, highlightbackground=BORDER, highlightthickness=1)
        outer.grid(row=0, column=col, sticky="nsew", padx=4, pady=2)
        parent.columnconfigure(col, weight=1)

        if title:
            tk.Label(outer, text=title, fg=GREY, bg=PANEL_BG, font=("Courier", 8)).pack(anchor="w", padx=8, pady=(6, 0))

        inner = tk.Frame(outer, bg=PANEL_BG)
        inner.pack(fill="both", expand=True, padx=8, pady=6)
        return inner

    def _build_left_panel(self, parent):
        p = self._panel(parent, "", 0)

        tk.Label(p, text="Race Position", fg=GREY, bg=PANEL_BG, font=("Courier", 9)).pack(anchor="w")
        self.lbl_race_pos = tk.Label(p, text="--", fg=ORANGE, bg=PANEL_BG, font=("Courier", 28, "bold"))
        self.lbl_race_pos.pack(anchor="w", pady=2)

        tk.Frame(p, bg=BORDER, height=1).pack(fill="x", pady=6)

        tk.Label(p, text="Current Lap", fg=GREY, bg=PANEL_BG, font=("Courier", 9)).pack(anchor="w")
        self.lbl_current_lap = tk.Label(p, text="--:--.---", fg=YELLOW, bg=PANEL_BG, font=("Courier", 20, "bold"))
        self.lbl_current_lap.pack(anchor="w", pady=(2, 0))

        tk.Frame(p, bg=BORDER, height=1).pack(fill="x", pady=6)

        tk.Label(p, text="Previous Lap", fg=GREY, bg=PANEL_BG, font=("Courier", 9)).pack(anchor="w")
        self.lbl_prev_lap = tk.Label(p, text="--:--.---", fg=GREEN, bg=PANEL_BG, font=("Courier", 14, "bold"))
        self.lbl_prev_lap.pack(anchor="w")

        tk.Label(p, text="Best Lap", fg=GREY, bg=PANEL_BG, font=("Courier", 9)).pack(anchor="w", pady=(6, 0))
        self.lbl_best_lap = tk.Label(p, text="--:--.---", fg=PURPLE, bg=PANEL_BG, font=("Courier", 14, "bold"))
        self.lbl_best_lap.pack(anchor="w")

    def _build_center_panel(self, parent):
        p = self._panel(parent, "", 1)

        tk.Label(p, text="Speed", fg=GREY, bg=PANEL_BG, font=("Courier", 9)).pack()
        self.lbl_speed = tk.Label(p, text="0", fg=WHITE, bg=PANEL_BG, font=("Courier", 52, "bold"))
        self.lbl_speed.pack()
        tk.Label(p, text="km/h", fg=GREY, bg=PANEL_BG, font=("Courier", 10)).pack()

        tk.Frame(p, bg=BORDER, height=1).pack(fill="x", pady=8)

        gear_row = tk.Frame(p, bg=PANEL_BG)
        gear_row.pack()
        self.gear_box = tk.Label(gear_row, text="1", fg=WHITE, bg=BORDER, font=("Courier", 36, "bold"), width=3)
        self.gear_box.pack(side="left", padx=8, ipadx=4, ipady=4)
        tk.Label(gear_row, text="gear", fg=GREY, bg=PANEL_BG, font=("Courier", 9)).pack(side="left")

        tk.Frame(p, bg=BORDER, height=1).pack(fill="x", pady=8)

        for label, colour, attr in [("Throttle", RED, "throttle_bar"), ("Brake", BLUE, "brake_bar")]:
            row = tk.Frame(p, bg=PANEL_BG)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=label, fg=colour, bg=PANEL_BG, font=("Courier", 9), width=8, anchor="w").pack(side="left")
            bar = BarWidget(row, colour=colour, width=160, height=16)
            bar.pack(side="left")
            setattr(self, attr, bar)

        tk.Frame(p, bg=BORDER, height=1).pack(fill="x", pady=8)

        tk.Label(p, text="Track Position", fg=GREY, bg=PANEL_BG, font=("Courier", 9)).pack(anchor="w")
        self.track_pos_bar = TrackPositionBar(p, width=160, height=20)
        self.track_pos_bar.pack(anchor="w", pady=(2, 0))

        tk.Frame(p, bg=BORDER, height=1).pack(fill="x", pady=8)

        tk.Label(p, text="Fuel", fg=GREY, bg=PANEL_BG, font=("Courier", 9)).pack(anchor="w")
        fuel_row = tk.Frame(p, bg=PANEL_BG)
        fuel_row.pack(fill="x", pady=3)
        self.fuel_bar = BarWidget(fuel_row, colour=BLUE, width=160, height=20)
        self.fuel_bar.pack(side="left")
        self.lbl_fuel_pct = tk.Label(fuel_row, text="--", fg=BLUE, bg=PANEL_BG, font=("Courier", 10, "bold"))
        self.lbl_fuel_pct.pack(side="left", padx=6)

    def _build_right_panel(self, parent):
        p = self._panel(parent, "", 2)

        tk.Label(p, text="Wheel Spin", fg=GREY, bg=PANEL_BG, font=("Courier", 9)).pack(anchor="w")
        self.wheel_spin_bar = BarWidget(p, colour=GREEN, width=160, height=18)
        self.wheel_spin_bar.pack(anchor="w", pady=(2, 6))
        self.lbl_wheel_spin = tk.Label(p, text="-- rad/s", fg=GREY, bg=PANEL_BG, font=("Courier", 9))
        self.lbl_wheel_spin.pack(anchor="w")

        tk.Frame(p, bg=BORDER, height=1).pack(fill="x", pady=8)

        tk.Label(p, text="Car Angle vs Track", fg=GREY, bg=PANEL_BG, font=("Courier", 9)).pack(anchor="w")
        self.lbl_angle = tk.Label(p, text="0.00 rad", fg=WHITE, bg=PANEL_BG, font=("Courier", 16, "bold"))
        self.lbl_angle.pack(anchor="w", pady=(2, 0))

        tk.Frame(p, bg=BORDER, height=1).pack(fill="x", pady=8)

        self.lbl_off_track = tk.Label(p, text="ON TRACK", fg=GREEN, bg=PANEL_BG, font=("Courier", 14, "bold"))
        self.lbl_off_track.pack(anchor="w", pady=(6, 0))

    def _build_bottom_row(self):
        bottom = tk.Frame(self.root, bg=BG)
        bottom.pack(fill="x", padx=10, pady=6)

        rpm_panel = tk.Frame(bottom, bg=PANEL_BG, highlightbackground=BORDER, highlightthickness=1)
        rpm_panel.pack(side="left", padx=4)
        tk.Label(rpm_panel, text="RPM", fg=GREY, bg=PANEL_BG, font=("Courier", 8)).pack(anchor="w", padx=8, pady=(4, 0))
        self.rpm_gauge = RPMGauge(rpm_panel, size=140)
        self.rpm_gauge.pack(padx=8, pady=(0, 8))

        coach_panel = tk.Frame(bottom, bg=PANEL_BG, highlightbackground=BORDER, highlightthickness=1)
        coach_panel.pack(side="left", padx=4, fill="both", expand=True)
        tk.Label(coach_panel, text="Race Engineer", fg=GREY, bg=PANEL_BG, font=("Courier", 8)).pack(anchor="w", padx=8, pady=(6, 0))
        self.lbl_coach = tk.Label(coach_panel, text="(waiting for first lap...)", fg=WHITE, bg=PANEL_BG,
                                   font=("Courier", 11, "italic"), wraplength=380, justify="left")
        self.lbl_coach.pack(padx=12, pady=10, anchor="w")

    # data 

    def _update(self):
        try:
            if USING_REAL_CACHE:
                data = cache.get_telemetry()
                status = cache.get_status()
            else:
                data = get_fake_telemetry()
                status = None

            self._check_connection_status(status)

            if data:
                self._refresh_ui(data, status)
        except Exception as e:
            print(f"dashboard update error: {e}")  

        self.root.after(self.REFRESH_MS, self._update)

    def _check_connection_status(self, status):
        if not USING_REAL_CACHE or status is None or self._shown_connection_popup:
            return

        if status == GameStatus.RACING:
            self._shown_connection_popup = True
            messagebox.showinfo("Connected", "Connected to TORCS successfully - telemetry is live, go drive!")
        elif status == GameStatus.ERROR:
            self._shown_connection_popup = True
            messagebox.showerror(
                "Connection Failed",
                "Couldn't connect to TORCS.\n\n"
                "Make sure TORCS is running with the SCR patch enabled, then close this and try again."
            )

    def _update_coach_panel(self):
        try:
            payload = json.loads(COACHING_FILE.read_text(encoding="utf-8"))
            feedback = payload.get("feedback")
            severity = payload.get("severity")
            if feedback and feedback != self._last_coach_text:
                self._last_coach_text = feedback
                colour = {"high": RED, "medium": ORANGE, "low": YELLOW}.get(severity, WHITE)
                self.lbl_coach.config(text=f'"{feedback}"', fg=colour)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        self.root.after(self.COACH_REFRESH_MS, self._update_coach_panel)

    def _refresh_ui(self, data, status):
        speed = data.get("speed_kmh", 0)
        gear = data.get("gear", 1)
        rpm = data.get("rpm", 0)
        throttle = data.get("throttle", 0)
        brake = data.get("brake", 0)
        fuel = data.get("fuel", 0)
        lap_time = data.get("lap_time", 0)
        lap_dist = data.get("lap_distance", 0)
        race_pos = data.get("race_pos", "--")
        track_pos = data.get("track_pos", 0.0)
        angle = data.get("angle", 0.0)
        wheel_spin = data.get("wheel_spin", 0.0)

        self.lbl_speed.config(text=str(int(max(0, speed))))
        self.gear_box.config(text=str(max(0, gear)) if gear > 0 else "R")

        # colour code speed so you know when going too fast
        if speed > 200:
            self.lbl_speed.config(fg=RED)
        elif speed > 120:
            self.lbl_speed.config(fg=YELLOW)
        else:
            self.lbl_speed.config(fg=WHITE)

        self.throttle_bar.set_value(throttle)
        self.brake_bar.set_value(brake)

        fuel_fraction = min(fuel / 100.0, 1.0)
        self.fuel_bar.set_value(fuel_fraction)
        self.lbl_fuel_pct.config(text=f"{int(fuel_fraction * 100)}%", fg=RED if fuel_fraction < 0.2 else BLUE)

        self.rpm_gauge.update_rpm(rpm)
        self.lbl_race_pos.config(text=str(race_pos))

        self.track_pos_bar.set_position(track_pos)
        self.lbl_angle.config(text=f"{angle:.2f} rad")

        wheel_spin_fraction = min(wheel_spin / 150.0, 1.0)
        spin_colour = RED if wheel_spin > 100 else (YELLOW if wheel_spin > 70 else GREEN)
        self.wheel_spin_bar.set_value(wheel_spin_fraction, colour=spin_colour)
        self.lbl_wheel_spin.config(text=f"{wheel_spin:.0f} rad/s")

        if abs(track_pos) > 1.0:
            self.lbl_off_track.config(text="OFF TRACK", fg=RED)
        else:
            self.lbl_off_track.config(text="ON TRACK", fg=GREEN)
            
        if lap_time < self._last_lap_time_seen - 5:
            self.prev_lap = self._last_lap_time_seen
            if self.best_lap is None or self._last_lap_time_seen < self.best_lap:
                self.best_lap = self._last_lap_time_seen

        self._last_lap_time_seen = lap_time
        self.lbl_current_lap.config(text=format_lap_time(lap_time))

        if self.prev_lap:
            self.lbl_prev_lap.config(text=format_lap_time(self.prev_lap))
        if self.best_lap:
            self.lbl_best_lap.config(text=format_lap_time(self.best_lap))

        if status is not None:
            status_str = status.name if hasattr(status, "name") else str(status)
            self.lbl_session.config(text=f"Olethros Road 1  ·  {status_str}")

    def run(self):
        self.root.mainloop()

    def close(self):
        try:
            self.root.quit()
            self.root.destroy()
        except tk.TclError:
            pass  


if __name__ == "__main__":
    print("Starting dashboard standalone (no TORCS connection)...")
    print("run main.py instead if you want this wired up to a real session\n")
    dash = TelemetryDashboard()
    dash.run()