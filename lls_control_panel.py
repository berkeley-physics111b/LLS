"""
LLS Control Panel
=======================================
A two-tab Tkinter application that replicates the two LabVIEW instrument-
control programs documented by the UC Berkeley Advanced Lab "Low Light
Signal Measurements" experiment:

  Tab 1  "SR760 FFT Interface"   -- replicates
         https://experimentationlab.berkeley.edu/node/96
         (Appendix A: SR760 FFT Interface Program)

  Tab 2  "SR830 Lock-In Interface" -- replicates
         https://experimentationlab.berkeley.edu/node/97
         (Appendix B: SR830 Lock-In Interface Program)

Both tabs talk to real hardware over GPIB via the SR760 / SR830 PyVISA
wrapper classes (sr760_interface.py / sr830_interface.py, must be in the
same folder as this script, or importable on the path).

Requirements:
    pip install pyvisa matplotlib

A working NI-VISA (or equivalent) backend and a GPIB interface card /
USB-GPIB adapter are required to actually talk to the instruments.  The
GUI itself will run and display without one; it simply reports a
connection error when you try to talk to an instrument that isn't there.
"""

import csv
import os
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from sr760_interface import SR760, SR760Error, SPAN_VALUES
from sr830_interface import (
    SR830,
    SR830Error,
    TIME_CONSTANT_VALUES,
    SAMPLE_RATE_VALUES,
    SENSITIVITY_VALUES,
    FILTER_SLOPE_VALUES,
)


# =============================================================================
# Color scheme -- dark, colorblind-friendly
# =============================================================================
# Built around the Okabe-Ito palette (a standard 8-color palette that stays
# distinguishable under the common forms of color-vision deficiency), laid
# over a dark UI so it's easier on the eyes for long lab sessions too.
COLORS = {
    "bg":          "#1e1f26",  # window / frame background
    "bg_panel":    "#262832",  # LabelFrame / notebook body background
    "bg_field":    "#32343f",  # entry / combobox / spinbox field background
    "bg_trough":   "#2a2c36",  # progressbar trough, tab strip background
    "fg":          "#e8e8ec",  # primary text
    "fg_muted":    "#9a9dab",  # secondary / unselected text
    "border":      "#454857",
    "accent":      "#56B4E9",  # sky blue     -- selected tab, focus, links
    "accent_dark": "#0072B2",  # blue         -- buttons / hover
    "success":     "#009E73",  # bluish green -- averages / good status
    "warning":     "#E69F00",  # orange       -- min/max, cautions
    "error":       "#D55E00",  # vermillion   -- cursor readout, errors
    "select":      "#CC79A7",  # reddish purple -- secondary accent
    "plot_bg":     "#20222b",
    "plot_grid":   "#3c3f4c",
    "tab_text_selected": "#12141a",
}


def apply_dark_theme(root: tk.Tk) -> ttk.Style:
    """
    Apply a dark, colorblind-friendly ttk theme to the whole application,
    including a notebook tab bar styled so the active/inactive tabs (and the
    fact that there are two of them) are obvious at a glance.
    """
    c = COLORS
    style = ttk.Style(root)
    # 'clam' is the ttk theme most amenable to full color customization
    # (the native Windows/aqua themes largely ignore these options).
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    root.configure(bg=c["bg"])

    style.configure(
        ".", background=c["bg"], foreground=c["fg"],
        fieldbackground=c["bg_field"], bordercolor=c["border"],
        darkcolor=c["bg_panel"], lightcolor=c["bg_panel"],
        troughcolor=c["bg_trough"], focuscolor=c["accent"],
    )

    style.configure("TFrame", background=c["bg"])
    style.configure("TLabel", background=c["bg"], foreground=c["fg"])
    style.configure(
        "TLabelframe", background=c["bg"], foreground=c["fg"], bordercolor=c["border"]
    )
    style.configure(
        "TLabelframe.Label", background=c["bg"], foreground=c["accent"],
        font=("TkDefaultFont", 9, "bold"),
    )

    style.configure(
        "TButton", background=c["bg_field"], foreground=c["fg"],
        bordercolor=c["border"], focuscolor=c["accent"], padding=4,
    )
    style.map(
        "TButton",
        background=[("disabled", c["bg_panel"]), ("pressed", c["accent_dark"]),
                    ("active", c["accent_dark"])],
        foreground=[("disabled", c["fg_muted"])],
    )

    style.configure("TCheckbutton", background=c["bg"], foreground=c["fg"])
    style.map(
        "TCheckbutton",
        background=[("active", c["bg"])],
        indicatorcolor=[("selected", c["accent"]), ("!selected", c["bg_field"])],
    )

    style.configure(
        "TEntry", fieldbackground=c["bg_field"], foreground=c["fg"],
        insertcolor=c["fg"], bordercolor=c["border"],
    )
    style.map("TEntry", fieldbackground=[("disabled", c["bg_panel"])])

    style.configure(
        "TSpinbox", fieldbackground=c["bg_field"], foreground=c["fg"],
        arrowcolor=c["fg"], bordercolor=c["border"], insertcolor=c["fg"],
    )
    style.map("TSpinbox", fieldbackground=[("disabled", c["bg_panel"])])

    style.configure(
        "TCombobox", fieldbackground=c["bg_field"], foreground=c["fg"],
        background=c["bg_field"], arrowcolor=c["fg"], bordercolor=c["border"],
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", c["bg_field"]), ("disabled", c["bg_panel"])],
        foreground=[("disabled", c["fg_muted"])],
        selectbackground=[("readonly", c["bg_field"])],
        selectforeground=[("readonly", c["fg"])],
    )
    # The combobox popdown list is a plain Tk Listbox, styled separately.
    root.option_add("*TCombobox*Listbox.background", c["bg_field"])
    root.option_add("*TCombobox*Listbox.foreground", c["fg"])
    root.option_add("*TCombobox*Listbox.selectBackground", c["accent"])
    root.option_add("*TCombobox*Listbox.selectForeground", c["tab_text_selected"])

    style.configure(
        "TProgressbar", background=c["accent"], troughcolor=c["bg_trough"],
        bordercolor=c["border"],
    )

    # ---- Notebook / tab bar --------------------------------------------
    # Strong contrast + bold text on the selected tab makes it obvious
    # which of the two tabs is active, and that there IS more than one.
    style.configure("TNotebook", background=c["bg"], bordercolor=c["border"])
    style.configure(
        "TNotebook.Tab", background=c["bg_trough"], foreground=c["fg_muted"],
        padding=(18, 9), font=("TkDefaultFont", 10),
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", c["accent"]), ("active", c["bg_field"])],
        foreground=[("selected", c["tab_text_selected"]), ("active", c["fg"])],
        font=[("selected", ("TkDefaultFont", 10, "bold"))],
        expand=[("selected", (2, 2, 2, 0))],
    )

    return style


