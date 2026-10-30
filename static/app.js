// =========================================================
// ISO Audit & Document Control System - Frontend Controller
// =========================================================

let currentUser = null;

let allUsersCache = [];
let allDocumentsCache = [];
let allEmployeesCache = [];
let allEquipmentCache = [];
let allPmCache = [];
let currentDossierEmpId = null;

document.addEventListener("DOMContentLoaded", async () => {
    const saved = sessionStorage.getItem("iso_current_user");
    if (saved) {
        try {
            currentUser = JSON.parse(saved);
            if (currentUser.must_change_password) {
                openChangePasswordScreen(true, "");
                return;
            }
            showMainApp();
            await refreshAllData();
            return;
        } catch (e) {
            sessionStorage.removeItem("iso_current_user");
        }
    }
    showLoginScreen();
});

function showLoginScreen() {
    document.getElementById("loginScreen").classList.remove("hidden");
    document.getElementById("forceChangePasswordScreen").classList.add("hidden");
    document.getElementById("appWrapper").classList.add("hidden");
    const errBox = document.getElementById("loginErrorAlert");
    if (errBox) errBox.classList.add("hidden");
}

function showMainApp() {
    document.getElementById("loginScreen").classList.add("hidden");
    document.getElementById("forceChangePasswordScreen").classList.add("hidden");
    document.getElementById("appWrapper").classList.remove("hidden");
    applyRolePermissionsUI();
    switchTab("dashboard");
}

function openChangePasswordScreen(isFirstLogin = true, prefillCurrentPassword = "") {
    if (!currentUser) return;
    document.getElementById("loginScreen").classList.add("hidden");
    document.getElementById("appWrapper").classList.add("hidden");
    document.getElementById("forceChangePasswordScreen").classList.remove("hidden");

    const subtitle = document.getElementById("forceChangeSubtitle");
    if (subtitle) {
        subtitle.textContent = isFirstLogin
            ? "เพื่อความปลอดภัยของระบบ กรุณาเปลี่ยนรหัสผ่านในการเข้าสู่ระบบครั้งแรกก่อนใช้งาน"
            : "กำหนดรหัสผ่านใหม่สำหรับบัญชีของคุณ";
    }

    const errBox = document.getElementById("changePwErrorAlert");
    if (errBox) errBox.classList.add("hidden");

    document.getElementById("cp_username").value = `${currentUser.username} (${currentUser.full_name})`;
    document.getElementById("cp_current_password").value = prefillCurrentPassword || "";
    document.getElementById("cp_new_password").value = "";
    document.getElementById("cp_confirm_password").value = "";
}

function cancelChangePassword() {
    if (!currentUser || currentUser.must_change_password) {
        handleLogout();
    } else {
        showMainApp();
    }
}

async function submitForceChangePasswordForm(e) {
    e.preventDefault();
    if (!currentUser) return;

    const current_password = document.getElementById("cp_current_password").value;
    const new_password = document.getElementById("cp_new_password").value.trim();
    const confirm_password = document.getElementById("cp_confirm_password").value.trim();
    const errBox = document.getElementById("changePwErrorAlert");
    const errText = document.getElementById("changePwErrorText");

    if (new_password !== confirm_password) {
        errText.textContent = "รหัสผ่านใหม่และการยืนยันรหัสผ่านไม่ตรงกัน";
        errBox.classList.remove("hidden");
        return;
    }
    if (new_password.length < 6) {
        errText.textContent = "รหัสผ่านใหม่ต้องมีความยาวอย่างน้อย 6 ตัวอักษร";
        errBox.classList.remove("hidden");
        return;
    }
    if (new_password === current_password) {
        errText.textContent = "รหัสผ่านใหม่ต้องไม่ซ้ำกับรหัสผ่านเดิม";
        errBox.classList.remove("hidden");
        return;
    }

    try {
        const res = await fetch("/api/auth/change-password", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                username: currentUser.username,
                current_password,
                new_password,
            }),
        });
        const data = await res.json();
        if (!res.ok) {
            errText.textContent = data.detail || "ไม่สามารถเปลี่ยนรหัสผ่านได้";
            errBox.classList.remove("hidden");
            return;
        }

        currentUser.must_change_password = false;
        sessionStorage.setItem("iso_current_user", JSON.stringify(currentUser));
        showMainApp();
        await refreshAllData();
        showToast("เปลี่ยนรหัสผ่านสำเร็จเรียบร้อย ยินดีต้อนรับเข้าสู่ระบบ");
    } catch (err) {
        errText.textContent = "เกิดข้อผิดพลาดในการเชื่อมต่อเซิร์ฟเวอร์";
        errBox.classList.remove("hidden");
    }
}

function handleLogout() {
    currentUser = null;
    sessionStorage.removeItem("iso_current_user");
    document.getElementById("login_password").value = "";
    showLoginScreen();
}

function applyRolePermissionsUI() {
    if (!currentUser) return;
    const isAdmin = currentUser.role === "Admin";

    // Strictly show/hide "Synology Drive (ISO)" and "ตั้งค่า SMTP & ผู้ใช้งาน" for Admin only
    document.querySelectorAll(".admin-only").forEach((el) => {
        el.classList.toggle("hidden", !isAdmin);
    });

    const pmBox = document.getElementById("dashboardPmBox");
    if (pmBox) {
        pmBox.className = isAdmin
            ? "lg:col-span-2 bg-white rounded-2xl shadow-sm border border-slate-200 p-5"
            : "lg:col-span-3 bg-white rounded-2xl shadow-sm border border-slate-200 p-5";
    }

    updateUserHeaderUI();
}

function showToast(message, isError = false) {
    const banner = document.getElementById("toastBanner");
    const icon = document.getElementById("toastIcon");
    const text = document.getElementById("toastText");
    text.textContent = message;
    if (isError) {
        icon.className = "fa-solid fa-triangle-exclamation text-rose-400 text-lg";
    } else {
        icon.className = "fa-solid fa-circle-check text-emerald-400 text-lg";
    }
    banner.classList.remove("hidden");
    setTimeout(() => {
        banner.classList.add("hidden");
    }, 4500);
}

function apiHeaders() {
    return {
        "X-Username": currentUser ? currentUser.username : "",
    };
}

function switchTab(tabName) {
    if ((tabName === "synology" || tabName === "settings") && (!currentUser || currentUser.role !== "Admin")) {
        showToast("เฉพาะผู้ดูแลระบบ (Admin) เท่านั้นที่สามารถเข้าถึงเมนูนี้ได้", true);
        return;
    }

    const tabs = ["dashboard", "documents", "employees", "equipment", "synology", "settings"];
    tabs.forEach((t) => {
        const sec = document.getElementById(`tab-${t}`);
        const btn = document.getElementById(`tabBtn-${t}`);
        if (sec) sec.classList.toggle("hidden", t !== tabName);
        if (btn) btn.classList.toggle("nav-active", t === tabName);
    });

    if (tabName === "dashboard") loadDashboard();
    if (tabName === "documents") loadDocuments();
    if (tabName === "employees") loadEmployees();
    if (tabName === "equipment") loadEquipmentAndPm();
    if (tabName === "synology") loadSynologyExplorer("");
    if (tabName === "settings") {
        loadSystemSettings();
        loadUsersList();
    }
}

function openModal(id) {
    document.getElementById(id).classList.remove("hidden");
}

function closeModal(id) {
    document.getElementById(id).classList.add("hidden");
}

