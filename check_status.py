import json, requests, sys
sys.stdout.reconfigure(encoding='utf-8')

with open("config.json") as f:
    cfg = json.load(f)

token = cfg.get("bot_token", "")
admin = cfg.get("admin_id")
gid   = cfg.get("forward_group_id")

print("=== Config Check ===")
print(f"Bot Token  : {token[:20]}...  OK" if token else "Bot Token  : MISSING!")
print(f"Admin ID   : {admin}")
print(f"Forward GID: {gid}")

# Bot connection
r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=8)
d = r.json()
if d.get("ok"):
    b = d["result"]
    print("")
    print("=== Bot Info ===")
    print(f"Name     : {b['first_name']}")
    print(f"Username : @{b['username']}")
    print(f"Bot ID   : {b['id']}")
    print(f"Status   : ONLINE [OK]")
else:
    print(f"Bot API  : ERROR - {d}")

# Forward group access
if gid:
    r2 = requests.get(
        f"https://api.telegram.org/bot{token}/getChat",
        params={"chat_id": gid}, timeout=8
    )
    d2 = r2.json()
    print("")
    print("=== Forward Group ===")
    print(f"Group ID : {gid}")
    if d2.get("ok"):
        chat = d2["result"]
        print(f"Title    : {chat.get('title','?')}")
        print(f"Type     : {chat.get('type','?')}")
        print(f"Access   : OK - Bot can see the group [OK]")
    else:
        print(f"Access   : FAIL [X]")
        print(f"Reason   : {d2.get('description', d2)}")
        print(f"Fix      : Add the bot to the group first!")
else:
    print("Forward Group: NOT SET")