def style_dark_axes(fig: "Figure", ax) -> None:
    """Apply the dark/colorblind-friendly palette to a matplotlib Axes.
    Call again after ax.clear(), since clear() resets rcParams-derived styling."""
    c = COLORS
    fig.patch.set_facecolor(c["bg_panel"])
    ax.set_facecolor(c["plot_bg"])
    ax.tick_params(colors=c["fg_muted"])
    for spine in ax.spines.values():
        spine.set_color(c["border"])
    ax.xaxis.label.set_color(c["fg"])
    ax.yaxis.label.set_color(c["fg"])
    ax.title.set_color(c["fg"])
    ax.grid(True, alpha=0.35, color=c["plot_grid"])


# =============================================================================
# Small shared helpers
# =============================================================================

def fmt_hz(hz: float) -> str:
    """Pretty-print a Hz value the way the SR760 front panel would."""
    if hz >= 1000.0:
        return f"{hz / 1000.0:g} kHz"
    return f"{hz:g} Hz"


def parse_value_with_unit(s: str, base_unit: str) -> float:
    """
    Parse strings like '32 Hz', '62.5 mHz', '100 ms', '3 ks', '10 s'
    into a float in the *base* unit (Hz for rates, seconds for times).
    """
    s = s.strip()
    num_str, _, unit = s.partition(" ")
    val = float(num_str)
    unit = unit.strip()
    prefix = unit[0] if len(unit) > len(base_unit) else ""
    multipliers = {"m": 1e-3, "k": 1e3, "µ": 1e-6, "u": 1e-6}
    if unit == base_unit:
        return val
    if prefix in multipliers and unit[1:] == base_unit:
        return val * multipliers[prefix]
    # Fallback: try to strip any leading non-digit unit prefix char
    return val


def nearest_index(mapping: dict, target: float) -> int:
    """Return the dict key whose numeric (parsed) value is closest to target."""
    best_key, best_diff = None, None
    for k, v in mapping.items():
        num = parse_value_with_unit(v, v.strip().split()[-1].lstrip("mkµu"))
        diff = abs(num - target)
        if best_diff is None or diff < best_diff:
            best_key, best_diff = k, diff
    return best_key


def sample_rate_hz(index: int) -> float:
    s = SAMPLE_RATE_VALUES[index]
    if s == "Trigger":
        return 0.0
    return parse_value_with_unit(s, "Hz")


def time_constant_seconds(index: int) -> float:
    return parse_value_with_unit(TIME_CONSTANT_VALUES[index], "s")


# =============================================================================
# Tab 1 -- SR760 FFT Interface
# =============================================================================

