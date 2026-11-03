import os
import re
import json
import time
import sqlite3
import asyncio
import threading
from datetime import datetime
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from database import init_db, get_db, hash_password, get_settings_dict, update_settings_dict
from synology_client import SynologyClient
from email_service import check_and_send_pm_notifications, check_and_send_avl_notifications, send_smtp_email

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORAGE_DIR = os.path.join(BASE_DIR, "storage")
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(STORAGE_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

app = FastAPI(title="ISO Audit & Document Control System (Sunlogis)")


def get_synology_client() -> SynologyClient:
    cfg = get_settings_dict()
    return SynologyClient(
        quickconnect_id=cfg.get("synology_quickconnect", "Sunlogis"),
        username=cfg.get("synology_user", "Watcharagorn"),
        password=cfg.get("synology_password", "Cervelo$3"),
        base_folder=cfg.get("synology_folder", "ISO"),
        custom_host=cfg.get("synology_custom_host", ""),
        local_storage_root=STORAGE_DIR,
    )


def sanitize_code_filename(code: str, original_filename: str) -> str:
    """
    Ensure uploaded file is named strictly after the Document Code (or Record Code)
    preserving the original file extension (e.g. QP-DCC-001.pdf).
    """
    _, ext = os.path.splitext(original_filename or "")
    if not ext:
        ext = ".pdf"
    safe_code = re.sub(r'[\\/*?:"<>|\s]+', "_", code.strip())
    return f"{safe_code}{ext.lower()}"


def create_minimal_pdf(file_path: str, title_text: str, subtitle_text: str) -> None:
    """Create a valid minimal PDF file for initial sample demonstration."""
    os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
    safe_t1 = re.sub(r"[^\x20-\x7E]", "", title_text)[:70] or "ISO Controlled Document"
    safe_t2 = re.sub(r"[^\x20-\x7E]", "", subtitle_text)[:70] or "Sunlogis ISO Management System"
    content = (
        f"BT /F1 16 Tf 50 750 Td ({safe_t1}) Tj "
        f"/F1 11 Tf 0 -28 Td ({safe_t2}) Tj "
        f"0 -22 Td (Stored in Synology NAS: QuickConnect Sunlogis / Folder ISO) Tj ET"
    )
    stream_bytes = content.encode("latin-1", errors="ignore")
    pdf_bytes = (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 612 792] /Contents 5 0 R >> endobj\n"
        b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
        + f"5 0 obj << /Length {len(stream_bytes)} >> stream\n".encode("latin-1")
        + stream_bytes
        + b"\nendstream endobj\n"
        b"xref\n0 6\n0000000000 65535 f \n"
        b"0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n"
        b"0000000229 00000 n \n0000000297 00000 n \n"
        b"trailer << /Size 6 /Root 1 0 R >>\nstartxref\n400\n%%EOF\n"
    )
    with open(file_path, "wb") as f:
        f.write(pdf_bytes)


def sync_users_state_to_storage(upload_async: bool = True):
    """
    Persist current `users` and `deleted_users` from SQLite DB to local storage
    AND Synology NAS (/DATA/ISO/System_DB/users_state.json) so user creations/deletions
    survive container rebuilds and GitHub deployments without holding any DB lock.
    """
    try:
        conn = get_db()
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS deleted_users (username TEXT PRIMARY KEY, deleted_at TEXT NOT NULL)"
            )
            users_rows = [dict(r) for r in conn.execute("SELECT * FROM users ORDER BY id ASC").fetchall()]
            del_rows = [dict(r) for r in conn.execute("SELECT * FROM deleted_users").fetchall()]
        finally:
            conn.close()

        state_dir = os.path.join(STORAGE_DIR, "ISO", "System_DB")
        os.makedirs(state_dir, exist_ok=True)
        state_file = os.path.join(state_dir, "users_state.json")
        payload = {
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "users": users_rows,
            "deleted_users": del_rows,
        }
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        def _upload_state():
            try:
                syno = get_synology_client()
                syno.upload_file(state_file, "System_DB", "users_state.json")
            except Exception:
                pass

        if upload_async:
            threading.Thread(target=_upload_state, daemon=True).start()
        else:
            _upload_state()
    except Exception:
        pass


