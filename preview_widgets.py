# preview_widgets.py  (temporary — deleted in Task 9)
import sys
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget, QHBoxLayout
from app.ui.widgets.nt_panel import NtPanel
from app.ui.widgets.nt_button import NtButton
from app.ui.widgets.nt_status_dot import NtStatusDot
from app.ui.widgets.log_panel import LogPanel

app = QApplication(sys.argv)
win = QWidget()
win.resize(260, 400)
win.setWindowTitle("Widget Preview")

panel = NtPanel(win)
panel.setGeometry(0, 0, 260, 400)

layout = QVBoxLayout(panel)

layout.addWidget(NtButton("START BOT"))
b = NtButton("ACTIVE BUTTON")
b.set_active(True)
layout.addWidget(b)

dot_row = QWidget()
row = QHBoxLayout(dot_row)
for state in ["offline", "running", "error"]:
    dot = NtStatusDot()
    if state == "running": dot.set_running()
    elif state == "error": dot.set_error()
    row.addWidget(dot)
layout.addWidget(dot_row)

log = LogPanel()
log.add_log("bot started", "success")
log.add_log("pressed D")
log.add_log("game window not found", "error")
layout.addWidget(log)

win.show()
sys.exit(app.exec())
