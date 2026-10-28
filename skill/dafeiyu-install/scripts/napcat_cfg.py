"""改 NapCat 配置（connect-napcat.ps1 调用）。不打印 token 和 QQ 号。
python napcat_cfg.py --dir C:\\dafeiyu --config-dir <...\\napcat\\config> --uin <小号>
"""
import argparse, json, os, secrets

ap = argparse.ArgumentParser()
ap.add_argument("--dir", required=True)
ap.add_argument("--config-dir", required=True)
ap.add_argument("--uin", required=True)
A = ap.parse_args()
priv = os.path.join(A.dir, "private")
ws = open(os.path.join(priv, "napcat-ws-token.txt"), encoding="utf-8-sig").read().strip()

f = os.path.join(A.config_dir, f"onebot11_{A.uin}.json")
c = json.load(open(f, encoding="utf-8-sig"))
c.setdefault("network", {})["websocketClients"] = [{
    "name": "AstrBot", "enable": True, "url": "ws://127.0.0.1:6199/ws", "messagePostFormat": "array",
    "reportSelfMessage": False, "reconnectInterval": 1000, "token": ws, "debug": False, "heartInterval": 1000}]
json.dump(c, open(f, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

w = os.path.join(A.config_dir, "webui.json")
wc = json.load(open(w, encoding="utf-8-sig")) if os.path.exists(w) else {}
tok = secrets.token_hex(12)
wc.update({"token": tok, "host": "127.0.0.1", "port": 6099, "autoLoginAccount": A.uin})
json.dump(wc, open(w, "w", encoding="utf-8"), ensure_ascii=False, indent=4)
open(os.path.join(priv, "napcat-webui-token.txt"), "w", encoding="utf-8").write(tok)
print("NapCat 已设置：连 ws://127.0.0.1:6199/ws（token 和 AstrBot 一致）；网页后台只听本机 127.0.0.1:6099，token 存 private\\napcat-webui-token.txt；自动登录这个小号")