def restore_users_state_from_storage():
    """
    On startup, if a saved users_state.json exists in local storage or Synology NAS,
    restore created/deleted users into SQLite DB so they are never lost across deployments.
    """
    try:
        state_dir = os.path.join(STORAGE_DIR, "ISO", "System_DB")
        os.makedirs(state_dir, exist_ok=True)
        state_file = os.path.join(state_dir, "users_state.json")

        # Only download from Synology if local state_file does not exist yet
        if not os.path.exists(state_file):
            try:
                syno = get_synology_client()
                ok, _ = syno.connect_and_login()
                if ok and syno.resolved_root_path:
                    remote_state = f"{syno.resolved_root_path.rstrip('/')}/System_DB/users_state.json"
                    syno.download_file_to_local(remote_state, state_file)
            except Exception:
                pass

        if not os.path.exists(state_file):
            return

        with open(state_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        saved_users = data.get("users")
        saved_deleted = data.get("deleted_users", [])
        if not isinstance(saved_users, list) or len(saved_users) == 0:
            return

        conn = get_db()
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS deleted_users (username TEXT PRIMARY KEY, deleted_at TEXT NOT NULL)"
            )
            for d in saved_deleted:
                if d.get("username"):
                    conn.execute(
                        "INSERT OR REPLACE INTO deleted_users (username, deleted_at) VALUES (?, ?)",
                        (d["username"], d.get("deleted_at", "")),
                    )

            deleted_set = {d["username"].lower() for d in saved_deleted if d.get("username")}
            for uname_low in deleted_set:
                conn.execute("DELETE FROM users WHERE LOWER(username) = ?", (uname_low,))

            for u in saved_users:
                uname = (u.get("username") or "").strip()
                if not uname or uname.lower() in deleted_set:
                    continue
                conn.execute(
                    """
                    INSERT INTO users (username, password_hash, full_name, role, department, email, is_active, must_change_password, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(username) DO UPDATE SET
                        password_hash = excluded.password_hash,
                        full_name = excluded.full_name,
                        role = excluded.role,
                        department = excluded.department,
                        email = excluded.email,
                        is_active = excluded.is_active,
                        must_change_password = excluded.must_change_password
                    """,
                    (
                        uname,
                        u.get("password_hash", ""),
                        u.get("full_name", uname),
                        u.get("role", "Viewer"),
                        u.get("department", "General"),
                        u.get("email", ""),
                        int(u.get("is_active", 1)),
                        int(u.get("must_change_password", 0)),
                        u.get("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                    ),
                )
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('users_initialized', '1')")
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def bootstrap_sample_files_and_synology():
    """
    Ensure sample documents have local PDF files named by doc_code and sync to Synology.
    IMPORTANT: Never keep a SQLite connection open while creating files or uploading to Synology!
    """
    restore_users_state_from_storage()

    conn = get_db()
    try:
        docs = [dict(r) for r in conn.execute("SELECT id, doc_code, doc_title, revision_no, file_name, local_path, synology_synced FROM documents").fetchall()]
        emps = [dict(r) for r in conn.execute("SELECT id, emp_code, full_name, application_file_name, application_local_path FROM employees").fetchall()]
        trs = [
            dict(r)
            for r in conn.execute(
                "SELECT t.id, t.course_code, t.status, t.evidence_file_name, t.evidence_local_path, e.emp_code FROM training_records t JOIN employees e ON t.emp_id = e.id WHERE t.status = 'Completed'"
            ).fetchall()
        ]
        pms = [
            dict(r)
            for r in conn.execute(
                "SELECT p.id, p.pm_code, p.status, p.evidence_file_name, p.evidence_local_path, e.equip_code FROM pm_schedules p JOIN equipment e ON p.equip_id = e.id WHERE p.status = 'Completed'"
            ).fetchall()
        ]
        vends = [dict(r) for r in conn.execute("SELECT id, vendor_code, vendor_name, cert_filename, local_cert_path FROM vendors").fetchall()]
        vevals = [
            dict(r)
            for r in conn.execute(
                "SELECT ve.id, ve.eval_code, ve.status, ve.evidence_filename, ve.local_evidence_path, v.vendor_code FROM vendor_evaluations ve JOIN vendors v ON ve.vendor_id = v.id WHERE ve.status = 'Completed'"
            ).fetchall()
        ]
    finally:
        conn.close()

    syno = get_synology_client()

    for d in docs:
        if not d["file_name"] or not d["local_path"] or not os.path.exists(d["local_path"]):
            fname = f"{d['doc_code']}.pdf"
            local_p = os.path.join(STORAGE_DIR, "ISO", "Documents", fname)
            create_minimal_pdf(
                local_p,
                f"Document Code: {d['doc_code']} (Rev.{d['revision_no']:02d})",
                f"Effective ISO Controlled Document - {d['doc_code']}",
            )
            up_res = syno.upload_file(local_p, "Documents", fname)
            c = get_db()
            try:
                c.execute(
                    """
                    UPDATE documents
                    SET file_name = ?, original_name = ?, local_path = ?, synology_path = ?, synology_synced = ?
                    WHERE id = ?
                    """,
                    (
                        fname,
                        fname,
                        up_res["local_path"],
                        up_res["synology_path"],
                        1 if up_res["synology_synced"] else 0,
                        d["id"],
                    ),
                )
                c.execute(
                    """
                    UPDATE document_revisions
                    SET file_name = ?, local_path = ?, synology_path = ?
                    WHERE doc_id = ? AND revision_no = ?
                    """,
                    (fname, up_res["local_path"], up_res["synology_path"], d["id"], d["revision_no"]),
                )
                c.commit()
            finally:
                c.close()

    for e in emps:
        if not e["application_file_name"] or not e["application_local_path"] or not os.path.exists(e["application_local_path"]):
            fname = f"{e['emp_code']}_Application.pdf"
            local_p = os.path.join(STORAGE_DIR, "ISO", "HR_Employees", e["emp_code"], "Application", fname)
            create_minimal_pdf(local_p, f"Job Application: {e['emp_code']}", f"Employee: {e['full_name']}")
            up_res = syno.upload_file(local_p, f"HR_Employees/{e['emp_code']}/Application", fname)
            c = get_db()
            try:
                c.execute(
                    """
                    UPDATE employees
                    SET application_file_name = ?, application_local_path = ?, application_synology_path = ?, application_synced = ?
                    WHERE id = ?
                    """,
                    (fname, up_res["local_path"], up_res["synology_path"], 1 if up_res["synology_synced"] else 0, e["id"]),
                )
                c.commit()
            finally:
                c.close()

    for t in trs:
        if not t["evidence_file_name"] or not t["evidence_local_path"] or not os.path.exists(t["evidence_local_path"]):
            fname = f"{t['emp_code']}_{t['course_code']}_Evidence.pdf"
            local_p = os.path.join(STORAGE_DIR, "ISO", "HR_Employees", t["emp_code"], "Training", fname)
            create_minimal_pdf(local_p, f"Training Record & Certificate: {t['course_code']}", f"Employee: {t['emp_code']} - Status: Completed")
            up_res = syno.upload_file(local_p, f"HR_Employees/{t['emp_code']}/Training", fname)
            c = get_db()
            try:
                c.execute(
                    """
                    UPDATE training_records
                    SET evidence_file_name = ?, evidence_local_path = ?, evidence_synology_path = ?, evidence_synced = ?
                    WHERE id = ?
                    """,
                    (fname, up_res["local_path"], up_res["synology_path"], 1 if up_res["synology_synced"] else 0, t["id"]),
                )
                c.commit()
            finally:
                c.close()

    for p in pms:
        if not p["evidence_file_name"] or not p["evidence_local_path"] or not os.path.exists(p["evidence_local_path"]):
            fname = f"{p['equip_code']}_{p['pm_code']}_Evidence.pdf"
            local_p = os.path.join(STORAGE_DIR, "ISO", "MHE_Maintenance", p["equip_code"], fname)
            create_minimal_pdf(local_p, f"PM Service Report: {p['pm_code']}", f"Equipment: {p['equip_code']} - Preventive Maintenance Completed")
            up_res = syno.upload_file(local_p, f"MHE_Maintenance/{p['equip_code']}", fname)
            c = get_db()
            try:
                c.execute(
                    """
                    UPDATE pm_schedules
                    SET evidence_file_name = ?, evidence_local_path = ?, evidence_synology_path = ?, evidence_synced = ?
                    WHERE id = ?
                    """,
                    (fname, up_res["local_path"], up_res["synology_path"], 1 if up_res["synology_synced"] else 0, p["id"]),
                )
                c.commit()
            finally:
                c.close()

    for v in vends:
        if not v["cert_filename"] or not v["local_cert_path"] or not os.path.exists(v["local_cert_path"]):
            fname = f"{v['vendor_code']}_Certificate.pdf"
            local_p = os.path.join(STORAGE_DIR, "ISO", "AVL_Vendors", v["vendor_code"], fname)
            create_minimal_pdf(local_p, f"AVL Vendor Certificate: {v['vendor_code']}", f"Vendor: {v['vendor_name']}")
            up_res = syno.upload_file(local_p, f"AVL_Vendors/{v['vendor_code']}", fname)
            c = get_db()
            try:
                c.execute(
                    """
                    UPDATE vendors
                    SET cert_filename = ?, local_cert_path = ?, synology_cert_path = ?
                    WHERE id = ?
                    """,
                    (fname, up_res["local_path"], up_res["synology_path"], v["id"]),
                )
                c.commit()
            finally:
                c.close()

    for ve in vevals:
        if not ve["evidence_filename"] or not ve["local_evidence_path"] or not os.path.exists(ve["local_evidence_path"]):
            fname = f"{ve['vendor_code']}_{ve['eval_code']}_EvalReport.pdf"
            local_p = os.path.join(STORAGE_DIR, "ISO", "AVL_Vendors", ve["vendor_code"], "Evaluations", fname)
            create_minimal_pdf(local_p, f"Supplier Evaluation Report: {ve['eval_code']}", f"Vendor: {ve['vendor_code']} - Section 3.2 & 3.3")
            up_res = syno.upload_file(local_p, f"AVL_Vendors/{ve['vendor_code']}/Evaluations", fname)
            c = get_db()
            try:
                c.execute(
                    """
                    UPDATE vendor_evaluations
                    SET evidence_filename = ?, local_evidence_path = ?, synology_evidence_path = ?
                    WHERE id = ?
                    """,
                    (fname, up_res["local_path"], up_res["synology_path"], ve["id"]),
                )
                c.commit()
            finally:
                c.close()


async def periodic_pm_alert_scheduler():
    """Background task that checks 30-day advance PM & AVL notifications automatically."""
    await asyncio.sleep(2)
    while True:
        try:
            check_and_send_pm_notifications(force_resend=False)
            check_and_send_avl_notifications(force_resend=False)
        except Exception:
            pass
        await asyncio.sleep(3600 * 6)  # Check every 6 hours


@app.on_event("startup")
async def on_startup():
    init_db()
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, bootstrap_sample_files_and_synology)
    asyncio.create_task(periodic_pm_alert_scheduler())


# ==========================================
# Helper: Access Control Evaluation (Req #1)
# ==========================================
def can_user_access_doc(user: Dict[str, Any], doc_row: sqlite3.Row) -> bool:
    if not user:
        return False
    role = user.get("role", "Viewer")
    username = user.get("username", "")
    dept = user.get("department", "")

    if role in ("Admin", "DCC"):
        return True

    try:
        allowed_users = json.loads(doc_row["allowed_users"] or "[]")
    except Exception:
        allowed_users = []
    if username in allowed_users:
        return True

    try:
        allowed_roles = json.loads(doc_row["allowed_roles"] or "[]")
    except Exception:
        allowed_roles = []
    try:
        allowed_depts = json.loads(doc_row["allowed_departments"] or '["All"]')
    except Exception:
        allowed_depts = ["All"]

    role_ok = (not allowed_roles) or (role in allowed_roles)
    dept_ok = ("All" in allowed_depts) or (not allowed_depts) or (dept in allowed_depts)

    # If allowed_users is explicitly populated and confidentiality is Confidential, restrict to allowed_users/roles
    if doc_row["confidentiality"] == "Confidential":
        return (username in allowed_users) or (role in allowed_roles and dept in allowed_depts)

    return role_ok and dept_ok


def get_current_user_from_header(request: Request) -> Dict[str, Any]:
    username = request.headers.get("X-Username", "")
    if not username:
        return {"id": 0, "username": "", "full_name": "Guest", "role": "Viewer", "department": ""}
    conn = get_db()
    u = conn.execute("SELECT id, username, full_name, role, department, email FROM users WHERE LOWER(username) = LOWER(?)", (username,)).fetchone()
    conn.close()
    if u:
        return dict(u)
    return {"id": 0, "username": "", "full_name": "Guest", "role": "Viewer", "department": ""}


def require_admin(request: Request) -> Dict[str, Any]:
    user = get_current_user_from_header(request)
    if user.get("role") != "Admin":
        raise HTTPException(status_code=403, detail="เฉพาะผู้ดูแลระบบ (Admin) เท่านั้นที่มีสิทธิ์เข้าถึงส่วนนี้")
    return user


# ==========================================
# 1. Authentication & Users API
# ==========================================
class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
def login(payload: LoginRequest):
    conn = get_db()
    user = conn.execute(
        "SELECT id, username, password_hash, full_name, role, department, email, is_active, must_change_password FROM users WHERE LOWER(username) = LOWER(?)",
        (payload.username.strip(),),
    ).fetchone()
    conn.close()
    if not user or not user["is_active"]:
        raise HTTPException(status_code=401, detail="ไม่พบชื่อผู้ใช้งานนี้ในระบบ")
    if user["password_hash"] != hash_password(payload.password):
        raise HTTPException(status_code=401, detail="รหัสผ่านไม่ถูกต้อง")
    return {
        "success": True,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "full_name": user["full_name"],
            "role": user["role"],
            "department": user["department"],
            "email": user["email"],
            "must_change_password": bool(user["must_change_password"]),
        },
    }


class ChangePasswordRequest(BaseModel):
    username: str
    current_password: str
    new_password: str


@app.post("/api/auth/change-password")
def change_password(payload: ChangePasswordRequest):
    new_pw = payload.new_password.strip()
    if len(new_pw) < 6:
        raise HTTPException(status_code=400, detail="รหัสผ่านใหม่ต้องมีความยาวอย่างน้อย 6 ตัวอักษร")
    if new_pw == payload.current_password:
        raise HTTPException(status_code=400, detail="รหัสผ่านใหม่ต้องไม่ซ้ำกับรหัสผ่านเดิม")

    conn = get_db()
    try:
        user = conn.execute(
            "SELECT id, username, password_hash FROM users WHERE LOWER(username) = LOWER(?)",
            (payload.username.strip(),),
        ).fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="ไม่พบผู้ใช้งาน")
        if user["password_hash"] != hash_password(payload.current_password):
            raise HTTPException(status_code=401, detail="รหัสผ่านปัจจุบันไม่ถูกต้อง")

        conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
            (hash_password(new_pw), user["id"]),
        )
        conn.commit()
    finally:
        conn.close()
    sync_users_state_to_storage(upload_async=True)
    return {"success": True, "message": "เปลี่ยนรหัสผ่านสำเร็จเรียบร้อย"}


@app.get("/api/users")
def list_users(request: Request):
    require_admin(request)
    conn = get_db()
    try:
        rows = conn.execute("SELECT id, username, full_name, role, department, email, is_active, must_change_password, created_at FROM users ORDER BY id ASC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


class CreateUserRequest(BaseModel):
    username: str
    password: str
    full_name: str
    role: str
    department: str
    email: str = ""


@app.post("/api/users")
def create_user(request: Request, payload: CreateUserRequest):
    require_admin(request)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    clean_uname = payload.username.strip()
    if not clean_uname or not payload.password.strip():
        raise HTTPException(status_code=400, detail="กรุณาระบุชื่อผู้ใช้งาน (Username) และรหัสผ่าน")

    last_err = None
    for attempt in range(3):
        conn = get_db()
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS deleted_users (username TEXT PRIMARY KEY, deleted_at TEXT NOT NULL)"
            )
            conn.execute("DELETE FROM deleted_users WHERE LOWER(username) = LOWER(?)", (clean_uname,))
            conn.execute(
                """
                INSERT INTO users (username, password_hash, full_name, role, department, email, is_active, must_change_password, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, 1, ?)
                """,
                (
                    clean_uname,
                    hash_password(payload.password),
                    payload.full_name.strip(),
                    payload.role,
                    payload.department.strip(),
                    payload.email.strip(),
                    now_str,
                ),
            )
            conn.commit()
            last_err = None
            break
        except sqlite3.OperationalError as e:
            last_err = e
            time.sleep(0.5)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"ไม่สามารถสร้างผู้ใช้ได้: {e}")
        finally:
            conn.close()

    if last_err is not None:
        raise HTTPException(status_code=400, detail=f"ไม่สามารถสร้างผู้ใช้ได้: {last_err}")

    sync_users_state_to_storage(upload_async=True)
    return {"success": True, "message": "เพิ่มผู้ใช้งานสำเร็จ (ผู้ใช้จะต้องเปลี่ยนรหัสผ่านเมื่อเข้าสู่ระบบครั้งแรก)"}


