"""The first-run window: the welcome screen (free edition) or activation (paid).

Tk is used rather than a web page because this runs *before* the app starts —
there is no server to serve a page from yet. It is in the standard library, so
it adds no dependency and both PyInstaller and py2app bundle it without help.

This module is intentionally thin. Every decision lives in `launcher.py`, which
is tested headlessly; what is here is layout and event plumbing, which a test
could only assert tautologically. Tk is imported inside the functions so that
importing this module on a headless build machine cannot fail.
"""

from __future__ import annotations

from .edition import FREE_EDITION
from .launcher import FirstRunInput

WINDOW_TITLE = "Your Pit Box — welcome" if FREE_EDITION else "Your Pit Box — activation"

_INTRO_PAID = (
    "Enter the activation code from your purchase email, and an OpenAI API "
    "key for the race engineer.\n\n"
    "The code activates once and is then tied to this computer. The API key "
    "is billed to your own OpenAI account and is stored only on this machine; "
    "you can change it later under Connection."
)

_INTRO_FREE = (
    "Your Pit Box is free. The race engineer's voice and reasoning run on an "
    "AI API key of your own, billed to your own account, never to me.\n\n"
    "Paste an OpenAI API key now, or skip and add one later under Connection "
    "— where you can also choose Claude, DeepSeek or Kimi for the reasoning. "
    "Telemetry, strategy maths and the dashboard all work without a key."
)


def prompt_first_run(message: str = "", free: bool = FREE_EDITION) -> FirstRunInput | None:
    """Show the first-run form. Returns None if the window was closed.

    Free edition: a welcome screen asking only for an (optional) API key;
    closing it means "skip", and the launcher starts the app regardless.
    Paid edition: the activation form, unchanged.
    """
    import tkinter as tk
    from tkinter import ttk

    result: dict[str, FirstRunInput | None] = {"value": None}

    root = tk.Tk()
    root.title(WINDOW_TITLE)
    root.resizable(False, False)

    frame = ttk.Frame(root, padding=20)
    frame.grid(sticky="nsew")

    heading = "Welcome to Your Pit Box" if free else "Activate Your Pit Box"
    ttk.Label(frame, text=heading, font=("Segoe UI", 15, "bold")).grid(
        column=0, row=0, columnspan=2, sticky="w"
    )
    ttk.Label(
        frame, text=_INTRO_FREE if free else _INTRO_PAID, wraplength=460, justify="left"
    ).grid(column=0, row=1, columnspan=2, sticky="w", pady=(8, 14))

    error_var = tk.StringVar(value=message)
    error_label = ttk.Label(
        frame, textvariable=error_var, wraplength=460, justify="left",
        foreground="#b3261e",
    )
    error_label.grid(column=0, row=2, columnspan=2, sticky="w", pady=(0, 8))

    row = 3
    code_var = tk.StringVar()
    code_entry: ttk.Entry | None = None
    if not free:
        ttk.Label(frame, text="Activation code").grid(column=0, row=row, sticky="w")
        code_entry = ttk.Entry(frame, textvariable=code_var, width=34)
        code_entry.grid(column=1, row=row, sticky="ew", pady=4)
        row += 1

    ttk.Label(frame, text="OpenAI API key" + (" (optional)" if free else "")).grid(
        column=0, row=row, sticky="w"
    )
    key_var = tk.StringVar()
    key_entry = ttk.Entry(frame, textvariable=key_var, width=34, show="•")
    key_entry.grid(column=1, row=row, sticky="ew", pady=4)
    row += 1

    show_var = tk.BooleanVar(value=False)

    def toggle_key() -> None:
        key_entry.configure(show="" if show_var.get() else "•")

    ttk.Checkbutton(
        frame, text="Show key", variable=show_var, command=toggle_key
    ).grid(column=1, row=row, sticky="w")
    row += 1

    def submit() -> None:
        code = code_var.get().strip()
        if not free and not code:
            error_var.set("Enter your activation code.")
            if code_entry is not None:
                code_entry.focus_set()
            return
        result["value"] = FirstRunInput(activation_code=code, api_key=key_var.get())
        root.destroy()

    def cancel() -> None:
        result["value"] = None
        root.destroy()

    buttons = ttk.Frame(frame)
    buttons.grid(column=0, row=row, columnspan=2, sticky="e", pady=(16, 0))
    ttk.Button(
        buttons, text="Skip for now" if free else "Quit", command=cancel
    ).grid(column=0, row=0, padx=(0, 8))
    ttk.Button(buttons, text="Continue" if free else "Activate", command=submit).grid(
        column=1, row=0
    )

    root.bind("<Return>", lambda _event: submit())
    root.bind("<Escape>", lambda _event: cancel())
    root.protocol("WM_DELETE_WINDOW", cancel)
    frame.columnconfigure(1, weight=1)
    (code_entry or key_entry).focus_set()

    root.eval("tk::PlaceWindow . center")
    root.mainloop()
    return result["value"]


def show_message(message: str, *, title: str = WINDOW_TITLE) -> None:
    """Report a blocking problem (tamper, or a failed key save)."""
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.withdraw()
    try:
        messagebox.showerror(title, message)
    finally:
        root.destroy()


__all__ = ["WINDOW_TITLE", "prompt_first_run", "show_message"]
