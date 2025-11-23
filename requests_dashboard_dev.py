#!/usr/bin/env python3
# Updated requests_dashboard_dev.py - DB-based version (TiDB/PyMySQL)
# Replaces file-based storage with database tables: jobs, job_logs, users, request_types
# Assumes db.py exists and exposes get_conn() which returns a pymysql connection (DictCursor)

import os
import time
import uuid
import threading
import traceback
import subprocess
import sys
from datetime import datetime
from flask import Flask, render_template, jsonify, request, Response, redirect, url_for, session
from db import get_conn
from teams_notify import notify_teams

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Simple async wrapper for Teams notifications
def async_notify_teams(event, user, type_, items, status, time=None):
    def _worker():
        try:
            notify_teams(event=event, user=user, type_=type_, items=items, status=status, time=time)
        except Exception as e:
            print("⚠️ async_notify_teams failed:", e)
    threading.Thread(target=_worker, daemon=True).start()

# Utilities
def now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def generate_job_id():
    return uuid.uuid4().hex[:12]

# ------------------------------------------------------------------
# LOGIN/USERS (reads from users table)
# ------------------------------------------------------------------
@app.route("/login", methods=["GET","POST"])
def login():
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()
        if not username or not password:
            error = "Please provide username and password"
            return render_template("login.html", error=error)
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE username=%s AND password=%s LIMIT 1", (username, password))
                row = cur.fetchone()
        finally:
            conn.close()
        if not row:
            error = "Invalid username or password"
            return render_template("login.html", error=error)
        # set session
        session.clear()
        session["logged_in"] = True
        session["username"] = row.get("username")
        session["role"] = row.get("role") or ""
        session["display_name"] = row.get("display_name") or row.get("username")
        return redirect(url_for("home"))
    return render_template("login.html", error=None)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ------------------------------------------------------------------
# HOME / DASHBOARD
# ------------------------------------------------------------------
@app.route("/")
@app.route("/requests_dashboard")
def home():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    is_approver = (session.get("role","").strip().lower() == "approver")
    username = session.get("username")
    # fetch request types & colors from DB to render UI
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT type_name, item_name, color FROM request_types ORDER BY type_name, item_name")
            rows = cur.fetchall()
    finally:
        conn.close()
    reqs = {}
    colors = {}
    for r in rows:
        t = r.get("type_name") or ""
        it = r.get("item_name") or ""
        reqs.setdefault(t, []).append(it)
        if r.get("color"):
            colors[t] = r.get("color")
    return render_template("dev_ver.html", requests_data=reqs, colors=colors, username=username, is_approver=is_approver)

# ------------------------------------------------------------------
# JOBS API - read jobs from DB
# ------------------------------------------------------------------
@app.route("/jobs")
def get_jobs():
    if not session.get("logged_in"):
        return jsonify([])
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM jobs ORDER BY COALESCE(end_time, created_at) DESC")
            rows = cur.fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        out.append({
            "id": r.get("id"),
            "filename": r.get("filename"),
            "status": r.get("status"),
            "progress": int(r.get("progress") or 0),
            "user": r.get("user"),
            "display_name": r.get("display_name"),
            "type": r.get("type"),
            "items": r.get("items"),
            "created_at": r.get("created_at").strftime("%Y-%m-%d %H:%M:%S") if r.get("created_at") else None,
            "approved_at": r.get("approved_at").strftime("%Y-%m-%d %H:%M:%S") if r.get("approved_at") else None,
            "start": r.get("start_time").strftime("%Y-%m-%d %H:%M:%S") if r.get("start_time") else None,
            "end": r.get("end_time").strftime("%Y-%m-%d %H:%M:%S") if r.get("end_time") else None,
            "rejected_reason": r.get("rejected_reason") if r.get("status") == "rejected" else None,
        })
    return jsonify(out)

