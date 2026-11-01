import os
import tkinter as tk


root = tk.Tk()
root.title("WindowTint Test Target")
root.attributes("-toolwindow", True)
width = int(os.environ.get("WINDOWTINT_TEST_WIDTH", "720"))
height = int(os.environ.get("WINDOWTINT_TEST_HEIGHT", "420"))
root.geometry(f"{width}x{height}+260+180")
root.configure(background="#f5f7f6")
tk.Label(
    root,
    text="WindowTint\nInjected backdrop target\n\nButtons and labels stay native",
    background="#ffffff",
    foreground="#23302d",
    font=("Microsoft YaHei UI", 16, "bold"),
    justify="center",
    padx=28,
    pady=22,
).place(relx=0.5, rely=0.54, anchor="center")
root.mainloop()