@app.delete("/api/users/{user_id}")
def delete_user(user_id: int, request: Request):
    current_admin = require_admin(request)
    conn = get_db()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS deleted_users (username TEXT PRIMARY KEY, deleted_at TEXT NOT NULL)"
        )
        target = conn.execute("SELECT id, username FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="ไม่พบผู้ใช้งาน")
        if target["username"].lower() == (current_admin.get("username") or "").lower():
            raise HTTPException(status_code=400, detail="ไม่สามารถลบบัญชีที่คุณกำลังใช้งานอยู่ได้")

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT OR REPLACE INTO deleted_users (username, deleted_at) VALUES (?, ?)",
            (target["username"], now_str),
        )
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()
    sync_users_state_to_storage(upload_async=True)
    return {"success": True}


# ==========================================
# 2, 3, 4. ISO Master File, Access Control, Upload by Doc Code & Search
# ==========================================
@app.get("/api/documents")
def list_documents(
    request: Request,
    q: str = Query("", description="Search by doc_code or doc_title"),
    category: str = Query("", description="Filter by category"),
    status: str = Query("", description="Filter by status"),
    department: str = Query("", description="Filter by allowed department"),
):
    user = get_current_user_from_header(request)
    conn = get_db()
    sql = "SELECT * FROM documents WHERE 1=1"
    params: List[Any] = []

    if q.strip():
        kw = f"%{q.strip()}%"
        sql += " AND (doc_code LIKE ? OR doc_title LIKE ? OR notes LIKE ?)"
        params.extend([kw, kw, kw])
    if category.strip():
        sql += " AND category = ?"
        params.append(category.strip())
    if status.strip():
        sql += " AND status = ?"
        params.append(status.strip())

    sql += " ORDER BY doc_code ASC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()

    results = []
    for r in rows:
        d = dict(r)
        d["allowed_roles"] = json.loads(r["allowed_roles"] or "[]")
        d["allowed_departments"] = json.loads(r["allowed_departments"] or '["All"]')
        d["allowed_users"] = json.loads(r["allowed_users"] or "[]")
        if department.strip() and department.strip() not in d["allowed_departments"] and "All" not in d["allowed_departments"]:
            continue
        d["has_access"] = can_user_access_doc(user, r)
        results.append(d)

    return results


def generate_next_doc_code(conn: sqlite3.Connection, category: str, dept_code: str) -> str:
    cat = (category or "PM").strip().upper()
    if cat == "QP":
        cat = "PM"
    dept = (dept_code or "DC").strip().upper()
    prefix = f"{cat}-{dept}-"
    rows = conn.execute("SELECT doc_code FROM documents WHERE doc_code LIKE ?", (f"{prefix}%",)).fetchall()
    max_num = 0
    for r in rows:
        code_str = r["doc_code"] or ""
        m = re.match(rf"^{re.escape(prefix)}(\d+)$", code_str.strip().upper())
        if m:
            num = int(m.group(1))
            if num > max_num:
                max_num = num
    return f"{prefix}{max_num + 1:03d}"


@app.get("/api/documents/next-code")
def get_next_document_code(
    category: str = Query("PM"),
    dept_code: str = Query("DC"),
):
    conn = get_db()
    next_code = generate_next_doc_code(conn, category, dept_code)
    conn.close()
    return {"doc_code": next_code}


