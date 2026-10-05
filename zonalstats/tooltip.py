"""Small reusable Tkinter widgets: a hover tooltip and a scrollable frame."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class ToolTip:
    """Show *text* in a small popup when the mouse hovers over *widget*.

    Used so that hovering an index shows its formula and description.
    """

    def __init__(self, widget: tk.Widget, text_provider, delay: int = 350):
        self.widget = widget
        # text_provider may be a string or a zero-arg callable returning a string,
        # so the tooltip can reflect the current formula even if it changes.
        self.text_provider = text_provider
        self.delay = delay
        self._after_id = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _text(self) -> str:
        tp = self.text_provider
        return tp() if callable(tp) else str(tp)

    def _schedule(self, _event=None):
        self._cancel()
        self._after_id = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def _show(self):
        text = self._text()
        if not text or self._tip is not None:
            return
        x = self.widget.winfo_pointerx() + 14
        y = self.widget.winfo_pointery() + 18
        self._tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            tw, text=text, justify="left", background="#ffffe0",
            relief="solid", borderwidth=1, padx=6, pady=4, wraplength=360,
            font=("Segoe UI", 9),
        )
        label.pack()

    def _hide(self, _event=None):
        self._cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


class ScrollableFrame(ttk.Frame):
    """A vertically scrollable container. Add children to ``.body``."""

    def __init__(self, master, height: int = 260, **kw):
        super().__init__(master, **kw)
        self._canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0,
                                 height=height)
        vsb = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)

        self.body = ttk.Frame(self._canvas)
        self._win = self._canvas.create_window((0, 0), window=self.body, anchor="nw")

        self.body.bind("<Configure>", self._on_body_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        # Mouse wheel scrolling while hovering the list.
        self.body.bind("<Enter>", lambda e: self._bind_wheel(True))
        self.body.bind("<Leave>", lambda e: self._bind_wheel(False))

    def _on_body_configure(self, _e):
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, e):
        self._canvas.itemconfigure(self._win, width=e.width)

    def _bind_wheel(self, on: bool):
        if on:
            self._canvas.bind_all("<MouseWheel>", self._on_wheel)
        else:
            self._canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event):
        self._canvas.yview_scroll(int(-event.delta / 120), "units")

    def clear(self):
        for child in self.body.winfo_children():
            child.destroy()