# ------------------------------------------------------------------
# Submit request(s)
# ------------------------------------------------------------------
@app.route("/submit/<req_type>", methods=["POST"])
def submit_request(req_type):
    try:
        if not session.get("logged_in"):
            return jsonify({"error":"Not authenticated"}), 403
        items = request.form.getlist("items")
        if not items:
            return jsonify({"error": "No items selected"}), 400
        username = session.get("username") or "anonymous"
        display_name = session.get("display_name") or username
        created = 0
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                for item in items:
                    jid = generate_job_id()
                    created_at = datetime.now()
                    # filename kept for compatibility
                    filename = f"{jid}.db"
                    # Prevent duplicate submissions (same user, type, item in last 3 seconds)
                    cur.execute("""
                        SELECT id FROM jobs
                        WHERE user=%s AND type=%s AND items=%s
                        AND created_at > NOW() - INTERVAL 3 SECOND
                    """, (username, req_type, item))
                    if cur.fetchone():
                        continue  # skip duplicate

                    cur.execute('INSERT INTO jobs (id, filename, user, display_name, type, items, status, progress, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                                (jid, filename, username, display_name, req_type, item, "pending", 0, created_at))
                    created += 1
        finally:
            conn.close()
        async_notify_teams(event="request_raised", user=username, type_=req_type, items=items, status="pending")
        return jsonify({"status":"ok","created":created}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ------------------------------------------------------------------
# Approve / Reject endpoints
# ------------------------------------------------------------------
@app.route("/approve/<jid>", methods=["POST"])
def approve(jid):
    if session.get("role","").strip().lower() != "approver":
        return jsonify({"ok": False, "msg":"Forbidden"}), 403
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            approved_at = datetime.now()
            cur.execute(
                'UPDATE jobs SET status=%s, approved_at=%s, approved_by=%s WHERE id=%s AND status=%s',
                ("queued", approved_at, session.get("username"), jid, "pending")
            )

            if cur.rowcount == 0:
                return jsonify({"ok": False, "msg":"Job not found"}), 404
            # add a small log entry
            cur.execute('INSERT INTO job_logs (job_id, line) VALUES (%s,%s)', (jid, f"[{approved_at.strftime('%H:%M:%S')}] Approved and queued"))
            cur.execute('UPDATE jobs SET progress=%s WHERE id=%s', (0, jid))
    finally:
        conn.close()
    return jsonify({"ok": True})

@app.route("/reject/<jid>", methods=["POST"])
def reject(jid):
    if session.get("role","").strip().lower() != "approver":
        return jsonify({"ok": False, "msg":"Forbidden"}), 403
    data = request.get_json() or {}
    reason = data.get("reason","")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            end = datetime.now()
            cur.execute(
                'UPDATE jobs SET status=%s, end_time=%s, rejected_by=%s, rejected_reason=%s WHERE id=%s AND status=%s',
                ("rejected", end, session.get("username"), reason, jid, "pending")
            )

            if cur.rowcount == 0:
                return jsonify({"ok": False, "msg":"Job not found"}), 404
            cur.execute('INSERT INTO job_logs (job_id, line) VALUES (%s,%s)', (jid, f"[{end.strftime('%H:%M:%S')}] REJECTED: {reason}"))
    finally:
        conn.close()
    async_notify_teams(event="job_rejected", user=session.get("username"), type_="", items="", status="rejected", time=end.isoformat())
    return jsonify({"ok": True})

# ------------------------------------------------------------------
# Reason / Logs endpoints
# ------------------------------------------------------------------
@app.route("/reason/<jid>")
def reason(jid):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT status, rejected_by, rejected_reason, approved_by FROM jobs WHERE id=%s", (jid,))
            row = cur.fetchone()

            if not row:
                return jsonify({"ok": False, "msg": "Job not found"})

            status = row.get("status")

            # REJECTED CASE
            if status == "rejected":
                return jsonify({
                    "ok": True,
                    "status": "rejected",
                    "rejected_by": row.get("rejected_by"),
                    "rejected_reason": row.get("rejected_reason"),
                })

            # ERROR CASE (optional)
            if status == "error":
                # you can keep last logs or use an error_message column if you add one
                cur.execute("""
                    SELECT line FROM job_logs
                    WHERE job_id=%s
                    ORDER BY log_id DESC
                    LIMIT 10
                """, (jid,))
                lines = [r["line"] for r in cur.fetchall()]
                return jsonify({
                    "ok": True,
                    "status": "error",
                    "error_details": "\n".join(lines)
                })

            return jsonify({"ok": False, "msg": "No reason available for this status"})

    finally:
        conn.close()

# ------------------------------------------------------------------
# Kill running job endpoint
# ------------------------------------------------------------------
@app.route("/kill/<jid>", methods=["POST"])
def kill_job(jid):
    if session.get("role","").strip().lower() != "approver":
        return jsonify({"ok": False, "msg":"Forbidden"}), 403

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Check if job is running
            cur.execute("SELECT status FROM jobs WHERE id=%s", (jid,))
            row = cur.fetchone()

            if not row:
                return jsonify({"ok": False, "msg": "Job not found"}), 404

            if row.get("status") != "running":
                return jsonify({"ok": False, "msg": "Job is not running"}), 400

            # Actually kill the process if it exists
            if jid in running_processes:
                try:
                    proc = running_processes[jid]
                    proc.terminate()  # Try graceful termination first
                    try:
                        proc.wait(timeout=2)  # Wait up to 2 seconds
                    except subprocess.TimeoutExpired:
                        proc.kill()  # Force kill if it doesn't terminate
                    running_processes.pop(jid, None)
                    enqueue_log_to_db(jid, f"[{datetime.now().strftime('%H:%M:%S')}] Process terminated by {session.get('username')}")
                except Exception as e:
                    print(f"Error killing process for job {jid}: {e}")

            # Mark job as killed
            end = datetime.now()
            cur.execute(
                'UPDATE jobs SET status=%s, end_time=%s WHERE id=%s',
                ("killed", end, jid)
            )
            cur.execute('INSERT INTO job_logs (job_id, line) VALUES (%s,%s)',
                       (jid, f"[{end.strftime('%H:%M:%S')}] 🛑 Job killed by {session.get('username')}"))
    finally:
        conn.close()

    async_notify_teams(event="job_killed", user=session.get("username"),
                      type_="", items="", status="killed", time=datetime.now().isoformat())
    return jsonify({"ok": True})

# ------------------------------------------------------------------
# Re-submit job endpoint (for rejected/error jobs)
# ------------------------------------------------------------------
@app.route("/resubmit/<jid>", methods=["POST"])
def resubmit_job(jid):
    if not session.get("logged_in"):
        return jsonify({"error":"Not authenticated"}), 403

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Get original job details
            cur.execute("SELECT * FROM jobs WHERE id=%s", (jid,))
            original = cur.fetchone()

            if not original:
                return jsonify({"ok": False, "msg": "Job not found"}), 404

            # Only allow re-submit for rejected/error/killed jobs
            if original.get("status") not in ["rejected", "error", "killed"]:
                return jsonify({"ok": False, "msg": "Job is not rejected, error, or killed"}), 400

            # Create new job with same details
            new_jid = generate_job_id()
            created_at = datetime.now()
            filename = f"{new_jid}.db"

            cur.execute(
                'INSERT INTO jobs (id, filename, user, display_name, type, items, status, progress, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (new_jid, filename, session.get("username"), session.get("display_name"),
                 original.get("type"), original.get("items"), "pending", 0, created_at)
            )

    finally:
        conn.close()

    async_notify_teams(event="request_raised", user=session.get("username"),
                      type_=original.get("type"), items=[original.get("items")], status="pending")
    return jsonify({"ok": True, "new_id": new_jid})

@app.route("/logs/<jid>")
def stream_logs(jid):
    # Simple SSE by polling job_logs table for new lines
    def gen():
        last_id = 0
        conn = None
        try:
            conn = get_conn()
            while True:
                try:
                    with conn.cursor() as cur:
                        cur.execute('SELECT log_id, line FROM job_logs WHERE job_id=%s AND log_id>%s ORDER BY log_id ASC', (jid, last_id))
                        rows = cur.fetchall()
                        for r in rows:
                            last_id = r["log_id"]
                            yield f"data:{r['line']}\n\n"
                        # check finished state
                        cur.execute('SELECT status FROM jobs WHERE id=%s', (jid,))
                        s = cur.fetchone()
                        if s and s.get("status") in ("completed","error","rejected"):
                            yield "data:__STREAM_END__\n\n"
                            break
                except Exception as e:
                    # If DB read fails, yield a small warning and retry after a short pause
                    print("logs stream db read error:", e)
                    try:
                        yield f"data:[WARN] log stream temporary DB error: {str(e)}\n\n"
                    except Exception:
                        pass
                time.sleep(0.6)
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass
    headers = {"Content-Type": "text/event-stream", "Cache-Control":"no-cache", "X-Accel-Buffering":"no"}
    return Response(gen(), headers=headers)

# ------------------------------------------------------------------
# Scheduled jobs APIs (store scheduled_time in jobs table)
# ------------------------------------------------------------------
@app.route("/submit_scheduled/<req_type>", methods=["POST"])
def submit_scheduled(req_type):
    try:
        if not session.get("logged_in"):
            return jsonify({"error":"Not authenticated"}), 403
        items = request.form.getlist("items")
        sched_time = request.form.get("scheduled_time","")
        if not items:
            return jsonify({"error":"No items selected"}), 400
        if not sched_time:
            return jsonify({"error":"No scheduled time"}), 400
        try:
            if "T" in sched_time and len(sched_time.split("T")[1]) <= 5:
                sched_time += ":00"
            dt = datetime.fromisoformat(sched_time)
        except Exception:
            return jsonify({"error":"Invalid datetime format"}), 400
        username = session.get("username") or "scheduled"
        display_name = session.get("display_name") or username
        created = 0
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                for item in items:
                    jid = generate_job_id()
                    cur.execute('INSERT INTO jobs (id, filename, user, display_name, type, items, status, progress, created_at, scheduled_time) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                                (jid, jid+".db", username, display_name, req_type, item, "scheduled", 0, datetime.now(), dt))
                    created += 1
        finally:
            conn.close()
        return jsonify({"status":"ok","created":created}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/scheduled_jobs")
def get_scheduled_jobs():
    if not session.get("logged_in"):
        return jsonify([])
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM jobs WHERE status=%s ORDER BY scheduled_time ASC', ("scheduled",))
            rows = cur.fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        out.append({
            "filename": r.get("filename"),
            "user": r.get("user"),
            "type": r.get("type"),
            "items": r.get("items"),
            "scheduled_time": r.get("scheduled_time").strftime("%Y-%m-%d %H:%M:%S") if r.get("scheduled_time") else None
        })
    return jsonify(out)

# ------------------------------------------------------------------
# Manage request types (DB-backed)
# ------------------------------------------------------------------
@app.route("/add_db_type", methods=["POST"])
def add_db_type():
    data = request.get_json() or {}
    t = (data.get("type") or "").strip()
    items = data.get("items") or []
    color = (data.get("color") or "").strip() or "#00e0ff"
    if not t or not items:
        return jsonify({"ok": False, "msg":"Missing type or items"}), 400
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            for it in items:
                cur.execute('INSERT IGNORE INTO request_types (type_name, item_name, color) VALUES (%s,%s,%s)', (t, it, color))
    finally:
        conn.close()
    return jsonify({"ok": True, "added": len(items)})

@app.route("/delete_db_type", methods=["POST"])
def delete_db_type():
    data = request.get_json() or {}
    t = (data.get("type") or "").strip()
    item = (data.get("item") or "").strip()
    if not t or not item:
        return jsonify({"ok": False, "msg":"Missing type or item"}), 400
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('DELETE FROM request_types WHERE type_name=%s AND item_name=%s', (t, item))
    finally:
        conn.close()
    return jsonify({"ok": True, "deleted": 1})

# ------------------------------------------------------------------
# Worker: pick queued jobs and run scripts
# ------------------------------------------------------------------

# Global dict to track running processes by job_id
running_processes = {}

def enqueue_log_to_db(job_id, text):
    try:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO job_logs (job_id, line) VALUES (%s, %s)",
                    (job_id, text)
                )
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception as e:
        print(f"[WARN] enqueue_log_to_db failed for {job_id}: {e}")
        return False

def run_script_for_job(job):
    job_id = job.get("id")
    script_path = os.path.join("scripts", f"{(job.get('type') or '').lower()}.py")
    args = [job.get("items") or ""]
    enqueue_log_to_db(job_id, f"[{datetime.now().strftime('%H:%M:%S')}] Launching: {script_path} {args}")
    # Use same interpreter as current process for reliability
    try:
        proc = subprocess.Popen([sys.executable, "-u", script_path] + args,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        # Store process in global dict so we can kill it later
        running_processes[job_id] = proc
    except Exception as e:
        enqueue_log_to_db(job_id, f"❌ Failed to launch script: {e}")
        return False
    try:
        # Iterate stdout line by line
        for raw_line in proc.stdout:
            try:
                line = raw_line.rstrip("\n")
            except Exception:
                line = raw_line
            if not line:
                continue
            # Process progress messages specially
            if line.upper().startswith("PROGRESS:"):
                try:
                    pct = int(line.split(":",1)[1].strip().rstrip("%"))
                    conn = get_conn()
                    try:
                        with conn.cursor() as cur:
                            cur.execute('UPDATE jobs SET progress=%s WHERE id=%s', (pct, job_id))
                        try:
                            conn.commit()
                        except Exception:
                            pass
                    finally:
                        try:
                            conn.close()
                        except Exception:
                            pass
                    enqueue_log_to_db(job_id, f"PROGRESS: {pct}")
                except Exception:
                    enqueue_log_to_db(job_id, f"[WARN] bad progress format: {line}")
            else:
                enqueue_log_to_db(job_id, line)
        proc.wait()
        # Clean up from running_processes dict
        running_processes.pop(job_id, None)
        return proc.returncode == 0
    except Exception as e:
        enqueue_log_to_db(job_id, f"❌ Exception while running: {e}")
        try:
            proc.kill()
        except Exception:
            pass
        running_processes.pop(job_id, None)
        return False

def worker_loop():
    """
    Main worker loop, resilient to exceptions. If an unexpected exception
    occurs, log to DB (best-effort) and continue after a short sleep.
    """
    while True:
        try:
            # 1. Prevent parallel running jobs
            conn = get_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) AS c FROM jobs WHERE status='running'")
                    running_count = cur.fetchone()['c']
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

            if running_count > 0:
                time.sleep(1)
                continue

            # 2. Pick next queued job
            conn = get_conn()
            job = None
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY approved_at ASC LIMIT 1")
                    job = cur.fetchone()
                    if job:
                        start = datetime.now()
                        cur.execute(
                            "UPDATE jobs SET status=%s, start_time=%s, progress=%s WHERE id=%s",
                            ("running", start, 0, job['id'])
                        )
                        try:
                            conn.commit()
                        except Exception:
                            pass
                        enqueue_log_to_db(job['id'], f"[{start.strftime('%H:%M:%S')}] Started job")
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

            # 3. If no queued job, check scheduled
            if not job:
                conn = get_conn()
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT * FROM jobs WHERE status='scheduled' AND scheduled_time<=%s ORDER BY scheduled_time ASC LIMIT 1",
                            (datetime.now(),)
                        )
                        job = cur.fetchone()
                        if job:
                            now = datetime.now()
                            cur.execute(
                                "UPDATE jobs SET status=%s, approved_at=%s WHERE id=%s",
                                ("queued", now, job['id'])
                            )
                            try:
                                conn.commit()
                            except Exception:
                                pass
                            enqueue_log_to_db(job['id'], f"[{now.strftime('%H:%M:%S')}] Auto-queued scheduled job")
                finally:
                    try:
                        conn.close()
                    except Exception:
                        pass

            # 4. If still no job → sleep
            if not job:
                time.sleep(1)
                continue

            # 5. Run the job (ETL/VIEW/TABLE)
            ok = run_script_for_job(job)

            # 6. Update status accordingly (only if not killed)
            conn = get_conn()
            try:
                with conn.cursor() as cur:
                    # Check if job was killed while running
                    cur.execute("SELECT status FROM jobs WHERE id=%s", (job['id'],))
                    current_status = cur.fetchone()
                    if current_status and current_status.get('status') == 'killed':
                        # Job was killed, don't update status
                        continue

                    end = datetime.now()
                    if ok:
                        # job succeeded
                        cur.execute(
                            "UPDATE jobs SET status=%s, progress=%s, end_time=%s WHERE id=%s",
                            ("completed", 100, end, job['id'])
                        )

                        # success log
                        enqueue_log_to_db(job['id'], f"[{end.strftime('%H:%M:%S')}] ✔ Script finished successfully.")

                        # 🔥 DELETE ALL LOGS FOR SUCCESS
                        try:
                            with get_conn().cursor() as c2:
                                c2.execute("DELETE FROM job_logs WHERE job_id=%s", (job['id'],))
                        except Exception as e:
                            print("⚠️ Could not clean logs for success job:", e)

                        async_notify_teams(
                            event="job_completed",
                            user=job.get("user"),
                            type_=job.get("type"),
                            items=job.get("items"),
                            status="completed",
                            time=end.isoformat()
                        )

                    else:
                        # job failed
                        cur.execute(
                            "UPDATE jobs SET status=%s, end_time=%s WHERE id=%s",
                            ("error", end, job['id'])
                        )

                        enqueue_log_to_db(job['id'], f"[{end.strftime('%H:%M:%S')}] ❌ Script failed.")

                        # 🔥 KEEP ONLY LAST 10 LOG LINES
                        try:
                            with get_conn().cursor() as c2:
                                c2.execute("""
                                    DELETE FROM job_logs 
                                    WHERE job_id=%s 
                                    AND log_id NOT IN (
                                        SELECT log_id 
                                        FROM (
                                            SELECT log_id 
                                            FROM job_logs 
                                            WHERE job_id=%s 
                                            ORDER BY log_id DESC 
                                            LIMIT 10
                                        ) AS t
                                    )
                                """, (job['id'], job['id']))
                        except Exception as e:
                            print("⚠️ Could not truncate logs for error job:", e)

                        async_notify_teams(
                            event="job_error",
                            user=job.get("user"),
                            type_=job.get("type"),
                            items=job.get("items"),
                            status="error",
                            time=end.isoformat()
                        )

                        try:
                            conn.commit()
                        except Exception:
                            pass
                        enqueue_log_to_db(job['id'], f"[{end.strftime('%H:%M:%S')}] ❌ Script failed.")
                        async_notify_teams(
                            event="job_error",
                            user=job.get("user"),
                            type_=job.get("type"),
                            items=job.get("items"),
                            status="error",
                            time=end.isoformat()
                        )
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

        except Exception as e:
            # Log the worker-level exception (best-effort) and continue the loop
            tb = traceback.format_exc()
            print("Worker loop exception (recovering):", e)
            print(tb)
            # Try to write to a generic system log job if available (best-effort)
            try:
                enqueue_log_to_db("system", f"[{datetime.now().strftime('%H:%M:%S')}] Worker exception: {str(e)}")
            except Exception:
                pass
            # short sleep to avoid tight-loop on persistent error
            time.sleep(2)
            continue

# ------------------------------------------------------------------
# Render manage_types and analytics pages using DB
# ------------------------------------------------------------------
@app.route("/manage_types")
def manage_types():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT type_name, item_name, color FROM request_types ORDER BY type_name, item_name')
            rows = cur.fetchall()
    finally:
        conn.close()
    reqs = {}
    colors = {}
    for r in rows:
        t = r.get("type_name") or ""
        it = r.get("item_name") or ""
        reqs.setdefault(t, []).append(it)
        if r.get("color"):
            colors[t] = r.get("color")
    return render_template("manage_types.html", requests_data=reqs, colors=colors)

@app.route("/analytics_dashboard")
def analytics_dashboard():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    return render_template("analytics_dashboard.html")

# ------------------------------------------------------------------
# MAIN
# ------------------------------------------------------------------
if __name__ == "__main__":
    print("🚀 Requests Dashboard (DB) running at http://127.0.0.1:5002/requests_dashboard")

    # ✅ Start background worker
    threading.Thread(target=worker_loop, daemon=True).start()

    # Note: debug=True enables the reloader which can spawn multiple
    # processes/threads; if you see odd behaviour consider setting debug=False.
    app.run(host="127.0.0.1", port=5002, threaded=True, debug=True)
