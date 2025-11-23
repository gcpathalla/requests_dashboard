# teams_notify.py
import os
import requests
from datetime import datetime
from teams_webhook import webhook
# 👉 Put your Power Automate flow URL here
#FLOW_URL = os.getenv("TEAMS_FLOW_URL", webhook)

def notify_teams(event, user, type_, items, status, time=None):
    """
    Send a notification to Microsoft Teams via Power Automate HTTP trigger.
    - event: string, e.g. 'job_completed' or 'request_raised'
    - user:  string, e.g. 'john.doe'
    - type_: string, e.g. 'ETL', 'VIEW', 'TABLE'
    - items: string or list of items
    - status: string, e.g. 'completed', 'pending', 'failed'
    - time: optional datetime string (auto-filled if missing)
    """
    # Always send items as a SINGLE STRING because Flow requires a string, not array
    if isinstance(items, (list, tuple)):
        items = ", ".join([str(i) for i in items])
    elif not isinstance(items, str):
        items = str(items)

    payload = {
        "event": event,
        "user": user,
        "type": type_,
        "items": items,
        "status": status,
        "time": time or datetime.now().isoformat()
    }

    try:
        r = requests.post(FLOW_URL, json=payload, timeout=5)
        if r.status_code not in (200, 202):
            print(f"❌ Teams notify failed [{r.status_code}]: {r.text}")
            return False
        print(f"✅ Teams notified for {event} ({type_}) → {user}")
        return True
    except Exception as e:
        print("⚠️ Error sending Teams notification:", e)
        return False
