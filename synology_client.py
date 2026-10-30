import os
import json
import urllib3
import requests
from typing import Optional, Dict, Any, List, Tuple

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class SynologyClient:
    """
    Client for connecting to Synology NAS via QuickConnect ID or Direct Host/IP
    using Synology File Station API (SYNO.API.Auth, SYNO.FileStation.*).
    Also maintains a local mirror under ./storage/ISO for instant access and fallback.
    """

    def __init__(
        self,
        quickconnect_id: str = "Sunlogis",
        username: str = "Watcharagorn",
        password: str = "Cervelo$3",
        base_folder: str = "ISO",
        custom_host: str = "",
        local_storage_root: str = "./storage",
    ):
        self.quickconnect_id = quickconnect_id.strip()
        self.username = username.strip()
        self.password = password
        self.base_folder = base_folder.strip().strip("/") or "ISO"
        self.custom_host = custom_host.strip().rstrip("/")
        self.local_storage_root = os.path.abspath(local_storage_root)

        self.resolved_base_url: Optional[str] = None
        self.sid: Optional[str] = None
        self.resolved_root_path: Optional[str] = None
        self.last_error: Optional[str] = None

    def _probe_dsm_url(self, base_url: str, timeout: float = 4.0) -> bool:
        try:
            url = f"{base_url.rstrip('/')}/webapi/query.cgi"
            resp = requests.get(
                url,
                params={
                    "api": "SYNO.API.Info",
                    "version": "1",
                    "method": "query",
                    "query": "SYNO.API.Auth,SYNO.FileStation.List",
                },
                verify=False,
                timeout=timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                return bool(data.get("success"))
        except Exception:
            pass
        return False

    def resolve_quickconnect(self) -> List[str]:
        """Resolve QuickConnect ID to candidate DSM URLs (LAN, WAN, Relay)."""
        candidates: List[str] = []
        if self.custom_host:
            if self.custom_host.startswith("http"):
                candidates.append(self.custom_host)
            else:
                candidates.append(f"https://{self.custom_host}:5001")
                candidates.append(f"http://{self.custom_host}:5000")

        if not self.quickconnect_id:
            return candidates

        payload = {
            "version": 1,
            "command": "get_server_info",
            "stop_when_error": False,
            "stop_when_success": False,
            "id": "dsm_portal_https",
            "serverID": self.quickconnect_id,
            "is_gofile": False,
        }

        try:
            resp = requests.post(
                "https://global.quickconnect.to/Serv.php",
                json=payload,
                timeout=6,
                verify=False,
            )
            info = resp.json()
            control_host = info.get("env", {}).get("control_host")
            if info.get("errno") != 0 and control_host:
                resp = requests.post(
                    f"https://{control_host}/Serv.php",
                    json=payload,
                    timeout=6,
                    verify=False,
                )
                info = resp.json()

            if info.get("errno") == 0:
                server = info.get("server", {})
                service = info.get("service", {})
                smartdns = info.get("smartdns", {})
                port = service.get("port", 5001)
                ext_port = service.get("ext_port") or port

                # 1. LAN interfaces
                for iface in server.get("interface", []):
                    ip = iface.get("ip")
                    if ip:
                        candidates.append(f"https://{ip}:{port}")
                        candidates.append(f"http://{ip}:5000")

                for lan_host in smartdns.get("lan", []):
                    candidates.append(f"https://{lan_host}:{port}")

                # 2. Relay / Tunnel if present
                relay_dn = service.get("relay_dn") or service.get("relay_dualstack")
                relay_port = service.get("relay_port")
                if relay_dn and relay_port:
                    candidates.append(f"https://{relay_dn}:{relay_port}")
                relay_ip = service.get("relay_ip")
                if relay_ip and relay_port:
                    candidates.append(f"https://{relay_ip}:{relay_port}")

                # 3. Request tunnel from control_host if needed
                if control_host and not relay_port:
                    try:
                        tunnel_payload = dict(payload)
                        tunnel_payload["command"] = "request_tunnel"
                        t_resp = requests.post(
                            f"https://{control_host}/Serv.php",
                            json=tunnel_payload,
                            timeout=6,
                            verify=False,
                        ).json()
                        t_svc = t_resp.get("service", {})
                        t_dn = t_svc.get("relay_dn") or t_svc.get("relay_ip")
                        t_port = t_svc.get("relay_port")
                        if t_dn and t_port:
                            candidates.append(f"https://{t_dn}:{t_port}")
                    except Exception:
                        pass

                # 4. External IP / DDNS
                ext_ip = server.get("external", {}).get("ip")
                if ext_ip:
                    candidates.append(f"https://{ext_ip}:{ext_port}")
                if server.get("ddns") and server["ddns"] != "NULL":
                    candidates.append(f"https://{server['ddns']}:{ext_port}")
        except Exception as e:
            self.last_error = f"QuickConnect resolution error: {e}"

        # Always include local NAS IP fallback so Docker containers on the NAS work even without external DNS
        candidates.append("https://172.16.100.30:5001")
        candidates.append("http://172.16.100.30:5000")

        # Deduplicate while preserving order
        seen = set()
        unique_candidates = []
        for c in candidates:
            if c not in seen:
                seen.add(c)
                unique_candidates.append(c)
        return unique_candidates

    def connect_and_login(self) -> Tuple[bool, str]:
        """Find working DSM endpoint, authenticate, and resolve the target ISO root folder."""
        candidates = self.resolve_quickconnect()
        working_url = None
        for url in candidates:
            if self._probe_dsm_url(url):
                working_url = url
                break

        if not working_url:
            self.last_error = "ไม่สามารถเชื่อมต่อ Synology NAS ได้ (ตรวจสอบ QuickConnect หรือเครือข่าย)"
            return False, self.last_error

        self.resolved_base_url = working_url
        entry_url = f"{working_url}/webapi/entry.cgi"

        try:
            for ver in ("6", "3", "2"):
                resp = requests.get(
                    entry_url,
                    params={
                        "api": "SYNO.API.Auth",
                        "version": ver,
                        "method": "login",
                        "account": self.username,
                        "passwd": self.password,
                        "session": "FileStation",
                        "format": "sid",
                    },
                    verify=False,
                    timeout=8,
                )
                data = resp.json()
                if data.get("success") and "sid" in data.get("data", {}):
                    self.sid = data["data"]["sid"]
                    break
                elif data.get("error", {}).get("code") in (400, 401, 402, 403, 404):
                    err_code = data.get("error", {}).get("code")
                    self.last_error = f"เข้าสู่ระบบ Synology ไม่สำเร็จ (Error Code: {err_code})"
                    return False, self.last_error

            if not self.sid:
                self.last_error = "ไม่สามารถรับ Session ID (SID) จาก Synology ได้"
                return False, self.last_error

            root_ok, root_msg = self._resolve_or_create_iso_root()
            if not root_ok:
                return False, root_msg

            return True, f"เชื่อมต่อสำเร็จ ({self.resolved_base_url}) โฟลเดอร์: {self.resolved_root_path}"
        except Exception as e:
            self.last_error = f"Synology API Exception: {str(e)}"
            return False, self.last_error

    def _resolve_or_create_iso_root(self) -> Tuple[bool, str]:
        """
        Locate the target folder (e.g. 'ISO') on Synology NAS:
        1. Check if '/ISO' is directly a shared folder.
        2. Check inside '/home/Drive' (Synology Drive root) or '/home' or any writable shared folder.
        3. Automatically create the 'ISO' folder and sub-folders if they don't exist yet.
        """
        entry_url = f"{self.resolved_base_url}/webapi/entry.cgi"
        resp = requests.get(
            entry_url,
            params={
                "api": "SYNO.FileStation.List",
                "version": "2",
                "method": "list_share",
                "_sid": self.sid,
            },
            verify=False,
            timeout=8,
        ).json()

        shares = resp.get("data", {}).get("shares", [])
        share_paths = [s.get("path", "") for s in shares if s.get("path")]

        target_lower = f"/{self.base_folder.lower()}"
        # 1. Exact shared folder match (e.g. /ISO)
        for sp in share_paths:
            if sp.lower() == target_lower:
                self.resolved_root_path = sp
                return True, sp

        # 2. Check if '/home/Drive' exists (Synology Drive default) or '/home'
        preferred_parents = []
        if "/home" in share_paths:
            # Check if /home/Drive exists
            chk = requests.get(
                entry_url,
                params={
                    "api": "SYNO.FileStation.List",
                    "version": "2",
                    "method": "list",
                    "folder_path": "/home",
                    "_sid": self.sid,
                },
                verify=False,
                timeout=6,
            ).json()
            subfolders = [f.get("path") for f in chk.get("data", {}).get("files", []) if f.get("isdir")]
            if "/home/Drive" in subfolders:
                preferred_parents.append("/home/Drive")
            preferred_parents.append("/home")

        # Also check any other shares if /ISO already exists as a subfolder
        for sp in share_paths:
            if sp not in preferred_parents:
                preferred_parents.append(sp)

        for parent in preferred_parents:
            chk = requests.get(
                entry_url,
                params={
                    "api": "SYNO.FileStation.List",
                    "version": "2",
                    "method": "list",
                    "folder_path": parent,
                    "_sid": self.sid,
                },
                verify=False,
                timeout=6,
            ).json()
            for f in chk.get("data", {}).get("files", []):
                if f.get("isdir") and f.get("name", "").lower() == self.base_folder.lower():
                    self.resolved_root_path = f.get("path")
                    return True, self.resolved_root_path

        # 3. Not found yet -> Create inside first preferred parent (e.g. /home/Drive/ISO or first share)
        for parent in preferred_parents:
            created_ok, created_path = self.create_remote_folder(parent, self.base_folder)
            if created_ok:
                self.resolved_root_path = created_path
                return True, created_path

        if share_paths:
            self.resolved_root_path = share_paths[0]
            return True, self.resolved_root_path

        return False, "ไม่พบ Shared Folder ที่เข้าถึงได้ใน Synology NAS"

    def create_remote_folder(self, folder_path: str, name: str) -> Tuple[bool, str]:
        if not self.sid or not self.resolved_base_url:
            ok, msg = self.connect_and_login()
            if not ok:
                return False, msg

        entry_url = f"{self.resolved_base_url}/webapi/entry.cgi"
        resp = requests.get(
            entry_url,
            params={
                "api": "SYNO.FileStation.CreateFolder",
                "version": "2",
                "method": "create",
                "folder_path": json.dumps([folder_path]),
                "name": json.dumps([name]),
                "force_parent": "true",
                "_sid": self.sid,
            },
            verify=False,
            timeout=8,
        ).json()

        if resp.get("success"):
            folders = resp.get("data", {}).get("folders", [])
            if folders:
                return True, folders[0].get("path", f"{folder_path.rstrip('/')}/{name}")
            return True, f"{folder_path.rstrip('/')}/{name}"
        return False, json.dumps(resp.get("error", {}))

    def upload_file(
        self,
        local_file_path: str,
        remote_subfolder: str,
        target_filename: str,
    ) -> Dict[str, Any]:
        """
        Save file to local mirror AND upload to Synology NAS under <ISO_ROOT>/<remote_subfolder>/<target_filename>.
        Returns dict with synology_status, synology_path, local_path, message.
        """
        # 1. Always save to local storage mirror
        clean_sub = remote_subfolder.strip("/")
        local_dir = os.path.join(self.local_storage_root, self.base_folder, clean_sub)
        os.makedirs(local_dir, exist_ok=True)
        local_target = os.path.join(local_dir, target_filename)

        if os.path.abspath(local_file_path) != os.path.abspath(local_target):
            with open(local_file_path, "rb") as src, open(local_target, "wb") as dst:
                dst.write(src.read())

        # 2. Upload to Synology NAS
        ok, msg = self.connect_and_login()
        if not ok:
            return {
                "synology_synced": False,
                "synology_path": f"/{self.base_folder}/{clean_sub}/{target_filename}",
                "local_path": local_target,
                "message": f"บันทึกไฟล์ในระบบแล้ว (รอซิงค์ Synology: {msg})",
            }

        dest_folder = f"{self.resolved_root_path.rstrip('/')}/{clean_sub}" if clean_sub else self.resolved_root_path
        # Ensure subfolder exists
        if clean_sub:
            parts = clean_sub.split("/")
            curr = self.resolved_root_path.rstrip("/")
            for p in parts:
                self.create_remote_folder(curr, p)
                curr = f"{curr}/{p}"

        entry_url = f"{self.resolved_base_url}/webapi/entry.cgi?api=SYNO.FileStation.Upload&version=2&method=upload&_sid={self.sid}"
        try:
            with open(local_target, "rb") as f_obj:
                # Note: Synology FileStation.Upload requires parameters BEFORE the 'file' field in multipart/form-data
                files_payload = [
                    ("path", (None, dest_folder)),
                    ("create_parents", (None, "true")),
                    ("overwrite", (None, "true")),
                    ("file", (target_filename, f_obj, "application/octet-stream")),
                ]
                resp = requests.post(
                    entry_url,
                    files=files_payload,
                    verify=False,
                    timeout=30,
                ).json()

            if resp.get("success"):
                remote_full_path = f"{dest_folder}/{target_filename}"
                return {
                    "synology_synced": True,
                    "synology_path": remote_full_path,
                    "local_path": local_target,
                    "message": f"อัปโหลดไปยัง Synology ({remote_full_path}) สำเร็จ",
                }
            else:
                err_code = resp.get("error", {}).get("code")
                return {
                    "synology_synced": False,
                    "synology_path": f"{dest_folder}/{target_filename}",
                    "local_path": local_target,
                    "message": f"บันทึกไฟล์ในเครื่องแล้ว แต่ Synology ตอบกลับ Error {err_code}",
                }
        except Exception as e:
            return {
                "synology_synced": False,
                "synology_path": f"{dest_folder}/{target_filename}",
                "local_path": local_target,
                "message": f"บันทึกไฟล์ในเครื่องแล้ว (Synology Upload Error: {str(e)})",
            }

    def list_remote_files(self, subfolder: str = "") -> Dict[str, Any]:
        ok, msg = self.connect_and_login()
        if not ok:
            return {"success": False, "message": msg, "files": [], "shares": []}

        entry_url = f"{self.resolved_base_url}/webapi/entry.cgi"
        target_path = (
            f"{self.resolved_root_path.rstrip('/')}/{subfolder.strip('/')}"
            if subfolder.strip("/")
            else self.resolved_root_path
        )
        try:
            resp = requests.get(
                entry_url,
                params={
                    "api": "SYNO.FileStation.List",
                    "version": "2",
                    "method": "list",
                    "folder_path": target_path,
                    "additional": '["size","time","type"]',
                    "_sid": self.sid,
                },
                verify=False,
                timeout=8,
            ).json()

            files = resp.get("data", {}).get("files", [])
            return {
                "success": True,
                "resolved_url": self.resolved_base_url,
                "root_path": self.resolved_root_path,
                "current_path": target_path,
                "files": files,
                "message": msg,
            }
        except Exception as e:
            return {"success": False, "message": str(e), "files": []}

    def download_file_to_local(self, remote_path: str, local_target: str) -> bool:
        """Download a file from Synology FileStation to local_target if needed."""
        ok, _ = self.connect_and_login()
        if not ok:
            return False
        entry_url = f"{self.resolved_base_url}/webapi/entry.cgi"
        try:
            resp = requests.get(
                entry_url,
                params={
                    "api": "SYNO.FileStation.Download",
                    "version": "2",
                    "method": "download",
                    "path": remote_path,
                    "mode": "download",
                    "_sid": self.sid,
                },
                verify=False,
                timeout=30,
                stream=True,
            )
            if resp.status_code == 200 and "application/json" not in resp.headers.get("Content-Type", ""):
                os.makedirs(os.path.dirname(os.path.abspath(local_target)), exist_ok=True)
                with open(local_target, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                return True
        except Exception:
            pass
        return False

