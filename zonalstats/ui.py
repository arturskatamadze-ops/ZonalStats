"""
Shared visual theme for the ZonalStats GUIs.

Centralises the ttk styling and the colour palette so the main window and the
Plot Designer look like one application. Deliberately light-weight (no Pillow /
rasterio imports) so importing it never slows app start-up or pulls in the
designer's heavier dependencies.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# Palette — an agronomy-leaning green accent on a clean light-grey UI.
ACCENT = "#2e7d32"
ACCENT_ACTIVE = "#27682b"
ACCENT_DISABLED = "#a8c6aa"
ACCENT_SOFT = "#e8f2e9"   # accent wash for selected / highlighted rows
INK = "#222831"
MUTED = "#6b7178"
BASE = "#f3f5f7"          # window / widget background
FIELD = "#ffffff"         # entry / list interiors
LINE = "#dce1e6"          # subtle borders
HEADER_FG = "#eaf4ec"     # secondary text on the accent header
CANVAS_BG = "#23262b"

# Status colours for inline validation ("this input is fine / missing / wrong").
OK = "#2a7a2a"
WARN = "#8a6100"
BAD = "#a11c1c"

UI_FONT = ("Segoe UI", 10)
MONO_FONT = ("Consolas", 9)

# Glyphs used by the inline status dots. Kept here so both windows agree.
GLYPH_OK = "✓"       # check mark
GLYPH_TODO = "○"     # hollow circle
GLYPH_BAD = "✕"      # cross


def apply_theme(root) -> ttk.Style:
    """Apply the shared theme to *root* and return the configured ttk.Style.

    ttk styles are interpreter-wide, so calling this once (from the main window)
    also styles any Toplevel such as the designer; calling it again is harmless.
    A single cohesive background colour is set on every widget class so there are
    no mismatched grey panels — a flat, modern look.
    """
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")

    try:
        root.configure(bg=BASE)
    except Exception:
        pass

    root.option_add("*Font", UI_FONT)
    # Combobox drop-down lists are classic Tk widgets, so they need option_add
    # rather than a ttk style to match the rest of the chrome.
    root.option_add("*TCombobox*Listbox.background", FIELD)
    root.option_add("*TCombobox*Listbox.foreground", INK)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    root.option_add("*TCombobox*Listbox.font", UI_FONT)

    style.configure(".", font=UI_FONT, background=BASE, foreground=INK)
    for cls in ("TFrame", "TLabel", "TLabelframe", "TCheckbutton",
                "TRadiobutton", "TNotebook", "TMenubutton", "TPanedwindow"):
        style.configure(cls, background=BASE)
    style.configure("TLabel", foreground=INK)
    style.map("TCheckbutton", background=[("active", BASE)])
    style.map("TRadiobutton", background=[("active", BASE)])

    # Tick boxes read better with an accent fill than clam's default grey.
    style.configure("TCheckbutton", focuscolor=BASE)
    style.map("TCheckbutton",
              indicatorcolor=[("selected", ACCENT), ("!selected", FIELD)],
              indicatorbackground=[("selected", ACCENT), ("!selected", FIELD)],
              foreground=[("disabled", MUTED)])
    style.configure("TRadiobutton", focuscolor=BASE)
    style.map("TRadiobutton",
              indicatorcolor=[("selected", ACCENT), ("!selected", FIELD)],
              foreground=[("disabled", MUTED)])

    style.configure("TButton", padding=(10, 6))
    style.map("TButton",
              background=[("active", "#e6eaee"), ("disabled", BASE)],
              foreground=[("disabled", "#a5abb2")])
    # Tight buttons for rows of small controls (the designer's rotation nudges).
    style.configure("Compact.TButton", padding=(2, 4))
    style.configure("TMenubutton", padding=(10, 6), relief="flat",
                    background=FIELD, arrowcolor=INK)
    style.map("TMenubutton", background=[("active", "#eef1f4")])

    # --- text inputs -------------------------------------------------------
    # clam draws entry borders from bordercolor/lightcolor/darkcolor, so all
    # three move together to get a crisp 1px box that turns green on focus.
    for cls in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(cls, fieldbackground=FIELD, background=FIELD,
                        foreground=INK, bordercolor=LINE, lightcolor=LINE,
                        darkcolor=LINE, insertcolor=INK, arrowcolor=INK,
                        padding=(6, 4), relief="flat")
        style.map(cls,
                  bordercolor=[("focus", ACCENT), ("invalid", BAD)],
                  lightcolor=[("focus", ACCENT), ("invalid", BAD)],
                  darkcolor=[("focus", ACCENT), ("invalid", BAD)],
                  fieldbackground=[("disabled", BASE), ("readonly", FIELD)],
                  foreground=[("disabled", MUTED)])
    # Path entries that point at something missing get a red box.
    style.configure("Invalid.TEntry", fieldbackground="#fff5f5",
                    bordercolor=BAD, lightcolor=BAD, darkcolor=BAD)

    style.configure("TLabelframe", padding=(10, 7), bordercolor=LINE,
                    relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", font=("Segoe UI", 10, "bold"),
                    foreground=INK, background=BASE)
    # Numbered step sections in the main window: accent title, same box.
    style.configure("Step.TLabelframe", bordercolor=LINE)
    style.configure("Step.TLabelframe.Label", font=("Segoe UI", 10, "bold"),
                    foreground=ACCENT, background=BASE)

    # Notebook with roomy, modern tabs.
    style.configure("TNotebook", borderwidth=0, tabmargins=(2, 4, 2, 0))
    style.configure("TNotebook.Tab", padding=(16, 8), background="#e4e8ec",
                    foreground=MUTED, borderwidth=0)
    style.map("TNotebook.Tab",
              background=[("selected", BASE), ("active", "#edf0f3")],
              foreground=[("selected", ACCENT)],
              expand=[("selected", (1, 1, 1, 0))])

    style.configure("Header.TLabel", font=("Segoe UI", 11, "bold"), foreground=INK)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Mono.TLabel", font=MONO_FONT, foreground=MUTED)
    style.configure("OK.TLabel", foreground=OK)
    style.configure("Warn.TLabel", foreground=WARN)
    style.configure("Bad.TLabel", foreground=BAD)
    style.configure("Hint.TLabel", foreground=MUTED, font=("Segoe UI", 9))

    # Data table (the designer's plot list) — flat, white rows on the grey chrome.
    style.configure("Treeview", background=FIELD, fieldbackground=FIELD,
                    foreground=INK, borderwidth=1, rowheight=22)
    style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"),
                    background="#e4e8ec", foreground=INK, relief="flat",
                    padding=(2, 4))
    style.map("Treeview.Heading", background=[("active", "#dae0e6")])
    style.map("Treeview",
              background=[("selected", ACCENT)], foreground=[("selected", "#ffffff")])

    # Sliders that pair with the numeric entry boxes in the designer.
    style.configure("Horizontal.TScale", background=BASE, troughcolor="#dfe4e9",
                    borderwidth=0)

    # Scrollbars: clam's default is a chunky 3-D affair; flatten it.
    for cls in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
        style.configure(cls, background="#cfd6dd", troughcolor=BASE,
                        bordercolor=BASE, arrowcolor=MUTED, borderwidth=0,
                        relief="flat")
        style.map(cls, background=[("active", "#b7c0c9")])

    # Progress bar in the accent colour so a run reads at a glance.
    style.configure("Accent.Horizontal.TProgressbar", background=ACCENT,
                    troughcolor="#dfe4e9", bordercolor=LINE,
                    lightcolor=ACCENT, darkcolor=ACCENT, borderwidth=0,
                    thickness=10)

    # Primary call-to-action buttons (Run extraction, Build grid, Export).
    style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"),
                    foreground="#ffffff", background=ACCENT,
                    padding=(14, 8), borderwidth=0)
    style.map("Accent.TButton",
              background=[("active", ACCENT_ACTIVE), ("disabled", ACCENT_DISABLED)],
              foreground=[("disabled", "#eef3ee")])
    # Quieter secondary action that still reads as a button next to the CTA.
    style.configure("Link.TButton", foreground=ACCENT, background=BASE,
                    borderwidth=0, padding=(6, 4))
    style.map("Link.TButton",
              background=[("active", ACCENT_SOFT)],
              foreground=[("disabled", MUTED)])
    return style


class StatusDot(ttk.Label):
    """A one-glyph inline indicator: done / still to do / wrong.

    Used beside each required input so the window shows at a glance what is
    still missing, instead of only saying so in an error box after Run.
    """

    def __init__(self, master, **kw):
        super().__init__(master, text=GLYPH_TODO, style="Muted.TLabel",
                         width=2, anchor="center", **kw)

    def set_state(self, state: str):
        """*state* is one of 'ok', 'todo', 'bad'."""
        if state == "ok":
            self.config(text=GLYPH_OK, style="OK.TLabel")
        elif state == "bad":
            self.config(text=GLYPH_BAD, style="Bad.TLabel")
        else:
            self.config(text=GLYPH_TODO, style="Muted.TLabel")


def accent_header(parent, title: str, subtitle: str = "", trailing: str = ""):
    """Build the app's accent title bar. Returns the frame.

    A plain ``tk.Frame`` rather than ttk because a solid background colour is
    the whole point and ttk frames fight it on some themes.
    """
    header = tk.Frame(parent, bg=ACCENT)
    header.pack(fill="x")
    tk.Label(header, text=title, bg=ACCENT, fg="#ffffff",
             font=("Segoe UI", 14, "bold")).pack(side="left", padx=(14, 8), pady=7)
    if subtitle:
        tk.Label(header, text=subtitle, bg=ACCENT, fg=HEADER_FG,
                 font=("Segoe UI", 9)).pack(side="left", pady=7)
    if trailing:
        tk.Label(header, text=trailing, bg=ACCENT, fg=HEADER_FG,
                 font=("Segoe UI", 9)).pack(side="right", padx=14, pady=7)
    return header