@app.post("/api/documents")
async def create_or_update_document(
    request: Request,
    doc_id: Optional[int] = Form(None),
    doc_code: str = Form(""),
    doc_title: str = Form(...),
    category: str = Form("PM"),
    dept_code: str = Form("DC"),
    effective_date: str = Form(...),
    revision_no: int = Form(0),
    revision_date: str = Form(...),
    status: str = Form("Active"),
    confidentiality: str = Form("Internal"),
    allowed_roles: str = Form('["Admin","DCC","HR","Maintenance","Viewer"]'),
    allowed_departments: str = Form('["All"]'),
    allowed_users: str = Form("[]"),
    notes: str = Form(""),
    change_summary: str = Form("อัปเดตข้อมูลเอกสาร Master File"),
    file: Optional[UploadFile] = File(None),
):
    user = get_current_user_from_header(request)
    clean_code = doc_code.strip().upper()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = get_db()
    try:
        if not clean_code or clean_code == "AUTO":
            clean_code = generate_next_doc_code(conn, category, dept_code)
        existing_row = None
        if doc_id:
            existing_row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        else:
            existing_row = conn.execute("SELECT * FROM documents WHERE doc_code = ?", (clean_code,)).fetchone()
        existing = dict(existing_row) if existing_row else None
    finally:
        conn.close()

    file_name = existing["file_name"] if existing else ""
    original_name = existing["original_name"] if existing else ""
    local_path = existing["local_path"] if existing else ""
    synology_path = existing["synology_path"] if existing else ""
    synology_synced = existing["synology_synced"] if existing else 0
    sync_msg = "บันทึกข้อมูลเอกสารเรียบร้อย"

    if file and file.filename:
        original_name = file.filename
        # Requirement #3: Force file name to be the Document Code (<doc_code>.<ext>)
        file_name = sanitize_code_filename(clean_code, original_name)
        temp_dir = os.path.join(STORAGE_DIR, "ISO", "Documents")
        os.makedirs(temp_dir, exist_ok=True)
        local_path = os.path.join(temp_dir, file_name)
        file_bytes = await file.read()
        with open(local_path, "wb") as f_out:
            f_out.write(file_bytes)

        # Also keep revision archive copy
        rev_ext = os.path.splitext(file_name)[1]
        rev_archive_name = f"{clean_code}_Rev{int(revision_no):02d}{rev_ext}"
        rev_local_dir = os.path.join(STORAGE_DIR, "ISO", "Documents", "Revisions")
        os.makedirs(rev_local_dir, exist_ok=True)
        with open(os.path.join(rev_local_dir, rev_archive_name), "wb") as f_rev:
            f_rev.write(file_bytes)

        # Upload to Synology Drive (/DATA/ISO/Documents/<doc_code>.<ext>)
        syno = get_synology_client()
        up_res = syno.upload_file(local_path, "Documents", file_name)
        syno.upload_file(os.path.join(rev_local_dir, rev_archive_name), "Documents/Revisions", rev_archive_name)
        synology_path = up_res["synology_path"]
        synology_synced = 1 if up_res["synology_synced"] else 0
        sync_msg = up_res["message"]

    if existing and existing["doc_code"] != clean_code and local_path and os.path.exists(local_path) and not (file and file.filename):
        file_name = sanitize_code_filename(clean_code, local_path)
        new_local = os.path.join(STORAGE_DIR, "ISO", "Documents", file_name)
        os.replace(local_path, new_local)
        local_path = new_local
        syno = get_synology_client()
        up_res = syno.upload_file(local_path, "Documents", file_name)
        synology_path = up_res["synology_path"]
        synology_synced = 1 if up_res["synology_synced"] else 0

    conn = get_db()
    try:
        if existing:
            target_id = existing["id"]
            conn.execute(
                """
                UPDATE documents
                SET doc_code = ?, doc_title = ?, category = ?, effective_date = ?,
                    revision_no = ?, revision_date = ?, status = ?, confidentiality = ?,
                    allowed_roles = ?, allowed_departments = ?, allowed_users = ?,
                    file_name = ?, original_name = ?, local_path = ?, synology_path = ?,
                    synology_synced = ?, uploaded_by = ?, notes = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    clean_code,
                    doc_title.strip(),
                    category,
                    effective_date,
                    revision_no,
                    revision_date,
                    status,
                    confidentiality,
                    allowed_roles,
                    allowed_departments,
                    allowed_users,
                    file_name,
                    original_name,
                    local_path,
                    synology_path,
                    synology_synced,
                    user["username"],
                    notes,
                    now_str,
                    target_id,
                ),
            )
        else:
            cur = conn.execute(
                """
                INSERT INTO documents (
                    doc_code, doc_title, category, effective_date, revision_no, revision_date,
                    status, confidentiality, allowed_roles, allowed_departments, allowed_users,
                    file_name, original_name, local_path, synology_path, synology_synced,
                    uploaded_by, notes, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    clean_code,
                    doc_title.strip(),
                    category,
                    effective_date,
                    revision_no,
                    revision_date,
                    status,
                    confidentiality,
                    allowed_roles,
                    allowed_departments,
                    allowed_users,
                    file_name,
                    original_name,
                    local_path,
                    synology_path,
                    synology_synced,
                    user["username"],
                    notes,
                    now_str,
                ),
            )
            target_id = cur.lastrowid

        # Record revision history
        conn.execute(
            """
            INSERT INTO document_revisions (
                doc_id, doc_code, revision_no, effective_date, revision_date,
                change_summary, file_name, local_path, synology_path, updated_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                target_id,
                clean_code,
                revision_no,
                effective_date,
                revision_date,
                change_summary or f"Revision {revision_no:02d}",
                file_name,
                local_path,
                synology_path,
                user["username"],
                now_str,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "success": True,
        "doc_id": target_id,
        "doc_code": clean_code,
        "file_name": file_name,
        "synology_path": synology_path,
        "synology_synced": bool(synology_synced),
        "message": sync_msg,
    }


@app.get("/api/documents/{doc_id}/revisions")
def get_document_revisions(doc_id: int):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM document_revisions WHERE doc_id = ? ORDER BY revision_no DESC, id DESC",
        (doc_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/documents/{doc_id}/file")
def download_or_preview_document(
    doc_id: int,
    request: Request,
    username: Optional[str] = Query(None),
    download: bool = Query(False),
):
    conn = get_db()
    doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if username:
        u_row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        user = dict(u_row) if u_row else get_current_user_from_header(request)
    else:
        user = get_current_user_from_header(request)
    conn.close()

    if not doc:
        raise HTTPException(status_code=404, detail="ไม่พบเอกสาร")

    if not can_user_access_doc(user, doc):
        raise HTTPException(status_code=403, detail="คุณไม่มีสิทธิ์เข้าถึงเอกสารนี้ตามข้อกำหนดการควบคุมเอกสาร")

    local_path = doc["local_path"]
    if (not local_path or not os.path.exists(local_path)) and doc["synology_path"]:
        fallback_local = os.path.join(STORAGE_DIR, "ISO", "Documents", doc["file_name"] or f"{doc['doc_code']}.pdf")
        syno = get_synology_client()
        if syno.download_file_to_local(doc["synology_path"], fallback_local):
            local_path = fallback_local

    if not local_path or not os.path.exists(local_path):
        raise HTTPException(status_code=404, detail="ยังไม่มีไฟล์แนบสำหรับเอกสารนี้")

    disposition = "attachment" if download else "inline"
    return FileResponse(
        local_path,
        filename=doc["file_name"] or f"{doc['doc_code']}.pdf",
        headers={"Content-Disposition": f'{disposition}; filename="{doc["file_name"]}"'},
    )


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: int):
    conn = get_db()
    conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    conn.commit()
    conn.close()
    return {"success": True}


# ==========================================
# 5. HR Employee History, Application, Training Plan & Records
# ==========================================
@app.get("/api/employees")
def list_employees(q: str = Query("", description="ค้นหาตามชื่อ หรือ รหัสพนักงาน"), department: str = Query("")):
    conn = get_db()
    sql = "SELECT * FROM employees WHERE 1=1"
    params: List[Any] = []
    if q.strip():
        kw = f"%{q.strip()}%"
        sql += " AND (emp_code LIKE ? OR full_name LIKE ? OR position LIKE ?)"
        params.extend([kw, kw, kw])
    if department.strip():
        sql += " AND department = ?"
        params.append(department.strip())
    sql += " ORDER BY emp_code ASC"
    emps = conn.execute(sql, params).fetchall()

    results = []
    for e in emps:
        ed = dict(e)
        t_rows = conn.execute("SELECT * FROM training_records WHERE emp_id = ? ORDER BY planned_date DESC", (e["id"],)).fetchall()
        f_rows = conn.execute("SELECT * FROM employee_files WHERE emp_id = ? ORDER BY uploaded_at DESC", (e["id"],)).fetchall()
        ed["trainings"] = [dict(t) for t in t_rows]
        ed["files"] = [dict(f) for f in f_rows]
        ed["training_total"] = len(t_rows)
        ed["training_completed"] = sum(1 for t in t_rows if t["status"] == "Completed")
        ed["training_planned"] = sum(1 for t in t_rows if t["status"] != "Completed")
        results.append(ed)
    conn.close()
    return results


@app.get("/api/employees/{emp_id}")
def get_employee_dossier(emp_id: int):
    conn = get_db()
    emp = conn.execute("SELECT * FROM employees WHERE id = ?", (emp_id,)).fetchone()
    if not emp:
        conn.close()
        raise HTTPException(status_code=404, detail="ไม่พบข้อมูลพนักงาน")
    ed = dict(emp)
    t_rows = conn.execute("SELECT * FROM training_records WHERE emp_id = ? ORDER BY planned_date DESC", (emp_id,)).fetchall()
    f_rows = conn.execute("SELECT * FROM employee_files WHERE emp_id = ? ORDER BY uploaded_at DESC", (emp_id,)).fetchall()
    conn.close()
    ed["trainings"] = [dict(t) for t in t_rows]
    ed["files"] = [dict(f) for f in f_rows]
    return ed


@app.post("/api/employees")
async def save_employee(
    emp_id: Optional[int] = Form(None),
    emp_code: str = Form(...),
    full_name: str = Form(...),
    department: str = Form(...),
    position: str = Form(...),
    hire_date: str = Form(...),
    email: str = Form(""),
    phone: str = Form(""),
    status: str = Form("Active"),
    notes: str = Form(""),
    application_file: Optional[UploadFile] = File(None),
):
    clean_code = emp_code.strip().upper()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    try:
        existing_row = None
        if emp_id:
            existing_row = conn.execute("SELECT * FROM employees WHERE id = ?", (emp_id,)).fetchone()
        else:
            existing_row = conn.execute("SELECT * FROM employees WHERE emp_code = ?", (clean_code,)).fetchone()
        existing = dict(existing_row) if existing_row else None
    finally:
        conn.close()

    app_fname = existing["application_file_name"] if existing else ""
    app_local = existing["application_local_path"] if existing else ""
    app_syno = existing["application_synology_path"] if existing else ""
    app_synced = existing["application_synced"] if existing else 0
    app_up_at = existing["application_uploaded_at"] if existing else ""
    msg = "บันทึกประวัติพนักงานสำเร็จ"

    if application_file and application_file.filename:
        _, ext = os.path.splitext(application_file.filename)
        ext = ext.lower() or ".pdf"
        app_fname = f"{clean_code}_Application{ext}"
        local_dir = os.path.join(STORAGE_DIR, "ISO", "HR_Employees", clean_code, "Application")
        os.makedirs(local_dir, exist_ok=True)
        app_local = os.path.join(local_dir, app_fname)
        with open(app_local, "wb") as f_out:
            f_out.write(await application_file.read())

        syno = get_synology_client()
        up_res = syno.upload_file(app_local, f"HR_Employees/{clean_code}/Application", app_fname)
        app_syno = up_res["synology_path"]
        app_synced = 1 if up_res["synology_synced"] else 0
        app_up_at = now_str
        msg = up_res["message"]

    conn = get_db()
    try:
        if existing:
            target_emp_id = existing["id"]
            conn.execute(
                """
                UPDATE employees
                SET emp_code = ?, full_name = ?, department = ?, position = ?, hire_date = ?,
                    email = ?, phone = ?, status = ?, application_file_name = ?,
                    application_local_path = ?, application_synology_path = ?,
                    application_synced = ?, application_uploaded_at = ?, notes = ?
                WHERE id = ?
                """,
                (
                    clean_code,
                    full_name.strip(),
                    department.strip(),
                    position.strip(),
                    hire_date,
                    email.strip(),
                    phone.strip(),
                    status,
                    app_fname,
                    app_local,
                    app_syno,
                    app_synced,
                    app_up_at,
                    notes,
                    target_emp_id,
                ),
            )
        else:
            cur = conn.execute(
                """
                INSERT INTO employees (
                    emp_code, full_name, department, position, hire_date, email, phone, status,
                    application_file_name, application_local_path, application_synology_path,
                    application_synced, application_uploaded_at, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    clean_code,
                    full_name.strip(),
                    department.strip(),
                    position.strip(),
                    hire_date,
                    email.strip(),
                    phone.strip(),
                    status,
                    app_fname,
                    app_local,
                    app_syno,
                    app_synced,
                    app_up_at,
                    notes,
                    now_str,
                ),
            )
            target_emp_id = cur.lastrowid

        conn.commit()
    finally:
        conn.close()
    return {"success": True, "emp_id": target_emp_id, "message": msg}


@app.get("/api/employees/{emp_id}/application-file")
def get_employee_application_file(emp_id: int):
    conn = get_db()
    emp = conn.execute("SELECT * FROM employees WHERE id = ?", (emp_id,)).fetchone()
    conn.close()
    if not emp or not emp["application_local_path"] or not os.path.exists(emp["application_local_path"]):
        raise HTTPException(status_code=404, detail="ไม่พบไฟล์ใบสมัครงาน")
    return FileResponse(emp["application_local_path"], filename=emp["application_file_name"])


