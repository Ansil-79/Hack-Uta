"""Admin panel for the NFC blackjack demo: create players in MongoDB and write their chips.

Run it on the computer that has the ACR122U reader plugged in:

    python3 -m pip install pyscard "pymongo[srv]>=4.2,<5"
    read -rs 'MONGODB_URI?MongoDB URI: ';       export MONGODB_URI
    read -rs 'ADMIN_PASSWORD?Admin password: '; export ADMIN_PASSWORD
    python3 admin_panel.py                      # this computer only
    python3 admin_panel.py --host 0.0.0.0       # reachable from other devices on the network

Without pyscard or a reader the panel still manages players; it just can't write chips.
"""

import argparse
import base64
import hmac
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError

try:
    from smartcard.Exceptions import CardConnectionException, NoCardException
    from smartcard.System import readers
    CHIP_SUPPORT = True
except ImportError:
    CHIP_SUPPORT = False

ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,40}")  # same rule the NFC server enforces
MAX_BALANCE = 10_000_000
CHIP_BYTES = 144  # nfc_server.py reads pages 4-39 (144 bytes) of user memory
players = None
chip_lock = threading.Lock()


class ChipError(ValueError):
    pass


class ChipConflict(ChipError):
    pass


# ── Player data ────────────────────────────────────────────────────────────

def clean_balance(value):
    if type(value) is not int or not 0 <= value <= MAX_BALANCE:
        raise ValueError(f"Balance must be a whole number from 0 to {MAX_BALANCE}.")
    return value


