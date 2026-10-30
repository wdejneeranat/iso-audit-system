import os
import re
import json
import sqlite3
import asyncio
from datetime import datetime
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from database import init_db, get_db, hash_password, get_settings_dict, update_settings_dict
from synology_client import SynologyClient
from email_service import check_and_send_pm_notifications, send_smtp_email

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


def bootstrap_sample_files_and_synology():
    """Ensure sample documents have local PDF files named by doc_code and sync to Synology."""
    conn = get_db()
    docs = conn.execute("SELECT id, doc_code, doc_title, revision_no, file_name, local_path, synology_synced FROM documents").fetchall()
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
            conn.execute(
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
            conn.execute(
                """
                UPDATE document_revisions
                SET file_name = ?, local_path = ?, synology_path = ?
                WHERE doc_id = ? AND revision_no = ?
                """,
                (fname, up_res["local_path"], up_res["synology_path"], d["id"], d["revision_no"]),
            )

    # Also bootstrap sample Employee Job Application PDFs
    emps = conn.execute("SELECT id, emp_code, full_name, application_file_name, application_local_path FROM employees").fetchall()
    for e in emps:
        if not e["application_file_name"] or not e["application_local_path"] or not os.path.exists(e["application_local_path"]):
            fname = f"{e['emp_code']}_Application.pdf"
            local_p = os.path.join(STORAGE_DIR, "ISO", "HR_Employees", e["emp_code"], "Application", fname)
            create_minimal_pdf(local_p, f"Job Application: {e['emp_code']}", f"Employee: {e['full_name']}")
            up_res = syno.upload_file(local_p, f"HR_Employees/{e['emp_code']}/Application", fname)
            conn.execute(
                """
                UPDATE employees
                SET application_file_name = ?, application_local_path = ?, application_synology_path = ?, application_synced = ?
                WHERE id = ?
                """,
                (fname, up_res["local_path"], up_res["synology_path"], 1 if up_res["synology_synced"] else 0, e["id"]),
            )

    # Also bootstrap sample Training Evidence PDFs for Completed trainings
    trs = conn.execute(
        "SELECT t.id, t.course_code, t.status, t.evidence_file_name, t.evidence_local_path, e.emp_code FROM training_records t JOIN employees e ON t.emp_id = e.id WHERE t.status = 'Completed'"
    ).fetchall()
    for t in trs:
        if not t["evidence_file_name"] or not t["evidence_local_path"] or not os.path.exists(t["evidence_local_path"]):
            fname = f"{t['emp_code']}_{t['course_code']}_Evidence.pdf"
            local_p = os.path.join(STORAGE_DIR, "ISO", "HR_Employees", t["emp_code"], "Training", fname)
            create_minimal_pdf(local_p, f"Training Record & Certificate: {t['course_code']}", f"Employee: {t['emp_code']} - Status: Completed")
            up_res = syno.upload_file(local_p, f"HR_Employees/{t['emp_code']}/Training", fname)
            conn.execute(
                """
                UPDATE training_records
                SET evidence_file_name = ?, evidence_local_path = ?, evidence_synology_path = ?, evidence_synced = ?
                WHERE id = ?
                """,
                (fname, up_res["local_path"], up_res["synology_path"], 1 if up_res["synology_synced"] else 0, t["id"]),
            )

    # Also bootstrap sample PM Evidence PDF for Completed PM schedules
    pms = conn.execute(
        "SELECT p.id, p.pm_code, p.status, p.evidence_file_name, p.evidence_local_path, e.equip_code FROM pm_schedules p JOIN equipment e ON p.equip_id = e.id WHERE p.status = 'Completed'"
    ).fetchall()
    for p in pms:
        if not p["evidence_file_name"] or not p["evidence_local_path"] or not os.path.exists(p["evidence_local_path"]):
            fname = f"{p['equip_code']}_{p['pm_code']}_Evidence.pdf"
            local_p = os.path.join(STORAGE_DIR, "ISO", "MHE_Maintenance", p["equip_code"], fname)
            create_minimal_pdf(local_p, f"PM Service Report: {p['pm_code']}", f"Equipment: {p['equip_code']} - Preventive Maintenance Completed")
            up_res = syno.upload_file(local_p, f"MHE_Maintenance/{p['equip_code']}", fname)
            conn.execute(
                """
                UPDATE pm_schedules
                SET evidence_file_name = ?, evidence_local_path = ?, evidence_synology_path = ?, evidence_synced = ?
                WHERE id = ?
                """,
                (fname, up_res["local_path"], up_res["synology_path"], 1 if up_res["synology_synced"] else 0, p["id"]),
            )

    conn.commit()
    conn.close()


async def periodic_pm_alert_scheduler():
    """Background task that checks 30-day advance PM notifications automatically."""
    await asyncio.sleep(2)
    while True:
        try:
            check_and_send_pm_notifications(force_resend=False)
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
    user = conn.execute(
        "SELECT id, username, password_hash FROM users WHERE username = ?",
        (payload.username.strip(),),
    ).fetchone()
    if not user:
        conn.close()
        raise HTTPException(status_code=404, detail="ไม่พบผู้ใช้งาน")
    if user["password_hash"] != hash_password(payload.current_password):
        conn.close()
        raise HTTPException(status_code=401, detail="รหัสผ่านปัจจุบันไม่ถูกต้อง")

    conn.execute(
        "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
        (hash_password(new_pw), user["id"]),
    )
    conn.commit()
    conn.close()
    return {"success": True, "message": "เปลี่ยนรหัสผ่านสำเร็จเรียบร้อย"}


@app.get("/api/users")
def list_users(request: Request):
    require_admin(request)
    conn = get_db()
    rows = conn.execute("SELECT id, username, full_name, role, department, email, is_active, must_change_password, created_at FROM users ORDER BY id ASC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


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
    conn = get_db()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn.execute(
            """
            INSERT INTO users (username, password_hash, full_name, role, department, email, is_active, must_change_password, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, 1, ?)
            """,
            (
                payload.username.strip(),
                hash_password(payload.password),
                payload.full_name.strip(),
                payload.role,
                payload.department.strip(),
                payload.email.strip(),
                now_str,
            ),
        )
        conn.commit()
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=400, detail=f"ไม่สามารถสร้างผู้ใช้ได้: {e}")
    conn.close()
    return {"success": True, "message": "เพิ่มผู้ใช้งานสำเร็จ (ผู้ใช้จะต้องเปลี่ยนรหัสผ่านเมื่อเข้าสู่ระบบครั้งแรก)"}


@app.delete("/api/users/{user_id}")
def delete_user(user_id: int, request: Request):
    require_admin(request)
    if user_id == 1:
        raise HTTPException(status_code=400, detail="ไม่สามารถลบบัญชี Admin หลักได้")
    conn = get_db()
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
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


@app.post("/api/documents")
async def create_or_update_document(
    request: Request,
    doc_id: Optional[int] = Form(None),
    doc_code: str = Form(...),
    doc_title: str = Form(...),
    category: str = Form("QP"),
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
    existing = None
    if doc_id:
        existing = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    else:
        existing = conn.execute("SELECT * FROM documents WHERE doc_code = ?", (clean_code,)).fetchone()

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

    if existing:
        target_id = existing["id"]
        # If doc_code changed and a file exists, rename file to new doc_code
        if existing["doc_code"] != clean_code and local_path and os.path.exists(local_path) and not (file and file.filename):
            file_name = sanitize_code_filename(clean_code, local_path)
            new_local = os.path.join(STORAGE_DIR, "ISO", "Documents", file_name)
            os.replace(local_path, new_local)
            local_path = new_local
            syno = get_synology_client()
            up_res = syno.upload_file(local_path, "Documents", file_name)
            synology_path = up_res["synology_path"]
            synology_synced = 1 if up_res["synology_synced"] else 0

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

    existing = None
    if emp_id:
        existing = conn.execute("SELECT * FROM employees WHERE id = ?", (emp_id,)).fetchone()
    else:
        existing = conn.execute("SELECT * FROM employees WHERE emp_code = ?", (clean_code,)).fetchone()

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
    emp = conn.execute("SELECT * FROM employees WHERE id = ?", (emp_id,)).fetchone()
    if not emp:
        conn.close()
        raise HTTPException(status_code=404, detail="ไม่พบข้อมูลพนักงาน")

    existing = None
    if training_id:
        existing = conn.execute("SELECT * FROM training_records WHERE id = ?", (training_id,)).fetchone()

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
    eq = conn.execute("SELECT * FROM equipment WHERE id = ?", (equip_id,)).fetchone()
    if not eq:
        conn.close()
        raise HTTPException(status_code=404, detail="ไม่พบข้อมูลอุปกรณ์")

    existing = None
    if pm_id:
        existing = conn.execute("SELECT * FROM pm_schedules WHERE id = ?", (pm_id,)).fetchone()

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
    synced_count = 0

    # 1. Sync Documents
    docs = conn.execute("SELECT id, doc_code, file_name, local_path FROM documents WHERE local_path != ''").fetchall()
    for d in docs:
        if os.path.exists(d["local_path"]):
            res = syno.upload_file(d["local_path"], "Documents", d["file_name"] or f"{d['doc_code']}.pdf")
            if res["synology_synced"]:
                synced_count += 1
                conn.execute(
                    "UPDATE documents SET synology_path = ?, synology_synced = 1 WHERE id = ?",
                    (res["synology_path"], d["id"]),
                )

    # 2. Sync Employee Applications & Training Evidence
    emps = conn.execute("SELECT id, emp_code, application_file_name, application_local_path FROM employees WHERE application_local_path != ''").fetchall()
    for e in emps:
        if os.path.exists(e["application_local_path"]):
            res = syno.upload_file(e["application_local_path"], f"HR_Employees/{e['emp_code']}/Application", e["application_file_name"])
            if res["synology_synced"]:
                synced_count += 1
                conn.execute(
                    "UPDATE employees SET application_synology_path = ?, application_synced = 1 WHERE id = ?",
                    (res["synology_path"], e["id"]),
                )

    trs = conn.execute(
        "SELECT t.id, t.evidence_file_name, t.evidence_local_path, e.emp_code FROM training_records t JOIN employees e ON t.emp_id = e.id WHERE t.evidence_local_path != ''"
    ).fetchall()
    for t in trs:
        if os.path.exists(t["evidence_local_path"]):
            res = syno.upload_file(t["evidence_local_path"], f"HR_Employees/{t['emp_code']}/Training", t["evidence_file_name"])
            if res["synology_synced"]:
                synced_count += 1
                conn.execute(
                    "UPDATE training_records SET evidence_synology_path = ?, evidence_synced = 1 WHERE id = ?",
                    (res["synology_path"], t["id"]),
                )

    # 3. Sync PM Evidence
    pms = conn.execute(
        "SELECT p.id, p.evidence_file_name, p.evidence_local_path, e.equip_code FROM pm_schedules p JOIN equipment e ON p.equip_id = e.id WHERE p.evidence_local_path != ''"
    ).fetchall()
    for p in pms:
        if os.path.exists(p["evidence_local_path"]):
            res = syno.upload_file(p["evidence_local_path"], f"MHE_Maintenance/{p['equip_code']}", p["evidence_file_name"])
            if res["synology_synced"]:
                synced_count += 1
                conn.execute(
                    "UPDATE pm_schedules SET evidence_synology_path = ?, evidence_synced = 1 WHERE id = ?",
                    (res["synology_path"], p["id"]),
                )

    conn.commit()
    conn.close()
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

    pms = conn.execute(
        """
        SELECT p.*, e.equip_code, e.equip_name, e.category, e.location
        FROM pm_schedules p
        JOIN equipment e ON p.equip_id = e.id
        WHERE p.status != 'Completed'
        ORDER BY p.planned_date ASC
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

    return {
        "total_docs": total_docs,
        "synced_docs": synced_docs,
        "total_emps": total_emps,
        "total_trainings": total_trainings,
        "completed_trainings": completed_trainings,
        "total_equip": total_equip,
        "upcoming_pm_30d_count": len(upcoming_30d),
        "upcoming_pm_30d": upcoming_30d,
    }


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def serve_index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    with open(index_path, "r", encoding="utf-8") as f:
        return f.read()
