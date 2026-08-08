"""
All Google Calendar + Google Tasks access lives here
"""

import datetime as dt
import json
import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

APP_DIR = Path(__file__).resolve().parent
CRED_FILE = APP_DIR / "credentials.json"
TOKEN_FILE = APP_DIR / "token.json"

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
]


class AuthError(Exception):
    pass


def _iso(d: dt.datetime) -> str:
    if d.tzinfo is None:
        d = d.astimezone()
    return d.isoformat()


def parse_dt(value: str) -> dt.datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    if len(value) == 10:  # all-day event: 'YYYY-MM-DD'
        d = dt.datetime.strptime(value, "%Y-%m-%d")
        return d.replace(tzinfo=dt.datetime.now().astimezone().tzinfo)
    return dt.datetime.fromisoformat(value).astimezone()


class GoogleBackend:
    def __init__(self):
        self.creds = None
        self.cal = None
        self.tasks = None

    # ------------------------------------------------------------------ auth
    def connect(self):
        if not CRED_FILE.exists():
            raise AuthError(
                "credentials.json not found.\n\n"
                "Follow SETUP steps 1-6 in README.md, then drop the downloaded "
                f"file into:\n{APP_DIR}\nand rename it to credentials.json"
            )

        creds = None
        if TOKEN_FILE.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
            except Exception:
                creds = None

        if creds and creds.valid:
            pass
        elif creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None

        if not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_secrets_file(str(CRED_FILE), SCOPES)
            creds = flow.run_local_server(
                port=0,
                prompt="consent",
                authorization_prompt_message="Opening your browser to sign in to Google...",
                success_message="Signed in. You can close this tab and go back to the dashboard.",
            )

        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
        try:
            os.chmod(TOKEN_FILE, 0o600)
        except Exception:
            pass

        self.creds = creds
        self.cal = build("calendar", "v3", credentials=creds, cache_discovery=False)
        self.tasks = build("tasks", "v1", credentials=creds, cache_discovery=False)
        return True

    def _ensure(self):
        if self.cal is None or self.tasks is None:
            self.connect()

    # ------------------------------------------------------------- calendars
    def list_calendars(self, writable_only=False):
        self._ensure()
        out, page = [], None
        while True:
            resp = self.cal.calendarList().list(pageToken=page, maxResults=250).execute()
            for c in resp.get("items", []):
                if not c.get("selected", True) and not writable_only:
                    continue
                role = c.get("accessRole", "reader")
                if writable_only and role not in ("owner", "writer"):
                    continue
                out.append(
                    {
                        "id": c["id"],
                        "name": c.get("summaryOverride") or c.get("summary", c["id"]),
                        "color": c.get("backgroundColor", "#5b8dee"),
                        "primary": bool(c.get("primary")),
                    }
                )
            page = resp.get("nextPageToken")
            if not page:
                break
        out.sort(key=lambda c: (not c["primary"], c["name"].lower()))
        return out

    def list_events(self, start: dt.datetime = None, end: dt.datetime = None,
                    days_ahead: int = 14, max_per_cal: int = 100):
        """Merged, time-sorted events across every visible calendar."""
        self._ensure()
        now = dt.datetime.now().astimezone()
        start = start or now
        end = end or (start + dt.timedelta(days=days_ahead))

        events = []
        for c in self.list_calendars():
            try:
                resp = (
                    self.cal.events()
                    .list(
                        calendarId=c["id"],
                        timeMin=_iso(start),
                        timeMax=_iso(end),
                        singleEvents=True,
                        orderBy="startTime",
                        maxResults=max_per_cal,
                    )
                    .execute()
                )
            except Exception:
                continue  # a calendar we lost access to shouldn't kill the refresh

            for e in resp.get("items", []):
                if e.get("status") == "cancelled":
                    continue
                s = e.get("start", {})
                t = e.get("end", {})
                all_day = "date" in s
                try:
                    s_dt = parse_dt(s.get("dateTime") or s.get("date"))
                    e_dt = parse_dt(t.get("dateTime") or t.get("date"))
                except Exception:
                    continue
                events.append(
                    {
                        "id": e["id"],
                        "calendar_id": c["id"],
                        "calendar": c["name"],
                        "color": c["color"],
                        "title": e.get("summary", "(no title)"),
                        "location": e.get("location", ""),
                        "link": e.get("htmlLink", ""),
                        "all_day": all_day,
                        "start": s_dt,
                        "end": e_dt,
                    }
                )
        events.sort(key=lambda e: (e["start"], not e["all_day"]))
        return events

    def create_event(self, calendar_id, title, start: dt.datetime, end: dt.datetime,
                     all_day=False, location="", description="", reminder_minutes=None):
        self._ensure()
        if all_day:
            body = {
                "summary": title,
                "start": {"date": start.strftime("%Y-%m-%d")},
                "end": {"date": (end + dt.timedelta(days=1)).strftime("%Y-%m-%d")},
            }
        else:
            body = {
                "summary": title,
                "start": {"dateTime": _iso(start)},
                "end": {"dateTime": _iso(end)},
            }
        if location:
            body["location"] = location
        if description:
            body["description"] = description
        if reminder_minutes is not None:
            body["reminders"] = {
                "useDefault": False,
                "overrides": [{"method": "popup", "minutes": int(reminder_minutes)}],
            }
        return self.cal.events().insert(calendarId=calendar_id, body=body).execute()

    def delete_event(self, calendar_id, event_id):
        self._ensure()
        self.cal.events().delete(calendarId=calendar_id, eventId=event_id).execute()

    def list_tasklists(self):
        self._ensure()
        resp = self.tasks.tasklists().list(maxResults=100).execute()
        return [{"id": t["id"], "name": t.get("title", "Tasks")} for t in resp.get("items", [])]

    def list_tasks(self, include_completed=False):
        self._ensure()
        out = []
        for tl in self.list_tasklists():
            try:
                resp = (
                    self.tasks.tasks()
                    .list(
                        tasklist=tl["id"],
                        showCompleted=include_completed,
                        showHidden=include_completed,
                        maxResults=100,
                    )
                    .execute()
                )
            except Exception:
                continue
            for t in resp.get("items", []):
                due = None
                if t.get("due"):
                    try:
                        due = parse_dt(t["due"])
                    except Exception:
                        due = None
                out.append(
                    {
                        "id": t["id"],
                        "list_id": tl["id"],
                        "list": tl["name"],
                        "title": t.get("title") or "(untitled)",
                        "notes": t.get("notes", ""),
                        "due": due,
                        "completed": t.get("status") == "completed",
                    }
                )
        far = dt.datetime.max.replace(tzinfo=dt.datetime.now().astimezone().tzinfo)
        out.sort(key=lambda t: (t["completed"], t["due"] or far, t["title"].lower()))
        return out

    def create_task(self, tasklist_id, title, due: dt.date = None, notes=""):
        self._ensure()
        body = {"title": title}
        if notes:
            body["notes"] = notes
        if due:
            # Google Tasks stores due as a date at UTC midnight
            body["due"] = dt.datetime(due.year, due.month, due.day,
                                      tzinfo=dt.timezone.utc).isoformat().replace("+00:00", "Z")
        return self.tasks.tasks().insert(tasklist=tasklist_id, body=body).execute()

    def complete_task(self, tasklist_id, task_id, done=True):
        self._ensure()
        body = {"status": "completed"} if done else {"status": "needsAction", "completed": None}
        return self.tasks.tasks().patch(tasklist=tasklist_id, task=task_id, body=body).execute()

    def delete_task(self, tasklist_id, task_id):
        self._ensure()
        self.tasks.tasks().delete(tasklist=tasklist_id, task=task_id).execute()


# python google_backend.py
if __name__ == "__main__":
    g = GoogleBackend()
    g.connect()
    print("Calendars:", [c["name"] for c in g.list_calendars()])
    for e in g.list_events(days_ahead=7)[:10]:
        print(" ", e["start"].strftime("%a %d %b %H:%M"), "-", e["title"])
    for t in g.list_tasks()[:10]:
        print(" [ ]", t["title"], t["due"].strftime("%d %b") if t["due"] else "")
