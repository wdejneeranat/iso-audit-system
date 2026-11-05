import os
import json
import shutil
import sqlite3
import hashlib
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REPO_DB = os.path.join(BASE_DIR, "iso_audit.db")
DB_PATH = os.environ.get("ISO_DB_PATH", DEFAULT_REPO_DB)
os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)

# If ISO_DB_PATH points to a volume path (e.g. /app/storage/iso_audit.db) that does not exist yet,
# seed it from the repository's iso_audit.db so existing users and records are preserved.
if os.path.abspath(DB_PATH) != os.path.abspath(DEFAULT_REPO_DB):
    if not os.path.exists(DB_PATH) and os.path.exists(DEFAULT_REPO_DB):
        try:
            shutil.copy2(DEFAULT_REPO_DB, DB_PATH)
        except Exception:
            pass


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=60.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    conn = get_db()
    cur = conn.cursor()

    # 1. Users table (Role-Based & User-Specific Access Control)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'Viewer',
            department TEXT NOT NULL DEFAULT 'General',
            email TEXT DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1,
            must_change_password INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
        """
    )

    # Ensure must_change_password column exists for existing databases
    user_cols = [c["name"] for c in cur.execute("PRAGMA table_info(users)").fetchall()]
    if "must_change_password" not in user_cols:
        cur.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 1")

    # Track deleted usernames so deleted default users are never re-created on restart/deploy
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS deleted_users (
            username TEXT PRIMARY KEY,
            deleted_at TEXT NOT NULL
        )
        """
    )

    # 2. ISO Master File Documents table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_code TEXT UNIQUE NOT NULL,
            doc_title TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'QP',
            effective_date TEXT NOT NULL,
            revision_no INTEGER NOT NULL DEFAULT 0,
            revision_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Active',
            confidentiality TEXT NOT NULL DEFAULT 'Internal',
            allowed_roles TEXT NOT NULL DEFAULT '["Admin","DCC","HR","Maintenance","Viewer"]',
            allowed_departments TEXT NOT NULL DEFAULT '["All"]',
            allowed_users TEXT NOT NULL DEFAULT '[]',
            file_name TEXT DEFAULT '',
            original_name TEXT DEFAULT '',
            local_path TEXT DEFAULT '',
            synology_path TEXT DEFAULT '',
            synology_synced INTEGER NOT NULL DEFAULT 0,
            uploaded_by TEXT DEFAULT 'admin',
            submitted_by TEXT DEFAULT '',
            submitted_at TEXT DEFAULT '',
            approved_by TEXT DEFAULT '',
            approved_at TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            updated_at TEXT NOT NULL
        )
        """
    )

    doc_cols = [c["name"] for c in cur.execute("PRAGMA table_info(documents)").fetchall()]
    for col_name in ("submitted_by", "submitted_at", "approved_by", "approved_at"):
        if col_name not in doc_cols:
            cur.execute(f"ALTER TABLE documents ADD COLUMN {col_name} TEXT DEFAULT ''")

    # 3. Document Revisions History table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS document_revisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id INTEGER NOT NULL,
            doc_code TEXT NOT NULL,
            revision_no INTEGER NOT NULL,
            effective_date TEXT NOT NULL,
            revision_date TEXT NOT NULL,
            change_summary TEXT DEFAULT '',
            file_name TEXT DEFAULT '',
            local_path TEXT DEFAULT '',
            synology_path TEXT DEFAULT '',
            updated_by TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(doc_id) REFERENCES documents(id) ON DELETE CASCADE
        )
        """
    )

    # 4. Employees table (HR Profile & Job Application)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            emp_code TEXT UNIQUE NOT NULL,
            full_name TEXT NOT NULL,
            department TEXT NOT NULL,
            position TEXT NOT NULL,
            hire_date TEXT NOT NULL,
            email TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'Active',
            application_file_name TEXT DEFAULT '',
            application_local_path TEXT DEFAULT '',
            application_synology_path TEXT DEFAULT '',
            application_synced INTEGER NOT NULL DEFAULT 0,
            application_uploaded_at TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )

    # 5. Additional Employee Documents (Job Application, Certificates, ID, etc.)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS employee_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            emp_id INTEGER NOT NULL,
            doc_type TEXT NOT NULL DEFAULT 'Application',
            title TEXT NOT NULL,
            file_name TEXT NOT NULL,
            local_path TEXT NOT NULL,
            synology_path TEXT DEFAULT '',
            synology_synced INTEGER NOT NULL DEFAULT 0,
            uploaded_at TEXT NOT NULL,
            FOREIGN KEY(emp_id) REFERENCES employees(id) ON DELETE CASCADE
        )
        """
    )

    # 6. Employee Training Plans & Training Records + Evidence
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS training_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            emp_id INTEGER NOT NULL,
            course_code TEXT NOT NULL DEFAULT '',
            course_title TEXT NOT NULL,
            training_type TEXT NOT NULL DEFAULT 'In-house',
            planned_date TEXT NOT NULL,
            actual_date TEXT DEFAULT '',
            duration_hours REAL NOT NULL DEFAULT 3.0,
            trainer TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'Planned',
            evaluation_result TEXT NOT NULL DEFAULT 'Pending',
            score TEXT DEFAULT '',
            evidence_file_name TEXT DEFAULT '',
            evidence_local_path TEXT DEFAULT '',
            evidence_synology_path TEXT DEFAULT '',
            evidence_synced INTEGER NOT NULL DEFAULT 0,
            remarks TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(emp_id) REFERENCES employees(id) ON DELETE CASCADE
        )
        """
    )

    # 7. Equipment Registry (MHE, Rack, Facility, Measuring)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS equipment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            equip_code TEXT UNIQUE NOT NULL,
            equip_name TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'MHE',
            brand_model TEXT DEFAULT '',
            serial_no TEXT DEFAULT '',
            location TEXT DEFAULT '',
            purchase_install_date TEXT NOT NULL,
            warranty_expiry TEXT DEFAULT '',
            vendor TEXT DEFAULT '',
            responsible_person TEXT DEFAULT '',
            responsible_email TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'Active',
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )

    # 8. Preventive Maintenance (PM) Schedules & Evidence
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pm_schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            equip_id INTEGER NOT NULL,
            pm_code TEXT NOT NULL,
            pm_title TEXT NOT NULL,
            frequency TEXT NOT NULL DEFAULT 'Quarterly',
            planned_date TEXT NOT NULL,
            notify_days_before INTEGER NOT NULL DEFAULT 30,
            notify_emails TEXT NOT NULL DEFAULT '',
            notification_sent INTEGER NOT NULL DEFAULT 0,
            notification_sent_at TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'Scheduled',
            actual_date TEXT DEFAULT '',
            performed_by TEXT DEFAULT '',
            result_summary TEXT DEFAULT '',
            cost REAL NOT NULL DEFAULT 0.0,
            evidence_file_name TEXT DEFAULT '',
            evidence_local_path TEXT DEFAULT '',
            evidence_synology_path TEXT DEFAULT '',
            evidence_synced INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(equip_id) REFERENCES equipment(id) ON DELETE CASCADE
        )
        """
    )

    # 9. Email Notification Logs
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS email_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pm_id INTEGER,
            equip_code TEXT DEFAULT '',
            recipients TEXT NOT NULL,
            subject TEXT NOT NULL,
            body_preview TEXT DEFAULT '',
            status TEXT NOT NULL,
            error_message TEXT DEFAULT '',
            sent_at TEXT NOT NULL
        )
        """
    )

    # 11. Approved Vendor List (AVL - ทะเบียนผู้จัดจำหน่ายที่ผ่านการอนุมัติ)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS vendors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_code TEXT UNIQUE NOT NULL,
            vendor_name TEXT NOT NULL,
            category TEXT NOT NULL, -- MHE & Storage Systems, Fire Protection Systems, Other
            products_services TEXT DEFAULT '',
            contact_person TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            email TEXT DEFAULT '',
            address TEXT DEFAULT '',
            tax_id TEXT DEFAULT '',
            iso_certified TEXT DEFAULT '',
            avl_status TEXT DEFAULT 'Approved', -- Approved, Conditional (CAR), Suspended / Removed
            latest_score REAL DEFAULT 0.0,
            latest_grade TEXT DEFAULT '-',
            latest_review_action TEXT DEFAULT '',
            last_eval_date TEXT DEFAULT '',
            next_eval_date TEXT DEFAULT '',
            cert_filename TEXT DEFAULT '',
            synology_cert_path TEXT DEFAULT '',
            local_cert_path TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )

    # 12. Supplier Evaluation & Schedule (การประเมินผู้จัดจำหน่าย ข้อ 3.2 & แผนการประเมิน ข้อ 3.3 + แจ้งเตือนล่วงหน้า 30 วัน)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS vendor_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_id INTEGER NOT NULL,
            eval_code TEXT UNIQUE NOT NULL,
            eval_period TEXT NOT NULL, -- e.g. 2026-Q4, Annual 2026
            frequency TEXT NOT NULL, -- Quarterly, Semi-Annual, Annual, CAR Follow-up
            planned_date TEXT NOT NULL,
            notify_days_before INTEGER DEFAULT 30,
            notify_emails TEXT DEFAULT '',
            notification_sent INTEGER DEFAULT 0,
            notification_sent_at TEXT DEFAULT '',
            status TEXT DEFAULT 'Scheduled', -- Scheduled, Notified, Completed, Overdue
            actual_date TEXT DEFAULT '',
            evaluator TEXT DEFAULT '',
            score_quality REAL DEFAULT 0.0, -- Max 35%, Expected >= 28%
            score_service_sla REAL DEFAULT 0.0, -- Max 30%, Expected >= 24%
            score_cost REAL DEFAULT 0.0, -- Max 20%
            score_cooperation REAL DEFAULT 0.0, -- Max 15%
            total_score REAL DEFAULT 0.0, -- Max 100%, Expected >= 80%
            pass_criteria_flag INTEGER DEFAULT 1, -- 1 if quality>=28 and service>=24 and total>=80
            grade TEXT DEFAULT '-', -- A (90-100%), B (80-89%), C (70-79%), D (<70%)
            review_action TEXT DEFAULT '', -- Action per Manual Section 3.3
            car_no TEXT DEFAULT '',
            car_due_date TEXT DEFAULT '',
            remarks TEXT DEFAULT '',
            evidence_filename TEXT DEFAULT '',
            synology_evidence_path TEXT DEFAULT '',
            local_evidence_path TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )

    # 10. System Settings (Synology Drive & SMTP Configuration)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )

    default_settings = {
        "synology_quickconnect": "Sunlogis",
        "synology_user": "Watcharagorn",
        "synology_password": "Cervelo$3",
        "synology_folder": "ISO",
        "synology_custom_host": "",
        "smtp_host": "smtp.gmail.com",
        "smtp_port": "587",
        "smtp_user": "",
        "smtp_password": "",
        "smtp_sender": "iso-notify@sunlogis.com",
        "smtp_use_tls": "true",
        "pm_advance_days": "30",
    }
    for k, v in default_settings.items():
        cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today = datetime.now().date()

    # Seed default users ONLY ONCE if users table has never been initialized
    users_init_row = cur.execute("SELECT value FROM settings WHERE key = 'users_initialized'").fetchone()
    users_already_initialized = bool(users_init_row and users_init_row["value"] == "1")
    cur.execute("SELECT COUNT(*) as cnt FROM users")
    user_count = cur.fetchone()["cnt"]

    if not users_already_initialized and user_count == 0:
        default_users = [
            ("Watcharagorn", hash_password("Cervelo$3"), "Watcharagorn (System Admin)", "Admin", "Management", "watcharagorn@sunlogis.com", 0),
            ("admin", hash_password("Cervelo$3"), "System Administrator", "Admin", "Management", "admin@sunlogis.com", 1),
            ("dcc", hash_password("dcc123"), "เจ้าหน้าที่ควบคุมเอกสาร (DCC)", "DCC", "QMR/ISO", "dcc@sunlogis.com", 1),
            ("hr", hash_password("hr123"), "เจ้าหน้าที่ทรัพยากรบุคคล (HR)", "HR", "HR", "hr@sunlogis.com", 1),
            ("maint", hash_password("maint123"), "วิศวกรซ่อมบำรุงและคลังสินค้า (MHE/PM)", "Maintenance", "Warehouse", "maintenance@sunlogis.com", 1),
            ("viewer", hash_password("viewer123"), "พนักงานทั่วไป / Auditor (Viewer)", "Viewer", "Operations", "auditor@sunlogis.com", 1),
        ]
        for u, pw, fname, role, dept, email, must_chg in default_users:
            is_del = cur.execute("SELECT 1 FROM deleted_users WHERE LOWER(username) = LOWER(?)", (u,)).fetchone()
            if not is_del:
                cur.execute(
                    """
                    INSERT OR IGNORE INTO users (username, password_hash, full_name, role, department, email, is_active, must_change_password, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (u, pw, fname, role, dept, email, must_chg, now_str),
                )
        cur.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('users_initialized', '1')")
    elif not users_already_initialized:
        # Mark existing database as initialized without overwriting any user passwords or re-adding deleted users
        is_del = cur.execute("SELECT 1 FROM deleted_users WHERE LOWER(username) = 'watcharagorn'").fetchone()
        if not is_del:
            cur.execute(
                """
                INSERT OR IGNORE INTO users (username, password_hash, full_name, role, department, email, is_active, must_change_password, created_at)
                VALUES ('Watcharagorn', ?, 'Watcharagorn (System Admin)', 'Admin', 'Management', 'watcharagorn@sunlogis.com', 1, 0, ?)
                """,
                (hash_password("Cervelo$3"), now_str),
            )
        cur.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('users_initialized', '1')")

    db_seeded_row = cur.execute("SELECT value FROM settings WHERE key = 'db_seeded'").fetchone()
    db_already_seeded = bool(db_seeded_row and db_seeded_row["value"] == "1")

    # Seed sample Master List ISO Documents ONLY if db has never been seeded
    cur.execute("SELECT COUNT(*) as cnt FROM documents")
    if not db_already_seeded and cur.fetchone()["cnt"] == 0:
        sample_docs = [
            (
                "QM-MR-001",
                "คู่มือคุณภาพและการจัดการมาตรฐาน ISO 9001:2015 (Quality Manual)",
                "QM",
                "2025-01-15",
                2,
                "2026-03-10",
                "Active",
                "Public",
                json.dumps(["Admin", "DCC", "HR", "Maintenance", "Viewer"]),
                json.dumps(["All"]),
                json.dumps([]),
                "เอกสารหลักสำหรับทุกแผนก ใช้ในการตรวจประเมิน ISO",
            ),
            (
                "PM-DC-001",
                "ระเบียบปฏิบัติงานการควบคุมเอกสารและบันทึกคุณภาพ (Document & Record Control)",
                "PM",
                "2025-02-01",
                1,
                "2026-01-20",
                "Active",
                "Internal",
                json.dumps(["Admin", "DCC", "HR", "Maintenance", "Viewer"]),
                json.dumps(["All"]),
                json.dumps([]),
                "กำหนดมาตรฐานการออกรหัสเอกสาร การแก้ไข และการเก็บรักษาบน Synology Drive",
            ),
            (
                "PM-HR-001",
                "ระเบียบปฏิบัติงานการสรรหา คัดเลือก และฝึกอบรมบุคลากร (Recruitment & Training)",
                "PM",
                "2025-03-01",
                3,
                "2026-06-15",
                "Active",
                "Restricted",
                json.dumps(["Admin", "DCC", "HR"]),
                json.dumps(["Management", "QMR/ISO", "HR"]),
                json.dumps(["admin", "dcc", "hr"]),
                "จำกัดสิทธิ์เฉพาะฝ่าย HR, DCC และผู้บริหาร",
            ),
            (
                "WI-WH-001",
                "วิธีปฏิบัติงานการตรวจสอบและบำรุงรักษารถโฟร์คลิฟท์และชั้นวางสินค้า (MHE & Rack PM)",
                "WI",
                "2025-04-10",
                1,
                "2026-05-01",
                "Active",
                "Restricted",
                json.dumps(["Admin", "DCC", "Maintenance"]),
                json.dumps(["Management", "QMR/ISO", "Warehouse"]),
                json.dumps(["admin", "dcc", "maint"]),
                "คู่มือตรวจสอบความปลอดภัย MHE และ Rack ประจำเดือน/ประจำไตรมาส",
            ),
            (
                "FM-HR-001",
                "แบบฟอร์มบันทึกประวัติการฝึกอบรมรายบุคคล (Individual Training Record Form)",
                "FM",
                "2025-01-15",
                0,
                "2025-01-15",
                "Active",
                "Internal",
                json.dumps(["Admin", "DCC", "HR", "Maintenance", "Viewer"]),
                json.dumps(["All"]),
                json.dumps([]),
                "แบบฟอร์มมาตรฐานสำหรับแนบหลักฐานการอบรมพนักงาน",
            ),
        ]
        for code, title, cat, eff_date, rev_no, rev_date, status, conf, roles, depts, users_list, notes in sample_docs:
            cur.execute(
                """
                INSERT INTO documents (
                    doc_code, doc_title, category, effective_date, revision_no, revision_date,
                    status, confidentiality, allowed_roles, allowed_departments, allowed_users,
                    uploaded_by, notes, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'admin', ?, ?)
                """,
                (code, title, cat, eff_date, rev_no, rev_date, status, conf, roles, depts, users_list, notes, now_str),
            )
            doc_id = cur.lastrowid
            cur.execute(
                """
                INSERT INTO document_revisions (
                    doc_id, doc_code, revision_no, effective_date, revision_date,
                    change_summary, updated_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'admin', ?)
                """,
                (doc_id, code, rev_no, eff_date, rev_date, f"ประกาศใช้เอกสาร (Rev.{rev_no:02d})", now_str),
            )

    # Seed sample Employees & Training Records ONLY if db has never been seeded
    cur.execute("SELECT COUNT(*) as cnt FROM employees")
    if not db_already_seeded and cur.fetchone()["cnt"] == 0:
        sample_emps = [
            ("EMP-001", "สมชาย สายชล (Somchai Saichon)", "Warehouse", "หัวหน้าคลังสินค้า (Warehouse Supervisor)", "2023-05-15", "somchai@sunlogis.com", "081-234-5678", "Active", "มีใบอนุญาตขับขี่รถโฟร์คลิฟท์ระดับมาตรฐาน"),
            ("EMP-002", "วิภาวดี ศรีสวัสดิ์ (Wipawadee Srisawat)", "QMR/ISO", "เจ้าหน้าที่ควบคุมเอกสาร (Document Controller)", "2024-02-01", "wipawadee@sunlogis.com", "089-876-5432", "Active", "ผู้รับผิดชอบดูแลระบบ ISO 9001:2015"),
            ("EMP-003", "กิตติพงศ์ ทองดี (Kittipong Thongdee)", "Warehouse", "พนักงานขับรถโฟร์คลิฟท์ (MHE Operator)", "2024-08-12", "kittipong@sunlogis.com", "086-111-2233", "Active", "ผ่านการอบรมความปลอดภัยการใช้ MHE และตรวจสอบ Rack"),
        ]
        for ecode, fname, dept, pos, hdate, email, phone, st, notes in sample_emps:
            cur.execute(
                """
                INSERT INTO employees (
                    emp_code, full_name, department, position, hire_date, email, phone, status, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (ecode, fname, dept, pos, hdate, email, phone, st, notes, now_str),
            )

        # Add Training Plans & Records for these employees
        trainings = [
            (1, "TRN-ISO-01", "ข้อกำหนดมาตรฐาน ISO 9001:2015 และการตรวจประเมินภายใน (Internal Audit)", "ISO Standard", "2026-03-15", "2026-03-15", 6.0, "อาจารย์ประเสริฐ (ISO Consultant)", "Completed", "Pass", "92/100", "ผ่านเกณฑ์ดีเยี่ยม"),
            (1, "TRN-MHE-02", "ความปลอดภัยในการควบคุมรถยกโฟร์คลิฟท์และการตรวจสอบ Rack ประจำปี", "Safety/MHE", "2026-11-15", "", 6.0, "ศูนย์ฝึกอบรมความปลอดภัย จป.", "Planned", "Pending", "", "แผนอบรมประจำปีไตรมาส 4"),
            (2, "TRN-ISO-01", "ข้อกำหนดมาตรฐาน ISO 9001:2015 และการควบคุมเอกสาร (Document Control)", "ISO Standard", "2026-02-10", "2026-02-10", 6.0, "อาจารย์ประเสริฐ (ISO Consultant)", "Completed", "Pass", "96/100", "มีใบรับรองผ่านการอบรม"),
            (3, "TRN-MHE-01", "การขับขี่รถโฟร์คลิฟท์อย่างปลอดภัยและการบำรุงรักษาเบื้องต้น (Daily MHE Check)", "OJT", "2026-04-20", "2026-04-20", 12.0, "สมชาย สายชล (Warehouse Supervisor)", "Completed", "Pass", "88/100", "ผ่านการทดสอบภาคปฏิบัติ"),
            (3, "TRN-SAF-03", "การซ้อมดับเพลิงและอพยพหนีไฟประจำปี 2026", "Safety/MHE", "2026-12-05", "", 6.0, "ทีมวิทยากรป้องกันและบรรเทาสาธารณภัย", "Planned", "Pending", "", "อยู่ระหว่างเตรียมการอบรม"),
        ]
        for emp_id, ccode, ctitle, ttype, pdate, adate, hrs, trainer, st, res, score, rem in trainings:
            cur.execute(
                """
                INSERT INTO training_records (
                    emp_id, course_code, course_title, training_type, planned_date, actual_date,
                    duration_hours, trainer, status, evaluation_result, score, remarks, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (emp_id, ccode, ctitle, ttype, pdate, adate, hrs, trainer, st, res, score, rem, now_str),
            )

    # Seed sample Equipment (MHE, Rack, etc.) & PM Schedules ONLY if db has never been seeded
    cur.execute("SELECT COUNT(*) as cnt FROM equipment")
    if not db_already_seeded and cur.fetchone()["cnt"] == 0:
        sample_equip = [
            (
                "MHE-FL-001",
                "รถโฟร์คลิฟท์ไฟฟ้า (Electric Counterbalance Forklift 2.5T)",
                "MHE",
                "TOYOTA / 8FBE25",
                "SN-TYT-884920",
                "Warehouse Zone A (Loading Bay)",
                "2024-06-15",
                "2027-06-15",
                "Toyota Material Handling Thailand",
                "สมชาย สายชล",
                "maintenance@sunlogis.com, watcharagorn@sunlogis.com",
                "Active",
                "ตรวจสอบระบบไฮดรอลิก แบตเตอรี่ และเบรกทุก 3 เดือน",
            ),
            (
                "MHE-RT-002",
                "รถยกสูงสำหรับคลังสินค้า (Reach Truck 1.8T)",
                "MHE",
                "CROWN / ESR5200",
                "SN-CRW-552109",
                "Warehouse Zone B (High Rack)",
                "2025-01-20",
                "2028-01-20",
                "Crown Equipment Services",
                "กิตติพงศ์ ทองดี",
                "maintenance@sunlogis.com, watcharagorn@sunlogis.com",
                "Active",
                "ใช้งานโซนชั้นวางสูง 9 เมตร",
            ),
            (
                "RACK-WH1-A",
                "ระบบชั้นวางสินค้า Selective Pallet Rack (Zone A1 - A10)",
                "Rack",
                "SSI SCHAEFER / Heavy Duty 2000kg/beam",
                "SN-RCK-2024A",
                "Warehouse 1 - Zone A",
                "2023-11-01",
                "2028-11-01",
                "Schaefer Systems International",
                "สมชาย สายชล",
                "maintenance@sunlogis.com, watcharagorn@sunlogis.com",
                "Active",
                "ตรวจสอบความดิ่งเสา, สลักล็อกคาน (Safety Pin), และกันชนเสา (Column Guard)",
            ),
            (
                "MHE-PJ-005",
                "รถลากพาเลทไฟฟ้า (Electric Pallet Truck 2.0T)",
                "MHE",
                "LINDE / T20 AP",
                "SN-LND-309412",
                "Warehouse Zone C (Staging)",
                "2025-03-10",
                "2027-03-10",
                "Linde Material Handling",
                "กิตติพงศ์ ทองดี",
                "maintenance@sunlogis.com, watcharagorn@sunlogis.com",
                "Active",
                "ตรวจสอบล้อโพลียูรีเทนและชุดควบคุมไฟฟ้า",
            ),
        ]
        for ecode, ename, cat, brand, sn, loc, pdate, wdate, vendor, resp_p, resp_e, st, notes in sample_equip:
            cur.execute(
                """
                INSERT INTO equipment (
                    equip_code, equip_name, category, brand_model, serial_no, location,
                    purchase_install_date, warranty_expiry, vendor, responsible_person,
                    responsible_email, status, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (ecode, ename, cat, brand, sn, loc, pdate, wdate, vendor, resp_p, resp_e, st, notes, now_str),
            )

        # Create PM Schedules:
        # 1) Due in 25 days (within 30-day advance notification window!)
        due_25 = (today + timedelta(days=25)).strftime("%Y-%m-%d")
        # 2) Due in 14 days (within 30-day advance notification window!)
        due_14 = (today + timedelta(days=14)).strftime("%Y-%m-%d")
        # 3) Due in 65 days (future PM > 30 days)
        due_65 = (today + timedelta(days=65)).strftime("%Y-%m-%d")
        # 4) Completed PM last month
        done_last = (today - timedelta(days=35)).strftime("%Y-%m-%d")

        pms = [
            (
                1,
                "PM-2026-Q4-001",
                "ตรวจเช็คบำรุงรักษารถโฟร์คลิฟท์ตามรอบ 3 เดือน (ระบบเบรก, ไฮดรอลิก, โซ่ยก, แบตเตอรี่)",
                "Quarterly",
                due_25,
                30,
                "maintenance@sunlogis.com, watcharagorn@sunlogis.com",
                0,
                "",
                "Scheduled",
                "",
                "",
                "",
                0.0,
            ),
            (
                3,
                "PM-2026-Q4-002",
                "ตรวจสอบโครงสร้างความปลอดภัยชั้นวางสินค้า Selective Rack ประจำไตรมาส (เสา, คาน, Safety Pin, พุกยึดพื้น)",
                "Quarterly",
                due_14,
                30,
                "maintenance@sunlogis.com, watcharagorn@sunlogis.com",
                0,
                "",
                "Scheduled",
                "",
                "",
                "",
                0.0,
            ),
            (
                2,
                "PM-2026-Q4-003",
                "ตรวจเช็คระบบไฟฟ้าและเสายกสูง Reach Truck CROWN รอบ 6 เดือน",
                "Semi-Annual",
                due_65,
                30,
                "maintenance@sunlogis.com, watcharagorn@sunlogis.com",
                0,
                "",
                "Scheduled",
                "",
                "",
                "",
                0.0,
            ),
            (
                1,
                "PM-2026-Q3-001",
                "ตรวจเช็คบำรุงรักษารถโฟร์คลิฟท์ตามรอบไตรมาส 3 (เปลี่ยนถ่ายน้ำมันไฮดรอลิกและอัดจาระบี)",
                "Quarterly",
                done_last,
                30,
                "maintenance@sunlogis.com, watcharagorn@sunlogis.com",
                1,
                done_last,
                "Completed",
                done_last,
                "ช่างเทคนิค Toyota Material Handling",
                "สภาพสมบูรณ์ เปลี่ยนซีลไฮดรอลิกและตรวจเช็คแรงดันแบตเตอรี่ผ่านเกณฑ์มาตรฐาน",
                4500.0,
            ),
        ]
        for eq_id, pm_code, pm_title, freq, pdate, ndays, nemails, nsent, nsent_at, st, adate, perf_by, res_sum, cost in pms:
            cur.execute(
                """
                INSERT INTO pm_schedules (
                    equip_id, pm_code, pm_title, frequency, planned_date, notify_days_before,
                    notify_emails, notification_sent, notification_sent_at, status, actual_date,
                    performed_by, result_summary, cost, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (eq_id, pm_code, pm_title, freq, pdate, ndays, nemails, nsent, nsent_at, st, adate, perf_by, res_sum, cost, now_str),
            )

    # Ensure sample External Document (EX) exists in documents ONLY if db has never been seeded
    cur.execute("SELECT COUNT(*) as cnt FROM documents WHERE category = 'EX'")
    if not db_already_seeded and cur.fetchone()["cnt"] == 0:
        ex_docs = [
            (
                "EX-WH-001",
                "มาตรฐานสากลระบบป้องกันอัคคีภัยและตรวจสอบปั๊มดับเพลิง (NFPA 25 / UL / FM Standard Manual)",
                "EX",
                "2025-01-10",
                0,
                "2025-01-10",
                "Active",
                "Internal",
                json.dumps(["Admin", "DCC", "HR", "Maintenance", "Viewer"]),
                json.dumps(["All"]),
                json.dumps([]),
                "เอกสารภายนอก (External Document) อ้างอิงตามคู่มือการคัดสรรและติดตามคุณภาพผู้จัดจำหน่าย",
            ),
            (
                "QM-MR-002",
                "คู่มือการคัดสรร เลือกผู้จัดจำหน่าย และติดตามคุณภาพ (Supplier Selection & Quality Monitoring Manual)",
                "QM",
                "2026-01-01",
                0,
                "2026-01-01",
                "Active",
                "Public",
                json.dumps(["Admin", "DCC", "HR", "Maintenance", "Viewer"]),
                json.dumps(["All"]),
                json.dumps([]),
                "กำหนดเกณฑ์การให้คะแนนประเมินผู้จัดจำหน่าย ข้อ 3.2 และแผนการประเมิน ข้อ 3.3 สำหรับ AVL",
            ),
        ]
        for code, title, cat, eff_date, rev_no, rev_date, status, conf, roles, depts, users_list, notes in ex_docs:
            cur.execute(
                """
                INSERT OR IGNORE INTO documents (
                    doc_code, doc_title, category, effective_date, revision_no, revision_date,
                    status, confidentiality, allowed_roles, allowed_departments, allowed_users,
                    uploaded_by, notes, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Watcharagorn', ?, ?)
                """,
                (code, title, cat, eff_date, rev_no, rev_date, status, conf, roles, depts, users_list, notes, now_str),
            )

    # Seed sample Approved Vendor List (AVL) & Supplier Evaluations ONLY if db has never been seeded
    cur.execute("SELECT COUNT(*) as cnt FROM vendors")
    if not db_already_seeded and cur.fetchone()["cnt"] == 0:
        due_20 = (today + timedelta(days=20)).strftime("%Y-%m-%d")
        due_28 = (today + timedelta(days=28)).strftime("%Y-%m-%d")
        due_75 = (today + timedelta(days=75)).strftime("%Y-%m-%d")
        done_prev = (today - timedelta(days=15)).strftime("%Y-%m-%d")
        car_due = (today + timedelta(days=30)).strftime("%Y-%m-%d")

        sample_vendors = [
            (
                "AVL-MHE-001",
                "บริษัท โตโยต้า ทูโช ฟอร์คลิฟท์ (ไทยแลนด์) จำกัด (Toyota Material Handling)",
                "MHE & Storage Systems",
                "รถโฟร์คลิฟท์ไฟฟ้า (Forklift), รถยกสูง (Reach Truck), บริการซ่อมบำรุง PM และอะไหล่แท้",
                "คุณธนกร วิศวกรรมบริการ",
                "02-123-4567",
                "service@toyotaforklift-th.example.com",
                "นิคมอุตสาหกรรมอมตะซิตี้ จ.ชลบุรี",
                "0105551234567",
                "ISO 9001:2015, ISO 14001, มอก.",
                "Approved",
                93.0,
                "A",
                "ระดับ A (90-100%): คุณภาพดีเยี่ยม คงสถานะพันธมิตรหลักและให้สิทธิพิเศษพิจารณาเป็นเจ้าแรกในการประมูลรอบถัดไป",
                done_prev,
                due_20,
                "ผ่านเกณฑ์การประเมินขั้นต้นและมีทีมช่าง Response Time ภายใน 4 ชม.",
            ),
            (
                "AVL-FPS-001",
                "บริษัท ไฟร์โปร เอ็นจิเนียริ่ง แอนด์ ซิสเต็มส์ จำกัด (FirePro Engineering)",
                "Fire Protection Systems",
                "ระบบปั๊มดับเพลิง (Fire Pump), ตู้ควบคุม, สปริงเกอร์, ระบบแจ้งเหตุเพลิงไหม้ (Fire Alarm) มาตรฐาน NFPA/UL/FM",
                "คุณพงษ์ศักดิ์ วิศวกรระบบอัคคีภัย (กว.เครื่องกล)",
                "02-987-6543",
                "engineering@firepro-th.example.com",
                "เขตลาดกระบัง กรุงเทพมหานคร",
                "0105559876543",
                "ISO 9001:2015, UL/FM Listed, NFPA Standard",
                "Approved",
                85.0,
                "B",
                "ระดับ B (80-89%): คุณภาพอยู่ในเกณฑ์มาตรฐาน แจ้ง Feedback ถึงจุดที่สามารถพัฒนาต่อได้",
                done_prev,
                due_28,
                "มีวิศวกรได้รับใบอนุญาต กว. ควบคุมงานทดสอบระบบ Fire Pump ประจำปี",
            ),
            (
                "AVL-MHE-002",
                "บริษัท สยาม แร็ค แอนด์ สตอเรจ ซิสเต็มส์ จำกัด (Siam Rack & Storage)",
                "MHE & Storage Systems",
                "ระบบชั้นวางสินค้าอุตสาหกรรม (Selective Rack, Drive-in Rack), อุปกรณ์กันชนเสา และตรวจสอบโครงสร้าง",
                "คุณสุรชัย ฝ่ายโครงการคลังสินค้า",
                "02-555-8899",
                "sales@siamrack-th.example.com",
                "อ.บางพลี จ.สมุทรปราการ",
                "0105554433221",
                "ISO 9001:2015, FEM 10.2.02",
                "Conditional (CAR)",
                74.0,
                "C",
                "ระดับ C (70-79%): ต้องออกเอกสารแจ้งเตือน (Corrective Action Request - CAR) ให้ผู้จัดจำหน่ายทำแผนแก้ไขและปรับปรุงปัญหาภายใน 30-60 วัน",
                done_prev,
                due_75,
                "พบความล่าช้าในการจัดส่งรายงานตรวจสอบความดิ่งเสาในไตรมาสก่อน ออกใบ CAR เพื่อติดตามผล",
            ),
        ]
        for vcode, vname, cat, prod, contact, phone, email, addr, tax, iso, st, lscore, lgrade, laction, leval, neval, notes in sample_vendors:
            cur.execute(
                """
                INSERT INTO vendors (
                    vendor_code, vendor_name, category, products_services, contact_person,
                    phone, email, address, tax_id, iso_certified, avl_status,
                    latest_score, latest_grade, latest_review_action, last_eval_date, next_eval_date,
                    notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (vcode, vname, cat, prod, contact, phone, email, addr, tax, iso, st, lscore, lgrade, laction, leval, neval, notes, now_str),
            )

        sample_evals = [
            (
                1,
                "AVL-EVAL-2026-001",
                "2026-Q3 (ไตรมาส 3/2026)",
                "Quarterly",
                done_prev,
                30,
                "maintenance@sunlogis.com, watcharagorn@sunlogis.com",
                1,
                done_prev,
                "Completed",
                done_prev,
                "สมชาย สายชล (Warehouse Supervisor)",
                33.0,
                28.0,
                18.0,
                14.0,
                93.0,
                1,
                "A",
                "ระดับ A (90-100%): คุณภาพดีเยี่ยม คงสถานะพันธมิตรหลักและให้สิทธิพิเศษพิจารณาเป็นเจ้าแรกในการประมูลรอบถัดไป",
                "",
                "",
                "รถโฟร์คลิฟท์ทำงานเต็มประสิทธิภาพ เข้าทำ PM ตรงเวลาทุกครั้ง Report ชัดเจน",
            ),
            (
                2,
                "AVL-EVAL-2026-002",
                "2026-Q3 (ไตรมาส 3/2026)",
                "Quarterly",
                done_prev,
                30,
                "maintenance@sunlogis.com, watcharagorn@sunlogis.com",
                1,
                done_prev,
                "Completed",
                done_prev,
                "สมชาย สายชล (Warehouse Supervisor)",
                30.0,
                25.0,
                17.0,
                13.0,
                85.0,
                1,
                "B",
                "ระดับ B (80-89%): คุณภาพอยู่ในเกณฑ์มาตรฐาน แจ้ง Feedback ถึงจุดที่สามารถพัฒนาต่อได้",
                "",
                "",
                "ระบบ Fire Pump และ Fire Alarm ทำงานปกติ แนะนำให้ส่งกำหนดการเข้าตรวจล่วงหน้าเร็วขึ้น",
            ),
            (
                3,
                "AVL-EVAL-2026-003",
                "2026-Q3 (ไตรมาส 3/2026)",
                "Quarterly",
                done_prev,
                30,
                "maintenance@sunlogis.com, watcharagorn@sunlogis.com",
                1,
                done_prev,
                "Completed",
                done_prev,
                "วิภาวดี ศรีสวัสดิ์ (DCC / QMR)",
                28.0,
                21.0,
                15.0,
                10.0,
                74.0,
                0,
                "C",
                "ระดับ C (70-79%): ต้องออกเอกสารแจ้งเตือน (Corrective Action Request - CAR) ให้ผู้จัดจำหน่ายทำแผนแก้ไขและปรับปรุงปัญหาภายใน 30-60 วัน",
                "CAR-AVL-2026-001",
                car_due,
                "คะแนนบริการหลังการขาย & SLA (21/30) ต่ำกว่าเกณฑ์ขั้นต่ำที่คาดหวัง (24%) และส่ง Report ล่าช้า ออกใบ CAR ให้แก้ไขภายใน 30 วัน",
            ),
            (
                1,
                "AVL-EVAL-2026-004",
                "2026-Q4 (ประเมินประจำไตรมาส 4 + สรุปรายปี)",
                "Quarterly",
                due_20,
                30,
                "maintenance@sunlogis.com, watcharagorn@sunlogis.com",
                0,
                "",
                "Scheduled",
                "",
                "สมชาย สายชล / คณะกรรมการ AVL",
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                1,
                "-",
                "รอการประเมินตามแผน ข้อ 3.3 (อยู่ในช่วงแจ้งเตือนล่วงหน้า 30 วัน)",
                "",
                "",
                "แผนประเมินผู้จัดจำหน่ายหลักกลุ่ม MHE & Storage Systems รอบไตรมาส 4/2026",
            ),
            (
                2,
                "AVL-EVAL-2026-005",
                "2026-Q4 (ประเมินระบบป้องกันอัคคีภัยประจำปี)",
                "Annual",
                due_28,
                30,
                "maintenance@sunlogis.com, watcharagorn@sunlogis.com",
                0,
                "",
                "Scheduled",
                "",
                "สมชาย สายชล / วิศวกรความปลอดภัย",
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                1,
                "-",
                "รอการประเมินตามแผน ข้อ 3.3 (อยู่ในช่วงแจ้งเตือนล่วงหน้า 30 วัน)",
                "",
                "",
                "แผนประเมินผู้จัดจำหน่ายกลุ่ม Fire Protection Systems ประจำปี",
            ),
        ]
        for (
            vid, ecode, eperiod, freq, pdate, ndays, nemails, nsent, nsent_at,
            st, adate, evaluator, sq, ss, sc, sco, tot, pflag, grd, ract, cno, cdue, rem
        ) in sample_evals:
            cur.execute(
                """
                INSERT INTO vendor_evaluations (
                    vendor_id, eval_code, eval_period, frequency, planned_date,
                    notify_days_before, notify_emails, notification_sent, notification_sent_at,
                    status, actual_date, evaluator, score_quality, score_service_sla,
                    score_cost, score_cooperation, total_score, pass_criteria_flag,
                    grade, review_action, car_no, car_due_date, remarks, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    vid, ecode, eperiod, freq, pdate, ndays, nemails, nsent, nsent_at,
                    st, adate, evaluator, sq, ss, sc, sco, tot, pflag, grd, ract, cno, cdue, rem, now_str
                ),
            )

    if not db_already_seeded:
        # Migrate existing QP -> PM and standardize sample codes to {CAT}-{DEPT}-{NNN} and AVL-EVAL-{YYYY}-{NNN}
        cur.execute("UPDATE documents SET category = 'PM' WHERE category = 'QP'")
        doc_code_migrations = [
            ("QP-DCC-001", "PM-DC-001", "PM"),
            ("QP-HR-002", "PM-HR-001", "PM"),
            ("QM-ISO-001", "QM-MR-001", "QM"),
            ("WI-MHE-001", "WI-WH-001", "WI"),
            ("FM-HR-005", "FM-HR-001", "FM"),
            ("EX-NFPA-025", "EX-WH-001", "EX"),
            ("QM-AVL-001", "QM-MR-002", "QM"),
        ]
        for old_c, new_c, new_cat in doc_code_migrations:
            cur.execute(
                "UPDATE OR IGNORE documents SET doc_code = ?, category = ? WHERE doc_code = ?",
                (new_c, new_cat, old_c),
            )
            cur.execute(
                "UPDATE document_revisions SET doc_code = ? WHERE doc_code = ?",
                (new_c, old_c),
            )

        cur.execute("UPDATE vendors SET category = 'Others' WHERE category = 'Other'")
        eval_code_migrations = [
            ("AVL-EVAL-2026-Q3-01", "AVL-EVAL-2026-001"),
            ("AVL-EVAL-2026-Q3-02", "AVL-EVAL-2026-002"),
            ("AVL-EVAL-2026-Q3-03", "AVL-EVAL-2026-003"),
            ("AVL-EVAL-2026-Q4-01", "AVL-EVAL-2026-004"),
            ("AVL-EVAL-2026-Q4-02", "AVL-EVAL-2026-005"),
        ]
        for old_ec, new_ec in eval_code_migrations:
            cur.execute(
                "UPDATE OR IGNORE vendor_evaluations SET eval_code = ? WHERE eval_code = ?",
                (new_ec, old_ec),
            )
        cur.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('db_seeded', '1')")

    conn.commit()
    conn.close()


def get_settings_dict() -> Dict[str, str]:
    conn = get_db()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()


def update_settings_dict(new_settings: Dict[str, str]) -> None:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    try:
        for k, v in new_settings.items():
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (k, str(v)),
            )
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('settings_updated_at', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (now_str,),
        )
        conn.commit()
    finally:
        conn.close()