def clean_name(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 40:
        raise ValueError("Name is required (up to 40 characters).")
    return value.strip()


def next_player_id():
    taken = [int(d["_id"][1:]) for d in players.find({"_id": {"$regex": r"^P\d+$"}}, {"_id": 1})]
    return f"P{max(taken, default=0) + 1:03d}"


def insert_player(player_id, name, balance):
    try:
        players.insert_one({"_id": player_id, "name": name, "balance": balance, "revision": 0})
    except DuplicateKeyError:
        raise ValueError(f"Player ID {player_id} already exists.")


def set_balance(player_id, data):
    balance = clean_balance(data.get("balance"))
    # Bumping revision makes any open game tab drop its stale snapshot.
    result = players.update_one({"_id": player_id}, {"$set": {"balance": balance}, "$inc": {"revision": 1}})
    if result.matched_count == 0:
        raise ValueError("Player not found.")


def delete_player(player_id):
    if players.delete_one({"_id": player_id}).deleted_count == 0:
        raise ValueError("Player not found.")


# ── Chip (NTAG215 over the ACR122U) ────────────────────────────────────────

def parse_ndef_text(raw):
    """Same parser as nfc_server.py, so what we verify is what the game reads."""
    i = 0
    while i < len(raw):
        if raw[i] == 0x00: i += 1; continue
        if raw[i] == 0xFE: break
        tag = raw[i]; i += 1
        if i >= len(raw): break
        length = raw[i]; i += 1
        if tag != 0x03: i += length; continue
        ndef = raw[i: i + length]
        j = 0
        while j < len(ndef):
            flags = ndef[j]; j += 1
            tlen = ndef[j]; j += 1
            sr = (flags >> 4) & 1
            plen = ndef[j] if sr else int.from_bytes(ndef[j:j+4], 'big')
            j += 1 if sr else 4
            rtype = ndef[j: j + tlen]; j += tlen
            pay = ndef[j: j + plen]; j += plen
            if (flags & 7) == 1 and rtype == b'T' and len(pay) > 1:
                lang_len = pay[0] & 0x3F
                return pay[1 + lang_len:].decode("utf-8", errors="replace")
        i += length
    return None


def identity_of(raw):
    try:
        identity = json.loads(parse_ndef_text(raw) or "null")
    except Exception:
        return None
    if isinstance(identity, dict) and isinstance(identity.get("id"), str) and isinstance(identity.get("name"), str):
        return identity["id"], identity["name"].strip()
    return None


def encode_identity(player_id, name):
    text = json.dumps({"id": player_id, "name": name}, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    payload = b"\x02en" + text  # Text record: UTF-8, language "en"
    record = bytes([0xD1, 0x01, len(payload)]) + b"T" + payload  # MB|ME|SR, well-known Text
    tlv = bytes([0x03, len(record)]) + record + b"\xFE"
    if len(record) > 250 or len(tlv) > CHIP_BYTES:
        raise ChipError("That name is too long to fit on the chip.")
    return tlv.ljust(CHIP_BYTES, b"\x00")  # zero-fill also wipes any old data


def read_user_memory(conn):
    raw = bytearray()
    for page in range(4, 40):
        data, sw1, _ = conn.transmit([0xFF, 0xB0, 0x00, page, 0x04])
        if sw1 != 0x90:
            break
        raw.extend(data)
    return bytes(raw)


def open_chip(timeout):
    if not CHIP_SUPPORT:
        raise ChipError("Chip writing needs pyscard: python3 -m pip install pyscard")
    found = readers()
    if not found:
        raise ChipError("No NFC reader found. Plug in the ACR122U.")
    deadline = time.monotonic() + timeout
    while True:
        conn = found[0].createConnection()
        try:
            conn.connect()
            return conn
        except (NoCardException, CardConnectionException):
            if time.monotonic() >= deadline:
                raise ChipError("No chip detected. Hold the chip flat on the reader and try again.")
            time.sleep(0.4)


@contextmanager
def chip_session(timeout=8):
    with chip_lock:
        conn = open_chip(timeout)
        try:
            yield conn
        finally:
            try:
                conn.disconnect()
            except Exception:
                pass


def check_chip_usable(conn, player_id, overwrite):
    try:
        capability, sw1, _ = conn.transmit([0xFF, 0xB0, 0x00, 0x03, 0x04])
        if sw1 != 0x90 or capability[0] != 0xE1:
            raise ChipError("This chip isn't NDEF-formatted. In NFC Tools use Other → Format tag, then try again.")
        owner = identity_of(read_user_memory(conn))
    except ChipError:
        raise
    except Exception:
        raise ChipError("Couldn't read the chip. Keep it flat on the reader and try again.")
    if owner and owner[0] != player_id and not overwrite:
        raise ChipConflict(f"This chip already belongs to {owner[1]} ({owner[0]}).")


def write_identity(conn, player_id, name):
    data = encode_identity(player_id, name)
    try:
        for offset in range(0, len(data), 4):
            page = 4 + offset // 4
            _, sw1, sw2 = conn.transmit([0xFF, 0xD6, 0x00, page, 0x04] + list(data[offset:offset + 4]))
            if (sw1, sw2) != (0x90, 0x00):
                raise ChipError(f"The chip refused the write at page {page}. It may be locked.")
        stored = identity_of(read_user_memory(conn))
    except ChipError:
        raise
    except Exception:
        raise ChipError("The chip was removed during writing. Keep it on the reader and try again.")
    if stored != (player_id, name):
        raise ChipError("Written data didn't read back correctly. Try again.")


def chip_status():
    if not CHIP_SUPPORT:
        return {"available": False, "reason": "pyscard is not installed on this computer."}
    try:
        found = readers()
    except Exception:
        found = []
    if not found:
        return {"available": False, "reason": "no NFC reader detected."}
    return {"available": True, "reader": str(found[0])}


def create_player(data):
    name, balance = clean_name(data.get("name")), clean_balance(data.get("balance"))
    player_id = data.get("id")
    if player_id in (None, ""):
        player_id = next_player_id()
    elif not isinstance(player_id, str) or not ID_PATTERN.fullmatch(player_id):
        raise ValueError("ID can use letters, numbers, _ and - only (up to 40 characters).")
    if not data.get("write_chip"):
        insert_player(player_id, name, balance)
        return {"id": player_id, "name": name, "chip": None}
    # Nothing is saved to MongoDB unless a usable chip is on the reader.
    with chip_session() as conn:
        check_chip_usable(conn, player_id, bool(data.get("overwrite")))
        insert_player(player_id, name, balance)
        try:
            write_identity(conn, player_id, name)
            chip = {"ok": True}
        except ChipError as error:
            chip = {"ok": False, "error": str(error)}
    return {"id": player_id, "name": name, "chip": chip}


def write_chip_for(player_id, data):
    doc = players.find_one({"_id": player_id}, {"name": 1})
    if doc is None:
        raise ValueError("Player not found.")
    with chip_session() as conn:
        check_chip_usable(conn, player_id, bool(data.get("overwrite")))
        write_identity(conn, player_id, doc["name"])
    return {"ok": True}


# ── Web page ───────────────────────────────────────────────────────────────

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Royal Table Admin</title>
<style>
  :root { --bg:#0e1a14; --card:#15251c; --line:#27402f; --text:#ecece6; --dim:#8fa597; --gold:#d6b25e; --bad:#e07a6f; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text); font:15px/1.4 system-ui,sans-serif; padding:24px; }
  main { max-width:900px; margin:0 auto; }
  h1 { font-weight:600; margin:0 0 4px; } p.sub { color:var(--dim); margin:0 0 20px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; margin-bottom:18px; }
  form { display:grid; grid-template-columns:1fr 2fr 1fr auto; gap:10px; align-items:end; }
  label { display:block; font-size:12px; color:var(--dim); margin-bottom:4px; }
  input { width:100%; padding:9px 10px; border-radius:6px; border:1px solid var(--line); background:#0b150f; color:var(--text); font:inherit; }
  button { padding:9px 14px; border-radius:6px; border:1px solid var(--gold); background:transparent; color:var(--gold); font:inherit; cursor:pointer; }
  button:hover { background:var(--gold); color:#14130c; } button.danger { border-color:var(--bad); color:var(--bad); }
  button.danger:hover { background:var(--bad); color:#14130c; } button.small { padding:5px 9px; font-size:13px; }
  table { width:100%; border-collapse:collapse; } th,td { text-align:left; padding:9px 6px; border-bottom:1px solid var(--line); }
  th { color:var(--dim); font-weight:500; font-size:12px; } td.actions { text-align:right; white-space:nowrap; }
  td input { width:100px; } code { background:#0b150f; padding:2px 6px; border-radius:4px; }
  .chiprow { grid-column:1/-1; display:flex; flex-wrap:wrap; gap:12px; align-items:center; color:var(--dim); font-size:13px; }
  .chiprow label { display:flex; gap:6px; align-items:center; margin:0; color:var(--text); font-size:14px; }
  .chiprow input { width:auto; }
  #msg { min-height:22px; margin-bottom:12px; } .ok { color:var(--gold); } .err { color:var(--bad); }
  #chip { display:none; } #chip pre { margin:8px 0 0; padding:10px; background:#0b150f; border-radius:6px; overflow-x:auto; }
  @media (max-width:640px) { form { grid-template-columns:1fr; } }
</style></head>
<body><main>
  <h1>Royal Table Admin</h1>
  <p class="sub">Create players, write their chips and manage demo balances.</p>
  <div id="msg"></div>

  <div class="card">
    <form id="add">
      <div><label>Player ID (blank = auto)</label><input id="f-id" maxlength="40" placeholder="P002" autocomplete="off"></div>
      <div><label>Name</label><input id="f-name" maxlength="40" required autocomplete="off"></div>
      <div><label>Starting balance</label><input id="f-balance" type="number" min="0" step="1" value="250" required></div>
      <button type="submit">Add player</button>
      <div class="chiprow">
        <label><input type="checkbox" id="f-chip" disabled> Write chip when adding</label>
        <span id="chip-status">Checking reader…</span>
        <button type="button" class="small" id="recheck">Recheck</button>
      </div>
    </form>
  </div>

  <div class="card" id="chip">
    <strong>Chip not written.</strong> In NFC Tools, write this as a <b>Text</b> record on the chip:
    <pre id="chip-json"></pre>
    <button class="small" id="copy" style="margin-top:10px">Copy</button>
  </div>

  <div class="card">
    <table><thead><tr><th>ID</th><th>Name</th><th>Balance</th><th></th></tr></thead><tbody id="rows"></tbody></table>
  </div>
</main>
<script>
const $ = (id) => document.getElementById(id);
const msg = (text, bad) => { $("msg").textContent = text; $("msg").className = bad ? "err" : "ok"; };

async function api(method, path, body) {
  const res = await fetch(path, {
    method, headers: { "Content-Type": "application/json", "X-Admin": "1" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const error = new Error(data.error || "Request failed");
    error.needsConfirm = !!data.needs_confirm;
    throw error;
  }
  return data;
}

// If the chip belongs to another player, ask before overwriting it.
async function withConfirm(run) {
  try { return await run(false); }
  catch (error) {
    if (error.needsConfirm && confirm(error.message + " Overwrite it with the new player?")) return run(true);
    throw error;
  }
}

async function checkChip() {
  try {
    const status = await api("GET", "/api/chip");
    $("chip-status").textContent = status.available ? "Reader ready: " + status.reader : "Chip writing unavailable: " + status.reason;
    $("f-chip").disabled = !status.available;
    $("f-chip").checked = status.available;
  } catch (e) { $("chip-status").textContent = "Couldn't check the reader."; }
}

async function load() {
  try {
    const list = await api("GET", "/api/players");
    const rows = $("rows"); rows.innerHTML = "";
    for (const p of list) {
      const tr = document.createElement("tr");
      const cell = (node) => { const td = document.createElement("td"); td.append(node); tr.append(td); return td; };
      const code = document.createElement("code"); code.textContent = p.id; cell(code);
      cell(document.createTextNode(p.name));
      const input = document.createElement("input"); input.type = "number"; input.min = 0; input.step = 1; input.value = p.balance; cell(input);
      const actions = cell(document.createTextNode("")); actions.className = "actions";
      const save = document.createElement("button"); save.className = "small"; save.textContent = "Save balance";
      save.onclick = async () => {
        try { await api("POST", `/api/players/${encodeURIComponent(p.id)}/balance`, { balance: Number(input.value) }); msg(`Balance updated for ${p.name}.`); load(); }
        catch (e) { msg(e.message, true); }
      };
      const chip = document.createElement("button"); chip.className = "small"; chip.textContent = "Write chip"; chip.style.marginLeft = "6px";
      chip.onclick = async () => {
        msg("Hold the chip on the reader…");
        try { await withConfirm((overwrite) => api("POST", `/api/players/${encodeURIComponent(p.id)}/chip`, { overwrite })); msg(`Chip written for ${p.name}.`); }
        catch (e) { msg(e.message, true); }
      };
      const del = document.createElement("button"); del.className = "small danger"; del.textContent = "Delete"; del.style.marginLeft = "6px";
      del.onclick = async () => {
        if (!confirm(`Delete ${p.name} (${p.id})? This cannot be undone.`)) return;
        try { await api("DELETE", `/api/players/${encodeURIComponent(p.id)}`); msg(`${p.name} deleted.`); load(); }
        catch (e) { msg(e.message, true); }
      };
      actions.append(save, chip, del);
      rows.append(tr);
    }
    if (!list.length) { const tr = rows.insertRow(); const td = tr.insertCell(); td.colSpan = 4; td.textContent = "No players yet."; }
  } catch (e) { msg(e.message, true); }
}

$("add").onsubmit = async (event) => {
  event.preventDefault();
  const body = { id: $("f-id").value.trim(), name: $("f-name").value, balance: Number($("f-balance").value), write_chip: $("f-chip").checked };
  if (body.write_chip) msg("Hold the chip on the reader…");
  try {
    const made = await withConfirm((overwrite) => api("POST", "/api/players", { ...body, overwrite }));
    $("chip").style.display = "none";
    if (made.chip && made.chip.ok) {
      msg(`Added ${made.name} (${made.id}). Chip written and verified.`);
    } else {
      if (made.chip) msg(`Added ${made.name} (${made.id}), but the chip write failed: ${made.chip.error} Use "Write chip" in the list to retry.`, true);
      else msg(`Added ${made.name} (${made.id}).`);
      $("chip-json").textContent = JSON.stringify({ id: made.id, name: made.name });
      $("chip").style.display = "block";
    }
    $("f-id").value = ""; $("f-name").value = "";
    load();
  } catch (e) { msg(e.message, true); }
};
$("recheck").onclick = checkChip;
$("copy").onclick = () => navigator.clipboard?.writeText($("chip-json").textContent).then(() => msg("Copied."));
checkChip();
load();
</script></body></html>
"""


# ── HTTP server ────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    server_version = "RoyalTableAdmin"

    def log_message(self, fmt, *args):
        pass

    def send_json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        header = self.headers.get("Authorization", "")
        if header.startswith("Basic "):
            try:
                supplied = base64.b64decode(header[6:]).decode().partition(":")[2]
            except (ValueError, UnicodeDecodeError):
                return False
            return hmac.compare_digest(supplied.encode(), self.server.admin_password.encode())
        return False

    def gate(self, mutating):
        if not self.authorized():
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Royal Table Admin"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False
        # A custom header can't be sent cross-site without a preflight, which blocks CSRF.
        if (mutating or self.path.startswith("/api/")) and self.headers.get("X-Admin") != "1":
            self.send_json(403, {"error": "Forbidden."})
            return False
        return True

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 4096:
            raise ValueError("Request too large.")
        data = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("Invalid request.")
        return data

    def route(self, method):
        path = self.path.split("?")[0]
        parts = path.strip("/").split("/")
        if method == "GET" and path == "/api/chip":
            return 200, chip_status()
        if method == "GET" and path == "/api/players":
            docs = players.find({}, {"name": 1, "balance": 1}).sort("_id", 1)
            return 200, [{"id": d["_id"], "name": d.get("name", ""), "balance": d.get("balance", 0)} for d in docs]
        if method == "POST" and path == "/api/players":
            return 200, create_player(self.read_json())
        if parts[:2] == ["api", "players"] and len(parts) >= 3:
            player_id = unquote(parts[2])
            if method == "POST" and len(parts) == 4 and parts[3] == "balance":
                set_balance(player_id, self.read_json())
                return 200, {"ok": True}
            if method == "POST" and len(parts) == 4 and parts[3] == "chip":
                return 200, write_chip_for(player_id, self.read_json())
            if method == "DELETE" and len(parts) == 3:
                delete_player(player_id)
                return 200, {"ok": True}
        return 404, {"error": "Not found."}

    def handle_request(self, method):
        if not self.gate(method != "GET"):
            return
        if method == "GET" and self.path.split("?")[0] == "/":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        try:
            status, payload = self.route(method)
        except ChipConflict as error:
            status, payload = 409, {"error": str(error), "needs_confirm": True}
        except (ValueError, json.JSONDecodeError) as error:
            status, payload = 400, {"error": str(error)}
        except PyMongoError:
            status, payload = 503, {"error": "MongoDB unavailable. Try again."}
        self.send_json(status, payload)

    def do_GET(self): self.handle_request("GET")
    def do_POST(self): self.handle_request("POST")
    def do_DELETE(self): self.handle_request("DELETE")


def main():
    global players
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to allow other devices")
    parser.add_argument("--port", type=int, default=8800)
    args = parser.parse_args()

    uri, password = os.environ.get("MONGODB_URI"), os.environ.get("ADMIN_PASSWORD")
    if not uri:
        raise SystemExit("Set MONGODB_URI before starting the admin panel.")
    if not password or len(password) < 8:
        raise SystemExit("Set ADMIN_PASSWORD (at least 8 characters) before starting the admin panel.")

    client = MongoClient(uri, serverSelectionTimeoutMS=5000, timeoutMS=8000)
    try:
        client.admin.command("ping")
    except PyMongoError as error:
        raise SystemExit(f"MongoDB connection failed ({type(error).__name__}). Check your URI and that this computer's IP is allowed in Atlas.")
    players = client[os.environ.get("MONGODB_DATABASE", "hack_uta")].players

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.admin_password = password
    print(f"Admin panel ready at http://{'localhost' if args.host == '127.0.0.1' else args.host}:{args.port}")
    print("Log in with any username and your ADMIN_PASSWORD. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