async function refreshAllData() {
    if (!currentUser) return;
    applyRolePermissionsUI();
    await Promise.all([
        loadDashboard(),
        loadDocuments(),
        loadEmployees(),
        loadEquipmentAndPm(),
    ]);
}

function updateUserHeaderUI() {
    if (!currentUser) return;
    document.getElementById("sidebarUserName").textContent = currentUser.full_name;
    document.getElementById("sidebarUserRole").textContent = currentUser.role;
    document.getElementById("sidebarUserDept").textContent = currentUser.department;

    const headerName = document.getElementById("headerUserFullName");
    const headerRole = document.getElementById("headerUserRoleBadge");
    if (headerName) headerName.textContent = currentUser.full_name;
    if (headerRole) headerRole.textContent = `${currentUser.role} (${currentUser.department})`;
}

async function submitLoginForm(e) {
    e.preventDefault();
    const username = document.getElementById("login_username").value.trim();
    const password = document.getElementById("login_password").value;
    const errBox = document.getElementById("loginErrorAlert");
    const errText = document.getElementById("loginErrorText");

    try {
        const res = await fetch("/api/auth/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username, password }),
        });
        const data = await res.json();
        if (!res.ok) {
            if (errBox && errText) {
                errText.textContent = data.detail || "เข้าสู่ระบบไม่สำเร็จ";
                errBox.classList.remove("hidden");
            }
            return;
        }
        currentUser = data.user;
        sessionStorage.setItem("iso_current_user", JSON.stringify(currentUser));

        // Force password change on first login
        if (currentUser.must_change_password) {
            openChangePasswordScreen(true, password);
            return;
        }

        showMainApp();
        await refreshAllData();
        showToast(`ยินดีต้อนรับ ${currentUser.full_name} (สิทธิ์: ${currentUser.role})`);
    } catch (err) {
        if (errBox && errText) {
            errText.textContent = "เกิดข้อผิดพลาดในการเชื่อมต่อเซิร์ฟเวอร์";
            errBox.classList.remove("hidden");
        }
    }
}

// =========================================================
// 1. DASHBOARD
// =========================================================
async function loadDashboard() {
    try {
        const res = await fetch("/api/dashboard/summary", { headers: apiHeaders() });
        const d = await res.json();
        document.getElementById("kpiTotalDocs").textContent = d.total_docs;
        document.getElementById("kpiSyncedDocs").textContent = `ซิงค์ขึ้น Synology (/DATA/ISO) แล้ว ${d.synced_docs} ฉบับ`;
        document.getElementById("kpiTotalEmps").textContent = d.total_emps;
        document.getElementById("kpiTrainings").textContent = `แผนและประวัติฝึกอบรม ${d.total_trainings} รายการ (เสร็จสิ้น ${d.completed_trainings})`;
        document.getElementById("kpiTotalEquip").textContent = d.total_equip;
        document.getElementById("kpiUpcomingPm").textContent = d.upcoming_pm_30d_count;

        const pmList = document.getElementById("dashboardPmList");
        if (!d.upcoming_pm_30d || d.upcoming_pm_30d.length === 0) {
            pmList.innerHTML = `<div class="py-6 text-center text-xs text-slate-400">ไม่มีรายการ PM ที่ครบกำหนดภายใน 30 วัน</div>`;
        } else {
            pmList.innerHTML = d.upcoming_pm_30d
                .map((p) => {
                    const badgeClass =
                        p.days_remaining < 0
                            ? "bg-rose-100 text-rose-700"
                            : "bg-amber-100 text-amber-800";
                    const dayText =
                        p.days_remaining < 0
                            ? `เกินกำหนด ${Math.abs(p.days_remaining)} วัน`
                            : `อีก ${p.days_remaining} วันถึงกำหนด`;
                    return `
                    <div class="py-3 flex flex-wrap items-center justify-between gap-2 text-sm">
                        <div>
                            <div class="flex items-center gap-2">
                                <span class="font-mono font-bold text-xs px-2 py-0.5 rounded bg-slate-100 text-slate-800">${p.equip_code}</span>
                                <span class="font-semibold text-slate-800">${p.equip_name}</span>
                                <span class="text-xs px-2 py-0.5 rounded-full font-semibold ${badgeClass}">${dayText}</span>
                            </div>
                            <p class="text-xs text-slate-500 mt-0.5">${p.pm_code}: ${p.pm_title}</p>
                        </div>
                        <div class="text-right">
                            <div class="text-xs font-bold text-slate-700">กำหนดแผน: ${p.planned_date}</div>
                            <div class="text-[11px] text-slate-500">แจ้งเตือนไปยัง: ${p.notify_emails || "-"}</div>
                        </div>
                    </div>
                `;
                })
                .join("");
        }
    } catch (e) {
        console.error(e);
    }
}