class SR760Tab(ttk.Frame):
    MEAS_OPTIONS = {"Measure Spectrum": 0, "Measure Time Record": 2}
    WINDOW_OPTIONS = {"Uniform": 0, "Flattop": 1, "Hanning": 2, "Blackman-Harris (BMH)": 3}
    AVG_TYPE_OPTIONS = {"RMS": 0, "Vector": 1, "Peak Hold": 2}

    def __init__(self, master):
        super().__init__(master, padding=8)
        self.dev: SR760 | None = None
        self._worker: threading.Thread | None = None
        self._last_freqs = []
        self._last_amps = []

        self._build_ui()
        self._refresh_resolution()

    # ------------------------------------------------------------------
    def _build_ui(self):
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        left = ttk.Frame(self)
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 8))

        # ---- GPIB Address -------------------------------------------------
        gpib_frame = ttk.LabelFrame(left, text="GPIB Address")
        gpib_frame.pack(fill="x", pady=4)
        self.gpib_var = tk.StringVar(value="10")
        ttk.Entry(gpib_frame, width=8, textvariable=self.gpib_var).pack(
            side="left", padx=6, pady=6
        )
        self.connect_btn = ttk.Button(gpib_frame, text="Connect", command=self._toggle_connect)
        self.connect_btn.pack(side="left", padx=6, pady=6)

        # ---- FFT Configuration Group ---------------------------------------
        cfg = ttk.LabelFrame(left, text="FFT Configuration")
        cfg.pack(fill="x", pady=4)

        ttk.Label(cfg, text="Active Trace:").grid(row=0, column=0, sticky="w", padx=6, pady=3)
        self.active_trace_var = tk.StringVar(value="Trace 0")
        ttk.Combobox(
            cfg, textvariable=self.active_trace_var, state="readonly", width=10,
            values=["Trace 0", "Trace 1"],
        ).grid(row=0, column=1, sticky="w", padx=6, pady=3)

        ttk.Label(cfg, text="Measurement:").grid(row=1, column=0, sticky="w", padx=6, pady=3)
        self.meas_var = tk.StringVar(value="Measure Spectrum")
        ttk.Combobox(
            cfg, textvariable=self.meas_var, state="readonly", width=18,
            values=list(self.MEAS_OPTIONS.keys()),
        ).grid(row=1, column=1, sticky="w", padx=6, pady=3)

        ttk.Label(cfg, text="Set Span From:").grid(row=2, column=0, sticky="w", padx=6, pady=3)
        self.span_from_var = tk.StringVar(value="Start Frequency")
        span_from_combo = ttk.Combobox(
            cfg, textvariable=self.span_from_var, state="readonly", width=18,
            values=["Start Frequency", "Center Frequency"],
        )
        span_from_combo.grid(row=2, column=1, sticky="w", padx=6, pady=3)
        span_from_combo.bind("<<ComboboxSelected>>", lambda e: self._update_freq_label())

        # ---- Set Frequency Range Group -------------------------------------
        freq = ttk.LabelFrame(left, text="Set Frequency Range")
        freq.pack(fill="x", pady=4)

        ttk.Label(freq, text="Span:").grid(row=0, column=0, sticky="w", padx=6, pady=3)
        self.span_labels = {idx: fmt_hz(hz) for idx, hz in SPAN_VALUES.items()}
        self.span_var = tk.StringVar(value=self.span_labels[10])  # 195 Hz-ish default
        span_combo = ttk.Combobox(
            freq, textvariable=self.span_var, state="readonly", width=12,
            values=list(self.span_labels.values()),
        )
        span_combo.grid(row=0, column=1, sticky="w", padx=6, pady=3)
        span_combo.bind("<<ComboboxSelected>>", lambda e: self._refresh_resolution())

        self.freq_label_var = tk.StringVar(value="Start Frequency (Hz):")
        ttk.Label(freq, textvariable=self.freq_label_var).grid(
            row=1, column=0, sticky="w", padx=6, pady=3
        )
        self.freq_value_var = tk.StringVar(value="0")
        ttk.Entry(freq, width=12, textvariable=self.freq_value_var).grid(
            row=1, column=1, sticky="w", padx=6, pady=3
        )

        ttk.Label(freq, text="Resolution (bin width):").grid(
            row=2, column=0, sticky="w", padx=6, pady=3
        )
        self.resolution_var = tk.StringVar(value="-- Hz")
        ttk.Label(freq, textvariable=self.resolution_var).grid(
            row=2, column=1, sticky="w", padx=6, pady=3
        )

        # ---- Advanced Options Group -----------------------------------------
        adv = ttk.LabelFrame(left, text="Advanced Options")
        adv.pack(fill="x", pady=4)

        self.calibrate_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            adv, text="Calibrate Offset (~15 s, after data run)",
            variable=self.calibrate_var,
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=6, pady=3)

        ttk.Label(adv, text="Windowing:").grid(row=1, column=0, sticky="w", padx=6, pady=3)
        self.window_var = tk.StringVar(value="Blackman-Harris (BMH)")
        ttk.Combobox(
            adv, textvariable=self.window_var, state="readonly", width=18,
            values=list(self.WINDOW_OPTIONS.keys()),
        ).grid(row=1, column=1, sticky="w", padx=6, pady=3)

        self.avg_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            adv, text="Linear Averaging", variable=self.avg_var,
            command=self._toggle_avg_controls,
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=6, pady=3)

        ttk.Label(adv, text="Avg Type:").grid(row=3, column=0, sticky="w", padx=6, pady=3)
        self.avg_type_var = tk.StringVar(value="RMS")
        self.avg_type_combo = ttk.Combobox(
            adv, textvariable=self.avg_type_var, state="disabled", width=10,
            values=list(self.AVG_TYPE_OPTIONS.keys()),
        )
        self.avg_type_combo.grid(row=3, column=1, sticky="w", padx=6, pady=3)

        ttk.Label(adv, text="# Traces:").grid(row=4, column=0, sticky="w", padx=6, pady=3)
        self.avg_count_var = tk.StringVar(value="10")
        self.avg_count_spin = ttk.Spinbox(
            adv, from_=2, to=32000, textvariable=self.avg_count_var, width=8, state="disabled"
        )
        self.avg_count_spin.grid(row=4, column=1, sticky="w", padx=6, pady=3)

        # ---- FFT Status Group -----------------------------------------------
        status = ttk.LabelFrame(left, text="FFT Status")
        status.pack(fill="x", pady=4)
        ttk.Label(status, text="Status:").grid(row=0, column=0, sticky="w", padx=6, pady=3)
        self.status_var = tk.StringVar(value="Not connected")
        ttk.Label(status, textvariable=self.status_var, foreground=COLORS["accent"]).grid(
            row=0, column=1, sticky="w", padx=6, pady=3
        )
        ttk.Label(status, text="Operating Mode:").grid(row=1, column=0, sticky="w", padx=6, pady=3)
        self.mode_var = tk.StringVar(value="--")
        ttk.Label(status, textvariable=self.mode_var).grid(row=1, column=1, sticky="w", padx=6, pady=3)
        ttk.Label(status, text="Data Settling:").grid(row=2, column=0, sticky="w", padx=6, pady=3)
        self.settle_var = tk.StringVar(value="--")
        ttk.Label(status, textvariable=self.settle_var).grid(row=2, column=1, sticky="w", padx=6, pady=3)

        # ---- Set Parameters button -------------------------------------------
        self.set_params_btn = ttk.Button(
            left, text="Set Parameters", command=self._on_set_parameters, state="disabled"
        )
        self.set_params_btn.pack(fill="x", pady=8)

        # ---- Plot -------------------------------------------------------------
        right = ttk.LabelFrame(self, text="Plot of Data")
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)

        self.fig = Figure(figsize=(6, 5), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_xlabel("Frequency (Hz)")
        self.ax.set_ylabel("Amplitude (dBV)")
        style_dark_axes(self.fig, self.ax)
        self.canvas = FigureCanvasTkAgg(self.fig, master=right)
        self.canvas.get_tk_widget().configure(bg=COLORS["bg_panel"], highlightthickness=0)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")

        # ---- Draggable readout cursor ----------------------------------
        cursor_frame = ttk.Frame(right)
        cursor_frame.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        ttk.Label(cursor_frame, text="Cursor:").pack(side="left", padx=(4, 4))
        self.cursor_var = tk.StringVar(value="Click and drag on the plot to read values")
        ttk.Label(cursor_frame, textvariable=self.cursor_var, foreground=COLORS["accent"]).pack(
            side="left"
        )

        # Artists for the draggable cursor (created lazily the first time
        # data is plotted / clicked on).
        self._cursor_vline = None
        self._cursor_marker = None
        self._cursor_dragging = False

        self.canvas.mpl_connect("button_press_event", self._on_cursor_press)
        self.canvas.mpl_connect("motion_notify_event", self._on_cursor_drag)
        self.canvas.mpl_connect("button_release_event", self._on_cursor_release)

        self._toggle_avg_controls()

    # ------------------------------------------------------------------
    # Draggable cursor on the FFT plot
    # ------------------------------------------------------------------
    def _on_cursor_press(self, event):
        if event.inaxes != self.ax or event.button != 1:
            return
        self._cursor_dragging = True
        self._update_cursor(event.xdata)

    def _on_cursor_drag(self, event):
        if not self._cursor_dragging or event.inaxes != self.ax:
            return
        self._update_cursor(event.xdata)

    def _on_cursor_release(self, event):
        self._cursor_dragging = False

    def _update_cursor(self, x_click):
        """Snap the cursor to the nearest measured point and show its
        frequency/power values."""
        if x_click is None or not self._last_freqs:
            return

        freqs, amps = self._last_freqs, self._last_amps
        idx = min(range(len(freqs)), key=lambda i: abs(freqs[i] - x_click))
        fx, fy = freqs[idx], amps[idx]

        if self._cursor_vline is None or self._cursor_vline not in self.ax.lines:
            self._cursor_vline = self.ax.axvline(
                fx, color=COLORS["error"], linewidth=0.8, linestyle="--"
            )
        else:
            self._cursor_vline.set_xdata([fx, fx])

        if self._cursor_marker is None or self._cursor_marker not in self.ax.lines:
            (self._cursor_marker,) = self.ax.plot(
                [fx], [fy], marker="o", color=COLORS["error"], markersize=6, linestyle="None"
            )
        else:
            self._cursor_marker.set_data([fx], [fy])

        self.cursor_var.set(f"Frequency = {fmt_hz(fx)}   Power = {fy:.4g} dBV")
        self.canvas.draw_idle()

    # ------------------------------------------------------------------
    def _toggle_avg_controls(self):
        state = "readonly" if self.avg_var.get() else "disabled"
        spin_state = "normal" if self.avg_var.get() else "disabled"
        self.avg_type_combo.configure(state=state)
        self.avg_count_spin.configure(state=spin_state)

    def _update_freq_label(self):
        if self.span_from_var.get() == "Start Frequency":
            self.freq_label_var.set("Start Frequency (Hz):")
        else:
            self.freq_label_var.set("Center Frequency (Hz):")

    def _refresh_resolution(self):
        self._update_freq_label()
        label = self.span_var.get()
        idx = None
        for i, l in self.span_labels.items():
            if l == label:
                idx = i
                break
        if idx is not None:
            hz = SPAN_VALUES[idx]
            self.resolution_var.set(f"{hz / 400.0:g} Hz")

    def _selected_span_hz(self) -> float:
        label = self.span_var.get()
        for i, l in self.span_labels.items():
            if l == label:
                return SPAN_VALUES[i]
        return 195.0

    # ------------------------------------------------------------------
    def _toggle_connect(self):
        if self.dev is None:
            self._connect()
        else:
            self._disconnect()

    def _connect(self):
        try:
            addr = int(self.gpib_var.get())
        except ValueError:
            messagebox.showerror("Invalid GPIB Address", "GPIB address must be an integer.")
            return
        self.status_var.set("Connecting...")
        self.update_idletasks()
        try:
            self.dev = SR760(gpib_address=addr)
            self.dev.configure_gpib()
            self.dev.reset()
            self.dev.set_local(1)  # REMOTE
            self.dev.auto_offset(mode = 0) # turn off auto offset
            idn = self.dev.identify()
            self.status_var.set(f"Connected: {idn}")
            self.mode_var.set("Remote")
            self.connect_btn.configure(text="Disconnect")
            self.set_params_btn.configure(state="normal")
        except Exception as exc:  # noqa: BLE001 - surface any VISA/comm error
            self.dev = None
            self.status_var.set("Connection failed")
            self.mode_var.set("--")
            messagebox.showerror("Connection Error", f"Could not connect to SR760:\n{exc}")

    def _disconnect(self):
        if self.dev is not None:
            try:
                if self.calibrate_var.get():
                    self.dev.auto_offset(mode = 1)
                self.dev.set_local(0)  # back to LOCAL
                self.dev.close()
            except Exception:
                pass
        self.dev = None
        self.status_var.set("Not connected")
        self.mode_var.set("--")
        self.connect_btn.configure(text="Connect")
        self.set_params_btn.configure(state="disabled")

    # ------------------------------------------------------------------
    def _on_set_parameters(self):
        if self.dev is None:
            messagebox.showwarning("Not Connected", "Connect to the SR760 first.")
            return
        try:
            freq_val = float(self.freq_value_var.get())
        except ValueError:
            messagebox.showerror("Invalid Value", "Start/Center Frequency must be numeric.")
            return

        # Lock out controls while the run is in progress, matching the
        # LabVIEW program's "controls are ignored after Set Parameters" note.
        self.set_params_btn.configure(state="disabled")
        self.connect_btn.configure(state="disabled")

        params = dict(
            trace=0 if self.active_trace_var.get() == "Trace 0" else 1,
            meas_type=self.MEAS_OPTIONS[self.meas_var.get()],
            span_hz=self._selected_span_hz(),
            span_from_center=(self.span_from_var.get() == "Center Frequency"),
            freq_val=freq_val,
            window=self.WINDOW_OPTIONS[self.window_var.get()],
            calibrate=self.calibrate_var.get(),
            averaging=self.avg_var.get(),
            avg_type=self.AVG_TYPE_OPTIONS[self.avg_type_var.get()],
            avg_count=int(self.avg_count_var.get() or 2),
        )
        self._worker = threading.Thread(target=self._run_acquisition, args=(params,), daemon=True)
        self._worker.start()

    def _run_acquisition(self, p):
        try:
            dev = self.dev
            self._set_status("Configuring FFT...")
            dev.set_measurement(p["trace"], p["meas_type"])
            dev.set_display(p["trace"], 0)   # Log Magnitude
            dev.set_units(p["trace"], 2)     # dBV
            dev.set_window(p["trace"], p["window"])
            dev.set_active_trace(p["trace"])

            if p["averaging"]:
                self._set_status("Configuring averaging...")
                dev.set_averaging(True)
                dev.set_num_averages(max(2, p["avg_count"]))
                dev.set_averaging_type(p["avg_type"])
                dev.set_averaging_mode(0)  # Linear
            else:
                dev.set_averaging(False)
            time.sleep(0.5)
            self._set_settle("Not settled")

            if dev.get_serial_poll_byte(4):
                dev.clear_status() # does this clear queue...? i don't think so
            dev.set_span_hz(p["span_hz"])
            # depending on type, query and display third value? not strictly necessary
            if p["span_from_center"]:
                dev.set_center_frequency(p["freq_val"])
            else:
                dev.set_start_frequency(p["freq_val"])
            
            dev.start()
            #dev.wait_for_ready()
            dev.check_errors()

            dev.wait_for_ready_settling()
            #if p["averaging"]:
            #    dev.wait_for_ready_average()

            self._set_settle("Settled")

            self._set_status("Acquiring spectrum...")

            # split into x and y acquisition like in labview?
            # use of markers?
            freqs, amps = dev.get_spectrum(p["trace"])
            self._last_freqs, self._last_amps = freqs, amps

            self._set_status("Plotting...")
            self.after(0, self._plot_data, freqs, amps)

            self._set_status("Done")
            self.after(0, self._prompt_save)

            if p["calibrate"]:
                self._set_status("Calibrating offset - please wait")
                dev.auto_offset(mode=1)
                time.sleep(15)
            else:
                dev.auto_offset(mode=0)
            self._set_status("Done")

        except (SR760Error, Exception) as exc:  # noqa: BLE001
            self._set_status("Error")
            err = str(exc)
            self.after(0, lambda: messagebox.showerror("SR760 Error", err))
        finally:
            self.after(0, lambda: self.set_params_btn.configure(state="normal"))
            self.after(0, lambda: self.connect_btn.configure(state="normal"))

    def _set_status(self, text):
        self.after(0, self.status_var.set, text)

    def _set_settle(self, text):
        self.after(0, self.settle_var.set, text)

    def _plot_data(self, freqs, amps):
        self.ax.clear()
        # ax.clear() removes every artist, including any previous cursor
        # line/marker -- drop our references so they get recreated fresh.
        self._cursor_vline = None
        self._cursor_marker = None
        self.cursor_var.set("Click and drag on the plot to read values")

        self.ax.plot(freqs, amps, color=COLORS["accent"], linewidth=1)
        self.ax.set_xlabel("Frequency (Hz)")
        self.ax.set_ylabel("Log Magnitude (dBV)")
        style_dark_axes(self.fig, self.ax)
        self.canvas.draw()

    def _prompt_save(self):
        if not self._last_freqs:
            return
        if not messagebox.askyesno("Save Data", "Data run complete. Save data?"):
            return
        path = filedialog.asksaveasfilename(
            title="Save FFT Data",
            defaultextension=".xls",
            filetypes=[("Tab-delimited spreadsheet", "*.xls"), ("Text file", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", newline="") as f:
                writer = csv.writer(f, delimiter="\t")
                writer.writerow(["Frequency (Hz)", "Amplitude (dBV)"])
                for fr, am in zip(self._last_freqs, self._last_amps):
                    writer.writerow([fr, am])
            messagebox.showinfo("Saved", f"Data saved to:\n{path}")
        except OSError as exc:
            messagebox.showerror("Save Error", str(exc))


# =============================================================================
# Tab 2 -- SR830 Lock-In Interface
# =============================================================================

class SR830Tab(ttk.Frame):
    SAVE_TO_OPTIONS = ["One File (all runs)", "Separate File per Run"]
    MODE_OPTIONS = ["Custom", "Best Choice"]

    def __init__(self, master):
        super().__init__(master, padding=8)
        self.dev: SR830 | None = None
        self._worker: threading.Thread | None = None
        self._run_stats = []  # list of dicts per run

        self._build_ui()
        self._toggle_mode_controls()
        self._toggle_save_controls()

    # ------------------------------------------------------------------
    def _build_ui(self):
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        left = ttk.Frame(self)
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 8))

        # ---- Data Acquisition Options (GPIB + mode) ------------------------
        acq = ttk.LabelFrame(left, text="Data Acquisition Options")
        acq.pack(fill="x", pady=4)

        ttk.Label(acq, text="GPIB Address:").grid(row=0, column=0, sticky="w", padx=6, pady=3)
        self.gpib_var = tk.StringVar(value="8")
        gpib_row = ttk.Frame(acq)
        gpib_row.grid(row=0, column=1, sticky="w", padx=6, pady=3)
        ttk.Entry(gpib_row, width=6, textvariable=self.gpib_var).pack(side="left")
        self.connect_btn = ttk.Button(gpib_row, text="Connect", command=self._toggle_connect)
        self.connect_btn.pack(side="left", padx=4)

        ttk.Label(acq, text="Sample Mode:").grid(row=1, column=0, sticky="w", padx=6, pady=3)
        self.mode_var = tk.StringVar(value="Best Choice")
        mode_combo = ttk.Combobox(
            acq, textvariable=self.mode_var, state="readonly", width=14, values=self.MODE_OPTIONS
        )
        mode_combo.grid(row=1, column=1, sticky="w", padx=6, pady=3)
        mode_combo.bind("<<ComboboxSelected>>", lambda e: self._toggle_mode_controls())

        ttk.Label(acq, text="Time Constant:").grid(row=2, column=0, sticky="w", padx=6, pady=3)
        self.tc_var = tk.StringVar(value=TIME_CONSTANT_VALUES[8])  # 100 ms
        ttk.Combobox(
            acq, textvariable=self.tc_var, state="readonly", width=14,
            values=list(TIME_CONSTANT_VALUES.values()),
        ).grid(row=2, column=1, sticky="w", padx=6, pady=3)
        self.tc_var.trace_add("write", lambda *a: self._recompute())

        ttk.Label(acq, text="Sample Rate:").grid(row=3, column=0, sticky="w", padx=6, pady=3)
        self.rate_var = tk.StringVar(value=SAMPLE_RATE_VALUES[9])  # 32 Hz
        self.rate_combo = ttk.Combobox(
            acq, textvariable=self.rate_var, state="readonly", width=14,
            values=list(SAMPLE_RATE_VALUES.values())[:-1],  # exclude "Trigger"
        )
        self.rate_combo.grid(row=3, column=1, sticky="w", padx=6, pady=3)
        self.rate_var.trace_add("write", lambda *a: self._recompute())

        ttk.Label(acq, text="Span (s):").grid(row=4, column=0, sticky="w", padx=6, pady=3)
        self.span_var = tk.StringVar(value="43.75")
        self.span_entry = ttk.Entry(acq, width=10, textvariable=self.span_var)
        self.span_entry.grid(row=4, column=1, sticky="w", padx=6, pady=3)
        self.span_var.trace_add("write", lambda *a: self._recompute())

        ttk.Label(acq, text="Total # Points:").grid(row=5, column=0, sticky="w", padx=6, pady=3)
        self.points_var = tk.StringVar(value="--")
        ttk.Label(acq, textvariable=self.points_var).grid(row=5, column=1, sticky="w", padx=6, pady=3)

        ttk.Label(acq, text="Sensitivity:").grid(row=6, column=0, sticky="w", padx=6, pady=3)
        self.sensitivity_var = tk.StringVar(value=SENSITIVITY_VALUES[20])  # 10 mV/nA
        ttk.Combobox(
            acq, textvariable=self.sensitivity_var, state="readonly", width=14,
            values=list(SENSITIVITY_VALUES.values()),
        ).grid(row=6, column=1, sticky="w", padx=6, pady=3)

        ttk.Label(acq, text="Filter Slope:").grid(row=7, column=0, sticky="w", padx=6, pady=3)
        self.filter_slope_var = tk.StringVar(value=FILTER_SLOPE_VALUES[1])  # 12 dB/oct
        ttk.Combobox(
            acq, textvariable=self.filter_slope_var, state="readonly", width=14,
            values=list(FILTER_SLOPE_VALUES.values()),
        ).grid(row=7, column=1, sticky="w", padx=6, pady=3)

        # ---- Data Run and Save Options --------------------------------------
        run_frame = ttk.LabelFrame(left, text="Data Run and Save Options")
        run_frame.pack(fill="x", pady=4)

        ttk.Label(run_frame, text="Number of Runs:").grid(row=0, column=0, sticky="w", padx=6, pady=3)
        self.num_runs_var = tk.StringVar(value="1")
        ttk.Spinbox(run_frame, from_=1, to=1000, width=8, textvariable=self.num_runs_var).grid(
            row=0, column=1, sticky="w", padx=6, pady=3
        )

        self.save_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            run_frame, text="Save Data?", variable=self.save_var, command=self._toggle_save_controls
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=6, pady=3)

        ttk.Label(run_frame, text="Save Data To:").grid(row=2, column=0, sticky="w", padx=6, pady=3)
        self.save_to_var = tk.StringVar(value=self.SAVE_TO_OPTIONS[0])
        self.save_to_combo = ttk.Combobox(
            run_frame, textvariable=self.save_to_var, state="readonly", width=20,
            values=self.SAVE_TO_OPTIONS,
        )
        self.save_to_combo.grid(row=2, column=1, sticky="w", padx=6, pady=3)

        ttk.Label(run_frame, text="Base File Path:").grid(row=3, column=0, sticky="w", padx=6, pady=3)
        path_row = ttk.Frame(run_frame)
        path_row.grid(row=3, column=1, sticky="w", padx=6, pady=3)
        self.base_path_var = tk.StringVar(value="")
        self.base_path_entry = ttk.Entry(path_row, width=18, textvariable=self.base_path_var)
        self.base_path_entry.pack(side="left")
        self.browse_btn = ttk.Button(path_row, text="Browse...", command=self._browse_base_path)
        self.browse_btn.pack(side="left", padx=4)

        # ---- Mid-run checkpoint autosave ------------------------------------
        # Safeguard for long/overnight single runs: periodically flush
        # whatever has been acquired so far to disk, so a crash or power
        # loss loses at most one checkpoint interval, not the whole run.
        self.checkpoint_var = tk.BooleanVar(value=True)
        self.checkpoint_check = ttk.Checkbutton(
            run_frame, text="Mid-run checkpoint autosave", variable=self.checkpoint_var,
            command=self._toggle_save_controls,
        )
        self.checkpoint_check.grid(row=4, column=0, columnspan=2, sticky="w", padx=6, pady=(6, 0))

        ttk.Label(run_frame, text="Checkpoint every:").grid(row=5, column=0, sticky="w", padx=6, pady=3)
        checkpoint_row = ttk.Frame(run_frame)
        checkpoint_row.grid(row=5, column=1, sticky="w", padx=6, pady=3)
        self.checkpoint_interval_var = tk.StringVar(value="10")
        self.checkpoint_interval_spin = ttk.Spinbox(
            checkpoint_row, from_=1, to=180, width=5, textvariable=self.checkpoint_interval_var,
        )
        self.checkpoint_interval_spin.pack(side="left")
        ttk.Label(checkpoint_row, text="min").pack(side="left", padx=(4, 0))

        self.start_btn = ttk.Button(left, text="Start Run(s)", command=self._on_start, state="disabled")
        self.start_btn.pack(fill="x", pady=8)

        # ---- SR830 Status ring ------------------------------------------------
        status = ttk.LabelFrame(left, text="SR830 Status")
        status.pack(fill="x", pady=4)
        ttk.Label(status, text="Status:").grid(row=0, column=0, sticky="w", padx=6, pady=3)
        self.status_var = tk.StringVar(value="Not connected")
        ttk.Label(status, textvariable=self.status_var, foreground=COLORS["accent"]).grid(
            row=0, column=1, sticky="w", padx=6, pady=3
        )
        self.progress = ttk.Progressbar(status, orient="horizontal", mode="determinate", length=200)
        self.progress.grid(row=1, column=0, columnspan=2, sticky="we", padx=6, pady=4)

        # ---- Analyzed Data group -----------------------------------------------
        analyzed = ttk.LabelFrame(left, text="Analyzed Data")
        analyzed.pack(fill="x", pady=4)
        self.analyzed_vars = {}
        for i, key in enumerate(["<R>", "<dR^2>^1/2", "Min. R", "Max. R", "<dR>"]):
            ttk.Label(analyzed, text=f"{key}:").grid(row=i, column=0, sticky="w", padx=6, pady=2)
            v = tk.StringVar(value="--")
            ttk.Label(analyzed, textvariable=v).grid(row=i, column=1, sticky="w", padx=6, pady=2)
            self.analyzed_vars[key] = v

        # ---- Plot ---------------------------------------------------------------
        right = ttk.LabelFrame(self, text="Ch. 1 Data Plot")
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)

        self.fig = Figure(figsize=(6, 5), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_xlabel("Data Point Number")
        self.ax.set_ylabel("Volts")
        style_dark_axes(self.fig, self.ax)
        self.canvas = FigureCanvasTkAgg(self.fig, master=right)
        self.canvas.get_tk_widget().configure(bg=COLORS["bg_panel"], highlightthickness=0)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")

        # ---- Draggable readout cursor ----------------------------------
        cursor_frame = ttk.Frame(right)
        cursor_frame.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        ttk.Label(cursor_frame, text="Cursor:").pack(side="left", padx=(4, 4))
        self.cursor_var = tk.StringVar(value="Click and drag on the plot to read values")
        ttk.Label(cursor_frame, textvariable=self.cursor_var, foreground=COLORS["accent"]).pack(
            side="left"
        )

        self._last_plot_data = []
        self._cursor_vline = None
        self._cursor_marker = None
        self._cursor_dragging = False

        self.canvas.mpl_connect("button_press_event", self._on_cursor_press)
        self.canvas.mpl_connect("motion_notify_event", self._on_cursor_drag)
        self.canvas.mpl_connect("button_release_event", self._on_cursor_release)

        self._recompute()

    # ------------------------------------------------------------------
    # Draggable cursor on the data plot
    # ------------------------------------------------------------------
    def _on_cursor_press(self, event):
        if event.inaxes != self.ax or event.button != 1:
            return
        self._cursor_dragging = True
        self._update_cursor(event.xdata)

    def _on_cursor_drag(self, event):
        if not self._cursor_dragging or event.inaxes != self.ax:
            return
        self._update_cursor(event.xdata)

    def _on_cursor_release(self, event):
        self._cursor_dragging = False

    def _update_cursor(self, x_click):
        """Snap the cursor to the nearest measured point and show its
        point number/voltage values."""
        if x_click is None or not self._last_plot_data:
            return

        data = self._last_plot_data
        idx = min(range(len(data)), key=lambda i: abs(i - x_click))
        idx = max(0, min(idx, len(data) - 1))
        fx, fy = idx, data[idx]

        if self._cursor_vline is None or self._cursor_vline not in self.ax.lines:
            self._cursor_vline = self.ax.axvline(
                fx, color=COLORS["error"], linewidth=0.8, linestyle="--"
            )
        else:
            self._cursor_vline.set_xdata([fx, fx])

        if self._cursor_marker is None or self._cursor_marker not in self.ax.lines:
            (self._cursor_marker,) = self.ax.plot(
                [fx], [fy], marker="o", color=COLORS["error"], markersize=6, linestyle="None"
            )
        else:
            self._cursor_marker.set_data([fx], [fy])

        self.cursor_var.set(f"Point # = {fx:g}   R = {fy:.4g} V")
        self.canvas.draw_idle()

    # ------------------------------------------------------------------
    def _toggle_mode_controls(self):
        best_choice = self.mode_var.get() == "Best Choice"
        state = "disabled" if best_choice else "readonly"
        entry_state = "disabled" if best_choice else "normal"
        self.rate_combo.configure(state=state)
        self.span_entry.configure(state=entry_state)
        self._recompute()

    def _toggle_save_controls(self):
        state = "normal" if self.save_var.get() else "disabled"
        combo_state = "readonly" if self.save_var.get() else "disabled"
        self.save_to_combo.configure(state=combo_state)
        self.base_path_entry.configure(state=state)
        self.browse_btn.configure(state=state)
        self.checkpoint_check.configure(state=state)
        checkpoint_state = "normal" if (self.save_var.get() and self.checkpoint_var.get()) else "disabled"
        self.checkpoint_interval_spin.configure(state=checkpoint_state)

    def _browse_base_path(self):
        path = filedialog.asksaveasfilename(
            title="Choose Base File Path",
            defaultextension=".xls",
            filetypes=[("Spreadsheet", "*.xls"), ("All files", "*.*")],
        )
        if path:
            # Store without extension; extension/suffixes are appended per-file.
            base, _ext = os.path.splitext(path)
            self.base_path_var.set(base)

    def _recompute(self):
        """Recompute Best Choice sample rate / span, and the total-points display."""
        try:
            tc_s = parse_value_with_unit(self.tc_var.get(), "s")
        except Exception:
            self.points_var.set("--")
            return

        if self.mode_var.get() == "Best Choice":
            target_rate = 10.0 / tc_s if tc_s > 0 else 1.0
            best_idx = min(
                (i for i in SAMPLE_RATE_VALUES if SAMPLE_RATE_VALUES[i] != "Trigger"),
                key=lambda i: abs(sample_rate_hz(i) - target_rate),
            )
            rate_hz = sample_rate_hz(best_idx)
            self.rate_var.set(SAMPLE_RATE_VALUES[best_idx])
            span_s = 1400.0 / rate_hz if rate_hz > 0 else 0.0
            self.span_var.set(f"{span_s:.4g}")
            total_points = 1400
        else:
            try:
                rate_hz = parse_value_with_unit(self.rate_var.get(), "Hz")
                span_s = float(self.span_var.get())
                total_points = int(round(span_s * rate_hz))
            except Exception:
                self.points_var.set("--")
                return

        if total_points > 1400 and self.mode_var.get() == "Custom":
            self.points_var.set(f"{total_points}  (exceeds 1400 max!)")
        else:
            self.points_var.set(str(total_points))

    # ------------------------------------------------------------------
    def _toggle_connect(self):
        if self.dev is None:
            self._connect()
        else:
            self._disconnect()

    def _connect(self):
        try:
            addr = int(self.gpib_var.get())
        except ValueError:
            messagebox.showerror("Invalid GPIB Address", "GPIB address must be an integer.")
            return
        self.status_var.set("Connecting...")
        self.update_idletasks()
        try:
            self.dev = SR830(gpib_address=addr)
            self.dev.configure_gpib()
            self.dev.reset()
            self.dev.set_local(1) # Remote
            idn = self.dev.identify()
            self.status_var.set(f"Connected: {idn}")
            self.connect_btn.configure(text="Disconnect")
            self.start_btn.configure(state="normal")
        except Exception as exc:  # noqa: BLE001
            self.dev = None
            self.status_var.set("Connection failed")
            messagebox.showerror("Connection Error", f"Could not connect to SR830:\n{exc}")

    def _disconnect(self):
        if self.dev is not None:
            try:
                self.dev.set_local(0) # Local
                self.dev.close()
            except Exception:
                pass
        self.dev = None
        self.status_var.set("Not connected")
        self.connect_btn.configure(text="Connect")
        self.start_btn.configure(state="disabled")

    # ------------------------------------------------------------------
    def _on_start(self):
        if self.dev is None:
            messagebox.showwarning("Not Connected", "Connect to the SR830 first.")
            return
        try:
            num_runs = int(self.num_runs_var.get())
            span_s = float(self.span_var.get())
        except ValueError:
            messagebox.showerror("Invalid Value", "Number of Runs and Span must be numeric.")
            return

        rate_idx = None
        for i, v in SAMPLE_RATE_VALUES.items():
            if v == self.rate_var.get():
                rate_idx = i
                break
        tc_idx = None
        for i, v in TIME_CONSTANT_VALUES.items():
            if v == self.tc_var.get():
                tc_idx = i
                break
        sensitivity_idx = None
        for i, v in SENSITIVITY_VALUES.items():
            if v == self.sensitivity_var.get():
                sensitivity_idx = i
                break
        filter_slope_idx = None
        for i, v in FILTER_SLOPE_VALUES.items():
            if v == self.filter_slope_var.get():
                filter_slope_idx = i
                break
        if rate_idx is None or tc_idx is None or sensitivity_idx is None or filter_slope_idx is None:
            messagebox.showerror(
                "Invalid Value",
                "Select a valid time constant / sample rate / sensitivity / filter slope.",
            )
            return
        
        points_per_run = int(span_s * rate_idx)
        if points_per_run > self.dev.MAX_BUFFER_POINTS:
            messagebox.showerror("Invalid Value", "Points per run greater than buffer size; data will be overwritten.")
            return

        save = self.save_var.get()
        base_path = self.base_path_var.get().strip()
        if save and not base_path:
            messagebox.showwarning("Base File Path Required", "Enter or browse for a base file path.")
            return
        if save and num_runs > 1 and self.save_to_var.get() != self.SAVE_TO_OPTIONS[1]:
            # "if more than one run is to be performed, the data will automatically be saved"
            self.save_var.set(True)

        checkpoint_enabled = save and self.checkpoint_var.get()
        checkpoint_interval_s = 0.0
        if checkpoint_enabled:
            try:
                checkpoint_interval_s = float(self.checkpoint_interval_var.get()) * 60.0
                if checkpoint_interval_s <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Invalid Value", "Checkpoint interval must be a positive number of minutes.")
                return

        self.start_btn.configure(state="disabled")
        self.connect_btn.configure(state="disabled")
        self._run_stats = []

        params = dict(
            num_runs=num_runs, span_s=span_s, rate_idx=rate_idx, points_per_run=points_per_run, 
            tc_idx=tc_idx,
            sensitivity=sensitivity_idx, filter_slope=filter_slope_idx,
            save=save, base_path=base_path,
            multi_file=(self.save_to_var.get() == self.SAVE_TO_OPTIONS[1]),
            tc_label=self.tc_var.get(),
            checkpoint_enabled=checkpoint_enabled, checkpoint_interval_s=checkpoint_interval_s,
        )
        self._worker = threading.Thread(target=self._run_all, args=(params,), daemon=True)
        self._worker.start()

    def _run_all(self, p):
        try:
            dev = self.dev
            dev.pause_scan()
            dev.set_end_of_buffer_mode(0)
            dev.reset_scan()

            dev.set_sample_rate(p["rate_idx"])
            dev.set_time_constant(p["tc_idx"])
            dev.set_sensitivity(p["sensitivity"])
            dev.set_filter_slope(p["filter_slope"])
            dev.set_display(channel=1, display=0, ratio=0) # display X on ch. 1
            dev.set_display(channel=2, display=1, ratio=0) # display θ on ch. 2

            dev.check_errors()
            
            all_runs_data = []
            for run_idx in range(1, p["num_runs"] + 1):
                self._set_status(f"Run {run_idx}/{p['num_runs']}: acquiring...")
                self._set_progress(0)

                dev.set_end_of_buffer_mode(0)  # 1 Shot
                dev.pause_scan()
                dev.reset_scan()
                dev.start_scan()

                # Poll SPTS? until the requested number of points has been
                # stored. get_num_stored_points() is safe to call during
                # active storage (manual §5), so we also use this loop to
                # opportunistically flush partial data to disk -- our
                # crash-safety net for long/overnight runs.
                last_checkpoint = time.time()
                n_points = dev.get_num_stored_points()
                while n_points < p["points_per_run"]:
                    pct = min(100, n_points / p["points_per_run"]) if p["points_per_run"] > 0 else 100
                    self._set_progress(pct)

                    if (p["checkpoint_enabled"] and n_points > 0
                            and (time.time() - last_checkpoint) >= p["checkpoint_interval_s"]):
                        try:
                            partial = dev.get_trace_ascii(1, 0, n_points)
                            self._save_checkpoint(p, run_idx, partial)
                            self._set_status(
                                f"Run {run_idx}/{p['num_runs']}: acquiring... "
                                f"(checkpoint saved at {n_points} pts)"
                            )
                        except Exception:
                            pass  # a checkpoint write failure should never abort a live run
                        last_checkpoint = time.time()

                    time.sleep(0.25)
                    n_points = dev.get_num_stored_points()

                dev.pause_scan()

                self._set_status(f"Run {run_idx}/{p['num_runs']}: downloading...")
                data = dev.get_trace_ascii(1, 0, n_points) if n_points > 0 else []
                all_runs_data.append(data)

                dev.reset_scan()
                dev.start_scan()

                stats = self._compute_stats(data)
                self._run_stats.append(stats)
                self.after(0, self._update_analyzed, stats)
                self.after(0, self._plot_run, data, stats)

                if p["save"] and data:
                    self._save_run(p, run_idx, data)
                    if p["checkpoint_enabled"]:
                        # Run finished and was saved for real -- the interim
                        # checkpoint file for this run is no longer needed.
                        try:
                            os.remove(self._checkpoint_path(p, run_idx))
                        except OSError:
                            pass

            if p["save"] and self._run_stats:
                self._save_analyzed(p)
            
            dev.check_errors() # Don't interrupt runs, but do alert user to potential problems

            self._set_status("Done")
            self._set_progress(100)

        except (SR830Error, Exception) as exc:  # noqa: BLE001
            self._set_status("Error")
            err = str(exc)
            self.after(0, lambda: messagebox.showerror("SR830 Error", err))
        finally:
            self.after(0, lambda: self.start_btn.configure(state="normal"))
            self.after(0, lambda: self.connect_btn.configure(state="normal"))

    @staticmethod
    def _compute_stats(data):
        if not data:
            return {"mean": 0.0, "std": 0.0, "min": 0.0, "max": 0.0, "mad": 0.0}
        n = len(data)
        mean = sum(data) / n
        var = sum((x - mean) ** 2 for x in data) / n
        std = var ** 0.5
        mad = sum(abs(x - mean) for x in data) / n
        return {"mean": mean, "std": std, "min": min(data), "max": max(data), "mad": mad}

    def _update_analyzed(self, stats):
        self.analyzed_vars["<R>"].set(f"{stats['mean']:.6g} V")
        self.analyzed_vars["<dR^2>^1/2"].set(f"{stats['std']:.6g} V")
        self.analyzed_vars["Min. R"].set(f"{stats['min']:.6g} V")
        self.analyzed_vars["Max. R"].set(f"{stats['max']:.6g} V")
        self.analyzed_vars["<dR>"].set(f"{stats['mad']:.6g} V")

    def _plot_run(self, data, stats):
        self.ax.clear()
        # ax.clear() removes every artist, including any previous cursor
        # line/marker -- drop our references so they get recreated fresh.
        self._cursor_vline = None
        self._cursor_marker = None
        self.cursor_var.set("Click and drag on the plot to read values")
        self._last_plot_data = data

        if data:
            x = list(range(len(data)))
            self.ax.plot(x, data, color=COLORS["accent"], linewidth=0.8, label="Raw data")
            self.ax.axhline(stats["mean"], color=COLORS["success"], linewidth=1.2, label="Average")
            self.ax.axhline(stats["min"], color=COLORS["warning"], linestyle="--", linewidth=1, label="Min/Max")
            self.ax.axhline(stats["max"], color=COLORS["warning"], linestyle="--", linewidth=1)
            legend = self.ax.legend(loc="upper right", fontsize=8, facecolor=COLORS["bg_panel"],
                                     edgecolor=COLORS["border"], labelcolor=COLORS["fg"])
        self.ax.set_xlabel("Data Point Number")
        self.ax.set_ylabel("Volts")
        style_dark_axes(self.fig, self.ax)
        self.canvas.draw()

    def _set_status(self, text):
        self.after(0, self.status_var.set, text)

    def _set_progress(self, pct):
        self.after(0, self.progress.configure, {"value": pct})

    # ------------------------------------------------------------------
    @staticmethod
    def _checkpoint_path(p, run_idx):
        """Fixed filename for a run's checkpoint file, so repeated
        checkpoint writes overwrite in place instead of piling up files."""
        tc_label = p["tc_label"].replace(" ", "").replace("/", "")
        return f"{p['base_path']} (T={tc_label} Run{run_idx} CHECKPOINT).xls"

    def _save_checkpoint(self, p, run_idx, data):
        """Write whatever has been acquired so far for the current run to
        a checkpoint file (overwritten on every call). Best-effort: a
        failure here must never interrupt the live acquisition."""
        path = self._checkpoint_path(p, run_idx)
        try:
            with open(path, "w", newline="") as f:
                writer = csv.writer(f, delimiter="\t")
                writer.writerow([
                    f"-- Checkpoint: Run {run_idx}, {len(data)} of "
                    f"{p['points_per_run']} points, "
                    f"{time.strftime('%Y-%m-%d %H:%M:%S')} --"
                ])
                writer.writerow(["Point #", "R (V)"])
                for i, v in enumerate(data):
                    writer.writerow([i, v])
        except OSError as exc:
            err = str(exc)
            self.after(0, lambda: messagebox.showwarning(
                "Checkpoint Save Warning",
                f"Could not write checkpoint file (run continues):\n{err}",
            ))

    def _save_run(self, p, run_idx, data):
        tc_label = p["tc_label"].replace(" ", "").replace("/", "")
        if p["multi_file"]:
            path = f"{p['base_path']} (T={tc_label} Run{run_idx}).xls"
        else:
            path = f"{p['base_path']} (T={tc_label}).xls"
        mode = "a" if (not p["multi_file"] and run_idx > 1) else "w"
        try:
            with open(path, mode, newline="") as f:
                writer = csv.writer(f, delimiter="\t")
                if mode == "w":
                    writer.writerow(["Point #", "R (V)"])
                if mode == "a":
                    writer.writerow([])
                    writer.writerow([f"-- Run {run_idx} --"])
                for i, v in enumerate(data):
                    writer.writerow([i, v])
        except OSError as exc:
            err = str(exc)
            self.after(0, lambda: messagebox.showerror("Save Error", err))

    def _save_analyzed(self, p):
        tc_label = p["tc_label"].replace(" ", "").replace("/", "")
        path = f"{p['base_path']} Analyzed Data (T={tc_label}).xls"
        try:
            with open(path, "w", newline="") as f:
                writer = csv.writer(f, delimiter="\t")
                writer.writerow(["Run", "<R> (V)", "<dR^2>^1/2 (V)", "Min R (V)", "Max R (V)", "<dR> (V)"])
                for i, s in enumerate(self._run_stats, start=1):
                    writer.writerow([i, s["mean"], s["std"], s["min"], s["max"], s["mad"]])
            self.after(0, lambda: messagebox.showinfo("Saved", f"Data saved with base path:\n{p['base_path']}"))
        except OSError as exc:
            err = str(exc)
            self.after(0, lambda: messagebox.showerror("Save Error", err))


# =============================================================================
# Main application window
# =============================================================================

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("LLS Control Panel")
        self.geometry("1150x720")
        self.minsize(950, 600)

        apply_dark_theme(self)

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=6, pady=6)

        self.sr760_tab = SR760Tab(notebook)
        self.sr830_tab = SR830Tab(notebook)
        notebook.add(self.sr760_tab, text="SR760 FFT Interface")
        notebook.add(self.sr830_tab, text="SR830 Lock-In Interface")

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        for tab in (self.sr760_tab, self.sr830_tab):
            if tab.dev is not None:
                try:
                    tab._disconnect()
                except Exception:
                    pass
        self.destroy()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()