@app.post("/api/trainings")
async def save_training_record(
    training_id: Optional[int] = Form(None),
    emp_id: int = Form(...),
    course_code: str = Form(""),
    course_title: str = Form(...),
    training_type: str = Form("In-house"),
    planned_date: str = Form(...),
    actual_date: str = Form(""),
    duration_hours: float = Form(3.0),
    trainer: str = Form(""),
    status: str = Form("Planned"),
    evaluation_result: str = Form("Pending"),
    score: str = Form(""),
    remarks: str = Form(""),
    evidence_file: Optional[UploadFile] = File(None),
):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    try:
        emp_row = conn.execute("SELECT * FROM employees WHERE id = ?", (emp_id,)).fetchone()
        if not emp_row:
            raise HTTPException(status_code=404, detail="ไม่พบข้อมูลพนักงาน")
        emp = dict(emp_row)

        existing_row = None
        if training_id:
            existing_row = conn.execute("SELECT * FROM training_records WHERE id = ?", (training_id,)).fetchone()
        existing = dict(existing_row) if existing_row else None
    finally:
        conn.close()

    ev_fname = existing["evidence_file_name"] if existing else ""
    ev_local = existing["evidence_local_path"] if existing else ""
    ev_syno = existing["evidence_synology_path"] if existing else ""
    ev_synced = existing["evidence_synced"] if existing else 0
    msg = "บันทึกแผน/ประวัติการฝึกอบรมสำเร็จ"

    if evidence_file and evidence_file.filename:
        _, ext = os.path.splitext(evidence_file.filename)
        ext = ext.lower() or ".pdf"
        safe_course = re.sub(r'[\\/*?:"<>|\s]+', "_", (course_code or course_title)[:25])
        ev_fname = f"{emp['emp_code']}_{safe_course}_Evidence{ext}"
        local_dir = os.path.join(STORAGE_DIR, "ISO", "HR_Employees", emp["emp_code"], "Training")
        os.makedirs(local_dir, exist_ok=True)
        ev_local = os.path.join(local_dir, ev_fname)
        with open(ev_local, "wb") as f_out:
            f_out.write(await evidence_file.read())

        syno = get_synology_client()
        up_res = syno.upload_file(ev_local, f"HR_Employees/{emp['emp_code']}/Training", ev_fname)
        ev_syno = up_res["synology_path"]
        ev_synced = 1 if up_res["synology_synced"] else 0
        msg = up_res["message"]

    conn = get_db()
    try:
        if existing:
            conn.execute(
                """
                UPDATE training_records
                SET course_code = ?, course_title = ?, training_type = ?, planned_date = ?,
                    actual_date = ?, duration_hours = ?, trainer = ?, status = ?,
                    evaluation_result = ?, score = ?, evidence_file_name = ?,
                    evidence_local_path = ?, evidence_synology_path = ?, evidence_synced = ?, remarks = ?
                WHERE id = ?
                """,
                (
                    course_code.strip(),
                    course_title.strip(),
                    training_type,
                    planned_date,
                    actual_date,
                    duration_hours,
                    trainer.strip(),
                    status,
                    evaluation_result,
                    score.strip(),
                    ev_fname,
                    ev_local,
                    ev_syno,
                    ev_synced,
                    remarks,
                    training_id,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO training_records (
                    emp_id, course_code, course_title, training_type, planned_date, actual_date,
                    duration_hours, trainer, status, evaluation_result, score,
                    evidence_file_name, evidence_local_path, evidence_synology_path, evidence_synced,
                    remarks, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    emp_id,
                    course_code.strip(),
                    course_title.strip(),
                    training_type,
                    planned_date,
                    actual_date,
                    duration_hours,
                    trainer.strip(),
                    status,
                    evaluation_result,
                    score.strip(),
                    ev_fname,
                    ev_local,
                    ev_syno,
                    ev_synced,
                    remarks,
                    now_str,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return {"success": True, "message": msg}


@app.get("/api/trainings/{training_id}/evidence")
def get_training_evidence_file(training_id: int):
    conn = get_db()
    tr = conn.execute("SELECT * FROM training_records WHERE id = ?", (training_id,)).fetchone()
    conn.close()
    if not tr or not tr["evidence_local_path"] or not os.path.exists(tr["evidence_local_path"]):
        raise HTTPException(status_code=404, detail="ไม่พบไฟล์หลักฐานการฝึกอบรม")
    return FileResponse(tr["evidence_local_path"], filename=tr["evidence_file_name"])


@app.delete("/api/trainings/{training_id}")
def delete_training_record(training_id: int):
    conn = get_db()
    conn.execute("DELETE FROM training_records WHERE id = ?", (training_id,))
    conn.commit()
    conn.close()
    return {"success": True}


# ==========================================
# 7. Equipment (MHE, Rack, etc.) & Preventive Maintenance (PM)
# ==========================================
@app.get("/api/equipment")
def list_equipment(q: str = Query(""), category: str = Query("")):
    conn = get_db()
    sql = "SELECT * FROM equipment WHERE 1=1"
    params: List[Any] = []
    if q.strip():
        kw = f"%{q.strip()}%"
        sql += " AND (equip_code LIKE ? OR equip_name LIKE ? OR location LIKE ? OR brand_model LIKE ?)"
        params.extend([kw, kw, kw, kw])
    if category.strip():
        sql += " AND category = ?"
        params.append(category.strip())
    sql += " ORDER BY equip_code ASC"
    rows = conn.execute(sql, params).fetchall()

    results = []
    for r in rows:
        d = dict(r)
        pms = conn.execute("SELECT * FROM pm_schedules WHERE equip_id = ? ORDER BY planned_date ASC", (r["id"],)).fetchall()
        d["pm_schedules"] = [dict(p) for p in pms]
        results.append(d)
    conn.close()
    return results


class EquipmentPayload(BaseModel):
    id: Optional[int] = None
    equip_code: str
    equip_name: str
    category: str = "MHE"
    brand_model: str = ""
    serial_no: str = ""
    location: str = ""
    purchase_install_date: str
    warranty_expiry: str = ""
    vendor: str = ""
    responsible_person: str = ""
    responsible_email: str = ""
    status: str = "Active"
    notes: str = ""


@app.post("/api/equipment")
def save_equipment(payload: EquipmentPayload):
    conn = get_db()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    clean_code = payload.equip_code.strip().upper()

    if payload.id:
        conn.execute(
            """
            UPDATE equipment
            SET equip_code = ?, equip_name = ?, category = ?, brand_model = ?, serial_no = ?,
                location = ?, purchase_install_date = ?, warranty_expiry = ?, vendor = ?,
                responsible_person = ?, responsible_email = ?, status = ?, notes = ?
            WHERE id = ?
            """,
            (
                clean_code,
                payload.equip_name.strip(),
                payload.category,
                payload.brand_model.strip(),
                payload.serial_no.strip(),
                payload.location.strip(),
                payload.purchase_install_date,
                payload.warranty_expiry,
                payload.vendor.strip(),
                payload.responsible_person.strip(),
                payload.responsible_email.strip(),
                payload.status,
                payload.notes,
                payload.id,
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO equipment (
                equip_code, equip_name, category, brand_model, serial_no, location,
                purchase_install_date, warranty_expiry, vendor, responsible_person,
                responsible_email, status, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clean_code,
                payload.equip_name.strip(),
                payload.category,
                payload.brand_model.strip(),
                payload.serial_no.strip(),
                payload.location.strip(),
                payload.purchase_install_date,
                payload.warranty_expiry,
                payload.vendor.strip(),
                payload.responsible_person.strip(),
                payload.responsible_email.strip(),
                payload.status,
                payload.notes,
                now_str,
            ),
        )
    conn.commit()
    conn.close()
    return {"success": True, "message": "บันทึกทะเบียนเครื่องจักร/อุปกรณ์สำเร็จ"}


@app.get("/api/pm")
def list_pm_schedules(status: str = Query(""), equip_id: Optional[int] = Query(None)):
    conn = get_db()
    sql = """
        SELECT p.*, e.equip_code, e.equip_name, e.category, e.location, e.responsible_person, e.responsible_email
        FROM pm_schedules p
        JOIN equipment e ON p.equip_id = e.id
        WHERE 1=1
    """
    params: List[Any] = []
    if status.strip():
        sql += " AND p.status = ?"
        params.append(status.strip())
    if equip_id:
        sql += " AND p.equip_id = ?"
        params.append(equip_id)
    sql += " ORDER BY p.planned_date ASC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()

    today = datetime.now().date()
    results = []
    for r in rows:
        d = dict(r)
        try:
            p_dt = datetime.strptime(r["planned_date"], "%Y-%m-%d").date()
            d["days_remaining"] = (p_dt - today).days
        except Exception:
            d["days_remaining"] = 999
        results.append(d)
    return results


@app.post("/api/pm")
async def save_pm_schedule(
    pm_id: Optional[int] = Form(None),
    equip_id: int = Form(...),
    pm_code: str = Form(...),
    pm_title: str = Form(...),
    frequency: str = Form("Quarterly"),
    planned_date: str = Form(...),
    notify_days_before: int = Form(30),
    notify_emails: str = Form(""),
    status: str = Form("Scheduled"),
    actual_date: str = Form(""),
    performed_by: str = Form(""),
    result_summary: str = Form(""),
    cost: float = Form(0.0),
    evidence_file: Optional[UploadFile] = File(None),
):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    try:
        eq_row = conn.execute("SELECT * FROM equipment WHERE id = ?", (equip_id,)).fetchone()
        if not eq_row:
            raise HTTPException(status_code=404, detail="ไม่พบข้อมูลอุปกรณ์")
        eq = dict(eq_row)

        existing_row = None
        if pm_id:
            existing_row = conn.execute("SELECT * FROM pm_schedules WHERE id = ?", (pm_id,)).fetchone()
        existing = dict(existing_row) if existing_row else None
    finally:
        conn.close()

    ev_fname = existing["evidence_file_name"] if existing else ""
    ev_local = existing["evidence_local_path"] if existing else ""
    ev_syno = existing["evidence_synology_path"] if existing else ""
    ev_synced = existing["evidence_synced"] if existing else 0
    msg = "บันทึกแผนและข้อมูลการซ่อมบำรุงรักษา (PM) สำเร็จ"

    if evidence_file and evidence_file.filename:
        _, ext = os.path.splitext(evidence_file.filename)
        ext = ext.lower() or ".pdf"
        safe_pm = re.sub(r'[\\/*?:"<>|\s]+', "_", pm_code.strip())
        ev_fname = f"{eq['equip_code']}_{safe_pm}_Evidence{ext}"
        local_dir = os.path.join(STORAGE_DIR, "ISO", "MHE_Maintenance", eq["equip_code"])
        os.makedirs(local_dir, exist_ok=True)
        ev_local = os.path.join(local_dir, ev_fname)
        with open(ev_local, "wb") as f_out:
            f_out.write(await evidence_file.read())

        syno = get_synology_client()
        up_res = syno.upload_file(ev_local, f"MHE_Maintenance/{eq['equip_code']}", ev_fname)
        ev_syno = up_res["synology_path"]
        ev_synced = 1 if up_res["synology_synced"] else 0
        msg = up_res["message"]

    conn = get_db()
    try:
        if existing:
            conn.execute(
                """
                UPDATE pm_schedules
                SET equip_id = ?, pm_code = ?, pm_title = ?, frequency = ?, planned_date = ?,
                    notify_days_before = ?, notify_emails = ?, status = ?, actual_date = ?,
                    performed_by = ?, result_summary = ?, cost = ?,
                    evidence_file_name = ?, evidence_local_path = ?, evidence_synology_path = ?, evidence_synced = ?
                WHERE id = ?
                """,
                (
                    equip_id,
                    pm_code.strip().upper(),
                    pm_title.strip(),
                    frequency,
                    planned_date,
                    notify_days_before,
                    notify_emails.strip() or eq["responsible_email"],
                    status,
                    actual_date,
                    performed_by.strip(),
                    result_summary.strip(),
                    cost,
                    ev_fname,
                    ev_local,
                    ev_syno,
                    ev_synced,
                    pm_id,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO pm_schedules (
                    equip_id, pm_code, pm_title, frequency, planned_date, notify_days_before,
                    notify_emails, status, actual_date, performed_by, result_summary, cost,
                    evidence_file_name, evidence_local_path, evidence_synology_path, evidence_synced, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    equip_id,
                    pm_code.strip().upper(),
                    pm_title.strip(),
                    frequency,
                    planned_date,
                    notify_days_before,
                    notify_emails.strip() or eq["responsible_email"],
                    status,
                    actual_date,
                    performed_by.strip(),
                    result_summary.strip(),
                    cost,
                    ev_fname,
                    ev_local,
                    ev_syno,
                    ev_synced,
                    now_str,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return {"success": True, "message": msg}


@app.get("/api/pm/{pm_id}/evidence")
def get_pm_evidence_file(pm_id: int):
    conn = get_db()
    pm = conn.execute("SELECT * FROM pm_schedules WHERE id = ?", (pm_id,)).fetchone()
    conn.close()
    if not pm or not pm["evidence_local_path"] or not os.path.exists(pm["evidence_local_path"]):
        raise HTTPException(status_code=404, detail="ไม่พบไฟล์หลักฐานการทำ PM")
    return FileResponse(pm["evidence_local_path"], filename=pm["evidence_file_name"])


@app.post("/api/pm/check-alerts")
def trigger_pm_alerts_now(force: bool = Query(False)):
    result = check_and_send_pm_notifications(force_resend=force)
    return result


# ==========================================
# 8. Approved Vendor List (AVL) & Supplier Evaluation (Manual Sec 3.2 & 3.3)
# ==========================================
def calculate_avl_grade_and_action(
    score_quality: float,
    score_service_sla: float,
    score_cost: float,
    score_cooperation: float,
) -> Dict[str, Any]:
    """
    Calculate Supplier Evaluation total score, pass criteria flag (Section 3.2),
    and Grade + Review Action (Section 3.3 of the Manual).
    """
    sq = max(0.0, min(35.0, float(score_quality or 0.0)))
    ss = max(0.0, min(30.0, float(score_service_sla or 0.0)))
    sc = max(0.0, min(20.0, float(score_cost or 0.0)))
    sco = max(0.0, min(15.0, float(score_cooperation or 0.0)))
    total = round(sq + ss + sc + sco, 2)

    # Section 3.2 minimum expectations:
    # Criterion 1 (Quality 35%) >= 28%
    # Criterion 2 (After-Sales & SLA 30%) >= 24%
    # Total >= 80%
    pass_flag = 1 if (sq >= 28.0 and ss >= 24.0 and total >= 80.0) else 0

    if total >= 90.0:
        grade = "A"
        action = "ระดับ A (90-100%): คุณภาพดีเยี่ยม คงสถานะพันธมิตรหลักและให้สิทธิพิเศษพิจารณาเป็นเจ้าแรกในการประมูลรอบถัดไป"
        recommended_status = "Approved"
    elif total >= 80.0:
        grade = "B"
        action = "ระดับ B (80-89%): คุณภาพอยู่ในเกณฑ์มาตรฐาน แจ้ง Feedback ถึงจุดที่สามารถพัฒนาต่อได้"
        recommended_status = "Approved"
    elif total >= 70.0:
        grade = "C"
        action = "ระดับ C (70-79%): ต้องออกเอกสารแจ้งเตือน (Corrective Action Request - CAR) ให้ผู้จัดจำหน่ายทำแผนแก้ไขและปรับปรุงปัญหาภายใน 30-60 วัน"
        recommended_status = "Conditional (CAR)"
    else:
        grade = "D"
        action = "ระดับ D (ต่ำกว่า 70%): ระงับการสั่งซื้อใหม่ชั่วคราว หากไม่มีการปรับปรุงตามกำหนดให้พิจารณาถอดชื่อออกจากรายชื่อผู้ค้า (Remove from AVL)"
        recommended_status = "Suspended / Removed"

    # Note if total >= 80 but sub-criterion (<28% or <24%) missed
    warnings = []
    if sq < 28.0:
        warnings.append(f"คะแนนข้อ 1 คุณภาพสินค้า/งานติดตั้ง ({sq}%) ต่ำกว่าเกณฑ์ขั้นต่ำที่คาดหวัง (28%)")
    if ss < 24.0:
        warnings.append(f"คะแนนข้อ 2 บริการหลังการขาย & SLA ({ss}%) ต่ำกว่าเกณฑ์ขั้นต่ำที่คาดหวัง (24%)")
    if warnings and total >= 80.0:
        action += " [หมายเหตุ: " + "; ".join(warnings) + " ควรแจ้งประเด็นให้ปรับปรุง]"

    return {
        "score_quality": sq,
        "score_service_sla": ss,
        "score_cost": sc,
        "score_cooperation": sco,
        "total_score": total,
        "pass_criteria_flag": pass_flag,
        "grade": grade,
        "review_action": action,
        "recommended_status": recommended_status,
    }


@app.get("/api/vendors")
def list_vendors(
    q: str = Query(""),
    category: str = Query(""),
    avl_status: str = Query(""),
    grade: str = Query(""),
):
    conn = get_db()
    try:
        cnt = conn.execute("SELECT COUNT(*) as c FROM vendors").fetchone()["c"]
        if cnt == 0:
            conn.close()
            init_db()
            bootstrap_sample_files_and_synology()
            conn = get_db()
    except Exception:
        conn.close()
        init_db()
        bootstrap_sample_files_and_synology()
        conn = get_db()

    sql = "SELECT * FROM vendors WHERE 1=1"
    params: List[Any] = []
    if q.strip():
        kw = f"%{q.strip()}%"
        sql += " AND (vendor_code LIKE ? OR vendor_name LIKE ? OR products_services LIKE ? OR contact_person LIKE ?)"
        params.extend([kw, kw, kw, kw])
    if category.strip():
        sql += " AND category = ?"
        params.append(category.strip())
    if avl_status.strip():
        sql += " AND avl_status = ?"
        params.append(avl_status.strip())
    if grade.strip():
        sql += " AND latest_grade = ?"
        params.append(grade.strip())
    sql += " ORDER BY vendor_code ASC"
    rows = conn.execute(sql, params).fetchall()

    results = []
    for r in rows:
        d = dict(r)
        evals = conn.execute(
            "SELECT * FROM vendor_evaluations WHERE vendor_id = ? ORDER BY planned_date DESC, id DESC",
            (r["id"],),
        ).fetchall()
        d["evaluations"] = [dict(ev) for ev in evals]
        results.append(d)
    conn.close()
    return results


def get_vendor_category_abbr(category: str) -> str:
    c = (category or "").strip().lower()
    if "mhe" in c or "storage" in c:
        return "MHE"
    if "fire" in c or "fps" in c:
        return "FPS"
    return "OTH"


def generate_next_vendor_code(conn: sqlite3.Connection, category: str) -> str:
    abbr = get_vendor_category_abbr(category)
    prefix = f"AVL-{abbr}-"
    rows = conn.execute("SELECT vendor_code FROM vendors WHERE vendor_code LIKE ?", (f"{prefix}%",)).fetchall()
    max_num = 0
    for r in rows:
        code_str = r["vendor_code"] or ""
        m = re.match(rf"^{re.escape(prefix)}(\d+)$", code_str.strip().upper())
        if m:
            num = int(m.group(1))
            if num > max_num:
                max_num = num
    return f"{prefix}{max_num + 1:03d}"


@app.get("/api/vendors/next-code")
def get_next_vendor_code_api(category: str = Query("MHE & Storage Systems")):
    conn = get_db()
    next_code = generate_next_vendor_code(conn, category)
    conn.close()
    return {"vendor_code": next_code}


@app.post("/api/vendors")
async def save_vendor(
    vendor_id: Optional[int] = Form(None),
    vendor_code: str = Form(""),
    vendor_name: str = Form(...),
    category: str = Form("MHE & Storage Systems"),
    products_services: str = Form(""),
    contact_person: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    address: str = Form(""),
    tax_id: str = Form(""),
    iso_certified: str = Form(""),
    avl_status: str = Form("Approved"),
    next_eval_date: str = Form(""),
    notes: str = Form(""),
    cert_file: Optional[UploadFile] = File(None),
):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    clean_code = vendor_code.strip().upper()
    conn = get_db()
    try:
        if not clean_code or clean_code == "AUTO":
            clean_code = generate_next_vendor_code(conn, category)

        existing_row = None
        if vendor_id:
            existing_row = conn.execute("SELECT * FROM vendors WHERE id = ?", (vendor_id,)).fetchone()
        else:
            existing_row = conn.execute("SELECT * FROM vendors WHERE vendor_code = ?", (clean_code,)).fetchone()
        existing = dict(existing_row) if existing_row else None
    finally:
        conn.close()

    cert_fname = existing["cert_filename"] if existing else ""
    cert_local = existing["local_cert_path"] if existing else ""
    cert_syno = existing["synology_cert_path"] if existing else ""
    msg = "บันทึกข้อมูลผู้จัดจำหน่าย (Approved Vendor List - AVL) สำเร็จ"

    if cert_file and cert_file.filename:
        _, ext = os.path.splitext(cert_file.filename)
        ext = ext.lower() or ".pdf"
        cert_fname = f"{clean_code}_Certificate{ext}"
        local_dir = os.path.join(STORAGE_DIR, "ISO", "AVL_Vendors", clean_code)
        os.makedirs(local_dir, exist_ok=True)
        cert_local = os.path.join(local_dir, cert_fname)
        with open(cert_local, "wb") as f_out:
            f_out.write(await cert_file.read())

        syno = get_synology_client()
        up_res = syno.upload_file(cert_local, f"AVL_Vendors/{clean_code}", cert_fname)
        cert_syno = up_res["synology_path"]
        msg = up_res["message"]

    conn = get_db()
    try:
        if existing:
            target_id = existing["id"]
            conn.execute(
                """
                UPDATE vendors
                SET vendor_code = ?, vendor_name = ?, category = ?, products_services = ?,
                    contact_person = ?, phone = ?, email = ?, address = ?, tax_id = ?,
                    iso_certified = ?, avl_status = ?, next_eval_date = ?,
                    cert_filename = ?, local_cert_path = ?, synology_cert_path = ?, notes = ?
                WHERE id = ?
                """,
                (
                    clean_code,
                    vendor_name.strip(),
                    category.strip(),
                    products_services.strip(),
                    contact_person.strip(),
                    phone.strip(),
                    email.strip(),
                    address.strip(),
                    tax_id.strip(),
                    iso_certified.strip(),
                    avl_status.strip(),
                    next_eval_date.strip(),
                    cert_fname,
                    cert_local,
                    cert_syno,
                    notes.strip(),
                    target_id,
                ),
            )
        else:
            cur = conn.execute(
                """
                INSERT INTO vendors (
                    vendor_code, vendor_name, category, products_services, contact_person,
                    phone, email, address, tax_id, iso_certified, avl_status, next_eval_date,
                    cert_filename, local_cert_path, synology_cert_path, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    clean_code,
                    vendor_name.strip(),
                    category.strip(),
                    products_services.strip(),
                    contact_person.strip(),
                    phone.strip(),
                    email.strip(),
                    address.strip(),
                    tax_id.strip(),
                    iso_certified.strip(),
                    avl_status.strip(),
                    next_eval_date.strip(),
                    cert_fname,
                    cert_local,
                    cert_syno,
                    notes.strip(),
                    now_str,
                ),
            )
            target_id = cur.lastrowid

        conn.commit()
    finally:
        conn.close()
    return {"success": True, "vendor_id": target_id, "message": msg}


@app.get("/api/vendors/{vendor_id}/cert")
def get_vendor_cert_file(vendor_id: int):
    conn = get_db()
    v = conn.execute("SELECT * FROM vendors WHERE id = ?", (vendor_id,)).fetchone()
    conn.close()
    if not v or not v["local_cert_path"] or not os.path.exists(v["local_cert_path"]):
        raise HTTPException(status_code=404, detail="ไม่พบไฟล์เอกสารรับรองของผู้จัดจำหน่าย")
    return FileResponse(v["local_cert_path"], filename=v["cert_filename"])


@app.delete("/api/vendors/{vendor_id}")
def delete_vendor(vendor_id: int):
    conn = get_db()
    conn.execute("DELETE FROM vendor_evaluations WHERE vendor_id = ?", (vendor_id,))
    conn.execute("DELETE FROM vendors WHERE id = ?", (vendor_id,))
    conn.commit()
    conn.close()
    return {"success": True, "message": "ลบข้อมูลผู้จัดจำหน่ายเรียบร้อย"}


@app.get("/api/avl-evaluations")
def list_avl_evaluations(
    status: str = Query(""),
    vendor_id: Optional[int] = Query(None),
    grade: str = Query(""),
):
    conn = get_db()
    sql = """
        SELECT ve.*, v.vendor_code, v.vendor_name, v.category, v.products_services, v.avl_status, v.contact_person, v.email AS vendor_email
        FROM vendor_evaluations ve
        JOIN vendors v ON ve.vendor_id = v.id
        WHERE 1=1
    """
    params: List[Any] = []
    if status.strip():
        sql += " AND ve.status = ?"
        params.append(status.strip())
    if vendor_id:
        sql += " AND ve.vendor_id = ?"
        params.append(vendor_id)
    if grade.strip():
        sql += " AND ve.grade = ?"
        params.append(grade.strip())
    sql += " ORDER BY ve.planned_date ASC, ve.id DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()

    today = datetime.now().date()
    results = []
    for r in rows:
        d = dict(r)
        try:
            p_dt = datetime.strptime(r["planned_date"], "%Y-%m-%d").date()
            d["days_remaining"] = (p_dt - today).days
        except Exception:
            d["days_remaining"] = 999
        results.append(d)
    return results


def generate_next_avl_eval_code(conn: sqlite3.Connection, year_str: str = "") -> str:
    yr = (year_str or "").strip()[:4]
    if not re.match(r"^\d{4}$", yr):
        yr = str(datetime.now().year)
    prefix = f"AVL-EVAL-{yr}-"
    rows = conn.execute("SELECT eval_code FROM vendor_evaluations WHERE eval_code LIKE ?", (f"{prefix}%",)).fetchall()
    max_num = 0
    for r in rows:
        code_str = r["eval_code"] or ""
        m = re.match(rf"^{re.escape(prefix)}(\d+)$", code_str.strip().upper())
        if m:
            num = int(m.group(1))
            if num > max_num:
                max_num = num
    return f"{prefix}{max_num + 1:03d}"


@app.get("/api/avl-evaluations/next-code")
def get_next_avl_eval_code_api(year: str = Query("")):
    conn = get_db()
    next_code = generate_next_avl_eval_code(conn, year)
    conn.close()
    return {"eval_code": next_code}


@app.post("/api/avl-evaluations")
async def save_avl_evaluation(
    eval_id: Optional[int] = Form(None),
    vendor_id: int = Form(...),
    eval_code: str = Form(""),
    eval_period: str = Form(...),
    frequency: str = Form("Quarterly"),
    planned_date: str = Form(...),
    notify_days_before: int = Form(30),
    notify_emails: str = Form(""),
    status: str = Form("Scheduled"),
    actual_date: str = Form(""),
    evaluator: str = Form(""),
    score_quality: float = Form(0.0),
    score_service_sla: float = Form(0.0),
    score_cost: float = Form(0.0),
    score_cooperation: float = Form(0.0),
    car_no: str = Form(""),
    car_due_date: str = Form(""),
    remarks: str = Form(""),
    update_vendor_status: bool = Form(True),
    evidence_file: Optional[UploadFile] = File(None),
):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    try:
        if not eval_code.strip() or eval_code.strip().upper() == "AUTO":
            eval_code = generate_next_avl_eval_code(conn, planned_date[:4])
        vendor_row = conn.execute("SELECT * FROM vendors WHERE id = ?", (vendor_id,)).fetchone()
        if not vendor_row:
            raise HTTPException(status_code=404, detail="ไม่พบข้อมูลผู้จัดจำหน่าย (Vendor)")
        vendor = dict(vendor_row)

        existing_row = None
        if eval_id:
            existing_row = conn.execute("SELECT * FROM vendor_evaluations WHERE id = ?", (eval_id,)).fetchone()
        existing = dict(existing_row) if existing_row else None
    finally:
        conn.close()

    ev_fname = existing["evidence_filename"] if existing else ""
    ev_local = existing["local_evidence_path"] if existing else ""
    ev_syno = existing["synology_evidence_path"] if existing else ""
    msg = "บันทึกแผน/ผลการประเมินผู้จัดจำหน่าย (AVL Evaluation) สำเร็จ"

    if evidence_file and evidence_file.filename:
        _, ext = os.path.splitext(evidence_file.filename)
        ext = ext.lower() or ".pdf"
        safe_code = re.sub(r'[\\/*?:"<>|\s]+', "_", eval_code.strip())
        ev_fname = f"{vendor['vendor_code']}_{safe_code}_EvalReport{ext}"
        local_dir = os.path.join(STORAGE_DIR, "ISO", "AVL_Vendors", vendor["vendor_code"], "Evaluations")
        os.makedirs(local_dir, exist_ok=True)
        ev_local = os.path.join(local_dir, ev_fname)
        with open(ev_local, "wb") as f_out:
            f_out.write(await evidence_file.read())

        syno = get_synology_client()
        up_res = syno.upload_file(ev_local, f"AVL_Vendors/{vendor['vendor_code']}/Evaluations", ev_fname)
        ev_syno = up_res["synology_path"]
        msg = up_res["message"]

    # Calculate score, grade, and review action if Completed or if scores > 0
    has_scores = (score_quality > 0 or score_service_sla > 0 or score_cost > 0 or score_cooperation > 0 or status == "Completed")
    if has_scores:
        calc = calculate_avl_grade_and_action(score_quality, score_service_sla, score_cost, score_cooperation)
        sq = calc["score_quality"]
        ss = calc["score_service_sla"]
        sc = calc["score_cost"]
        sco = calc["score_cooperation"]
        tot = calc["total_score"]
        pflag = calc["pass_criteria_flag"]
        grd = calc["grade"]
        ract = calc["review_action"]
        rec_status = calc["recommended_status"]
    else:
        sq, ss, sc, sco, tot, pflag = 0.0, 0.0, 0.0, 0.0, 0.0, 1
        grd = "-"
        ract = "รอการประเมินตามแผน ข้อ 3.3 (ตั้งแจ้งเตือนล่วงหน้า 30 วัน)"
        rec_status = vendor["avl_status"]

    clean_eval_code = eval_code.strip().upper()
    final_emails = notify_emails.strip() or "maintenance@sunlogis.com, watcharagorn@sunlogis.com"

    conn = get_db()
    try:
        if existing:
            conn.execute(
                """
                UPDATE vendor_evaluations
                SET vendor_id = ?, eval_code = ?, eval_period = ?, frequency = ?, planned_date = ?,
                    notify_days_before = ?, notify_emails = ?, status = ?, actual_date = ?,
                    evaluator = ?, score_quality = ?, score_service_sla = ?, score_cost = ?,
                    score_cooperation = ?, total_score = ?, pass_criteria_flag = ?,
                    grade = ?, review_action = ?, car_no = ?, car_due_date = ?,
                    remarks = ?, evidence_filename = ?, local_evidence_path = ?, synology_evidence_path = ?
                WHERE id = ?
                """,
                (
                    vendor_id,
                    clean_eval_code,
                    eval_period.strip(),
                    frequency,
                    planned_date,
                    notify_days_before,
                    final_emails,
                    status,
                    actual_date,
                    evaluator.strip(),
                    sq,
                    ss,
                    sc,
                    sco,
                    tot,
                    pflag,
                    grd,
                    ract,
                    car_no.strip(),
                    car_due_date.strip(),
                    remarks.strip(),
                    ev_fname,
                    ev_local,
                    ev_syno,
                    eval_id,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO vendor_evaluations (
                    vendor_id, eval_code, eval_period, frequency, planned_date,
                    notify_days_before, notify_emails, status, actual_date, evaluator,
                    score_quality, score_service_sla, score_cost, score_cooperation,
                    total_score, pass_criteria_flag, grade, review_action,
                    car_no, car_due_date, remarks,
                    evidence_filename, local_evidence_path, synology_evidence_path, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    vendor_id,
                    clean_eval_code,
                    eval_period.strip(),
                    frequency,
                    planned_date,
                    notify_days_before,
                    final_emails,
                    status,
                    actual_date,
                    evaluator.strip(),
                    sq,
                    ss,
                    sc,
                    sco,
                    tot,
                    pflag,
                    grd,
                    ract,
                    car_no.strip(),
                    car_due_date.strip(),
                    remarks.strip(),
                    ev_fname,
                    ev_local,
                    ev_syno,
                    now_str,
                ),
            )

        # Update parent vendor's latest evaluation info
        if status == "Completed" or has_scores:
            eval_dt_to_save = actual_date or planned_date
            new_avl_st = rec_status if update_vendor_status else vendor["avl_status"]
            conn.execute(
                """
                UPDATE vendors
                SET latest_score = ?, latest_grade = ?, latest_review_action = ?,
                    last_eval_date = ?, avl_status = ?
                WHERE id = ?
                """,
                (tot, grd, ract, eval_dt_to_save, new_avl_st, vendor_id),
            )
        else:
            # Scheduled future evaluation -> update next_eval_date on vendor if empty or earlier
            conn.execute(
                "UPDATE vendors SET next_eval_date = ? WHERE id = ?",
                (planned_date, vendor_id),
            )

        conn.commit()
    finally:
        conn.close()
    return {
        "success": True,
        "message": msg,
        "total_score": tot,
        "grade": grd,
        "review_action": ract,
    }


@app.get("/api/avl-evaluations/{eval_id}/evidence")
def get_avl_evaluation_evidence(eval_id: int):
    conn = get_db()
    ev = conn.execute("SELECT * FROM vendor_evaluations WHERE id = ?", (eval_id,)).fetchone()
    conn.close()
    if not ev or not ev["local_evidence_path"] or not os.path.exists(ev["local_evidence_path"]):
        raise HTTPException(status_code=404, detail="ไม่พบไฟล์หลักฐานการประเมินผู้จัดจำหน่าย")
    return FileResponse(ev["local_evidence_path"], filename=ev["evidence_filename"])


@app.delete("/api/avl-evaluations/{eval_id}")
def delete_avl_evaluation(eval_id: int):
    conn = get_db()
    conn.execute("DELETE FROM vendor_evaluations WHERE id = ?", (eval_id,))
    conn.commit()
    conn.close()
    return {"success": True}


@app.post("/api/avl-evaluations/check-alerts")
def trigger_avl_alerts_now(force: bool = Query(False)):
    result = check_and_send_avl_notifications(force_resend=force)
    return result


@app.get("/api/version")
def get_app_version():
    v_file = os.path.join(BASE_DIR, "version.json")
    if os.path.exists(v_file):
        try:
            with open(v_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"version": "1.1", "updated_at": datetime.now().strftime("%Y-%m-%d")}


@app.get("/api/email-logs")
def get_email_logs():
    conn = get_db()
    rows = conn.execute("SELECT * FROM email_logs ORDER BY id DESC LIMIT 50").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ==========================================
# 6. Synology Drive Status, Explorer & Settings (Admin Only)
# ==========================================
@app.get("/api/synology/status")
def synology_status(request: Request, subfolder: str = Query("")):
    require_admin(request)
    syno = get_synology_client()
    return syno.list_remote_files(subfolder=subfolder)


@app.post("/api/synology/sync-all")
def sync_all_to_synology(request: Request):
    require_admin(request)
    syno = get_synology_client()
    conn = get_db()
    try:
        docs = [dict(r) for r in conn.execute("SELECT id, doc_code, file_name, local_path FROM documents WHERE local_path != ''").fetchall()]
        emps = [dict(r) for r in conn.execute("SELECT id, emp_code, application_file_name, application_local_path FROM employees WHERE application_local_path != ''").fetchall()]
        trs = [
            dict(r)
            for r in conn.execute(
                "SELECT t.id, t.evidence_file_name, t.evidence_local_path, e.emp_code FROM training_records t JOIN employees e ON t.emp_id = e.id WHERE t.evidence_local_path != ''"
            ).fetchall()
        ]
        pms = [
            dict(r)
            for r in conn.execute(
                "SELECT p.id, p.evidence_file_name, p.evidence_local_path, e.equip_code FROM pm_schedules p JOIN equipment e ON p.equip_id = e.id WHERE p.evidence_local_path != ''"
            ).fetchall()
        ]
    finally:
        conn.close()

    synced_count = 0

    # 1. Sync Documents
    for d in docs:
        if os.path.exists(d["local_path"]):
            res = syno.upload_file(d["local_path"], "Documents", d["file_name"] or f"{d['doc_code']}.pdf")
            if res["synology_synced"]:
                synced_count += 1
                c = get_db()
                try:
                    c.execute(
                        "UPDATE documents SET synology_path = ?, synology_synced = 1 WHERE id = ?",
                        (res["synology_path"], d["id"]),
                    )
                    c.commit()
                finally:
                    c.close()

    # 2. Sync Employee Applications & Training Evidence
    for e in emps:
        if os.path.exists(e["application_local_path"]):
            res = syno.upload_file(e["application_local_path"], f"HR_Employees/{e['emp_code']}/Application", e["application_file_name"])
            if res["synology_synced"]:
                synced_count += 1
                c = get_db()
                try:
                    c.execute(
                        "UPDATE employees SET application_synology_path = ?, application_synced = 1 WHERE id = ?",
                        (res["synology_path"], e["id"]),
                    )
                    c.commit()
                finally:
                    c.close()

    for t in trs:
        if os.path.exists(t["evidence_local_path"]):
            res = syno.upload_file(t["evidence_local_path"], f"HR_Employees/{t['emp_code']}/Training", t["evidence_file_name"])
            if res["synology_synced"]:
                synced_count += 1
                c = get_db()
                try:
                    c.execute(
                        "UPDATE training_records SET evidence_synology_path = ?, evidence_synced = 1 WHERE id = ?",
                        (res["synology_path"], t["id"]),
                    )
                    c.commit()
                finally:
                    c.close()

    # 3. Sync PM Evidence
    for p in pms:
        if os.path.exists(p["evidence_local_path"]):
            res = syno.upload_file(p["evidence_local_path"], f"MHE_Maintenance/{p['equip_code']}", p["evidence_file_name"])
            if res["synology_synced"]:
                synced_count += 1
                c = get_db()
                try:
                    c.execute(
                        "UPDATE pm_schedules SET evidence_synology_path = ?, evidence_synced = 1 WHERE id = ?",
                        (res["synology_path"], p["id"]),
                    )
                    c.commit()
                finally:
                    c.close()

    return {"success": True, "synced_count": synced_count, "message": f"ซิงค์ไฟล์ขึ้น Synology Drive สำเร็จ {synced_count} รายการ"}


@app.get("/api/settings")
def get_settings(request: Request):
    require_admin(request)
    return get_settings_dict()


@app.post("/api/settings")
def save_settings(request: Request, payload: Dict[str, str]):
    require_admin(request)
    update_settings_dict(payload)
    return {"success": True, "message": "บันทึกการตั้งค่าระบบเรียบร้อย"}


@app.post("/api/settings/test-email")
def test_email_endpoint(request: Request, recipient: str = Query(...)):
    require_admin(request)
    ok, msg = send_smtp_email(
        recipient,
        "[ISO Audit System] ทดสอบการส่งอีเมลแจ้งเตือนแผนซ่อมบำรุง (PM 30-Day Alert)",
        "<h3>ทดสอบการส่งอีเมลสำเร็จ</h3><p>ระบบแจ้งเตือนแผนการตรวจเช็คและซ่อมบำรุงรักษา (PM) ล่วงหน้า 30 วัน พร้อมทำงาน</p>",
        equip_code="TEST-SMTP",
    )
    return {"success": ok, "message": msg}


@app.get("/api/dashboard/summary")
def dashboard_summary():
    conn = get_db()
    total_docs = conn.execute("SELECT COUNT(*) as c FROM documents").fetchone()["c"]
    synced_docs = conn.execute("SELECT COUNT(*) as c FROM documents WHERE synology_synced = 1").fetchone()["c"]
    total_emps = conn.execute("SELECT COUNT(*) as c FROM employees").fetchone()["c"]
    total_trainings = conn.execute("SELECT COUNT(*) as c FROM training_records").fetchone()["c"]
    completed_trainings = conn.execute("SELECT COUNT(*) as c FROM training_records WHERE status = 'Completed'").fetchone()["c"]
    total_equip = conn.execute("SELECT COUNT(*) as c FROM equipment").fetchone()["c"]
    total_vendors = conn.execute("SELECT COUNT(*) as c FROM vendors").fetchone()["c"]

    pms = conn.execute(
        """
        SELECT p.*, e.equip_code, e.equip_name, e.category, e.location
        FROM pm_schedules p
        JOIN equipment e ON p.equip_id = e.id
        WHERE p.status != 'Completed'
        ORDER BY p.planned_date ASC
        """
    ).fetchall()

    avl_evals = conn.execute(
        """
        SELECT ve.*, v.vendor_code, v.vendor_name, v.category
        FROM vendor_evaluations ve
        JOIN vendors v ON ve.vendor_id = v.id
        WHERE ve.status != 'Completed'
        ORDER BY ve.planned_date ASC
        """
    ).fetchall()
    conn.close()

    today = datetime.now().date()
    upcoming_30d = []
    for p in pms:
        try:
            dt = datetime.strptime(p["planned_date"], "%Y-%m-%d").date()
            days_left = (dt - today).days
            if days_left <= 30:
                item = dict(p)
                item["days_remaining"] = days_left
                upcoming_30d.append(item)
        except Exception:
            pass

    upcoming_avl_30d = []
    for ve in avl_evals:
        try:
            dt = datetime.strptime(ve["planned_date"], "%Y-%m-%d").date()
            days_left = (dt - today).days
            if days_left <= 30:
                item = dict(ve)
                item["days_remaining"] = days_left
                upcoming_avl_30d.append(item)
        except Exception:
            pass

    return {
        "total_docs": total_docs,
        "synced_docs": synced_docs,
        "total_emps": total_emps,
        "total_trainings": total_trainings,
        "completed_trainings": completed_trainings,
        "total_equip": total_equip,
        "total_vendors": total_vendors,
        "upcoming_pm_30d_count": len(upcoming_30d),
        "upcoming_pm_30d": upcoming_30d,
        "upcoming_avl_30d_count": len(upcoming_avl_30d),
        "upcoming_avl_30d": upcoming_avl_30d,
    }


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def disable_static_cache(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/") or request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.get("/", response_class=HTMLResponse)
def serve_index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    with open(index_path, "r", encoding="utf-8") as f:
        html = f.read()
    v_info = get_app_version()
    ver = v_info.get("version", "1.2")
    ts = int(datetime.now().timestamp())
    html = re.sub(r'/static/app\.js(\?[^"\']*)?', f"/static/app.js?v={ver}&t={ts}", html)
    return HTMLResponse(
        content=html,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )
