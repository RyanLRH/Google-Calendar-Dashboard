"""

A slide-out desktop dashboard for Google Calendar + Google Tasks.

- Lives on the right edge of the screen, above the wallpaper, below nothing.
- Click the arrow to slide it off to the right; the arrow flips to point left.
- Auto-refreshes on a timer, and after every add/complete.
- Calendar button opens a month view, which can also hand off to the browser.

"""

import datetime as dt
import json
import sys
import traceback
import webbrowser
from pathlib import Path

from PyQt6.QtCore import (QDate, QEasingCurve, QPoint, QPointF, QPropertyAnimation,
                          QRectF, QSize, Qt, QThread, QTime, QTimer, pyqtSignal)
from PyQt6.QtGui import (QAction, QBrush, QColor, QFont, QIcon, QPainter, QPen,
                         QPixmap, QPolygonF, QRegion, QTextCharFormat)
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import (QApplication, QCalendarWidget, QCheckBox, QComboBox,
                             QDateEdit, QDialog, QDialogButtonBox, QFormLayout,
                             QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu,
                             QMessageBox, QPlainTextEdit, QPushButton,
                             QScrollArea, QSizePolicy, QSpinBox, QSystemTrayIcon,
                             QTimeEdit, QVBoxLayout, QWidget)

from google_backend import AuthError, GoogleBackend

APP_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = APP_DIR / "settings.json"
LOG_FILE = APP_DIR / "dashboard.log"
INSTANCE_KEY = "ryan-desktop-dashboard-v1"

# if settings.json doesn't work then default will take over to make sure it will work
DEFAULTS = {
    "panel_width": 360,
    "panel_height": 620,
    "handle_width": 13,
    "handle_height": 68,
    "screen_margin": 8,
    "y_offset": 0,              # nudged when you drag the header up/down
    "refresh_minutes": 10,
    "days_ahead": 14,
    "max_events": 25,
    "max_tasks": 25,
    "start_hidden": False,
    "hover_peek": False,        # slide out just by hovering the handle
    "opacity": 0.97,
    "always_on_top": True,
    "theme": {
        "bg": "#171a21",
        "bg2": "#1e222b",
        "border": "#2c313d",
        "text": "#e6e9ef",
        "muted": "#8b93a7",
        "accent": "#5b8dee",
        "accent2": "#3ddc97",
        "danger": "#ef5b6b",
    },
}


def log(msg):
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except Exception:
        pass


def load_settings():
    s = json.loads(json.dumps(DEFAULTS))
    if SETTINGS_FILE.exists():
        try:
            user = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            theme = s["theme"]
            theme.update(user.pop("theme", {}) or {})
            s.update(user)
            s["theme"] = theme
        except Exception as e:
            log(f"settings.json unreadable ({e}), using defaults")
    else:
        SETTINGS_FILE.write_text(json.dumps(DEFAULTS, indent=2), encoding="utf-8")
    return s


def save_settings(s):
    try:
        SETTINGS_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")
    except Exception as e:
        log(f"could not save settings: {e}")


S = load_settings()
T = S["theme"]


# background worker: keeps every network call off the UI thread
class Worker(QThread):
    ok = pyqtSignal(object)
    err = pyqtSignal(str)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self._fn, self._args, self._kwargs = fn, args, kwargs

    def run(self):
        try:
            self.ok.emit(self._fn(*self._args, **self._kwargs))
        except Exception as e:
            log("worker failed:\n" + traceback.format_exc())
            self.err.emit(str(e))


# small helpers
def human_day(d: dt.date) -> str:
    today = dt.date.today()
    delta = (d - today).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Tomorrow"
    if delta == -1:
        return "Yesterday"
    if 0 < delta < 7:
        return d.strftime("%A")
    return d.strftime("%a %d %b")


def countdown(start: dt.datetime) -> str:
    now = dt.datetime.now().astimezone()
    diff = (start - now).total_seconds()
    if diff < -60:
        return "now"
    mins = int(diff // 60)
    if mins < 60:
        return f"in {max(mins, 0)}m"
    hours = mins // 60
    if hours < 24:
        return f"in {hours}h {mins % 60:02d}m"
    return f"in {hours // 24}d"


def make_icon() -> QIcon:
    ico = APP_DIR / "icon.ico"
    if ico.exists():
        icon = QIcon(str(ico))
        if not icon.isNull():
            return icon

    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QBrush(QColor(T["bg"])))
    p.setPen(QPen(QColor(T["border"]), 1))
    p.drawRoundedRect(2, 2, 60, 60, 14, 14)
    p.setPen(Qt.PenStyle.NoPen)
    for x, y, w, h, colour in [
        (12, 15, 4, 34, T["border"]),
        (22, 15, 30, 9, T["accent"]),
        (22, 28, 20, 9, T["accent2"]),
        (22, 40, 26, 9, "#264e7d"),
    ]:
        p.setBrush(QBrush(QColor(colour)))
        p.drawRoundedRect(x, y, w, h, h // 2, h // 2)
    p.end()
    return QIcon(pm)


# rows
class EventRow(QFrame):
    def __init__(self, ev, parent=None):
        super().__init__(parent)
        self.ev = ev
        self.setObjectName("row")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(9)

        bar = QFrame()
        bar.setFixedWidth(3)
        bar.setStyleSheet(f"background:{ev['color']}; border-radius:1px;")
        lay.addWidget(bar)

        mid = QVBoxLayout()
        mid.setSpacing(1)
        title = QLabel(ev["title"])
        title.setObjectName("rowTitle")
        title.setWordWrap(True)
        mid.addWidget(title)

        if ev["all_day"]:
            when = "All day"
        else:
            when = f"{ev['start']:%H:%M} – {ev['end']:%H:%M}"
        if ev["location"]:
            when += f"  ·  {ev['location'][:28]}"
        sub = QLabel(when)
        sub.setObjectName("rowSub")
        mid.addWidget(sub)
        lay.addLayout(mid, 1)

        if not ev["all_day"] and ev["start"] > dt.datetime.now().astimezone():
            cd = QLabel(countdown(ev["start"]))
            cd.setObjectName("badge")
            cd.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            lay.addWidget(cd)

        self.setToolTip(f"{ev['title']}\n{ev['calendar']}\nClick to open in Google Calendar")

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.ev.get("link"):
            webbrowser.open(self.ev["link"])


class TaskRow(QFrame):
    toggled = pyqtSignal(dict)
    removed = pyqtSignal(dict)

    def __init__(self, task, parent=None):
        super().__init__(parent)
        self.task = task
        self.setObjectName("row")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(9)

        self.box = QCheckBox()
        self.box.setChecked(task["completed"])
        self.box.clicked.connect(lambda: self.toggled.emit(self.task))
        lay.addWidget(self.box)

        mid = QVBoxLayout()
        mid.setSpacing(1)
        title = QLabel(task["title"])
        title.setObjectName("rowTitle")
        title.setWordWrap(True)
        mid.addWidget(title)

        bits = []
        if task["due"]:
            d = task["due"].date()
            bits.append(("Overdue · " if d < dt.date.today() else "") + human_day(d))
        if task["list"]:
            bits.append(task["list"])
        if bits:
            sub = QLabel("  ·  ".join(bits))
            overdue = task["due"] and task["due"].date() < dt.date.today()
            sub.setObjectName("rowDue" if overdue else "rowSub")
            mid.addWidget(sub)
        lay.addLayout(mid, 1)

        rm = QPushButton("✕")
        rm.setObjectName("iconBtn")
        rm.setFixedSize(20, 20)
        rm.setToolTip("Delete task")
        rm.clicked.connect(lambda: self.removed.emit(self.task))
        lay.addWidget(rm)

        if task["notes"]:
            self.setToolTip(task["notes"])


class HandleButton(QPushButton):
    """Slim grip on the left edge. Draws its own chevron so no font can mangle it."""

    def __init__(self, on_hover=None):
        super().__init__()
        self.pointing = "right"      # right = click to hide
        self._hover = False
        self._on_hover = on_hover
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Hide / show dashboard")

    def set_pointing(self, direction):
        self.pointing = direction
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()
        if self._on_hover:
            self._on_hover()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # overshoot to the right so only the left corners look rounded
        p.setBrush(QBrush(QColor(T["accent"] if self._hover else T["bg2"])))
        p.setPen(QPen(QColor(T["border"]), 1))
        p.drawRoundedRect(QRectF(0.5, 0.5, w + 8, h - 1), 6, 6)

        p.setPen(QPen(QColor("#ffffff" if self._hover else T["muted"]), 1.5,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        cx, cy, dx, dy = w / 2, h / 2, 2.0, 3.2
        if self.pointing == "right":
            pts = [QPointF(cx - dx, cy - dy), QPointF(cx + dx, cy), QPointF(cx - dx, cy + dy)]
        else:
            pts = [QPointF(cx + dx, cy - dy), QPointF(cx - dx, cy), QPointF(cx + dx, cy + dy)]
        p.drawPolyline(QPolygonF(pts))


class SectionHeader(QWidget):
    def __init__(self, text, action_text=None, on_action=None):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 2)
        lbl = QLabel(text.upper())
        lbl.setObjectName("section")
        lay.addWidget(lbl)
        lay.addStretch(1)
        if action_text:
            b = QPushButton(action_text)
            b.setObjectName("linkBtn")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            if on_action:
                b.clicked.connect(on_action)
            lay.addWidget(b)


# dialogs
class NewEventDialog(QDialog):
    def __init__(self, calendars, parent=None, default_date=None):
        super().__init__(parent)
        self.setWindowTitle("New event")
        self.setMinimumWidth(340)
        self.setStyleSheet(dialog_qss())

        self.title = QLineEdit()
        self.title.setPlaceholderText("What's happening?")

        self.calendar = QComboBox()
        for c in calendars:
            self.calendar.addItem(c["name"], c["id"])

        now = dt.datetime.now()
        start_default = (now + dt.timedelta(minutes=30)).replace(second=0, microsecond=0)
        start_default -= dt.timedelta(minutes=start_default.minute % 15)

        self.date = QDateEdit(QDate(default_date or start_default.date()))
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("ddd dd MMM yyyy")

        self.start = QTimeEdit(QTime(start_default.hour, start_default.minute))
        self.start.setDisplayFormat("HH:mm")
        self.end = QTimeEdit(QTime((start_default.hour + 1) % 24, start_default.minute))
        self.end.setDisplayFormat("HH:mm")

        self.all_day = QCheckBox("All day")
        self.all_day.toggled.connect(self._toggle_all_day)

        self.location = QLineEdit()
        self.location.setPlaceholderText("Optional")

        self.reminder = QComboBox()
        for label, mins in [("Calendar default", None), ("At start", 0), ("5 min before", 5),
                            ("10 min before", 10), ("30 min before", 30), ("1 hour before", 60),
                            ("1 day before", 1440)]:
            self.reminder.addItem(label, mins)

        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Optional")
        self.notes.setFixedHeight(56)

        times = QHBoxLayout()
        times.addWidget(self.start)
        times.addWidget(QLabel("to"))
        times.addWidget(self.end)
        times.addWidget(self.all_day)
        wrap = QWidget()
        wrap.setLayout(times)

        form = QFormLayout()
        form.setSpacing(8)
        form.addRow("Title", self.title)
        form.addRow("Calendar", self.calendar)
        form.addRow("Date", self.date)
        form.addRow("Time", wrap)
        form.addRow("Reminder", self.reminder)
        form.addRow("Location", self.location)
        form.addRow("Notes", self.notes)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(btns)
        self.title.setFocus()

    def _toggle_all_day(self, on):
        self.start.setEnabled(not on)
        self.end.setEnabled(not on)

    def _accept(self):
        if not self.title.text().strip():
            QMessageBox.warning(self, "Missing title", "Give the event a title.")
            return
        if not self.all_day.isChecked():
            s, e = self.start.time(), self.end.time()
            if e <= s:
                QMessageBox.warning(self, "Check the times", "End time must be after start time.")
                return
        self.accept()

    def values(self):
        d = self.date.date().toPyDate()
        all_day = self.all_day.isChecked()
        if all_day:
            start = dt.datetime(d.year, d.month, d.day)
            end = start
        else:
            st, et = self.start.time(), self.end.time()
            start = dt.datetime(d.year, d.month, d.day, st.hour(), st.minute()).astimezone()
            end = dt.datetime(d.year, d.month, d.day, et.hour(), et.minute()).astimezone()
        return {
            "calendar_id": self.calendar.currentData(),
            "title": self.title.text().strip(),
            "start": start,
            "end": end,
            "all_day": all_day,
            "location": self.location.text().strip(),
            "description": self.notes.toPlainText().strip(),
            "reminder_minutes": self.reminder.currentData(),
        }


class NewTaskDialog(QDialog):
    def __init__(self, tasklists, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New task")
        self.setMinimumWidth(320)
        self.setStyleSheet(dialog_qss())

        self.title = QLineEdit()
        self.title.setPlaceholderText("What needs doing?")

        self.list = QComboBox()
        for tl in tasklists:
            self.list.addItem(tl["name"], tl["id"])

        self.has_due = QCheckBox("Due date")
        self.has_due.setChecked(True)
        self.due = QDateEdit(QDate.currentDate())
        self.due.setCalendarPopup(True)
        self.due.setDisplayFormat("ddd dd MMM yyyy")
        self.has_due.toggled.connect(self.due.setEnabled)

        self.notes = QPlainTextEdit()
        self.notes.setFixedHeight(56)

        row = QHBoxLayout()
        row.addWidget(self.has_due)
        row.addWidget(self.due, 1)
        wrap = QWidget()
        wrap.setLayout(row)

        form = QFormLayout()
        form.setSpacing(8)
        form.addRow("Task", self.title)
        form.addRow("List", self.list)
        form.addRow("Due", wrap)
        form.addRow("Notes", self.notes)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(btns)
        self.title.setFocus()

    def _accept(self):
        if not self.title.text().strip():
            QMessageBox.warning(self, "Missing title", "Give the task a title.")
            return
        self.accept()

    def values(self):
        return {
            "tasklist_id": self.list.currentData(),
            "title": self.title.text().strip(),
            "due": self.due.date().toPyDate() if self.has_due.isChecked() else None,
            "notes": self.notes.toPlainText().strip(),
        }


# month calendar
class DottedCalendar(QCalendarWidget):
    """Month grid that paints a coloured dot under any day that has events."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.day_colors = {}   # date -> [hex, hex, ...]
        self.setGridVisible(False)
        self.setMinimumHeight(280)
        self.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.setHorizontalHeaderFormat(QCalendarWidget.HorizontalHeaderFormat.SingleLetterDayNames)
        # kill Qt's default red weekends
        plain = QTextCharFormat()
        plain.setForeground(QBrush(QColor(T["text"])))
        for day in (Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday):
            self.setWeekdayTextFormat(day, plain)
        head = QTextCharFormat()
        head.setForeground(QBrush(QColor(T["muted"])))
        self.setHeaderTextFormat(head)

    def set_events(self, events):
        self.day_colors = {}
        for e in events:
            d = e["start"].date()
            span_end = e["end"].date()
            for i in range((span_end - d).days + 1):
                day = d + dt.timedelta(days=i)
                self.day_colors.setdefault(day, [])
                if e["color"] not in self.day_colors[day] and len(self.day_colors[day]) < 4:
                    self.day_colors[day].append(e["color"])
        self.updateCells()

    def paintCell(self, painter, rect, date):
        super().paintCell(painter, rect, date)
        colors = self.day_colors.get(date.toPyDate())
        if not colors:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        d = 4
        total = len(colors) * d + (len(colors) - 1) * 2
        x = rect.center().x() - total // 2
        y = rect.bottom() - d - 2
        for c in colors:
            painter.setBrush(QBrush(QColor(c)))
            painter.drawEllipse(x, y, d, d)
            x += d + 2
        painter.restore()


class CalendarWindow(QDialog):
    def __init__(self, dash):
        super().__init__(None)
        self.dash = dash
        self.setWindowTitle("Calendar")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, S["always_on_top"])
        self.resize(430, 560)
        self.setStyleSheet(dialog_qss())
        self._worker = None

        self.cal = DottedCalendar()
        self.cal.selectionChanged.connect(self.show_day)
        self.cal.currentPageChanged.connect(lambda *_: self.reload())

        self.day_label = QLabel()
        self.day_label.setObjectName("section")

        self.day_list = QVBoxLayout()
        self.day_list.setSpacing(4)
        holder = QWidget()
        holder.setLayout(self.day_list)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(holder)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        add = QPushButton("＋  Event on this day")
        add.setObjectName("primaryBtn")
        add.clicked.connect(lambda: dash.new_event(self.cal.selectedDate().toPyDate()))

        gcal = QPushButton("Open Google Calendar")
        gcal.setObjectName("ghostBtn")
        gcal.clicked.connect(lambda: webbrowser.open(
            "https://calendar.google.com/calendar/r/day/"
            + self.cal.selectedDate().toString("yyyy/M/d")))

        btns = QHBoxLayout()
        btns.addWidget(add, 1)
        btns.addWidget(gcal, 1)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.addWidget(self.cal)
        root.addWidget(self.day_label)
        root.addWidget(scroll, 1)
        root.addLayout(btns)

        self.events = []
        self.reload()

    def reload(self):
        d = self.cal.selectedDate().toPyDate()
        first = dt.datetime(d.year, d.month, 1).astimezone()
        last = (first + dt.timedelta(days=45)).replace(day=1)
        self._worker = Worker(self.dash.g.list_events, first - dt.timedelta(days=7), last)
        self._worker.ok.connect(self._loaded)
        self._worker.err.connect(lambda e: log(f"calendar load: {e}"))
        self._worker.start()

    def _loaded(self, events):
        self.events = events
        self.cal.set_events(events)
        self.show_day()

    def show_day(self):
        d = self.cal.selectedDate().toPyDate()
        self.day_label.setText(human_day(d) + d.strftime(" · %d %B"))
        while self.day_list.count():
            item = self.day_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        todays = [e for e in self.events if e["start"].date() <= d <= e["end"].date()]
        if not todays:
            empty = QLabel("Nothing scheduled.")
            empty.setObjectName("rowSub")
            self.day_list.addWidget(empty)
        for e in todays:
            self.day_list.addWidget(EventRow(e))
        self.day_list.addStretch(1)


# stylesheets
def dialog_qss():
    return f"""
    QDialog {{ background:{T['bg']}; color:{T['text']}; }}
    QLabel {{ color:{T['text']}; font-size:12px; }}
    QLabel#section {{ color:{T['muted']}; font-size:11px; font-weight:700;
                      letter-spacing:1px; padding:6px 2px; }}
    QLabel#rowTitle {{ font-size:12px; font-weight:600; }}
    QLabel#rowSub {{ color:{T['muted']}; font-size:11px; }}
    QLabel#rowDue {{ color:{T['danger']}; font-size:11px; font-weight:600; }}
    QLabel#badge {{ color:{T['accent']}; font-size:11px; font-weight:600; }}
    QFrame#row {{ background:{T['bg2']}; border:1px solid {T['border']}; border-radius:7px; }}
    QFrame#row:hover {{ border:1px solid {T['accent']}; }}
    QLineEdit, QPlainTextEdit, QComboBox, QDateEdit, QTimeEdit, QSpinBox {{
        background:{T['bg2']}; color:{T['text']}; border:1px solid {T['border']};
        border-radius:6px; padding:5px 7px; font-size:12px; }}
    QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
    QDateEdit:focus, QTimeEdit:focus {{ border:1px solid {T['accent']}; }}
    QComboBox QAbstractItemView {{ background:{T['bg2']}; color:{T['text']};
        selection-background-color:{T['accent']}; border:1px solid {T['border']}; }}
    QPushButton {{ background:{T['bg2']}; color:{T['text']}; border:1px solid {T['border']};
        border-radius:6px; padding:6px 12px; font-size:12px; }}
    QPushButton:hover {{ border:1px solid {T['accent']}; }}
    QPushButton#primaryBtn {{ background:{T['accent']}; border:none; color:#fff; font-weight:600; }}
    QPushButton#primaryBtn:hover {{ background:#6f9cf2; }}
    QPushButton#ghostBtn {{ background:transparent; color:{T['muted']}; }}
    QPushButton#ghostBtn:hover {{ color:{T['text']}; }}
    QPushButton#iconBtn {{ background:transparent; border:none; color:{T['muted']};
        font-size:12px; padding:0; }}
    QPushButton#iconBtn:hover {{ color:{T['danger']}; }}
    QCheckBox {{ color:{T['text']}; font-size:12px; spacing:6px; }}
    QCheckBox::indicator {{ width:15px; height:15px; border-radius:4px;
        border:1px solid {T['border']}; background:{T['bg2']}; }}
    QCheckBox::indicator:checked {{ background:{T['accent2']}; border:1px solid {T['accent2']}; }}
    QCalendarWidget QWidget {{ alternate-background-color:{T['bg']}; }}
    QCalendarWidget QAbstractItemView:enabled {{
        background:{T['bg']}; color:{T['text']}; selection-background-color:{T['accent']};
        selection-color:#fff; outline:none; font-size:12px; }}
    QCalendarWidget QAbstractItemView:disabled {{ color:{T['border']}; }}
    QCalendarWidget QToolButton {{ background:transparent; color:{T['text']};
        font-size:13px; font-weight:600; padding:4px 8px; border-radius:5px; }}
    QCalendarWidget QToolButton:hover {{ background:{T['bg2']}; }}
    QCalendarWidget QMenu {{ background:{T['bg2']}; color:{T['text']}; }}
    QCalendarWidget QSpinBox {{ background:{T['bg2']}; color:{T['text']}; }}
    QCalendarWidget #qt_calendar_navigationbar {{ background:{T['bg']};
        border-bottom:1px solid {T['border']}; }}
    QScrollArea {{ background:transparent; border:none; }}
    QScrollArea > QWidget > QWidget {{ background:transparent; }}
    QScrollArea > QWidget > QScrollBar {{ background:transparent; }}
    QScrollBar:vertical {{ background:transparent; width:7px; margin:2px; }}
    QScrollBar::handle:vertical {{ background:{T['border']}; border-radius:3px; min-height:24px; }}
    QScrollBar::handle:vertical:hover {{ background:{T['muted']}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height:0; }}
    QToolTip {{ background:{T['bg2']}; color:{T['text']};
        border:1px solid {T['border']}; padding:4px; }}
    """


def dock_qss():
    return dialog_qss() + f"""
    QFrame#panel {{ background:{T['bg']}; border:1px solid {T['border']};
        border-top-left-radius:12px; border-bottom-left-radius:12px;
        border-top-right-radius:12px; border-bottom-right-radius:12px; }}
    QLabel#title {{ font-size:14px; font-weight:700; }}
    QLabel#clock {{ color:{T['muted']}; font-size:11px; }}
    QLabel#status {{ color:{T['muted']}; font-size:10px; }}
    QPushButton#linkBtn {{ background:transparent; border:none; color:{T['accent']};
        font-size:11px; font-weight:600; padding:0 2px; }}
    QPushButton#linkBtn:hover {{ color:{T['accent2']}; }}
    QPushButton#toolBtn {{ background:transparent; border:none; color:{T['muted']};
        font-size:14px; padding:2px 5px; border-radius:5px; }}
    QPushButton#toolBtn:hover {{ background:{T['bg2']}; color:{T['text']}; }}
    QMenu {{ background:{T['bg2']}; color:{T['text']};
        border:1px solid {T['border']}; padding:4px; }}
    QMenu::item {{ padding:5px 18px; border-radius:4px; }}
    QMenu::item:selected {{ background:{T['accent']}; color:#fff; }}
    """


# the dock itself
class Dashboard(QWidget):
    def __init__(self):
        super().__init__()
        self.g = GoogleBackend()
        self.hidden_state = bool(S["start_hidden"])
        self.calendars, self.tasklists = [], []
        self.events, self.tasks = [], []
        self._workers = []
        self._drag_origin = None
        self.cal_window = None

        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if S["always_on_top"]:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowOpacity(S["opacity"])
        self.setStyleSheet(dock_qss())

        self._build_ui()
        self._place()

        self.anim = QPropertyAnimation(self, b"pos")
        self.anim.setDuration(260)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_timer.start(max(1, int(S["refresh_minutes"])) * 60_000)

        self.tick_timer = QTimer(self)   # keeps the clock and "in 12m" badges live
        self.tick_timer.timeout.connect(self._tick)
        self.tick_timer.start(30_000)

        self._peek_timer = QTimer(self)
        self._peek_timer.setSingleShot(True)
        self._peek_timer.timeout.connect(lambda: self.set_hidden(True))

        QTimer.singleShot(300, self.first_connect)

    # ------------------------------------------------------------------ ui
    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.handle = HandleButton(
            on_hover=(lambda: self.set_hidden(False)) if S["hover_peek"] else None)
        self.handle.setFixedSize(S["handle_width"], S["handle_height"])
        self.handle.clicked.connect(self.toggle)

        grip = QVBoxLayout()          # keeps the grip short and vertically centred
        grip.setContentsMargins(0, 0, 0, 0)
        grip.addStretch(1)
        grip.addWidget(self.handle)
        grip.addStretch(1)
        root.addLayout(grip)

        self.panel = QFrame()
        self.panel.setObjectName("panel")
        self.panel.setFixedWidth(S["panel_width"])
        root.addWidget(self.panel)

        p = QVBoxLayout(self.panel)
        p.setContentsMargins(0, 0, 0, 0)
        p.setSpacing(0)

        # header (also the drag grip)
        self.header = QWidget()
        self.header.setCursor(Qt.CursorShape.SizeVerCursor)
        h = QHBoxLayout(self.header)
        h.setContentsMargins(12, 10, 8, 6)
        h.setSpacing(4)

        mark = QLabel()
        mark.setPixmap(make_icon().pixmap(22, 22))
        mark.setContentsMargins(0, 0, 7, 0)
        h.addWidget(mark)

        titles = QVBoxLayout()
        titles.setSpacing(0)
        t = QLabel("Dashboard")
        t.setObjectName("title")
        self.clock = QLabel()
        self.clock.setObjectName("clock")
        titles.addWidget(t)
        titles.addWidget(self.clock)
        h.addLayout(titles, 1)

        for text, tip, slot in [
            ("＋", "Add event or task", self.add_menu),
            ("▦", "Open month calendar", self.open_calendar),
            ("⟳", "Refresh now", self.refresh),
            ("⋯", "More", self.more_menu),
        ]:
            b = QPushButton(text)
            b.setObjectName("toolBtn")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            h.addWidget(b)
        p.addWidget(self.header)

        # scrolling body
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 8)
        self.body.setSpacing(5)
        holder = QWidget()
        holder.setLayout(self.body)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(holder)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        p.addWidget(self.scroll, 1)

        self.status = QLabel("Starting…")
        self.status.setObjectName("status")
        self.status.setContentsMargins(12, 0, 12, 8)
        p.addWidget(self.status)

        self._tick()

    def _place(self):
        geo = QApplication.primaryScreen().availableGeometry()
        m = S["screen_margin"]
        total_w = S["handle_width"] + S["panel_width"]
        h = min(S["panel_height"], geo.height() - 2 * m)
        self.resize(total_w, h)
        self.x_shown = geo.x() + geo.width() - total_w - m
        self.x_hidden = geo.x() + geo.width() - S["handle_width"] - m
        self.y_pos = geo.y() + geo.height() - h - m + int(S["y_offset"])
        self.y_pos = max(geo.y() + m, min(self.y_pos, geo.y() + geo.height() - h - m))
        self.move(self.x_hidden if self.hidden_state else self.x_shown, self.y_pos)
        self.handle.set_pointing("left" if self.hidden_state else "right")
        QTimer.singleShot(0, self._apply_mask)

    def _apply_mask(self):
        """Only the grip and the panel should catch clicks — the rest is desktop."""
        region = QRegion(self.panel.geometry())
        region = region.united(QRegion(self.handle.geometry()))
        self.setMask(region)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply_mask()

    def toggle(self):
        self.set_hidden(not self.hidden_state)

    def set_hidden(self, hide: bool):
        if hide == self.hidden_state:
            return
        self.hidden_state = hide
        self.anim.stop()
        self.anim.setStartValue(self.pos())
        self.anim.setEndValue(QPoint(self.x_hidden if hide else self.x_shown, self.y_pos))
        self.anim.start()
        self.handle.set_pointing("left" if hide else "right")
        if not hide:
            self.raise_()

    def leaveEvent(self, e):
        if S["hover_peek"] and not self.hidden_state:
            self._peek_timer.start(1200)
        super().leaveEvent(e)

    def enterEvent(self, e):
        self._peek_timer.stop()
        super().enterEvent(e)

    # drag the header to slide the dock up/down the right edge
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and \
                self.header.geometry().translated(self.panel.pos()).contains(e.position().toPoint()):
            self._drag_origin = e.globalPosition().toPoint().y() - self.y_pos
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_origin is not None:
            geo = QApplication.primaryScreen().availableGeometry()
            new_y = e.globalPosition().toPoint().y() - self._drag_origin
            new_y = max(geo.y(), min(new_y, geo.y() + geo.height() - self.height()))
            self.y_pos = new_y
            self.move(self.x(), new_y)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._drag_origin is not None:
            geo = QApplication.primaryScreen().availableGeometry()
            base = geo.y() + geo.height() - self.height() - S["screen_margin"]
            S["y_offset"] = self.y_pos - base
            save_settings(S)
            self._drag_origin = None
        super().mouseReleaseEvent(e)

    def _run(self, fn, on_ok, *args, **kwargs):
        w = Worker(fn, *args, **kwargs)
        w.ok.connect(on_ok)
        w.err.connect(self.on_error)
        w.finished.connect(lambda: self._workers.remove(w) if w in self._workers else None)
        self._workers.append(w)
        w.start()
        return w

    def first_connect(self):
        self.status.setText("Connecting to Google…")
        self._run(self._connect_job, self._connected)

    def _connect_job(self):
        self.g.connect()
        return {
            "calendars": self.g.list_calendars(writable_only=True),
            "tasklists": self.g.list_tasklists(),
        }

    def _connected(self, data):
        self.calendars = data["calendars"]
        self.tasklists = data["tasklists"]
        self.refresh()

    def refresh(self):
        if self.g.cal is None:
            return
        self.status.setText("Refreshing…")
        self._run(self._fetch_job, self._render)

    def _fetch_job(self):
        return {
            "events": self.g.list_events(days_ahead=S["days_ahead"]),
            "tasks": self.g.list_tasks(),
        }

    def on_error(self, msg):
        self.status.setText("⚠ " + msg[:90])
        log("error: " + msg)

    def _clear_body(self):
        while self.body.count():
            item = self.body.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _render(self, data):
        self.events = data["events"][: S["max_events"]]
        self.tasks = [t for t in data["tasks"] if not t["completed"]][: S["max_tasks"]]
        self._clear_body()

        self.body.addWidget(SectionHeader("Upcoming", "＋ event",
                                          lambda: self.new_event()))
        if not self.events:
            self.body.addWidget(self._empty("Clear skies for the next "
                                            f"{S['days_ahead']} days."))
        else:
            current_day = None
            for ev in self.events:
                d = ev["start"].date()
                if d != current_day:
                    current_day = d
                    lbl = QLabel(human_day(d))
                    lbl.setObjectName("section")
                    lbl.setContentsMargins(12, 6, 12, 0)
                    self.body.addWidget(lbl)
                row = EventRow(ev)
                row.setContentsMargins(0, 0, 0, 0)
                self.body.addWidget(self._padded(row))

        self.body.addWidget(SectionHeader("Tasks", "＋ task", self.new_task))
        if not self.tasks:
            self.body.addWidget(self._empty("No open tasks. Nice."))
        else:
            for t in self.tasks:
                row = TaskRow(t)
                row.toggled.connect(self.toggle_task)
                row.removed.connect(self.delete_task)
                self.body.addWidget(self._padded(row))

        self.body.addStretch(1)
        self.status.setText(
            f"{len(self.events)} events · {len(self.tasks)} tasks · "
            f"updated {dt.datetime.now():%H:%M}")

    def _padded(self, w):
        wrap = QWidget()
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.addWidget(w)
        return wrap

    def _empty(self, text):
        lbl = QLabel(text)
        lbl.setObjectName("rowSub")
        lbl.setContentsMargins(14, 4, 14, 8)
        lbl.setWordWrap(True)
        return lbl

    def _tick(self):
        now = dt.datetime.now()
        self.clock.setText(now.strftime("%A, %d %B · %H:%M"))
        if self.events:
            for i in range(self.body.count()):
                w = self.body.itemAt(i).widget()
                if w:
                    for row in w.findChildren(EventRow):
                        for lbl in row.findChildren(QLabel):
                            if lbl.objectName() == "badge":
                                lbl.setText(countdown(row.ev["start"]))

    def add_menu(self):
        m = QMenu(self)
        m.addAction("New event…", lambda: self.new_event())
        m.addAction("New task…", self.new_task)
        m.exec(self.cursor().pos())

    def more_menu(self):
        m = QMenu(self)
        m.addAction("Refresh now", self.refresh)
        m.addAction("Open Google Calendar",
                    lambda: webbrowser.open("https://calendar.google.com"))
        m.addAction("Open Google Tasks",
                    lambda: webbrowser.open("https://tasks.google.com"))
        m.addSeparator()
        m.addAction("Edit settings.json",
                    lambda: webbrowser.open(SETTINGS_FILE.as_uri()))
        m.addAction("Reload settings", self.reload_settings)
        m.addSeparator()
        m.addAction("Quit", QApplication.quit)
        m.exec(self.cursor().pos())

    def reload_settings(self):
        global S, T
        S = load_settings()
        T = S["theme"]
        self.setStyleSheet(dock_qss())
        self.setWindowOpacity(S["opacity"])
        self.panel.setFixedWidth(S["panel_width"])
        self.handle.setFixedSize(S["handle_width"], S["handle_height"])
        self._place()
        self.refresh_timer.start(max(1, int(S["refresh_minutes"])) * 60_000)
        self.refresh()

    def open_calendar(self):
        if self.g.cal is None:
            QMessageBox.information(self, "Not connected yet", "Still signing in to Google.")
            return
        if self.cal_window is None:
            self.cal_window = CalendarWindow(self)
        self.cal_window.show()
        self.cal_window.raise_()
        self.cal_window.activateWindow()

    def new_event(self, on_date=None):
        if not self.calendars:
            QMessageBox.information(self, "Not connected yet", "Still signing in to Google.")
            return
        dlg = NewEventDialog(self.calendars, self, default_date=on_date)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            v = dlg.values()
            self.status.setText("Adding event…")
            self._run(self.g.create_event, lambda _: self._after_write("Event added"),
                      v["calendar_id"], v["title"], v["start"], v["end"],
                      all_day=v["all_day"], location=v["location"],
                      description=v["description"], reminder_minutes=v["reminder_minutes"])

    def new_task(self):
        if not self.tasklists:
            QMessageBox.information(self, "Not connected yet", "Still signing in to Google.")
            return
        dlg = NewTaskDialog(self.tasklists, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            v = dlg.values()
            self.status.setText("Adding task…")
            self._run(self.g.create_task, lambda _: self._after_write("Task added"),
                      v["tasklist_id"], v["title"], due=v["due"], notes=v["notes"])

    def toggle_task(self, task):
        self.status.setText("Updating…")
        self._run(self.g.complete_task, lambda _: self._after_write("Task done"),
                  task["list_id"], task["id"], not task["completed"])

    def delete_task(self, task):
        if QMessageBox.question(self, "Delete task", f"Delete “{task['title']}”?") \
                != QMessageBox.StandardButton.Yes:
            return
        self._run(self.g.delete_task, lambda _: self._after_write("Task deleted"),
                  task["list_id"], task["id"])

    def _after_write(self, msg):
        self.status.setText(msg + " · refreshing…")
        self.refresh()
        if self.cal_window and self.cal_window.isVisible():
            self.cal_window.reload()


def main():
    sock = QLocalSocket()
    sock.connectToServer(INSTANCE_KEY)
    if sock.waitForConnected(300):
        sock.close()
        return 0

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Desktop Dashboard")

    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "ryan.desktop.dashboard.1")
        except Exception:
            pass
    app.setWindowIcon(make_icon())

    server = QLocalServer()
    QLocalServer.removeServer(INSTANCE_KEY)
    server.listen(INSTANCE_KEY)

    dash = Dashboard()
    dash.show()

    tray = QSystemTrayIcon(make_icon(), app)
    tray.setToolTip("Calendar & Tasks dashboard")
    menu = QMenu()
    menu.addAction("Show / hide", dash.toggle)
    menu.addAction("Refresh", dash.refresh)
    menu.addAction("New event…", lambda: dash.new_event())
    menu.addAction("New task…", dash.new_task)
    menu.addAction("Calendar…", dash.open_calendar)
    menu.addSeparator()
    menu.addAction("Quit", app.quit)
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda r: dash.toggle() if r == QSystemTrayIcon.ActivationReason.Trigger else None)
    tray.show()
    dash._tray = tray

    # surface auth problems instead of dying silently in the background
    def excepthook(kind, value, tb):
        log("".join(traceback.format_exception(kind, value, tb)))
        if isinstance(value, AuthError):
            QMessageBox.critical(None, "Setup needed", str(value))
    sys.excepthook = excepthook

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())