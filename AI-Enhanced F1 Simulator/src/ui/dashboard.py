# dashboard.py
# what the dashboard would be looking like  python -m ui.dashboard
# python3 dashboard.py to show the dashboard with fake data if TORCS isn't running

import json
import time
import threading
import tkinter as tk
from pathlib import Path

# Try to load the internal cache module and game status
try:
    from data_pipeline.cache import cache, GameStatus
    USING_REAL_CACHE = True
except ImportError:
    print("[Dashboard Warning] cache.py not found - running with mock data fallback.")
    USING_REAL_CACHE = False


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SUMMARY_FILE = PROJECT_ROOT / "data" / "lap_summary.json"
TRACK_LENGTH_M = 6283.0
DAMAGE_MAX = 10000 

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
        self.create_arc(cx - r, cy - r, cx + r, cy + r, start=220, extent=-260, style="arc", outline=BORDER, width=8)
        fraction = min(rpm / self.MAX_RPM, 1.0)
        colour = GREEN if fraction < 0.6 else (YELLOW if fraction < 0.85 else RED)
        if fraction > 0:
            self.create_arc(cx - r, cy - r, cx + r, cy + r, start=220, extent=-fraction * 260, style="arc", outline=colour, width=8)
        self.create_text(cx, cy - 8, text=f"{int(rpm):,}", fill=WHITE, font=("Courier", 13, "bold"))
        self.create_text(cx, cy + 12, text="RPM", fill=GREY, font=("Courier", 9))

    def update_rpm(self, rpm):
        self._draw(rpm)


