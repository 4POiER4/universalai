"""Disposable authenticated integration smoke test for the Messenger service."""

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

import jwt
import psycopg2


API = os.environ.get("MESSENGER_SMOKE_API", "http://messenger-api:8000")
USERS = ("messenger_smoke_a", "messenger_smoke_b", "messenger_smoke_outsider")


def request_json(method: str, path: str, token: str, payload=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        API + path,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def upload_file(path: str, token: str, filename: str, content_type: str, content: bytes):
    boundary = "----messenger-" + uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(
        API + path, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        return response.status, json.loads(response.read().decode())


def download(path: str, token: str):
    request = urllib.request.Request(
        API + path, headers={"Authorization": f"Bearer {token}"}
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, response.headers, response.read()


def token_for(username: str, version: int) -> str:
    now = int(time.time())
    private_key = os.environ["MESSENGER_JWT_PRIVATE_KEY"].replace("\\n", "\n")
    return jwt.encode(
        {
            "sub": username,
            "name": username,
            "roles": ["user"],
            "iss": os.environ.get("MESSENGER_JWT_ISSUER", "universalai"),
            "aud": os.environ.get("MESSENGER_JWT_AUDIENCE", "messenger"),
            "iat": now,
            "exp": now + 120,
            "jti": str(uuid.uuid4()),
            "ver": version,
        },
        private_key,
        algorithm="EdDSA",
    )


def main() -> None:
    connection = psycopg2.connect(os.environ["DATABASE_URL"])
    connection.autocommit = False
    chat_id = None
    try:
        with connection.cursor() as cursor:
            for username in USERS:
                cursor.execute(
                    """
                    INSERT INTO users
                      (username, display_name, auth_source, roles, is_active)
                    VALUES (%s, %s, 'sso', ARRAY['user']::TEXT[], TRUE)
                    ON CONFLICT (username) DO UPDATE SET is_active=TRUE
                    """,
                    (username, username),
                )
            connection.commit()
            cursor.execute(
                "SELECT username, messenger_token_version FROM users WHERE username=ANY(%s)",
                (list(USERS),),
            )
            versions = {row[0]: int(row[1]) for row in cursor.fetchall()}

        owner_token = token_for(USERS[0], versions[USERS[0]])
        member_token = token_for(USERS[1], versions[USERS[1]])
        outsider_token = token_for(USERS[2], versions[USERS[2]])
        status, chat = request_json(
            "POST",
            "/api/messenger/v1/chats",
            owner_token,
            {"kind": "direct", "title": "", "members": [USERS[1]]},
        )
        if status not in {200, 201}:
            raise RuntimeError(f"Unexpected create status: {status}")
        chat_id = chat["id"]

        payload_text = "<img src=x onerror=alert(1)> plain-text"
        client_id = str(uuid.uuid4())
        status, message = request_json(
            "POST",
            f"/api/messenger/v1/chats/{chat_id}/messages",
            owner_token,
            {"text": payload_text, "client_id": client_id, "reply_to_id": None},
        )
        if status != 201 or message["text"] != payload_text:
            raise RuntimeError("Message was not stored as exact plain text")

        status, repeated = request_json(
            "POST",
            f"/api/messenger/v1/chats/{chat_id}/messages",
            owner_token,
            {"text": payload_text, "client_id": client_id, "reply_to_id": None},
        )
        if status != 200 or repeated["id"] != message["id"]:
            raise RuntimeError("Idempotent retry created a duplicate")

        _, reply = request_json(
            "POST", f"/api/messenger/v1/chats/{chat_id}/messages", owner_token,
            {"text": "reply", "client_id": str(uuid.uuid4()), "reply_to_id": message["id"]},
        )
        if reply["reply_preview"]["id"] != message["id"]:
            raise RuntimeError("Reply preview contract failed")
        _, forwarded = request_json(
            "POST", f"/api/messenger/v1/chats/{chat_id}/messages", owner_token,
            {"text": "", "client_id": str(uuid.uuid4()), "forward_message_id": message["id"]},
        )
        if forwarded["forwarded_from_message_id"] != message["id"]:
            raise RuntimeError("Forward metadata contract failed")
        _, reaction = request_json(
            "PUT",
            f"/api/messenger/v1/chats/{chat_id}/messages/{message['id']}/reactions/%F0%9F%91%8D",
            owner_token,
        )
        if reaction["reactions"][0]["count"] != 1:
            raise RuntimeError("Reaction aggregation failed")
        request_json("PUT", f"/api/messenger/v1/chats/{chat_id}/pins/{message['id']}", owner_token)
        _, pins = request_json("GET", f"/api/messenger/v1/chats/{chat_id}/pins", owner_token)
        if not any(item["message_id"] == message["id"] for item in pins):
            raise RuntimeError("Pin contract failed")
        _, results = request_json(
            "GET", f"/api/messenger/v1/chats/{chat_id}/messages/search?q=plain-text",
            owner_token,
        )
        if not any(item["id"] == message["id"] for item in results):
            raise RuntimeError("Membership-scoped search failed")

        png = b"\x89PNG\r\n\x1a\n" + b"smoke attachment"
        status, attachment = upload_file(
            f"/api/messenger/v1/chats/{chat_id}/attachments", owner_token,
            "../../smoke.png", "image/png", png,
        )
        if status != 201 or attachment["original_name"] != "smoke.png":
            raise RuntimeError("Attachment quarantine/name contract failed")
        _, attachment_message = request_json(
            "POST", f"/api/messenger/v1/chats/{chat_id}/messages", owner_token,
            {
                "text": "attached", "client_id": str(uuid.uuid4()),
                "attachment_ids": [attachment["id"]],
            },
        )
        if attachment_message["attachments"][0]["sha256"] != attachment["sha256"]:
            raise RuntimeError("Attachment linking failed")
        status, headers, downloaded = download(
            f"/api/messenger/v1/chats/{chat_id}/attachments/{attachment['id']}?inline=true",
            owner_token,
        )
        if status != 200 or downloaded != png or headers.get("X-Content-Type-Options") != "nosniff":
            raise RuntimeError("Safe attachment download failed")
        try:
            download(
                f"/api/messenger/v1/chats/{chat_id}/attachments/{attachment['id']}",
                outsider_token,
            )
            raise RuntimeError("Outsider unexpectedly downloaded attachment")
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise

        def send_concurrent(index: int):
            return request_json(
                "POST",
                f"/api/messenger/v1/chats/{chat_id}/messages",
                owner_token,
                {
                    "text": f"concurrent-{index}",
                    "client_id": str(uuid.uuid4()),
                    "reply_to_id": None,
                },
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            concurrent_results = list(executor.map(send_concurrent, range(20)))
        if any(status_code != 201 for status_code, _ in concurrent_results):
            raise RuntimeError("Concurrent send failed")
        sequences = [item["sequence"] for _, item in concurrent_results]
        if len(set(sequences)) != len(sequences):
            raise RuntimeError("Concurrent messages received duplicate sequences")

        _, history = request_json(
            "GET",
            f"/api/messenger/v1/chats/{chat_id}/messages",
            owner_token,
        )
        if not any(item["id"] == message["id"] and item["text"] == payload_text for item in history):
            raise RuntimeError("Committed message is missing from history")

        if message.get("read") is not False:
            raise RuntimeError("Unread outgoing message must report read=false")
        status, receipt = request_json(
            "PUT",
            f"/api/messenger/v1/chats/{chat_id}/read",
            member_token,
            {"sequence": message["sequence"]},
        )
        if status != 200 or int(receipt["sequence"]) < int(message["sequence"]):
            raise RuntimeError("Read cursor contract failed")
        _, history_after_read = request_json(
            "GET",
            f"/api/messenger/v1/chats/{chat_id}/messages",
            owner_token,
        )
        read_row = next(item for item in history_after_read if item["id"] == message["id"])
        if read_row.get("read") is not True:
            raise RuntimeError("Read receipt was not applied to outgoing message")
        _, read_state = request_json(
            "GET",
            f"/api/messenger/v1/chats/{chat_id}/read-state",
            owner_token,
        )
        if not any(
            item["username"] == USERS[1] and int(item["sequence"]) >= int(message["sequence"])
            for item in read_state["members"]
        ):
            raise RuntimeError("Read state contract failed")

        try:
            request_json(
                "GET",
                f"/api/messenger/v1/chats/{chat_id}/messages",
                outsider_token,
            )
            raise RuntimeError("Outsider unexpectedly read the chat")
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise

        status, tombstone = request_json(
            "DELETE",
            f"/api/messenger/v1/chats/{chat_id}/messages/{message['id']}",
            owner_token,
        )
        if status != 200 or not tombstone["deleted_at"] or tombstone["text"]:
            raise RuntimeError("Message tombstone contract failed")
        print(
            "authenticated_flow=ok idempotency=ok concurrent_send=20 "
            "reply_forward_pin_reaction_search=ok attachment_scan_authz=ok "
            "plain_text_xss=ok outsider_idor=404 read_receipt=ok tombstone=ok"
        )
    finally:
        with connection.cursor() as cursor:
            if chat_id:
                cursor.execute("DELETE FROM messenger.chats WHERE id=%s", (chat_id,))
            cursor.execute(
                "DELETE FROM messenger.audit_events WHERE actor_username=ANY(%s)",
                (list(USERS),),
            )
            cursor.execute(
                "DELETE FROM messenger.profiles WHERE username=ANY(%s)",
                (list(USERS),),
            )
            cursor.execute("DELETE FROM users WHERE username=ANY(%s)", (list(USERS),))
        connection.commit()
        connection.close()


if __name__ == "__main__":
    main()
