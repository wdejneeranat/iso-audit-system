# SUNLOGIS ISO Audit & Document Control System

ระบบจัดการและจัดเก็บเอกสารสำหรับการตรวจประเมิน **ISO (Document Control, HR Training & MHE/Rack Preventive Maintenance)** เชื่อมต่อกับ **Synology Drive (QuickConnect: `Sunlogis` / Folder: `ISO`)**

## คุณสมบัติหลัก (Features)
1. **การระบุสิทธิ์การเข้าถึงแต่ละเอกสาร (Document Access Control):** กำหนดสิทธิ์ตาม Role (`Admin`, `DCC`, `HR`, `Maintenance`, `Viewer`), แผนก และรายชื่อผู้ใช้เฉพาะบุคคล
2. **บัญชีแม่บทเอกสาร (ISO Master File):** เก็บหมายเลขเอกสาร, ชื่อเอกสาร, วันที่เริ่มใช้, แก้ไขครั้งที่, และวันที่แก้ไข พร้อมประวัติ Revision
3. **Upload ไฟล์เอกสารโดยใช้รหัสเอกสารเป็นชื่อไฟล์อัตโนมัติ:** บังคับเปลี่ยนชื่อไฟล์เป็น `<รหัสเอกสาร>.<นามสกุล>` และซิงค์เข้า Synology Drive (`/DATA/ISO/Documents`)
4. **ค้นหาและกรองเอกสาร:** ค้นหาตามรหัส ชื่อ หมวดหมู่ และ Export Master File เป็น CSV/Excel
5. **ประวัติพนักงานรายบุคคลและการฝึกอบรม (HR & Training Dossier):** เก็บใบสมัครงาน, แผนการฝึกอบรม, บันทึกผลการฝึกอบรมพร้อมไฟล์หลักฐาน ค้นหารายบุคคลตามชื่อหรือรหัสพนักงาน
6. **เชื่อมต่อ Synology Drive อัตโนมัติ:** เชื่อมต่อผ่าน QuickConnect ID `Sunlogis` ไปยังโฟลเดอร์ `ISO`
7. **ทะเบียนอุปกรณ์ MHE, Rack และแผนซ่อมบำรุง (PM):** แจ้งเตือนทางอีเมลล่วงหน้า 30 วันก่อนถึงกำหนดตามแผน พร้อมอัปโหลดหลักฐานการซ่อมบำรุง (PM Evidence)

---

## การรันระบบ (Quick Start)

### 1. รันบนเครื่องคอมพิวเตอร์ (Local / LAN)
```bash
pip install -r requirements.txt
python -m uvicorn app:app --host 0.0.0.0 --port 8000
```
หรือดับเบิลคลิกไฟล์ `start_server.bat` แล้วเปิด `http://localhost:8000`

### 2. รันด้วย Docker
```bash
docker compose up -d --build
```
เปิดเข้าใช้งานที่ `http://localhost:8085`