// =========================================================
// 2, 3, 4. ISO MASTER FILE, ACCESS CONTROL & UPLOAD BY CODE
// =========================================================
async function loadDocuments() {
    const q = document.getElementById("docSearchInput")?.value || "";
    const category = document.getElementById("docCategoryFilter")?.value || "";
    const status = document.getElementById("docStatusFilter")?.value || "";

    const url = `/api/documents?q=${encodeURIComponent(q)}&category=${encodeURIComponent(category)}&status=${encodeURIComponent(status)}`;
    const res = await fetch(url, { headers: apiHeaders() });
    const docs = await res.json();
    allDocumentsCache = docs;

    const tbody = document.getElementById("documentsTableBody");
    if (!docs.length) {
        tbody.innerHTML = `<tr><td colspan="8" class="py-8 text-center text-slate-400">ไม่พบรายการเอกสารที่ค้นหา</td></tr>`;
        return;
    }

    tbody.innerHTML = docs
        .map((d) => {
            const revStr = String(d.revision_no).padStart(2, "0");
            const confColors = {
                Public: "bg-emerald-50 text-emerald-700 border-emerald-200",
                Internal: "bg-blue-50 text-blue-700 border-blue-200",
                Restricted: "bg-amber-50 text-amber-700 border-amber-200",
                Confidential: "bg-rose-50 text-rose-700 border-rose-200",
            };
            const confBadge = confColors[d.confidentiality] || confColors.Internal;

            const accessStatusBadge = d.has_access
                ? `<span class="inline-flex items-center gap-1 text-[11px] text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded font-semibold"><i class="fa-solid fa-unlock"></i> มีสิทธิ์เข้าถึง</span>`
                : `<span class="inline-flex items-center gap-1 text-[11px] text-rose-700 bg-rose-50 px-2 py-0.5 rounded font-semibold"><i class="fa-solid fa-lock"></i> จำกัดสิทธิ์</span>`;

            const rolesText = (d.allowed_roles || []).join(", ");
            const deptsText = (d.allowed_departments || []).join(", ");

            const synoBadge = d.synology_synced
                ? `<span class="text-[11px] text-emerald-600 flex items-center gap-1"><i class="fa-solid fa-cloud-check"></i> Synology: ${d.synology_path}</span>`
                : `<span class="text-[11px] text-amber-600 flex items-center gap-1"><i class="fa-solid fa-hard-drive"></i> Local Mirror</span>`;

            const fileActions = d.has_access
                ? `
                <div class="flex items-center gap-1.5 mt-1">
                    <a href="/api/documents/${d.id}/file?username=${encodeURIComponent(currentUser.username)}" target="_blank" class="text-xs bg-blue-50 hover:bg-blue-100 text-blue-700 font-semibold px-2.5 py-1 rounded-lg transition inline-flex items-center gap-1">
                        <i class="fa-solid fa-eye"></i> เปิดดู (${d.file_name || d.doc_code + ".pdf"})
                    </a>
                    <a href="/api/documents/${d.id}/file?username=${encodeURIComponent(currentUser.username)}&download=true" class="text-xs bg-slate-100 hover:bg-slate-200 text-slate-700 px-2 py-1 rounded-lg transition" title="ดาวน์โหลด">
                        <i class="fa-solid fa-download"></i>
                    </a>
                </div>
                `
                : `<div class="text-xs text-rose-600 font-medium mt-1"><i class="fa-solid fa-ban"></i> บัญชี ${currentUser.username} (${currentUser.role}) ไม่มีสิทธิ์เปิดไฟล์นี้</div>`;

            return `
            <tr class="hover:bg-slate-50/80 transition">
                <td class="py-3.5 px-3 align-top">
                    <span class="font-mono font-bold text-blue-700 bg-blue-50 px-2.5 py-1 rounded-lg border border-blue-200">${d.doc_code}</span>
                    <div class="text-[11px] text-slate-400 mt-1">หมวด: ${d.category} | ${d.status}</div>
                </td>
                <td class="py-3.5 px-3 align-top">
                    <div class="font-semibold text-slate-900">${d.doc_title}</div>
                    ${d.notes ? `<div class="text-xs text-slate-500 mt-0.5">${d.notes}</div>` : ""}
                </td>
                <td class="py-3.5 px-3 align-top text-center font-mono text-xs text-slate-700">${d.effective_date}</td>
                <td class="py-3.5 px-3 align-top text-center">
                    <span class="font-mono font-bold px-2.5 py-0.5 rounded bg-slate-100 text-slate-800">Rev.${revStr}</span>
                </td>
                <td class="py-3.5 px-3 align-top text-center font-mono text-xs text-slate-700">${d.revision_date}</td>
                <td class="py-3.5 px-3 align-top">
                    <div class="flex items-center gap-1.5 flex-wrap">
                        <span class="text-[11px] px-2 py-0.5 rounded border font-semibold ${confBadge}">${d.confidentiality}</span>
                        ${accessStatusBadge}
                    </div>
                    <div class="text-[11px] text-slate-500 mt-1">Roles: <strong>${rolesText}</strong></div>
                    <div class="text-[11px] text-slate-500">แผนก: <strong>${deptsText}</strong></div>
                </td>
                <td class="py-3.5 px-3 align-top">
                    ${synoBadge}
                    ${fileActions}
                </td>
                <td class="py-3.5 px-3 align-top text-right whitespace-nowrap">
                    <button onclick="openRevisionHistory(${d.id}, '${d.doc_code}')" class="text-xs bg-slate-100 hover:bg-slate-200 text-slate-700 px-2.5 py-1.5 rounded-lg mr-1" title="ประวัติการแก้ไข">
                        <i class="fa-solid fa-clock-rotate-left"></i>
                    </button>
                    <button onclick="editDocument(${d.id})" class="text-xs bg-amber-50 hover:bg-amber-100 text-amber-700 px-2.5 py-1.5 rounded-lg mr-1" title="แก้ไข / อัปโหลดไฟล์ใหม่">
                        <i class="fa-solid fa-pen-to-square"></i>
                    </button>
                    <button onclick="deleteDocument(${d.id})" class="text-xs bg-rose-50 hover:bg-rose-100 text-rose-600 px-2.5 py-1.5 rounded-lg" title="ลบเอกสาร">
                        <i class="fa-solid fa-trash"></i>
                    </button>
                </td>
            </tr>
            `;
        })
        .join("");
}

