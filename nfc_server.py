"""Read player identity from NFC and store blackjack demo balances in MongoDB."""

import asyncio
import json
import os
import re

import websockets
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import PyMongoError
from smartcard.CardMonitoring import CardMonitor, CardObserver


def read_pages(connection):
    raw = bytearray()
    for page in range(4, 40):
        data, sw1, _ = connection.transmit([0xFF, 0xB0, 0x00, page, 0x04])
        if sw1 == 0x90:
            raw.extend(data)
        else:
            break
    return bytes(raw)


def parse_ndef_text(raw):
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
            tlen  = ndef[j]; j += 1
            sr    = (flags >> 4) & 1
            plen  = ndef[j] if sr else int.from_bytes(ndef[j:j+4], 'big')
            j    += 1 if sr else 4
            rtype = ndef[j: j + tlen]; j += tlen
            pay   = ndef[j: j + plen]; j += plen
            if (flags & 7) == 1 and rtype == b'T' and len(pay) > 1:
                lang_len = pay[0] & 0x3F
                return pay[1 + lang_len:].decode("utf-8", errors="replace")
        i += length
    return None


def player_identity(raw):
    try:
        identity = json.loads(parse_ndef_text(raw) or "null")
    except ValueError:
        identity = None
    if isinstance(identity, dict):
        player_id = identity.get("id")
        name = identity.get("name")
        if (isinstance(player_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,40}", player_id)
                and isinstance(name, str) and name.strip()):
            return player_id, name.strip()
    raise ValueError("Use a chip with a player ID and name. Legacy balance chips need reprogramming.")


class NFCObserver(CardObserver):
    def __init__(self, loop, queue):
        self.loop = loop
        self.queue = queue

    def update(self, observable, actions):
        if not actions[0]:
            return
        connection = actions[0][0].createConnection()
        try:
            connection.connect()
            player_id, name = player_identity(read_pages(connection))
            event = {"type": "card_read", "player_id": player_id, "name": name}
        except Exception as error:
            event = {"type": "error", "message": str(error)}
        finally:
            try:
                connection.disconnect()
            except Exception:
                pass
        self.loop.call_soon_threadsafe(self.queue.put_nowait, event)


def adjust_balance(players, player_id, change, revision, operation):
    query = {"_id": player_id, "revision": revision}
    if change < 0:
        query["balance"] = {"$gte": -change}
    document = players.find_one_and_update(
        query,
        {"$inc": {"balance": change, "revision": 1}, "$set": {"last_operation": operation}},
        return_document=ReturnDocument.AFTER,
    )
    if document is not None:
        return document, True
    document = players.find_one({"_id": player_id})
    return document, bool(document and document.get("last_operation") == operation)


async def main():
    uri = os.environ.get("MONGODB_URI")
    if not uri:
        raise SystemExit("Set MONGODB_URI before starting the server.")
    with MongoClient(uri, serverSelectionTimeoutMS=5000, timeoutMS=8000) as client:
        try:
            await asyncio.to_thread(client.admin.command, "ping")
        except PyMongoError as error:
            raise SystemExit(f"MongoDB connection failed ({type(error).__name__}). Check your URI and database access.")
        await serve_table(client[os.environ.get("MONGODB_DATABASE", "hack_uta")].players)


async def serve_table(players):
    loop = asyncio.get_running_loop()
    taps = asyncio.Queue()
    # Each browser can update only the player it explicitly selected after a tap.
    clients = {}
    last_tapped = None

    async def handler(websocket):
        clients[websocket] = None
        try:
            async for message in websocket:
                operation = None
                try:
                    data = json.loads(message)
                    if not isinstance(data, dict):
                        raise ValueError("Invalid request.")
                    operation = data.get("operation")
                    player_id = data.get("player_id")
                    if data.get("type") == "select_player":
                        if player_id != last_tapped or not isinstance(player_id, str):
                            raise ValueError("Tap your chip again to select your player.")
                        clients[websocket] = player_id
                        result = {"success": True}
                    elif data.get("type") == "adjust_balance":
                        if not isinstance(player_id, str) or clients[websocket] != player_id:
                            raise ValueError("Tap your chip again before changing the balance.")
                        change, revision = data.get("change"), data.get("revision")
                        if (type(change) is not int or not 0 < abs(change) <= 100000
                                or type(revision) is not int or revision < 0
                                or not isinstance(operation, str) or len(operation) > 100):
                            raise ValueError("Invalid balance update.")
                        document, applied = await asyncio.to_thread(
                            adjust_balance, players, player_id, change, revision, operation
                        )
                        result = {"success": applied}
                        if document:
                            result.update(balance=document["balance"], revision=document["revision"])
                        if not applied:
                            result["message"] = "Balance changed or funds are insufficient. Check the displayed balance."
                    else:
                        raise ValueError("Unknown request.")
                except (ValueError, TypeError) as error:
                    result = {"success": False, "message": str(error)}
                except PyMongoError:
                    result = {"success": False, "retry": True, "message": "MongoDB unavailable. Retrying…"}
                await websocket.send(json.dumps({"type": "response", "operation": operation, **result}))
        except websockets.ConnectionClosed:
            pass
        finally:
            clients.pop(websocket, None)

    monitor = CardMonitor()
    observer = NFCObserver(loop, taps)
    monitor.addObserver(observer)
    try:
        async with websockets.serve(handler, "localhost", 8765):
            print("MongoDB and NFC WebSocket server ready on ws://localhost:8765")
            while True:
                event = await taps.get()
                last_tapped = None
                if event["type"] == "card_read":
                    try:
                        document = await asyncio.to_thread(players.find_one, {"_id": event["player_id"]})
                        if document is None:
                            event = {"type": "error", "message": "Player ID is missing from MongoDB."}
                        else:
                            balance, revision = document.get("balance"), document.get("revision")
                            if type(balance) is not int or type(revision) is not int:
                                event = {"type": "error", "message": "Player needs integer balance and revision in MongoDB."}
                            else:
                                last_tapped = event["player_id"]
                                event.update(balance=balance, revision=revision)
                    except PyMongoError:
                        event = {"type": "error", "message": "MongoDB unavailable. Try tapping again."}
                websockets.broadcast(list(clients), json.dumps(event))
    finally:
        monitor.deleteObserver(observer)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
