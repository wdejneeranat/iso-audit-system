import os
import json
import sqlite3
import hashlib
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

DB_PATH = os.environ.get(
    "ISO_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "iso_audit.db"),
)
os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
            notes TEXT DEFAULT '',
            updated_at TEXT NOT NULL
        )
        """
    )

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

    # Seed default users if empty
    cur.execute("SELECT COUNT(*) as cnt FROM users")
    if cur.fetchone()["cnt"] == 0:
        default_users = [
            ("admin", hash_password("Cervelo$3"), "Watcharagorn (System Admin)", "Admin", "Management", "watcharagorn@sunlogis.com"),
            ("dcc", hash_password("dcc123"), "เจ้าหน้าที่ควบคุมเอกสาร (DCC)", "DCC", "QMR/ISO", "dcc@sunlogis.com"),
            ("hr", hash_password("hr123"), "เจ้าหน้าที่ทรัพยากรบุคคล (HR)", "HR", "HR", "hr@sunlogis.com"),
            ("maint", hash_password("maint123"), "วิศวกรซ่อมบำรุงและคลังสินค้า (MHE/PM)", "Maintenance", "Warehouse", "maintenance@sunlogis.com"),
            ("viewer", hash_password("viewer123"), "พนักงานทั่วไป / Auditor (Viewer)", "Viewer", "Operations", "auditor@sunlogis.com"),
        ]
        for u, pw, fname, role, dept, email in default_users:
            cur.execute(
                """
                INSERT INTO users (username, password_hash, full_name, role, department, email, is_active, must_change_password, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, 1, ?)
                """,
                (u, pw, fname, role, dept, email, now_str),
            )
    else:
        cur.execute(
            "UPDATE users SET password_hash = ? WHERE username = 'admin' AND must_change_password = 1",
            (hash_password("Cervelo$3"),),
        )

    # Seed sample Master List ISO Documents if empty
    cur.execute("SELECT COUNT(*) as cnt FROM documents")
    if cur.fetchone()["cnt"] == 0:
        sample_docs = [
            (
                "QM-ISO-001",
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
                "QP-DCC-001",
                "ระเบียบปฏิบัติงานการควบคุมเอกสารและบันทึกคุณภาพ (Document & Record Control)",
                "QP",
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
                "QP-HR-002",
                "ระเบียบปฏิบัติงานการสรรหา คัดเลือก และฝึกอบรมบุคลากร (Recruitment & Training)",
                "QP",
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
                "WI-MHE-001",
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
                "FM-HR-005",
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

    # Seed sample Employees & Training Records if empty
    cur.execute("SELECT COUNT(*) as cnt FROM employees")
    if cur.fetchone()["cnt"] == 0:
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

    # Seed sample Equipment (MHE, Rack, etc.) & PM Schedules if empty
    cur.execute("SELECT COUNT(*) as cnt FROM equipment")
    if cur.fetchone()["cnt"] == 0:
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

    conn.commit()
    conn.close()


def get_settings_dict() -> Dict[str, str]:
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


def update_settings_dict(new_settings: Dict[str, str]) -> None:
    conn = get_db()
    for k, v in new_settings.items():
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (k, str(v)),
        )
    conn.commit()
    conn.close()
