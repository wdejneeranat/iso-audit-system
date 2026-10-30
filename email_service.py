import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import Dict, Any, List, Tuple
from database import get_db, get_settings_dict


def send_smtp_email(
    recipients_csv: str,
    subject: str,
    html_body: str,
    pm_id: int = None,
    equip_code: str = "",
) -> Tuple[bool, str]:
    """
    Send an email using the configured SMTP server in Settings.
    Logs every attempt to the email_logs table.
    """
    cfg = get_settings_dict()
    smtp_host = cfg.get("smtp_host", "").strip()
    smtp_port = int(cfg.get("smtp_port", "587") or 587)
    smtp_user = cfg.get("smtp_user", "").strip()
    smtp_password = cfg.get("smtp_password", "")
    smtp_sender = cfg.get("smtp_sender", "").strip() or smtp_user or "iso-notify@sunlogis.com"
    use_tls = cfg.get("smtp_use_tls", "true").lower() == "true"

    recipients = [r.strip() for r in recipients_csv.replace(";", ",").split(",") if r.strip()]
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not recipients:
        return False, "ไม่พบรายชื่ออีเมลผู้รับ (Recipients)"

    conn = get_db()
    if not smtp_host or not smtp_user or not smtp_password:
        # Log as Pending SMTP / Simulated Alert so user sees full audit trail even before entering SMTP password
        msg_detail = "สร้างรายการแจ้งเตือนในระบบแล้ว (กรุณาระบุ SMTP Username/Password ในหน้าตั้งค่าเพื่อส่งออกไปยัง Mail Server จริง)"
        conn.execute(
            """
            INSERT INTO email_logs (pm_id, equip_code, recipients, subject, body_preview, status, error_message, sent_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (pm_id, equip_code, ", ".join(recipients), subject, html_body[:500], "Simulated (Pending SMTP Auth)", msg_detail, now_str),
        )
        conn.commit()
        conn.close()
        return True, msg_detail

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = smtp_sender
        msg["To"] = ", ".join(recipients)
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        if smtp_port == 465:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=12)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=12)
            if use_tls:
                server.starttls()

        server.login(smtp_user, smtp_password)
        server.sendmail(smtp_sender, recipients, msg.as_string())
        server.quit()

        conn.execute(
            """
            INSERT INTO email_logs (pm_id, equip_code, recipients, subject, body_preview, status, error_message, sent_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (pm_id, equip_code, ", ".join(recipients), subject, html_body[:500], "Sent", "", now_str),
        )
        conn.commit()
        conn.close()
        return True, f"ส่งอีเมลแจ้งเตือนไปยัง {', '.join(recipients)} สำเร็จ"
    except Exception as e:
        err_str = str(e)
        conn.execute(
            """
            INSERT INTO email_logs (pm_id, equip_code, recipients, subject, body_preview, status, error_message, sent_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (pm_id, equip_code, ", ".join(recipients), subject, html_body[:500], "Failed", err_str, now_str),
        )
        conn.commit()
        conn.close()
        return False, f"ส่งอีเมลไม่สำเร็จ: {err_str}"


def check_and_send_pm_notifications(force_resend: bool = False) -> Dict[str, Any]:
    """
    Scan all PM schedules that are not Completed.
    If days remaining until planned_date <= notify_days_before (default 30 days),
    send email notification to stakeholders.
    """
    cfg = get_settings_dict()
    default_advance_days = int(cfg.get("pm_advance_days", "30") or 30)

    conn = get_db()
    rows = conn.execute(
        """
        SELECT p.*, e.equip_code, e.equip_name, e.category, e.location, e.responsible_person, e.responsible_email
        FROM pm_schedules p
        JOIN equipment e ON p.equip_id = e.id
        WHERE p.status != 'Completed'
        ORDER BY p.planned_date ASC
        """
    ).fetchall()
    conn.close()

    today = datetime.now().date()
    triggered = []
    errors = []

    for r in rows:
        try:
            planned_dt = datetime.strptime(r["planned_date"], "%Y-%m-%d").date()
        except Exception:
            continue

        days_left = (planned_dt - today).days
        advance_window = r["notify_days_before"] or default_advance_days

        if days_left <= advance_window and (not r["notification_sent"] or force_resend):
            recipients = r["notify_emails"] or r["responsible_email"]
            urgency_label = (
                f"เกินกำหนด {abs(days_left)} วัน (OVERDUE)"
                if days_left < 0
                else f"อีก {days_left} วันจะถึงกำหนด (แจ้งเตือนล่วงหน้า 30 วัน)"
            )
            subject = f"[ISO PM Alert] แจ้งเตือนแผนบำรุงรักษา {r['equip_code']} ({r['equip_name']}) - กำหนดวันที่ {r['planned_date']}"
            html_body = f"""
            <div style="font-family: 'Sarabun', sans-serif; max-width: 640px; border: 1px solid #e2e8f0; border-radius: 10px; overflow: hidden;">
                <div style="background: #1e3a8a; color: #ffffff; padding: 16px 20px;">
                    <h2 style="margin: 0; font-size: 18px;">แจ้งเตือนแผนการตรวจเช็คและซ่อมบำรุงรักษา (PM Notification)</h2>
                    <p style="margin: 4px 0 0; font-size: 13px; opacity: 0.9;">ระบบจัดการเอกสารและการตรวจประเมิน ISO (Sunlogis)</p>
                </div>
                <div style="padding: 20px; background: #ffffff; color: #1e293b; line-height: 1.6;">
                    <p>เรียน ผู้เกี่ยวข้อง ({r['responsible_person'] or 'ทีมซ่อมบำรุงและคลังสินค้า'}),</p>
                    <p>ระบบขอแจ้งเตือนล่วงหน้าสำหรับแผนการตรวจเช็ค/ซ่อมบำรุงรักษา (Preventive Maintenance) โดยมีรายละเอียดดังนี้:</p>
                    <table style="width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 14px;">
                        <tr><td style="padding: 8px; border-bottom: 1px solid #f1f5f9; font-weight: bold; width: 35%;">รหัสแผน PM:</td><td style="padding: 8px; border-bottom: 1px solid #f1f5f9;">{r['pm_code']}</td></tr>
                        <tr><td style="padding: 8px; border-bottom: 1px solid #f1f5f9; font-weight: bold;">รหัสอุปกรณ์:</td><td style="padding: 8px; border-bottom: 1px solid #f1f5f9;">{r['equip_code']} ({r['category']})</td></tr>
                        <tr><td style="padding: 8px; border-bottom: 1px solid #f1f5f9; font-weight: bold;">ชื่อเครื่องจักร/อุปกรณ์:</td><td style="padding: 8px; border-bottom: 1px solid #f1f5f9;">{r['equip_name']}</td></tr>
                        <tr><td style="padding: 8px; border-bottom: 1px solid #f1f5f9; font-weight: bold;">สถานที่ตั้ง:</td><td style="padding: 8px; border-bottom: 1px solid #f1f5f9;">{r['location']}</td></tr>
                        <tr><td style="padding: 8px; border-bottom: 1px solid #f1f5f9; font-weight: bold;">รายการตรวจเช็ค:</td><td style="padding: 8px; border-bottom: 1px solid #f1f5f9;">{r['pm_title']}</td></tr>
                        <tr><td style="padding: 8px; border-bottom: 1px solid #f1f5f9; font-weight: bold;">วันที่ตามแผน:</td><td style="padding: 8px; border-bottom: 1px solid #f1f5f9; color: #dc2626; font-weight: bold;">{r['planned_date']} ({urgency_label})</td></tr>
                    </table>
                    <p style="font-size: 13px; color: #475569;">กรุณาดำเนินการตรวจเช็คตามแผน และอัปโหลดหลักฐานการซ่อมบำรุงรักษา (PM Evidence) เข้าสู่ระบบ ISO Audit Management เพื่อจัดเก็บลง Synology Drive (/DATA/ISO)</p>
                </div>
            </div>
            """
            ok, msg = send_smtp_email(recipients, subject, html_body, pm_id=r["id"], equip_code=r["equip_code"])
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if ok:
                c2 = get_db()
                new_status = "Overdue" if days_left < 0 else "Notified"
                c2.execute(
                    """
                    UPDATE pm_schedules
                    SET notification_sent = 1, notification_sent_at = ?, status = ?
                    WHERE id = ?
                    """,
                    (now_str, new_status, r["id"]),
                )
                c2.commit()
                c2.close()
                triggered.append(
                    {
                        "pm_id": r["id"],
                        "pm_code": r["pm_code"],
                        "equip_code": r["equip_code"],
                        "planned_date": r["planned_date"],
                        "days_left": days_left,
                        "recipients": recipients,
                        "message": msg,
                    }
                )
            else:
                errors.append({"pm_code": r["pm_code"], "error": msg})

    return {
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "triggered_count": len(triggered),
        "triggered": triggered,
        "errors": errors,
    }