class BarWidget(tk.Canvas):
    """just a rectangle that fills up based on a 0-1 value, used for throttle/brake/fuel/wheelspin"""
    def __init__(self, parent, colour, width=180, height=18, **kwargs):
        super().__init__(parent, width=width, height=height, bg=PANEL_BG, highlightthickness=0, **kwargs)
        self.w, self.h = width, height
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
        self.w, self.h = width, height
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

    def __init__(self, init_callback=None, start_race_callback=None, on_game_finished_callback=None, stop_race_callback=None):
        self.root = tk.Tk()
        self.root.title("AI Race Telemetry System")
        self.root.geometry("800x600")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)

        # External callback interfaces
        self.init_callback = init_callback
        self.start_race_callback = start_race_callback
        self.on_game_finished_callback = on_game_finished_callback
        self.stop_race_callback = stop_race_callback

        self._update_job = None

        # Status variables
        self.llm_connected = False
        self.best_lap = None
        self.prev_lap = None
        self.current_lap = 1
        self._last_lap_time_seen = 0.0
        self.selected_style = "supportive"

        # sector timing 
        self.current_sector = 1
        self.sector_start_lap_time = 0.0
        self.sector_times = [None, None, None]
        self._track_corners = []

        # Frame containers for each page
        self.home_frame = None
        self.instructions_frame = None
        self.init_frame = None
        self.new_race_frame = None
        self.connecting_frame = None
        self.error_frame = None
        self.dashboard_frame = None
        self.summary_frame = None

        # Open the home page on startup
        self._build_home_page()


    # Home Page
    def _build_home_page(self):
        if self.home_frame:
            self.home_frame.destroy()

        self.home_frame = tk.Frame(self.root, bg=BG)
        self.home_frame.pack(fill="both", expand=True)

        center_box = tk.Frame(self.home_frame, bg=BG)
        center_box.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(center_box, text="AI Race Telemetry & Coaching System", fg=BLUE, bg=BG,
                 font=("Courier", 26, "bold"), justify="center").pack(pady=(0, 15))
        tk.Label(center_box, text="TORCS Simulator Companion", fg=GREY, bg=BG,
                 font=("Courier", 18)).pack(pady=(0, 40))

        btn_box = tk.Frame(center_box, bg=BG)
        btn_box.pack()

        btn_start = tk.Button(
            btn_box, text="START SYSTEM", fg="black", bg=WHITE,
            activebackground=GREY, activeforeground="black",
            font=("Courier", 18, "bold"), width=16, height=2, bd=1,
            command=self._on_click_start
        )
        btn_start.pack(side="left", padx=15)

        btn_how_to_play = tk.Button(
            btn_box, text="HOW TO PLAY", fg="black", bg=BLUE,
            activebackground="#64b5f6", activeforeground="black",
            font=("Courier", 18, "bold"), width=16, height=2, bd=1,
            command=self._on_click_how_to_play
        )
        btn_how_to_play.pack(side="left", padx=15)

    def _on_click_start(self):
        self.home_frame.pack_forget()
        self._build_init_page()
        self._start_async_init()

    def _on_click_how_to_play(self):
        self.home_frame.pack_forget()
        self._build_instructions_page()

    # How To Play Page
    def _build_instructions_page(self):
        if self.instructions_frame:
            self.instructions_frame.destroy()

        self.instructions_frame = tk.Frame(self.root, bg=BG)
        self.instructions_frame.pack(fill="both", expand=True)

        center_box = tk.Frame(self.instructions_frame, bg=BG)
        center_box.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(
            center_box, text="HOW TO PLAY & SYSTEM GUIDE", fg=BLUE, bg=BG,
            font=("Courier", 26, "bold")
        ).pack(pady=(0, 25))

        instructions_text = (
            "1. Driving Controls\n"
            "• ↑ / ↓ : Throttle / Brake\n"
            "• ← / → : Steer Left / Right\n"
            "• A(a) / Z(z) : Shift Up / Down (Hold Z past N for Reverse)\n"
            "• F1 : In-Game Keys Menu\n\n"
            "2. Setup & Connection\n"
            "• Launch TORCS on your PC.\n"
            "• Go to 'Race' > 'Quick Race' > 'Configure Race'.\n"
            "• Set Track to 'Olethros Road 1'.\n"
            "• Add 'scr_server 1' to the driver list.\n"
            "• Click 'New Race' in TORCS.\n"
            "• When TORCS shows 'Initializing Driver...', return here and click 'New Race'.\n"
            "• Once connected, live telemetry will start automatically!"
        )

        tk.Label(
            center_box, text=instructions_text, fg=WHITE, bg=PANEL_BG,
            font=("Courier", 14), justify="left", padx=25, pady=20, bd=1, relief="solid"
        ).pack(pady=(0, 30))

        btn_back = tk.Button(
            center_box, text="BACK TO HOME", fg="black", bg=WHITE,
            activebackground=GREY, activeforeground="black",
            font=("Courier", 18, "bold"), width=18, height=2, bd=1,
            command=self._on_click_back_from_instructions
        )
        btn_back.pack()

    def _on_click_back_from_instructions(self):
        self.instructions_frame.pack_forget()
        self._build_home_page()

    # AI Core & RAG Initialization Page
    def _build_init_page(self):
        if self.init_frame:
            self.init_frame.destroy()

        self.init_frame = tk.Frame(self.root, bg=BG)
        self.init_frame.pack(fill="both", expand=True)

        center_box = tk.Frame(self.init_frame, bg=BG)
        center_box.place(relx=0.5, rely=0.5, anchor="center")

        self.lbl_init_status = tk.Label(
            center_box, text="Initializing AI Core & RAG Knowledge Base...", 
            fg=YELLOW, bg=BG, font=("Courier", 21, "bold"), justify="center"
        )
        self.lbl_init_status.pack(pady=(0, 15))

        self.lbl_init_sub = tk.Label(
            center_box, text="Please wait while backend models load...", 
            fg=GREY, bg=BG, font=("Courier", 15), justify="center"
        )
        self.lbl_init_sub.pack(pady=(0, 30))

        self.btn_retry = tk.Button(
            center_box, text="RETRY INITIALIZATION", fg="black", bg=RED,
            activebackground="#f26666", activeforeground="black",
            font=("Courier", 18, "bold"), width=22, height=2, bd=0,
            command=self._start_async_init
        )

    def _start_async_init(self):
        self.btn_retry.pack_forget()
        self.lbl_init_status.config(text="Initializing AI Core & RAG Knowledge Base...", fg=YELLOW)
        self.lbl_init_sub.config(text="Please wait while backend models load...", fg=GREY)

        threading.Thread(target=self._worker_init_thread, daemon=True).start()

    def _worker_init_thread(self):
        success = False
        llm_connected = False
        msg = ""

        if self.init_callback:
            success, llm_connected, msg = self.init_callback()
        else:
            time.sleep(1.0)
            success = True
            llm_connected = False
            msg = "LLM API failed to connect. Running in offline mode."

        self.root.after(0, self._on_init_finished, success, llm_connected, msg)

    def _on_init_finished(self, success, llm_connected, msg):
        self.llm_connected = llm_connected
        if not success:
            self.lbl_init_status.config(text="Initialization Failed!", fg=RED)
            self.lbl_init_sub.config(text=f"Error: {msg}", fg=RED)
            self.btn_retry.pack(pady=10)
        else:
            if not llm_connected:
                self._show_center_warning_card(msg)
            else:
                self.init_frame.pack_forget()
                self._build_new_race_page()

    def _show_center_warning_card(self, msg):
        self.lbl_init_status.pack_forget()
        self.lbl_init_sub.pack_forget()

        warning_card = tk.Frame(self.init_frame, bg=PANEL_BG, highlightbackground=YELLOW, highlightthickness=2, padx=30, pady=25)
        warning_card.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(
            warning_card, text="AI CONNECTION WARNING", fg=YELLOW, bg=PANEL_BG,
            font=("Courier", 21, "bold")
        ).pack(pady=(0, 15))

        warning_text = msg if msg else "LLM API disconnected. System running in fallback mode."
        tk.Label(
            warning_card, text=warning_text, fg=WHITE, bg=PANEL_BG,
            font=("Courier", 15), wraplength=550, justify="center"
        ).pack(pady=(0, 20))

        btn_ok = tk.Button(
            warning_card, text="OK", fg="black", bg=WHITE,
            activebackground=BLUE, activeforeground="black",
            font=("Courier", 18, "bold"), width=10, bd=1,
            command=self._on_warning_ok_clicked
        )
        btn_ok.pack()

    def _on_warning_ok_clicked(self):
        self.init_frame.pack_forget()
        self._build_new_race_page()


    # Ready Page
    def _build_new_race_page(self):
        if self.new_race_frame:
            self.new_race_frame.destroy()

        self.new_race_frame = tk.Frame(self.root, bg=BG)
        self.new_race_frame.pack(fill="both", expand=True)

        center_box = tk.Frame(self.new_race_frame, bg=BG)
        center_box.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(
            center_box, text="SYSTEM READY", fg=GREEN, bg=BG,
            font=("Courier", 33, "bold"), justify="center"
        ).pack(pady=(0, 10))

        tk.Label(
            center_box, text="Launch TORCS simulator and press NEW RACE to start live session", fg=GREY, bg=BG,
            font=("Courier", 13), justify="center"
        ).pack(pady=(0, 25))

        # AI Coach Style selection area
        style_box = tk.Frame(center_box, bg=PANEL_BG, highlightbackground=BORDER, highlightthickness=1, padx=20, pady=12)
        style_box.pack(pady=(0, 30))

        tk.Label(
            style_box, text="AI Engineer Persona Style:", fg=WHITE, bg=PANEL_BG,
            font=("Courier", 12, "bold")
        ).pack(side="left", padx=(0, 15))

        self.style_var = tk.StringVar(value=self.selected_style)
        
        # Create style dropdown menu
        style_options = ["supportive", "technical","aggressive"]
        style_dropdown = tk.OptionMenu(style_box, self.style_var, *style_options, command=self._on_style_change)
        style_dropdown.config(
            bg=BORDER, fg=YELLOW, activebackground=PANEL_BG, activeforeground=YELLOW,
            font=("Courier", 11, "bold"), highlightthickness=0, bd=1, width=12
        )
        style_dropdown["menu"].config(bg=PANEL_BG, fg=WHITE, font=("Courier", 11))
        style_dropdown.pack(side="left")

        btn_new_race = tk.Button(
            center_box, text="NEW RACE", fg="black", bg="#3ce070",
            activebackground="#288f4a", activeforeground="black",
            font=("Courier", 22, "bold"), width=16, height=2, bd=0,
            command=self._on_click_new_race
        )
        btn_new_race.pack()

    def _on_style_change(self, value):
        self.selected_style = value
        print(f"[Dashboard] Selected AI Coaching Style: {self.selected_style}")

    def _on_click_new_race(self):
        if SUMMARY_FILE.exists():
            try:
                SUMMARY_FILE.unlink()
            except Exception:
                pass

        if USING_REAL_CACHE:
            try:
                cache.clear()
                print("[Dashboard] Cache cleared for New Race.")
            except Exception as e:
                print(f"[Dashboard Warning] Failed to clear cache: {e}")

        # this would use to reset the lap and sector tracking for the new race
        self.best_lap = None
        self.prev_lap = None
        self.current_lap = 1
        self._last_lap_time_seen = 0.0
        self.current_sector = 1
        self.sector_start_lap_time = 0.0
        self.sector_times = [None, None, None]
        self._track_corners = []

        self.new_race_frame.pack_forget()
        self._build_connecting_page()

        if self.start_race_callback:
            # Pass selected_style to the main program to start
            self.start_race_callback(self.llm_connected, style=self.selected_style)

        self.root.after(100, self._check_torcs_connection)

    def _on_click_back_from_dashboard(self):
        print("[Dashboard] Back button pressed. Stopping active race session...")

        if self._update_job is not None:
            self.root.after_cancel(self._update_job)
            self._update_job = None

        if self.stop_race_callback:
            self.stop_race_callback()

        if USING_REAL_CACHE:
            try:
                cache.clear()
            except Exception as e:
                print(f"[Dashboard Warning] Failed to reset cache on back: {e}")

        if self.dashboard_frame:
            self.dashboard_frame.destroy()
            self.dashboard_frame = None

        self._build_new_race_page()

    # Connecting Page
    def _build_connecting_page(self):
        if self.connecting_frame:
            self.connecting_frame.destroy()

        self.connecting_frame = tk.Frame(self.root, bg=BG)
        self.connecting_frame.pack(fill="both", expand=True)

        center_box = tk.Frame(self.connecting_frame, bg=BG)
        center_box.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(
            center_box, text="CONNECTING TO SIMULATOR...", fg=YELLOW, bg=BG,
            font=("Courier", 27, "bold"), justify="center"
        ).pack(pady=(0, 15))

        tk.Label(
            center_box, text="Waiting for TORCS UDP socket connection on port 3001...", fg=GREY, bg=BG,
            font=("Courier", 15), justify="center"
        ).pack()

    def _check_torcs_connection(self):
        if not self.connecting_frame or not self.connecting_frame.winfo_ismapped():
            return

        if USING_REAL_CACHE:
            status = cache.get_status()
            telemetry = cache.get_telemetry()
            
            # Switch to dashboard only when data is received or GameStatus changes to RACING
            if status == GameStatus.RACING or telemetry is not None:
                print("[Dashboard] Connection established! Moving to Live Dashboard.")
                self._track_corners = cache.get_corners()
                self.connecting_frame.pack_forget()
                self._build_dashboard_ui()
                self.root.after(self.REFRESH_MS, self._update)
                return

            # Jump to error page if an error occurs
            if status == GameStatus.ERROR:
                print("[Dashboard] Connection error detected.")
                self.connecting_frame.pack_forget()
                self._show_error_page("Failed to establish UDP connection with TORCS.")
                return
        else:
            self.connecting_frame.pack_forget()
            self._build_dashboard_ui()
            self.root.after(self.REFRESH_MS, self._update)
            return

        self.root.after(200, self._check_torcs_connection)


    # Error Page
    def _show_error_page(self, error_message):
        if self.error_frame:
            self.error_frame.destroy()

        self.error_frame = tk.Frame(self.root, bg=BG)
        self.error_frame.pack(fill="both", expand=True)

        center_box = tk.Frame(self.error_frame, bg=BG)
        center_box.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(
            center_box, text="CONNECTION ERROR", fg=RED, bg=BG,
            font=("Courier", 30, "bold"), justify="center"
        ).pack(pady=(0, 15))

        tk.Label(
            center_box, text=error_message, fg=WHITE, bg=BG,
            font=("Courier", 16), wraplength=600, justify="center"
        ).pack(pady=(0, 10))

        tk.Label(
            center_box, text="Please make sure TORCS simulator is running with UDP server enabled.", fg=GREY, bg=BG,
            font=("Courier", 13), justify="center"
        ).pack(pady=(0, 35))

        btn_back = tk.Button(
            center_box, text="BACK TO NEW RACE", fg="black", bg=WHITE,
            activebackground=BORDER, activeforeground="black",
            font=("Courier", 18, "bold"), width=20, height=2, bd=1,
            command=self._on_click_back_to_new_race
        )
        btn_back.pack()

    def _on_click_back_to_new_race(self):
        print("[Dashboard] Returning to NEW RACE setup page...")

        if USING_REAL_CACHE:
            try:
                cache.clear()
            except Exception as e:
                print(f"[Dashboard Warning] Failed to clear cache on back to new race: {e}")
        
        if self.error_frame:
            self.error_frame.destroy()

        # Delete lap_summary.json from the previous race
        if SUMMARY_FILE.exists():
            try:
                SUMMARY_FILE.unlink()
            except Exception:
                pass

        self._build_new_race_page()


    # Post-Race Summary Page
    def _build_summary_page(self):
        if self.summary_frame:
            self.summary_frame.destroy()

        self.summary_frame = tk.Frame(self.root, bg=BG)
        self.summary_frame.pack(fill="both", expand=True)

        center_box = tk.Frame(self.summary_frame, bg=BG)
        center_box.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(
            center_box, text="RACE FINISHED", fg=GREEN, bg=BG,
            font=("Courier", 28, "bold"), justify="center"
        ).pack(pady=(0, 10))

        tk.Label(
            center_box, text="POST-RACE AI COACHING SUMMARY", fg=BLUE, bg=BG,
            font=("Courier", 16, "bold"), justify="center"
        ).pack(pady=(0, 20))

        summary_card = tk.Frame(center_box, bg=PANEL_BG, highlightbackground=BORDER, highlightthickness=1, padx=25, pady=20)
        summary_card.pack(pady=(0, 30))

        tk.Label(
            summary_card, text="AI Race Engineer Debrief", fg=YELLOW, bg=PANEL_BG,
            font=("Courier", 14, "bold"), anchor="w"
        ).pack(fill="x", pady=(0, 10))

        lbl_summary_body = tk.Label(
            summary_card, text="Compiling AI Summary... Please wait...", fg=WHITE, bg=PANEL_BG,
            font=("Courier", 13), wraplength=580, justify="left"
        )
        lbl_summary_body.pack()


        btn_back = tk.Button(
            center_box, text="MAIN MENU", fg="black", bg=WHITE,
            activebackground=GREY, activeforeground="black",
            font=("Courier", 16, "bold"), width=16, height=2, bd=1,
            command=self._on_click_back_to_menu_from_summary
        )
        btn_back.pack()

        # Poll until the JSON file is written to ensure the latest summary is displayed
        
        def _poll_for_summary(retries=30):
            if SUMMARY_FILE.exists():
                try:
                    with open(SUMMARY_FILE, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        text = data.get("feedback", "No summary text generated.")
                        lbl_summary_body.config(text=text)
                        return
                except Exception:
                    pass
            
            if retries > 0:
                self.root.after(500, lambda: _poll_for_summary(retries - 1))
            else:
                lbl_summary_body.config(text="No infractions recorded or summary generation timed out.")

        _poll_for_summary()

    def _on_click_back_to_menu_from_summary(self):
        # Delete old lap_summary.json immediately when leaving the Summary page
        if SUMMARY_FILE.exists():
            try:
                SUMMARY_FILE.unlink()
                print("[Dashboard] Cleaned up previous lap_summary.json.")
            except Exception as e:
                print(f"[Dashboard Warning] Could not delete old summary: {e}")

        if self.summary_frame:
            self.summary_frame.pack_forget()
        self._build_home_page()


    # Live Telemetry Dashboard UI
    def _build_dashboard_ui(self):
        if self.dashboard_frame:
            self.dashboard_frame.destroy()

        self.dashboard_frame = tk.Frame(self.root, bg=BG)
        self.dashboard_frame.pack(fill="both", expand=True)

        title_frame = tk.Frame(self.dashboard_frame, bg=BG, pady=6)
        title_frame.pack(fill="x", padx=10)

        tk.Label(title_frame, text="Live Telemetry Dashboard", fg=BLUE, bg=BG, font=("Courier", 18, "bold")).pack(side="left")
        self.lbl_session = tk.Label(title_frame, text="Olethros Road 1", fg=GREY, bg=BG, font=("Courier", 11))
        self.lbl_session.pack(side="left", padx=20)

        # back button so we don't have to close the whole app to go back to menu
        btn_back = tk.Button(
            title_frame, text="< BACK", fg="black", bg=WHITE,
            activebackground=GREY, activeforeground="black",
            font=("Courier", 10, "bold"), width=8, height=2, bd=1,
            command=self._on_click_back_from_dashboard
        )
        btn_back.pack(side="right", padx=10)

        tk.Frame(self.dashboard_frame, bg=BORDER, height=1).pack(fill="x", padx=10)

        content = tk.Frame(self.dashboard_frame, bg=BG)
        content.pack(fill="both", expand=True, padx=10, pady=8)

        self._build_left_panel(content)
        self._build_center_panel(content)
        self._build_right_panel(content)

        tk.Frame(self.dashboard_frame, bg=BORDER, height=1).pack(fill="x", padx=10)
        self._build_bottom_row()
        self.root.focus_force()

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
        tk.Label(p, text="Lap", fg=GREY, bg=PANEL_BG, font=("Courier", 9)).pack(anchor="w")
        self.lbl_lap_number = tk.Label(p, text="1", fg=BLUE, bg=PANEL_BG, font=("Courier", 22, "bold"))
        self.lbl_lap_number.pack(anchor="w", pady=(2, 0))

        tk.Frame(p, bg=BORDER, height=1).pack(fill="x", pady=6)
        tk.Label(p, text="Current Lap", fg=GREY, bg=PANEL_BG, font=("Courier", 9)).pack(anchor="w")
        self.lbl_current_lap = tk.Label(p, text="--:--.---", fg=YELLOW, bg=PANEL_BG, font=("Courier", 20, "bold"))
        self.lbl_current_lap.pack(anchor="w", pady=(2, 0))

        self.lbl_lap_dist = tk.Label(p, text="0 m", fg=GREY, bg=PANEL_BG, font=("Courier", 10))
        self.lbl_lap_dist.pack(anchor="w", pady=(4, 0))

        self.lbl_sector = tk.Label(p, text="Sector 1", fg=YELLOW, bg=PANEL_BG, font=("Courier", 12, "bold"))
        self.lbl_sector.pack(anchor="w")

        # per-sector split times, to let you know which sector is fast/slow
        sector_time_row = tk.Frame(p, bg=PANEL_BG)
        sector_time_row.pack(anchor="w", pady=(4, 0))
        self.lbl_s1 = tk.Label(sector_time_row, text="S1: --", fg=GREY, bg=PANEL_BG, font=("Courier", 9, "bold"))
        self.lbl_s1.pack(side="left", padx=(0, 10))
        self.lbl_s2 = tk.Label(sector_time_row, text="S2: --", fg=GREY, bg=PANEL_BG, font=("Courier", 9, "bold"))
        self.lbl_s2.pack(side="left", padx=(0, 10))
        self.lbl_s3 = tk.Label(sector_time_row, text="S3: --", fg=GREY, bg=PANEL_BG, font=("Courier", 9, "bold"))
        self.lbl_s3.pack(side="left")

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

        # damage tracker - to lets us see how much damage the car is currently taking
        tk.Frame(p, bg=BORDER, height=1).pack(fill="x", pady=8)
        tk.Label(p, text="Car Damage", fg=GREY, bg=PANEL_BG, font=("Courier", 9)).pack(anchor="w")
        self.damage_bar = BarWidget(p, colour=GREEN, width=160, height=18)
        self.damage_bar.pack(anchor="w", pady=(2, 4))

        dmg_row = tk.Frame(p, bg=PANEL_BG)
        dmg_row.pack(anchor="w")
        self.lbl_damage_raw = tk.Label(dmg_row, text=f"0 / {DAMAGE_MAX}", fg=WHITE, bg=PANEL_BG, font=("Courier", 10, "bold"))
        self.lbl_damage_raw.pack(side="left")
        self.lbl_damage_pct = tk.Label(dmg_row, text="(0%)", fg=GREY, bg=PANEL_BG, font=("Courier", 10))
        self.lbl_damage_pct.pack(side="left", padx=(6, 0))

        tk.Frame(p, bg=BORDER, height=1).pack(fill="x", pady=8)
        tk.Label(p, text="Car Angle vs Track", fg=GREY, bg=PANEL_BG, font=("Courier", 9)).pack(anchor="w")
        self.lbl_angle = tk.Label(p, text="0.00 rad", fg=WHITE, bg=PANEL_BG, font=("Courier", 16, "bold"))
        self.lbl_angle.pack(anchor="w", pady=(2, 0))

        tk.Frame(p, bg=BORDER, height=1).pack(fill="x", pady=8)
        self.lbl_off_track = tk.Label(p, text="ON TRACK", fg=GREEN, bg=PANEL_BG, font=("Courier", 14, "bold"))
        self.lbl_off_track.pack(anchor="w", pady=(6, 0))

        self.lbl_turn = tk.Label(p, text="Straight", fg=GREY, bg=PANEL_BG, font=("Courier", 12, "bold"))
        self.lbl_turn.pack(anchor="w", pady=(4, 0))

    def _build_bottom_row(self):
        bottom = tk.Frame(self.dashboard_frame, bg=BG)
        bottom.pack(fill="x", padx=10, pady=6)

        rpm_panel = tk.Frame(bottom, bg=PANEL_BG, highlightbackground=BORDER, highlightthickness=1)
        rpm_panel.pack(side="left", padx=4)
        tk.Label(rpm_panel, text="RPM", fg=GREY, bg=PANEL_BG, font=("Courier", 8)).pack(anchor="w", padx=8, pady=(4, 0))
        self.rpm_gauge = RPMGauge(rpm_panel, size=140)
        self.rpm_gauge.pack(padx=8, pady=(0, 8))

        coach_panel = tk.Frame(bottom, bg=PANEL_BG, highlightbackground=BORDER, highlightthickness=1)
        coach_panel.pack(side="left", padx=4, fill="both", expand=True)
        tk.Label(coach_panel, text="Race Engineer Console", fg=GREY, bg=PANEL_BG, font=("Courier", 8)).pack(anchor="w", padx=8, pady=(4, 0))

        self.txt_coach = tk.Text(
            coach_panel, 
            bg=PANEL_BG, 
            fg=WHITE, 
            font=("Courier", 9),
            bd=0, 
            highlightthickness=0,
            wrap="word",
            height=6
        )
        self.txt_coach.pack(padx=8, pady=6, fill="both", expand=True)

        self.txt_coach.tag_config("fast", foreground=YELLOW, font=("Courier", 9, "bold"))
        self.txt_coach.tag_config("slow", foreground=WHITE, font=("Courier", 9, "normal"))
        self.txt_coach.tag_config("summary", foreground=GREEN, font=("Courier", 9, "bold"))
        self.txt_coach.tag_config("time", foreground=GREY, font=("Courier", 8))

        self.txt_coach.insert("end", "[System] Race Engineer initialized...\n", "time")
        self.txt_coach.config(state="disabled")


    # Real-time UI update and GameStatus monitoring
    def update_coach_feedback(self, text: str, layer: str = "slow"):
        def _append_chat():
            if not hasattr(self, 'txt_coach') or not self.txt_coach:
                return

            self.txt_coach.config(state="normal")
            current_time = time.strftime("%H:%M:%S")

            if layer == "fast":
                prefix = f"[{current_time}] [WARNING] "
                tag = "fast"
            elif layer == "summary":
                prefix = f"[{current_time}] [SUMMARY] "
                tag = "summary"
            else:
                prefix = f"[{current_time}] [AI COACH] "
                tag = "slow"

            self.txt_coach.insert("end", prefix, "time")
            self.txt_coach.insert("end", f"{text}\n", tag)
            self.txt_coach.see("end")
            self.txt_coach.config(state="disabled")

        self.root.after(0, _append_chat)

    def _update(self):
        try:
            if USING_REAL_CACHE:
                status = cache.get_status()
                data = cache.get_telemetry()

                # If there is a critical damage torcs started doing weird stuff, so we just end the session and show an error page
                if data is not None and data.get("damage", 0) >= DAMAGE_MAX:
                    print(f"[Dashboard] Damage exceeded {DAMAGE_MAX}, car's toast. Ending session.")
                    if self._update_job is not None:
                        self.root.after_cancel(self._update_job)
                        self._update_job = None
                    if self.stop_race_callback:
                        self.stop_race_callback()
                    if self.dashboard_frame:
                        self.dashboard_frame.pack_forget()
                    self._show_error_page(
                        f"CRITICAL DAMAGE ({int(data.get('damage', 0))}/{DAMAGE_MAX}) - the car took too "
                        f"much damage and flew off track. Restart the race to continue."
                    )
                    return

                # Detect race finished
                if status == GameStatus.FINISHED:
                    print("[Dashboard] GameStatus.FINISHED detected. Switching to Summary Page.")
                    
                    # Safely trigger the external processing logic
                    if self.on_game_finished_callback:
                        self.on_game_finished_callback()

                    if self.dashboard_frame:
                        self.dashboard_frame.pack_forget()
                    self._build_summary_page()
                    return

                # Detect error, switch to error page
                if status == GameStatus.ERROR:
                    print("[Dashboard] GameStatus.ERROR detected. Switching to Error Page.")
                    if self.dashboard_frame:
                        self.dashboard_frame.pack_forget()
                    self._show_error_page("Connection Lost: TORCS process ended or stopped sending data.")
                    return

                if data is not None:
                    self._refresh_ui(data, status)
        except Exception as e:
            print(f"[Dashboard Error] Update error: {e}")

        self._update_job = self.root.after(self.REFRESH_MS, self._update)

    def _refresh_ui(self, data, status):
        speed = data.get("speed_kmh", 0) / 3.6
        gear = data.get("gear", 1)
        rpm = data.get("rpm", 0)
        throttle = data.get("throttle", 0)
        brake = data.get("brake", 0)
        fuel = data.get("fuel", 0)
        lap_time = data.get("lap_time", 0)
        race_pos = data.get("race_pos", "--")
        track_pos = data.get("track_pos", 0.0)
        angle = data.get("angle", 0.0)
        wheel_spin = data.get("wheel_spin", 0.0)
        lap_dist = data.get("lap_distance", data.get("distFromStart", 0.0))

        self.lbl_speed.config(text=str(int(max(0, speed))))
        self.gear_box.config(text=str(gear) if gear > 0 else ("N" if gear == 0 else "R"))

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

        # damage bar - straight percentage of DAMAGE_MAX, clamp so bar never overflows
        dmg = data.get("damage", 0)
        dmg_pct = min((dmg / DAMAGE_MAX) * 100.0, 100.0)

        if dmg_pct < 40:
            dmg_colour = GREEN
        elif dmg_pct < 75:
            dmg_colour = YELLOW
        else:
            dmg_colour = RED

        self.damage_bar.set_value(dmg_pct / 100.0, colour=dmg_colour)
        self.lbl_damage_raw.config(text=f"{int(dmg)} / {DAMAGE_MAX}")
        self.lbl_damage_pct.config(text=f"({dmg_pct:.0f}%)", fg=dmg_colour)

        sector_len = TRACK_LENGTH_M / 3.0
        if lap_dist < sector_len:
            sector_num, sector_color = 1, YELLOW
        elif lap_dist < sector_len * 2:
            sector_num, sector_color = 2, BLUE
        else:
            sector_num, sector_color = 3, PURPLE

        # crossed into the next sector - lock in the split time for the one we just left
        if sector_num > self.current_sector:
            self.sector_times[self.current_sector - 1] = lap_time - self.sector_start_lap_time
            self.sector_start_lap_time = lap_time
            self.current_sector = sector_num

        self.lbl_lap_dist.config(text=f"{lap_dist:.1f} m")
        self.lbl_sector.config(text=f"Sector {sector_num}", fg=sector_color)

        # which turn are we currently in, if any - self._track_corners comes from
        # the shared cache (main.py fills it in from LiveCoach's detected corners)
        current_turn_name = None
        for corner in self._track_corners:
            if corner["start_m"] <= lap_dist <= corner["end_m"]:
                current_turn_name = corner["name"]
                break
        if current_turn_name:
            self.lbl_turn.config(text=f"Turn {current_turn_name[1:]}" if current_turn_name.startswith("T") else current_turn_name, fg=ORANGE)
        else:
            self.lbl_turn.config(text="Straight", fg=GREY)

        live_sector_time = lap_time - self.sector_start_lap_time
        for i, lbl in enumerate((self.lbl_s1, self.lbl_s2, self.lbl_s3)):
            n = i + 1
            if n < self.current_sector:
                t = self.sector_times[i]
                lbl.config(text=f"S{n}: {t:.2f}s" if t is not None else f"S{n}: --", fg=WHITE)
            elif n == self.current_sector:
                lbl.config(text=f"S{n}: {live_sector_time:.2f}s", fg=YELLOW)
            else:
                lbl.config(text=f"S{n}: --", fg=GREY)

        if abs(track_pos) > 1.0:
            self.lbl_off_track.config(text="OFF TRACK", fg=RED)
        else:
            self.lbl_off_track.config(text="ON TRACK", fg=GREEN)

        if lap_time < self._last_lap_time_seen - 5:
            self.prev_lap = self._last_lap_time_seen
            if self.best_lap is None or self._last_lap_time_seen < self.best_lap:
                self.best_lap = self._last_lap_time_seen
            self.current_lap += 1
            self.lbl_lap_number.config(text=str(self.current_lap))

            # fresh sector splits for the new lap
            self.current_sector = 1
            self.sector_start_lap_time = 0.0
            self.sector_times = [None, None, None]

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


if __name__ == "__main__":
    print("Starting dashboard standalone (no TORCS connection)...")
    print("run main.py instead if you want this wired up to a real session\n")
    dash = TelemetryDashboard()
    dash.run()