function updateExpectedFilenamePreview() {
    const codeInput = document.getElementById("doc_code").value.trim().toUpperCase() || "DOC-CODE";
    const fileInput = document.getElementById("doc_file");
    let ext = ".pdf";
    if (fileInput.files && fileInput.files.length > 0) {
        const name = fileInput.files[0].name;
        const dotIdx = name.lastIndexOf(".");
        if (dotIdx !== -1) ext = name.substring(dotIdx).toLowerCase();
    }
    const safeCode = codeInput.replace(/[\\/*?:"<>|\s]+/g, "_");
    document.getElementById("expectedFilenameText").textContent = `${safeCode}${ext} (ใน Synology: /DATA/ISO/Documents/${safeCode}${ext})`;
}

function openDocumentModal() {
    document.getElementById("documentForm").reset();
    document.getElementById("doc_id").value = "";
    const today = new Date().toISOString().split("T")[0];
    document.getElementById("doc_effective_date").value = today;
    document.getElementById("doc_revision_date").value = today;
    document.getElementById("doc_revision_no").value = 0;
    document.querySelectorAll('input[name="doc_role_chk"]').forEach((c) => (c.checked = true));
    document.querySelectorAll('input[name="doc_dept_chk"]').forEach((c) => (c.checked = c.value === "All"));
    updateExpectedFilenamePreview();
    openModal("documentModal");
}

function editDocument(docId) {
    const d = allDocumentsCache.find((x) => x.id === docId);
    if (!d) return;
    document.getElementById("doc_id").value = d.id;
    document.getElementById("doc_code").value = d.doc_code;
    document.getElementById("doc_category").value = d.category;
    document.getElementById("doc_status").value = d.status;
    document.getElementById("doc_title").value = d.doc_title;
    document.getElementById("doc_effective_date").value = d.effective_date;
    document.getElementById("doc_revision_no").value = d.revision_no;
    document.getElementById("doc_revision_date").value = d.revision_date;
    document.getElementById("doc_confidentiality").value = d.confidentiality;
    document.getElementById("doc_allowed_users").value = (d.allowed_users || []).join(", ");
    document.getElementById("doc_notes").value = d.notes || "";
    document.getElementById("doc_change_summary").value = "";

    document.querySelectorAll('input[name="doc_role_chk"]').forEach((c) => {
        c.checked = (d.allowed_roles || []).includes(c.value);
    });
    document.querySelectorAll('input[name="doc_dept_chk"]').forEach((c) => {
        c.checked = (d.allowed_departments || []).includes(c.value);
    });

    updateExpectedFilenamePreview();
    openModal("documentModal");
}

async function submitDocumentForm(e) {
    e.preventDefault();
    const formData = new FormData();
    const docId = document.getElementById("doc_id").value;
    if (docId) formData.append("doc_id", docId);
    formData.append("doc_code", document.getElementById("doc_code").value.trim().toUpperCase());
    formData.append("category", document.getElementById("doc_category").value);
    formData.append("status", document.getElementById("doc_status").value);
    formData.append("doc_title", document.getElementById("doc_title").value.trim());
    formData.append("effective_date", document.getElementById("doc_effective_date").value);
    formData.append("revision_no", document.getElementById("doc_revision_no").value);
    formData.append("revision_date", document.getElementById("doc_revision_date").value);
    formData.append("confidentiality", document.getElementById("doc_confidentiality").value);

    const roles = Array.from(document.querySelectorAll('input[name="doc_role_chk"]:checked')).map((c) => c.value);
    const depts = Array.from(document.querySelectorAll('input[name="doc_dept_chk"]:checked')).map((c) => c.value);
    const usersRaw = document.getElementById("doc_allowed_users").value;
    const usersList = usersRaw
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);

    formData.append("allowed_roles", JSON.stringify(roles));
    formData.append("allowed_departments", JSON.stringify(depts.length ? depts : ["All"]));
    formData.append("allowed_users", JSON.stringify(usersList));
    formData.append("notes", document.getElementById("doc_notes").value);
    formData.append("change_summary", document.getElementById("doc_change_summary").value);

    const fileInput = document.getElementById("doc_file");
    if (fileInput.files && fileInput.files.length > 0) {
        formData.append("file", fileInput.files[0]);
    }

    try {
        const res = await fetch("/api/documents", {
            method: "POST",
            headers: apiHeaders(),
            body: formData,
        });
        const data = await res.json();
        if (!res.ok) {
            showToast(data.detail || "ไม่สามารถบันทึกเอกสารได้", true);
            return;
        }
        closeModal("documentModal");
        await loadDocuments();
        await loadDashboard();
        showToast(`${data.message} (ชื่อไฟล์: ${data.file_name || data.doc_code})`);
    } catch (err) {
        showToast("เกิดข้อผิดพลาดในการอัปโหลดเอกสาร", true);
    }
}

async function openRevisionHistory(docId, docCode) {
    document.getElementById("revModalTitle").textContent = `ประวัติการแก้ไขเอกสาร: ${docCode}`;
    const res = await fetch(`/api/documents/${docId}/revisions`);
    const revs = await res.json();
    const container = document.getElementById("revisionListContainer");
    container.innerHTML = revs
        .map(
            (r) => `
        <div class="py-3 flex items-center justify-between">
            <div>
                <span class="font-mono font-bold text-xs px-2 py-0.5 rounded bg-blue-50 text-blue-700">ครั้งที่แก้ไข (Rev): ${String(r.revision_no).padStart(2, "0")}</span>
                <span class="text-xs text-slate-600 ml-2">วันที่เริ่มใช้: <strong>${r.effective_date}</strong> | วันที่แก้ไข: <strong>${r.revision_date}</strong></span>
                <div class="text-xs text-slate-800 mt-1">${r.change_summary || "-"}</div>
            </div>
            <div class="text-right text-xs text-slate-500">
                <div>โดย: ${r.updated_by}</div>
                <div class="font-mono text-[11px]">${r.synology_path || r.file_name || ""}</div>
            </div>
        </div>
    `
        )
        .join("");
    openModal("revisionModal");
}

async function deleteDocument(docId) {
    if (!confirm("ยืนยันการลบรายการเอกสารนี้ออกจาก Master File?")) return;
    await fetch(`/api/documents/${docId}`, { method: "DELETE", headers: apiHeaders() });
    await loadDocuments();
    await loadDashboard();
    showToast("ลบเอกสารเรียบร้อยแล้ว");
}

function exportMasterFileCSV() {
    if (!allDocumentsCache.length) {
        showToast("ไม่มีข้อมูลเอกสารสำหรับ Export", true);
        return;
    }
    const headers = [
        "หมายเลขเอกสาร (Document Code)",
        "ชื่อเอกสาร (Document Title)",
        "หมวดหมู่",
        "วันที่เริ่มใช้ (Effective Date)",
        "แก้ไขครั้งที่ (Revision No)",
        "วันที่แก้ไข (Revision Date)",
        "สถานะ",
        "ระดับการเข้าถึง",
        "กลุ่มสิทธิ์ที่เข้าถึงได้",
        "แผนกที่เข้าถึงได้",
        "ชื่อไฟล์ (รหัสเอกสาร)",
        "ที่เก็บใน Synology Drive",
    ];
    const rows = allDocumentsCache.map((d) => [
        d.doc_code,
        `"${(d.doc_title || "").replace(/"/g, '""')}"`,
        d.category,
        d.effective_date,
        d.revision_no,
        d.revision_date,
        d.status,
        d.confidentiality,
        `"${(d.allowed_roles || []).join(", ")}"`,
        `"${(d.allowed_departments || []).join(", ")}"`,
        d.file_name,
        d.synology_path,
    ]);
    const csvContent = "\uFEFF" + [headers.join(","), ...rows.map((r) => r.join(","))].join("\n");
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `ISO_Master_File_${new Date().toISOString().slice(0, 10)}.csv`;
    link.click();
}

// =========================================================
// 5. HR EMPLOYEE RECORDS, JOB APPLICATION & TRAINING
// =========================================================
async function loadEmployees() {
    const q = document.getElementById("empSearchInput")?.value || "";
    const dept = document.getElementById("empDeptFilter")?.value || "";
    const res = await fetch(`/api/employees?q=${encodeURIComponent(q)}&department=${encodeURIComponent(dept)}`);
    const emps = await res.json();
    allEmployeesCache = emps;

    const grid = document.getElementById("employeesGrid");
    if (!emps.length) {
        grid.innerHTML = `<div class="col-span-full py-10 text-center text-slate-400">ไม่พบข้อมูลพนักงานตามคำค้นหา</div>`;
        return;
    }

    grid.innerHTML = emps
        .map((e) => {
            const hasAppFile = Boolean(e.application_file_name);
            return `
            <div class="bg-slate-50/70 hover:bg-white rounded-2xl p-4 border border-slate-200 hover:border-indigo-300 shadow-sm transition flex flex-col justify-between">
                <div>
                    <div class="flex items-start justify-between gap-2">
                        <div>
                            <span class="font-mono font-bold text-xs px-2.5 py-0.5 rounded bg-indigo-100 text-indigo-800">${e.emp_code}</span>
                            <span class="text-xs px-2 py-0.5 rounded bg-slate-200 text-slate-700 ml-1">${e.department}</span>
                        </div>
                        <button onclick="editEmployee(${e.id})" class="text-xs text-slate-400 hover:text-indigo-600" title="แก้ไขข้อมูลพนักงาน">
                            <i class="fa-solid fa-pen"></i>
                        </button>
                    </div>
                    <h4 class="text-base font-bold text-slate-900 mt-2">${e.full_name}</h4>
                    <p class="text-xs text-slate-600">${e.position}</p>
                    <p class="text-[11px] text-slate-400 mt-0.5">วันที่เริ่มงาน: ${e.hire_date} | โทร: ${e.phone || "-"}</p>

                    <div class="mt-3 pt-3 border-t border-slate-200/80 grid grid-cols-2 gap-2 text-xs">
                        <div class="bg-white p-2 rounded-lg border border-slate-200">
                            <div class="text-[11px] text-slate-400">5a. ใบสมัครงาน</div>
                            <div class="font-semibold mt-0.5 ${hasAppFile ? "text-emerald-600" : "text-amber-600"}">
                                ${hasAppFile ? `<i class="fa-solid fa-file-circle-check"></i> แนบไฟล์แล้ว` : `<i class="fa-solid fa-circle-exclamation"></i> รอแนบไฟล์`}
                            </div>
                        </div>
                        <div class="bg-white p-2 rounded-lg border border-slate-200">
                            <div class="text-[11px] text-slate-400">5b,c. ประวัติฝึกอบรม</div>
                            <div class="font-semibold text-indigo-700 mt-0.5">
                                อบรมแล้ว ${e.training_completed}/${e.training_total} หลักสูตร
                            </div>
                        </div>
                    </div>
                </div>

                <div class="mt-4 flex gap-2">
                    <button onclick="openEmployeeDossier(${e.id})" class="flex-1 bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold py-2 px-3 rounded-xl transition flex items-center justify-center gap-1.5">
                        <i class="fa-solid fa-folder-open"></i> เรียกดูแฟ้มประวัติรายบุคคล (5d)
                    </button>
                </div>
            </div>
            `;
        })
        .join("");
}

function openEmployeeModal() {
    document.getElementById("employeeForm").reset();
    document.getElementById("emp_id").value = "";
    document.getElementById("emp_hire_date").value = new Date().toISOString().split("T")[0];
    openModal("employeeFormModal");
}

function editEmployee(empId) {
    const e = allEmployeesCache.find((x) => x.id === empId);
    if (!e) return;
    document.getElementById("emp_id").value = e.id;
    document.getElementById("emp_code").value = e.emp_code;
    document.getElementById("emp_hire_date").value = e.hire_date;
    document.getElementById("emp_full_name").value = e.full_name;
    document.getElementById("emp_department").value = e.department;
    document.getElementById("emp_position").value = e.position;
    document.getElementById("emp_email").value = e.email || "";
    document.getElementById("emp_phone").value = e.phone || "";
    document.getElementById("emp_notes").value = e.notes || "";
    openModal("employeeFormModal");
}

async function submitEmployeeForm(e) {
    e.preventDefault();
    const fd = new FormData();
    const empId = document.getElementById("emp_id").value;
    if (empId) fd.append("emp_id", empId);
    fd.append("emp_code", document.getElementById("emp_code").value.trim().toUpperCase());
    fd.append("hire_date", document.getElementById("emp_hire_date").value);
    fd.append("full_name", document.getElementById("emp_full_name").value.trim());
    fd.append("department", document.getElementById("emp_department").value.trim());
    fd.append("position", document.getElementById("emp_position").value.trim());
    fd.append("email", document.getElementById("emp_email").value.trim());
    fd.append("phone", document.getElementById("emp_phone").value.trim());
    fd.append("notes", document.getElementById("emp_notes").value.trim());

    const appFile = document.getElementById("emp_application_file");
    if (appFile.files && appFile.files.length > 0) {
        fd.append("application_file", appFile.files[0]);
    }

    const res = await fetch("/api/employees", { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) {
        showToast(data.detail || "ไม่สามารถบันทึกข้อมูลพนักงานได้", true);
        return;
    }
    closeModal("employeeFormModal");
    await loadEmployees();
    if (currentDossierEmpId) await openEmployeeDossier(currentDossierEmpId);
    showToast(data.message);
}

async function openEmployeeDossier(empId) {
    currentDossierEmpId = empId;
    const res = await fetch(`/api/employees/${empId}`);
    const e = await res.json();

    document.getElementById("dossierEmpCode").textContent = e.emp_code;
    document.getElementById("dossierEmpName").textContent = e.full_name;
    document.getElementById("dossierEmpMeta").textContent = `แผนก: ${e.department} | ตำแหน่ง: ${e.position} | วันที่เริ่มงาน: ${e.hire_date} | อีเมล: ${e.email || "-"}`;

    const appStatus = document.getElementById("dossierAppFileStatus");
    const appActions = document.getElementById("dossierAppActions");
    if (e.application_file_name) {
        appStatus.innerHTML = `<span class="text-emerald-700 font-medium"><i class="fa-solid fa-check-circle"></i> ไฟล์ใบสมัครงาน: <strong>${e.application_file_name}</strong> (Synology: ${e.application_synology_path || "/DATA/ISO/HR_Employees"})</span>`;
        appActions.innerHTML = `
            <a href="/api/employees/${e.id}/application-file" target="_blank" class="bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold px-3 py-1.5 rounded-lg">
                <i class="fa-solid fa-file-pdf"></i> เปิดดูใบสมัครงาน
            </a>
            <button onclick="editEmployee(${e.id})" class="bg-white border hover:bg-slate-100 text-slate-700 text-xs font-semibold px-3 py-1.5 rounded-lg">
                อัปเดตไฟล์
            </button>
        `;
    } else {
        appStatus.innerHTML = `<span class="text-amber-600">ยังไม่ได้อัปโหลดไฟล์ใบสมัครงาน</span>`;
        appActions.innerHTML = `
            <button onclick="editEmployee(${e.id})" class="bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold px-3 py-1.5 rounded-lg">
                <i class="fa-solid fa-upload"></i> Upload ใบสมัครงาน
            </button>
        `;
    }

    const tbody = document.getElementById("dossierTrainingTableBody");
    if (!e.trainings || !e.trainings.length) {
        tbody.innerHTML = `<tr><td colspan="8" class="py-6 text-center text-slate-400">ยังไม่มีประวัติหรือแผนการฝึกอบรม</td></tr>`;
    } else {
        tbody.innerHTML = e.trainings
            .map((t) => {
                const stBadge =
                    t.status === "Completed"
                        ? `<span class="px-2 py-0.5 rounded bg-emerald-100 text-emerald-800 font-semibold">Completed (อบรมแล้ว)</span>`
                        : `<span class="px-2 py-0.5 rounded bg-amber-100 text-amber-800 font-semibold">Planned (ตามแผน)</span>`;
                const evLink = t.evidence_file_name
                    ? `<a href="/api/trainings/${t.id}/evidence" target="_blank" class="text-emerald-700 hover:underline font-semibold flex items-center gap-1"><i class="fa-solid fa-file-shield"></i> ${t.evidence_file_name}</a>`
                    : `<span class="text-slate-400">ยังไม่มีไฟล์แนบ</span>`;

                return `
                <tr class="hover:bg-slate-50">
                    <td class="py-2.5 px-3">
                        <div class="font-bold text-slate-800">${t.course_code ? `[${t.course_code}] ` : ""}${t.course_title}</div>
                        <div class="text-[11px] text-slate-500">วิทยากร: ${t.trainer || "-"} (${t.duration_hours} ชม.)</div>
                    </td>
                    <td class="py-2.5 px-3">${t.training_type}</td>
                    <td class="py-2.5 px-3 text-center font-mono">${t.planned_date}</td>
                    <td class="py-2.5 px-3 text-center font-mono">${t.actual_date || "-"}</td>
                    <td class="py-2.5 px-3 text-center">${stBadge}</td>
                    <td class="py-2.5 px-3 text-center font-semibold">${t.evaluation_result} ${t.score ? `(${t.score})` : ""}</td>
                    <td class="py-2.5 px-3">${evLink}</td>
                    <td class="py-2.5 px-3 text-right no-print">
                        <button onclick='editTrainingRecord(${JSON.stringify(t).replace(/'/g, "&#39;")})' class="text-xs bg-amber-50 hover:bg-amber-100 text-amber-700 px-2 py-1 rounded mr-1">
                            <i class="fa-solid fa-pen"></i> / Upload
                        </button>
                    </td>
                </tr>
                `;
            })
            .join("");
    }

    openModal("employeeDossierModal");
}

function openTrainingModalForCurrentEmp() {
    document.getElementById("trainingForm").reset();
    document.getElementById("tr_id").value = "";
    document.getElementById("tr_emp_id").value = currentDossierEmpId;
    document.getElementById("tr_planned_date").value = new Date().toISOString().split("T")[0];
    openModal("trainingFormModal");
}

function editTrainingRecord(t) {
    document.getElementById("tr_id").value = t.id;
    document.getElementById("tr_emp_id").value = t.emp_id;
    document.getElementById("tr_course_code").value = t.course_code || "";
    document.getElementById("tr_training_type").value = t.training_type || "In-house";
    document.getElementById("tr_course_title").value = t.course_title || "";
    document.getElementById("tr_planned_date").value = t.planned_date || "";
    document.getElementById("tr_actual_date").value = t.actual_date || "";
    document.getElementById("tr_duration_hours").value = t.duration_hours || 6;
    document.getElementById("tr_status").value = t.status || "Planned";
    document.getElementById("tr_evaluation_result").value = t.evaluation_result || "Pending";
    document.getElementById("tr_score").value = t.score || "";
    document.getElementById("tr_trainer").value = t.trainer || "";
    openModal("trainingFormModal");
}

async function submitTrainingForm(e) {
    e.preventDefault();
    const fd = new FormData();
    const trId = document.getElementById("tr_id").value;
    if (trId) fd.append("training_id", trId);
    fd.append("emp_id", document.getElementById("tr_emp_id").value);
    fd.append("course_code", document.getElementById("tr_course_code").value.trim());
    fd.append("training_type", document.getElementById("tr_training_type").value);
    fd.append("course_title", document.getElementById("tr_course_title").value.trim());
    fd.append("planned_date", document.getElementById("tr_planned_date").value);
    fd.append("actual_date", document.getElementById("tr_actual_date").value);
    fd.append("duration_hours", document.getElementById("tr_duration_hours").value);
    fd.append("status", document.getElementById("tr_status").value);
    fd.append("evaluation_result", document.getElementById("tr_evaluation_result").value);
    fd.append("score", document.getElementById("tr_score").value.trim());
    fd.append("trainer", document.getElementById("tr_trainer").value.trim());

    const evFile = document.getElementById("tr_evidence_file");
    if (evFile.files && evFile.files.length > 0) {
        fd.append("evidence_file", evFile.files[0]);
    }

    const res = await fetch("/api/trainings", { method: "POST", body: fd });
    const data = await res.json();
    closeModal("trainingFormModal");
    await openEmployeeDossier(currentDossierEmpId);
    await loadEmployees();
    showToast(data.message);
}

// =========================================================
// 7. EQUIPMENT (MHE, RACK) & PM 30-DAY EMAIL ALERTS
// =========================================================
async function loadEquipmentAndPm() {
    const q = document.getElementById("equipSearchInput")?.value || "";
    const cat = document.getElementById("equipCatFilter")?.value || "";

    const [eqRes, pmRes, logRes] = await Promise.all([
        fetch(`/api/equipment?q=${encodeURIComponent(q)}&category=${encodeURIComponent(cat)}`),
        fetch(`/api/pm`),
        fetch(`/api/email-logs`),
    ]);

    allEquipmentCache = await eqRes.json();
    allPmCache = await pmRes.json();
    const emailLogs = await logRes.json();

    const eqBody = document.getElementById("equipmentTableBody");
    eqBody.innerHTML = allEquipmentCache
        .map(
            (eq) => `
        <tr class="hover:bg-slate-50">
            <td class="py-3 px-3 font-mono font-bold text-amber-800">${eq.equip_code}</td>
            <td class="py-3 px-3">
                <div class="font-bold text-slate-900">${eq.equip_name}</div>
                <div class="text-xs text-slate-500">${eq.notes || ""}</div>
            </td>
            <td class="py-3 px-3">
                <span class="text-xs px-2.5 py-0.5 rounded-full font-semibold bg-amber-100 text-amber-800">${eq.category}</span>
            </td>
            <td class="py-3 px-3 text-xs">
                <div class="font-semibold">${eq.brand_model || "-"}</div>
                <div class="text-slate-400 font-mono">${eq.serial_no || ""}</div>
            </td>
            <td class="py-3 px-3 text-center font-mono text-xs">
                <div class="font-bold text-slate-800">${eq.purchase_install_date}</div>
                ${eq.warranty_expiry ? `<div class="text-[11px] text-slate-400">ประกัน: ${eq.warranty_expiry}</div>` : ""}
            </td>
            <td class="py-3 px-3 text-xs">
                <div class="font-semibold text-slate-800">${eq.location || "-"}</div>
                <div class="text-slate-500">${eq.responsible_person || ""} (${eq.responsible_email || ""})</div>
            </td>
            <td class="py-3 px-3 text-center">
                <span class="text-xs px-2 py-0.5 rounded bg-emerald-100 text-emerald-800 font-semibold">${eq.status}</span>
            </td>
            <td class="py-3 px-3 text-right">
                <button onclick="editEquipment(${eq.id})" class="text-xs bg-slate-100 hover:bg-slate-200 px-2.5 py-1.5 rounded-lg">
                    <i class="fa-solid fa-pen"></i>
                </button>
            </td>
        </tr>
    `
        )
        .join("");

    const pmBody = document.getElementById("pmTableBody");
    pmBody.innerHTML = allPmCache
        .map((p) => {
            let alertBadge = "";
            if (p.status === "Completed") {
                alertBadge = `<span class="text-xs px-2.5 py-0.5 rounded-full bg-emerald-100 text-emerald-800 font-semibold"><i class="fa-solid fa-check"></i> ดำเนินการแล้ว (${p.actual_date || ""})</span>`;
            } else if (p.days_remaining <= 30) {
                alertBadge = `<span class="text-xs px-2.5 py-0.5 rounded-full bg-rose-100 text-rose-700 font-bold"><i class="fa-solid fa-bell"></i> เหลือ ${p.days_remaining} วัน (อยู่ในเกณฑ์แจ้งเตือน 30 วัน)</span>`;
            } else {
                alertBadge = `<span class="text-xs px-2.5 py-0.5 rounded-full bg-blue-50 text-blue-700">อีก ${p.days_remaining} วันถึงกำหนด</span>`;
            }

            const sentStatus = p.notification_sent
                ? `<div class="text-[11px] text-emerald-600 mt-1"><i class="fa-solid fa-envelope-circle-check"></i> แจ้งเมลแล้วเมื่อ ${p.notification_sent_at}</div>`
                : `<div class="text-[11px] text-slate-400 mt-1">ผู้รับเมล: ${p.notify_emails}</div>`;

            const evDisplay = p.evidence_file_name
                ? `<a href="/api/pm/${p.id}/evidence" target="_blank" class="text-xs text-emerald-700 font-semibold hover:underline flex items-center gap-1"><i class="fa-solid fa-file-circle-check"></i> ${p.evidence_file_name}</a>
                   <div class="text-[10px] font-mono text-slate-400">${p.evidence_synology_path || ""}</div>`
                : `<span class="text-xs text-slate-400">ยังไม่ได้แนบหลักฐาน PM</span>`;

            return `
            <tr class="hover:bg-slate-50">
                <td class="py-3 px-3 font-mono font-bold text-slate-800">${p.pm_code}</td>
                <td class="py-3 px-3">
                    <div class="font-bold text-slate-900">${p.equip_code}</div>
                    <div class="text-xs text-slate-500">${p.equip_name}</div>
                </td>
                <td class="py-3 px-3">
                    <div class="font-semibold text-slate-800">${p.pm_title}</div>
                    <div class="text-xs text-slate-500">ความถี่: ${p.frequency} ${p.result_summary ? `| ผล: ${p.result_summary}` : ""}</div>
                </td>
                <td class="py-3 px-3 text-center font-mono font-bold text-slate-800">${p.planned_date}</td>
                <td class="py-3 px-3 text-center">
                    ${alertBadge}
                    ${sentStatus}
                </td>
                <td class="py-3 px-3">${evDisplay}</td>
                <td class="py-3 px-3 text-right">
                    <button onclick="editPmSchedule(${p.id})" class="bg-blue-50 hover:bg-blue-100 text-blue-700 text-xs font-semibold px-3 py-1.5 rounded-lg">
                        <i class="fa-solid fa-cloud-arrow-up"></i> บันทึกผล / Upload หลักฐาน
                    </button>
                </td>
            </tr>
            `;
        })
        .join("");

    const logsContainer = document.getElementById("emailLogsContainer");
    if (!emailLogs.length) {
        logsContainer.innerHTML = `<div class="py-3 text-center text-slate-400">ยังไม่มีประวัติการส่งอีเมลแจ้งเตือน</div>`;
    } else {
        logsContainer.innerHTML = emailLogs
            .map(
                (l) => `
            <div class="py-2 px-2 flex flex-wrap items-center justify-between gap-2">
                <div>
                    <span class="font-mono font-bold text-slate-700">[${l.sent_at}]</span>
                    <span class="font-semibold text-blue-800 ml-1">${l.equip_code}</span>:
                    <span>${l.subject}</span>
                </div>
                <div class="text-right">
                    <span class="px-2 py-0.5 rounded bg-emerald-100 text-emerald-800 font-semibold">${l.status}</span>
                    <span class="text-slate-500 ml-1">To: ${l.recipients}</span>
                </div>
            </div>
        `
            )
            .join("");
    }
}

function openEquipmentModal() {
    document.getElementById("equipmentForm").reset();
    document.getElementById("eq_id").value = "";
    document.getElementById("eq_purchase_date").value = new Date().toISOString().split("T")[0];
    openModal("equipmentModal");
}

function editEquipment(id) {
    const eq = allEquipmentCache.find((x) => x.id === id);
    if (!eq) return;
    document.getElementById("eq_id").value = eq.id;
    document.getElementById("eq_code").value = eq.equip_code;
    document.getElementById("eq_category").value = eq.category;
    document.getElementById("eq_name").value = eq.equip_name;
    document.getElementById("eq_brand").value = eq.brand_model || "";
    document.getElementById("eq_serial").value = eq.serial_no || "";
    document.getElementById("eq_purchase_date").value = eq.purchase_install_date;
    document.getElementById("eq_warranty_date").value = eq.warranty_expiry || "";
    document.getElementById("eq_location").value = eq.location || "";
    document.getElementById("eq_responsible").value = eq.responsible_person || "";
    document.getElementById("eq_email").value = eq.responsible_email || "";
    openModal("equipmentModal");
}

async function submitEquipmentForm(e) {
    e.preventDefault();
    const idVal = document.getElementById("eq_id").value;
    const payload = {
        id: idVal ? parseInt(idVal) : null,
        equip_code: document.getElementById("eq_code").value.trim().toUpperCase(),
        category: document.getElementById("eq_category").value,
        equip_name: document.getElementById("eq_name").value.trim(),
        brand_model: document.getElementById("eq_brand").value.trim(),
        serial_no: document.getElementById("eq_serial").value.trim(),
        purchase_install_date: document.getElementById("eq_purchase_date").value,
        warranty_expiry: document.getElementById("eq_warranty_date").value,
        location: document.getElementById("eq_location").value.trim(),
        responsible_person: document.getElementById("eq_responsible").value.trim(),
        responsible_email: document.getElementById("eq_email").value.trim(),
        status: "Active",
    };

    const res = await fetch("/api/equipment", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });
    const data = await res.json();
    closeModal("equipmentModal");
    await loadEquipmentAndPm();
    await loadDashboard();
    showToast(data.message);
}

function populatePmEquipmentSelect() {
    const sel = document.getElementById("pm_equip_id");
    sel.innerHTML = allEquipmentCache
        .map((eq) => `<option value="${eq.id}">${eq.equip_code} - ${eq.equip_name}</option>`)
        .join("");
}

function openPmModal() {
    populatePmEquipmentSelect();
    document.getElementById("pmForm").reset();
    document.getElementById("pm_id").value = "";
    const in25Days = new Date(Date.now() + 25 * 86400000).toISOString().split("T")[0];
    document.getElementById("pm_planned_date").value = in25Days;
    document.getElementById("pm_notify_days").value = 30;
    openModal("pmModal");
}

function editPmSchedule(pmId) {
    populatePmEquipmentSelect();
    const p = allPmCache.find((x) => x.id === pmId);
    if (!p) return;
    document.getElementById("pm_id").value = p.id;
    document.getElementById("pm_equip_id").value = p.equip_id;
    document.getElementById("pm_code").value = p.pm_code;
    document.getElementById("pm_title").value = p.pm_title;
    document.getElementById("pm_frequency").value = p.frequency;
    document.getElementById("pm_planned_date").value = p.planned_date;
    document.getElementById("pm_notify_days").value = p.notify_days_before || 30;
    document.getElementById("pm_notify_emails").value = p.notify_emails || "";
    document.getElementById("pm_status").value = p.status;
    document.getElementById("pm_actual_date").value = p.actual_date || new Date().toISOString().split("T")[0];
    document.getElementById("pm_performed_by").value = p.performed_by || "";
    document.getElementById("pm_result_summary").value = p.result_summary || "";
    openModal("pmModal");
}

async function submitPmForm(e) {
    e.preventDefault();
    const fd = new FormData();
    const pmId = document.getElementById("pm_id").value;
    if (pmId) fd.append("pm_id", pmId);
    fd.append("equip_id", document.getElementById("pm_equip_id").value);
    fd.append("pm_code", document.getElementById("pm_code").value.trim().toUpperCase());
    fd.append("pm_title", document.getElementById("pm_title").value.trim());
    fd.append("frequency", document.getElementById("pm_frequency").value);
    fd.append("planned_date", document.getElementById("pm_planned_date").value);
    fd.append("notify_days_before", document.getElementById("pm_notify_days").value);
    fd.append("notify_emails", document.getElementById("pm_notify_emails").value.trim());
    fd.append("status", document.getElementById("pm_status").value);
    fd.append("actual_date", document.getElementById("pm_actual_date").value);
    fd.append("performed_by", document.getElementById("pm_performed_by").value.trim());
    fd.append("result_summary", document.getElementById("pm_result_summary").value.trim());

    const evFile = document.getElementById("pm_evidence_file");
    if (evFile.files && evFile.files.length > 0) {
        fd.append("evidence_file", evFile.files[0]);
    }

    const res = await fetch("/api/pm", { method: "POST", body: fd });
    const data = await res.json();
    closeModal("pmModal");
    await loadEquipmentAndPm();
    await loadDashboard();
    showToast(data.message);
}

async function triggerPmAlertsNow(force = true) {
    showToast("กำลังตรวจสอบแผน PM ล่วงหน้า 30 วันและส่งอีเมลแจ้งผู้เกี่ยวข้อง...");
    const res = await fetch(`/api/pm/check-alerts?force=${force}`, { method: "POST" });
    const data = await res.json();
    await loadEquipmentAndPm();
    await loadDashboard();
    showToast(`ดำเนินการแจ้งเตือนแผน PM ล่วงหน้า 30 วันจำนวน ${data.triggered_count} รายการเรียบร้อย`);
}

// =========================================================
// 6. SYNOLOGY DRIVE EXPLORER & SETTINGS (ADMIN ONLY)
// =========================================================
async function loadSynologyExplorer(subfolder = "") {
    if (!currentUser || currentUser.role !== "Admin") return;
    const subtitle = document.getElementById("synoStatusSubtitle");
    const listEl = document.getElementById("synoFilesList");
    subtitle.textContent = "กำลังเชื่อมต่อ Synology NAS (QuickConnect: Sunlogis)...";

    try {
        const res = await fetch(`/api/synology/status?subfolder=${encodeURIComponent(subfolder)}`, {
            headers: apiHeaders(),
        });
        const data = await res.json();
        if (!res.ok || !data.success) {
            subtitle.textContent = data.detail || data.message || "ไม่สามารถเชื่อมต่อได้";
            listEl.innerHTML = `<div class="p-6 text-center text-rose-600 text-sm">${data.detail || data.message}</div>`;
            return;
        }
        subtitle.textContent = data.message;
        document.getElementById("synoResolvedUrl").textContent = data.resolved_url || "Connected";
        document.getElementById("synoCurrentPathDisplay").textContent = `Path: ${data.current_path}`;

        if (!data.files || !data.files.length) {
            listEl.innerHTML = `<div class="p-8 text-center text-slate-400 text-sm">โฟลเดอร์นี้ยังว่างอยู่ (${data.current_path})</div>`;
            return;
        }

        listEl.innerHTML = data.files
            .map((f) => {
                const isDir = f.isdir;
                const relSub = f.path.replace(data.root_path, "").replace(/^\/+/, "");
                return `
                <div class="p-3.5 bg-white hover:bg-slate-50 flex items-center justify-between text-sm">
                    <div class="flex items-center gap-3">
                        <i class="fa-solid ${isDir ? "fa-folder text-amber-500 text-lg" : "fa-file-pdf text-blue-600 text-lg"}"></i>
                        <div>
                            <div class="font-semibold text-slate-800">${f.name}</div>
                            <div class="text-xs font-mono text-slate-400">${f.path}</div>
                        </div>
                    </div>
                    <div>
                        ${
                            isDir
                                ? `<button onclick="loadSynologyExplorer('${relSub}')" class="text-xs bg-slate-100 hover:bg-slate-200 text-slate-700 font-semibold px-3 py-1.5 rounded-lg">เปิดโฟลเดอร์</button>`
                                : `<span class="text-xs px-2.5 py-1 rounded bg-emerald-50 text-emerald-700 font-mono">Stored on Synology NAS</span>`
                        }
                    </div>
                </div>
                `;
            })
            .join("");
    } catch (e) {
        subtitle.textContent = "ไม่สามารถดึงรายการไฟล์จาก Synology ได้";
    }
}

async function syncAllToSynology() {
    if (!currentUser || currentUser.role !== "Admin") return;
    showToast("กำลังซิงค์ไฟล์ทั้งหมดในระบบขึ้น Synology Drive (/DATA/ISO)...");
    const res = await fetch("/api/synology/sync-all", { method: "POST", headers: apiHeaders() });
    const data = await res.json();
    await loadDocuments();
    await loadSynologyExplorer("");
    showToast(data.message || data.detail);
}

async function loadSystemSettings() {
    if (!currentUser || currentUser.role !== "Admin") return;
    const res = await fetch("/api/settings", { headers: apiHeaders() });
    if (!res.ok) return;
    const cfg = await res.json();
    const keys = [
        "synology_quickconnect",
        "synology_folder",
        "synology_user",
        "synology_password",
        "smtp_host",
        "smtp_port",
        "smtp_user",
        "smtp_password",
        "smtp_sender",
        "pm_advance_days",
    ];
    keys.forEach((k) => {
        const el = document.getElementById(`cfg_${k}`);
        if (el && cfg[k] !== undefined) el.value = cfg[k];
    });
}

async function saveSystemSettings() {
    if (!currentUser || currentUser.role !== "Admin") return;
    const keys = [
        "synology_quickconnect",
        "synology_folder",
        "synology_user",
        "synology_password",
        "smtp_host",
        "smtp_port",
        "smtp_user",
        "smtp_password",
        "smtp_sender",
        "pm_advance_days",
    ];
    const payload = {};
    keys.forEach((k) => {
        const el = document.getElementById(`cfg_${k}`);
        if (el) payload[k] = el.value;
    });
    const res = await fetch("/api/settings", {
        method: "POST",
        headers: { ...apiHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });
    const data = await res.json();
    showToast(data.message || data.detail);
}

async function testSmtpEmail() {
    if (!currentUser || currentUser.role !== "Admin") return;
    const recipient = prompt("ระบุอีเมลผู้รับเพื่อทดสอบการส่งแจ้งเตือน PM:", "watcharagorn@sunlogis.com");
    if (!recipient) return;
    await saveSystemSettings();
    const res = await fetch(`/api/settings/test-email?recipient=${encodeURIComponent(recipient)}`, {
        method: "POST",
        headers: apiHeaders(),
    });
    const data = await res.json();
    showToast(data.message || data.detail, !data.success);
}

async function loadUsersList() {
    if (!currentUser || currentUser.role !== "Admin") return;
    const res = await fetch("/api/users", { headers: apiHeaders() });
    if (!res.ok) return;
    const users = await res.json();
    allUsersCache = users;

    const container = document.getElementById("usersListContainer");
    if (container) {
        container.innerHTML = users
            .map(
                (u) => `
            <div class="py-2.5 flex items-center justify-between">
                <div>
                    <span class="font-bold text-slate-800">${u.full_name}</span>
                    <span class="text-xs font-mono text-slate-500 ml-1">(@${u.username})</span>
                    <div class="flex gap-1.5 mt-0.5">
                        <span class="text-[11px] px-2 py-0.5 rounded bg-blue-50 text-blue-700 font-semibold">Role: ${u.role}</span>
                        <span class="text-[11px] px-2 py-0.5 rounded bg-slate-100 text-slate-600">แผนก: ${u.department}</span>
                    </div>
                </div>
                <div>
                    ${
                        u.id !== 1
                            ? `<button onclick="deleteUser(${u.id})" class="text-xs text-rose-600 hover:bg-rose-50 px-2.5 py-1 rounded"><i class="fa-solid fa-trash"></i></button>`
                            : `<span class="text-xs text-slate-400">System Admin</span>`
                    }
                </div>
            </div>
        `
            )
            .join("");
    }
}

async function createNewUser() {
    if (!currentUser || currentUser.role !== "Admin") return;
    const username = document.getElementById("new_u_username").value.trim();
    const password = document.getElementById("new_u_password").value;
    const full_name = document.getElementById("new_u_fullname").value.trim();
    const role = document.getElementById("new_u_role").value;
    const department = document.getElementById("new_u_dept").value;
    if (!username || !password || !full_name) {
        showToast("กรุณากรอก Username, รหัสผ่าน และชื่อ-นามสกุล", true);
        return;
    }
    const res = await fetch("/api/users", {
        method: "POST",
        headers: { ...apiHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ username, password, full_name, role, department }),
    });
    const data = await res.json();
    if (!res.ok) {
        showToast(data.detail || "ไม่สามารถเพิ่มผู้ใช้ได้", true);
        return;
    }
    document.getElementById("new_u_username").value = "";
    document.getElementById("new_u_password").value = "";
    document.getElementById("new_u_fullname").value = "";
    await loadUsersList();
    showToast(data.message);
}

async function deleteUser(uid) {
    if (!currentUser || currentUser.role !== "Admin") return;
    if (!confirm("ยืนยันการลบผู้ใช้งานนี้?")) return;
    await fetch(`/api/users/${uid}`, { method: "DELETE", headers: apiHeaders() });
    await loadUsersList();
}
