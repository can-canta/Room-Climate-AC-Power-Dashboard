"""
SwitchBot Plug 電力監視 → 閾値未満でOFF
ステートレス設計。スケジューラ(GitHub Actions等)から定期実行する前提。
"""
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import uuid

import requests

API_BASE = "https://api.switch-bot.com/v1.1"

TOKEN = os.environ["SWITCHBOT_TOKEN"]
SECRET = os.environ["SWITCHBOT_SECRET"]
DEVICE_ID = os.environ["SWITCHBOT_DEVICE_ID"]
THRESHOLD_W = float(os.environ.get("THRESHOLD_W", "75"))
MIN_THRESHOLD_W = float(os.environ.get("MIN_THRESHOLD_W", "60"))


def _auth_headers() -> dict:
    t = str(int(time.time() * 1000))
    nonce = str(uuid.uuid4())
    sign_str = (TOKEN + t + nonce).encode("utf-8")
    sign = base64.b64encode(
        hmac.new(SECRET.encode("utf-8"), sign_str, hashlib.sha256).digest()
    ).decode("utf-8")
    return {
        "Authorization": TOKEN,
        "sign": sign,
        "t": t,
        "nonce": nonce,
        "Content-Type": "application/json",
    }


def list_devices() -> None:
    """deviceId検証用ユーティリティ。python switchbot_power_watch.py --list で実行。"""
    r = requests.get(f"{API_BASE}/devices", headers=_auth_headers(), timeout=10)
    r.raise_for_status()
    for d in r.json()["body"]["deviceList"]:
        print(d["deviceId"], d["deviceType"], d["deviceName"])


def get_status() -> dict:
    r = requests.get(
        f"{API_BASE}/devices/{DEVICE_ID}/status", headers=_auth_headers(), timeout=10
    )
    r.raise_for_status()
    return r.json()["body"]


def turn_off() -> None:
    payload = {"command": "turnOff", "parameter": "default", "commandType": "command"}
    r = requests.post(
        f"{API_BASE}/devices/{DEVICE_ID}/commands",
        headers=_auth_headers(),
        data=json.dumps(payload),
        timeout=10,
    )
    r.raise_for_status()
    print(f"[turnOff] statusCode={r.json().get('statusCode')}")


def main() -> None:
    status = get_status()
    power_state = status.get("power")  # "on" / "off"
    watt = status.get("weight")        # API仕様上 weight フィールド = 消費電力[W]
    print(
        f"power_state={power_state} watt={watt}W "
        f"range=[{MIN_THRESHOLD_W},{THRESHOLD_W})W"
    )

    if power_state != "on":
        print("既にOFF。処理不要。")
        return

    if watt is None:
        print(
            "weightフィールド取得不可。deviceTypeがPlug/Plug Mini以外の可能性。",
            file=sys.stderr,
        )
        sys.exit(1)

    if watt < MIN_THRESHOLD_W:
        print(f"{MIN_THRESHOLD_W}W未満。範囲外のため対象外(何もしない)。")
    elif watt < THRESHOLD_W:
        turn_off()
    else:
        print("閾値以上。継続稼働。")


if __name__ == "__main__":
    if "--list" in sys.argv:
        list_devices()
    else:
        main()
