import os
import re
import json
from decimal import Decimal
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VERSION_FILE = os.path.join(BASE_DIR, "version.json")
INDEX_FILE = os.path.join(BASE_DIR, "static", "index.html")


def bump_version():
    curr = Decimal("1.0")
    if os.path.exists(VERSION_FILE):
        try:
            with open(VERSION_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                curr = Decimal(str(data.get("version", "1.0")))
        except Exception:
            pass

    new_ver = str(curr + Decimal("0.1"))
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {"version": new_ver, "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
            f,
            indent=2,
        )

    if os.path.exists(INDEX_FILE):
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            html = f.read()
        html = re.sub(r"Version\s+\d+\.\d+", f"Version {new_ver}", html)
        with open(INDEX_FILE, "w", encoding="utf-8") as f:
            f.write(html)

    print(f"Bumped application version to Version {new_ver}")


if __name__ == "__main__":
    bump_version()
