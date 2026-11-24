from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import get_settings


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MemberView(StrictModel):
    username: str
    display_name: str
    role: Literal["owner", "member"]
    avatar_url: str | None = None


class PresenceView(StrictModel):
    username: str
    status: Literal["online", "away", "offline", "dnd"]
    display_name: str | None = None
    status_text: str | None = None
    status_until: datetime | None = None
    manual_status: Literal["dnd", "away"] | None = None


class ChatView(StrictModel):
    id: UUID
    kind: Literal["direct", "group"]
    title: str
    last_sequence: int
    last_message_preview: str
    updated_at: datetime
    unread_count: int = 0
    members: list[MemberView] = []


class CreateChat(StrictModel):
    kind: Literal["direct", "group"]
    title: str = Field(default="", max_length=120)
    members: list[str] = Field(min_length=1)

    @field_validator("members")
    @classmethod
    def normalize_members(cls, members: list[str]) -> list[str]:
        normalized = [item.strip().lower() for item in members if item.strip()]
        if len(set(normalized)) != len(normalized):
            raise ValueError("Duplicate members are not allowed")
        if len(normalized) > get_settings().group_max_members:
            raise ValueError("Too many members")
        return normalized

    @model_validator(mode="after")
    def validate_kind(self):
        if self.kind == "direct" and len(self.members) != 1:
            raise ValueError("A direct chat must contain exactly one other member")
        if self.kind == "group" and not self.title.strip():
            raise ValueError("A group title is required")
        return self


class MessageView(StrictModel):
    id: UUID
    client_id: UUID
    chat_id: UUID
    sequence: int
    sender_username: str
    sender_display_name: str
    text: str
    reply_to_id: UUID | None
    reply_preview: dict | None = None
    forwarded_from_message_id: UUID | None = None
    forwarded_from_chat_id: UUID | None = None
    forwarded_sender_username: str | None = None
    forwarded_at: datetime | None = None
    reactions: list[dict] = []
    attachments: list[dict] = []
    pinned: bool = False
    read: bool | None = None
    created_at: datetime
    edited_at: datetime | None
    deleted_at: datetime | None


class SendMessage(StrictModel):
    text: str = ""
    client_id: UUID
    reply_to_id: UUID | None = None
    forward_message_id: UUID | None = None
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=10)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        value = value.replace("\x00", "").strip()
        if len(value) > get_settings().message_max_chars:
            raise ValueError("Message is too long")
        return value

    @model_validator(mode="after")
    def require_content(self):
        if not self.text and not self.forward_message_id and not self.attachment_ids:
            raise ValueError("Message content is required")
        return self


class EditMessage(StrictModel):
    text: str

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return SendMessage.validate_text(value)


class ReadCursor(StrictModel):
    sequence: int = Field(ge=0)


class RenameChat(StrictModel):
    title: str = Field(min_length=1, max_length=120)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("A group title is required")
        return value


class AddMember(StrictModel):
    username: str = Field(min_length=1, max_length=128)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        value = value.strip().lower()
        if not value or any(character.isspace() for character in value):
            raise ValueError("Invalid username")
        return value


class WebSocketAuth(StrictModel):
    type: Literal["auth"]
    token: str = Field(min_length=1, max_length=8192)


CALL_PAYLOAD_TYPES = {"invite", "accept", "decline", "cancel", "hangup"}


class WebSocketCommand(StrictModel):
    type: Literal["subscribe", "unsubscribe", "ping", "typing", "call"]
    chat_id: UUID | None = None
    active: bool | None = None
    activity: Literal["active", "idle"] | None = None
    call_id: UUID | None = None
    to: str | None = None
    payload: dict | None = None

    @field_validator("to")
    @classmethod
    def normalize_to(cls, value: str | None) -> str | None:
        return value.strip().lower() if value else value

    @model_validator(mode="after")
    def require_chat_id(self):
        if self.type in {"subscribe", "unsubscribe", "typing"} and self.chat_id is None:
            raise ValueError("chat_id is required")
        if self.type == "typing" and self.active is None:
            raise ValueError("active is required")
        if self.type == "call":
            if self.call_id is None:
                raise ValueError("call_id is required")
            if not isinstance(self.payload, dict) or "type" not in self.payload:
                raise ValueError("payload.type is required")
            payload_type = self.payload["type"]
            if payload_type not in CALL_PAYLOAD_TYPES:
                raise ValueError("Unsupported call payload type")
            if payload_type == "invite" and not self.to:
                raise ValueError("to is required for invite")
        return self


class DirectCallToken(StrictModel):
    room_name: str
    livekit_url: str
    access_token: str


class CallParticipantView(StrictModel):
    username: str
    display_name: str
    joined_at: datetime
    left_at: datetime | None = None


class CallSessionView(StrictModel):
    session_id: UUID
    chat_id: UUID
    room_name: str
    livekit_url: str
    access_token: str
    started_at: datetime
    participants: list[CallParticipantView]


class ActiveCallSessionView(StrictModel):
    session_id: UUID
    chat_id: UUID
    room_name: str
    started_at: datetime
    participants: list[CallParticipantView]


class CallSessionHistoryEntry(StrictModel):
    id: UUID
    chat_id: UUID
    room_name: str
    started_by: str
    started_at: datetime
    ended_at: datetime | None
    ended_reason: str | None
    participants: list[CallParticipantView]


class UpdatePresenceStatus(StrictModel):
    dnd: bool = False
    manual_away: bool = False
    status_text: str = Field(default="", max_length=60)
    expires_in_minutes: int | None = Field(default=None, ge=1, le=10080)

    @field_validator("status_text")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def exclusive_manual_status(self):
        if self.dnd and self.manual_away:
            raise ValueError("dnd and manual_away are mutually exclusive")
        return self


def _retention_field():
    """Fresh Field per attribute: a shared FieldInfo instance would be reused
    across attributes, and omitting default= would make them required."""
    return Field(default=None, ge=0, le=3650)


class AdminConfigView(StrictModel):
    calls_enabled: bool
    max_attachment_bytes: int | None
    auto_purge_enabled: bool
    retention_messages_days: int
    retention_files_days: int
    retention_audit_days: int
    retention_backups_days: int
    rate_messages_per_minute: int
    rate_logins_per_hour: int
    group_max_members: int
    invites_per_day: int
    updated_at: datetime
    updated_by: str | None = None


class UpdateAdminConfig(StrictModel):
    """Every field is optional: the panel PATCHes one section at a time, so an
    omitted field must keep its stored value rather than reset to a default."""

    calls_enabled: bool | None = None
    # None = fall back to the MESSENGER_ATTACHMENT_MAX_BYTES env default.
    max_attachment_bytes: int | None = Field(default=None, ge=1024, le=500 * 1024 * 1024)
    max_attachment_bytes_set: bool = False
    auto_purge_enabled: bool | None = None
    # 0 = keep forever.
    retention_messages_days: int | None = _retention_field()
    retention_files_days: int | None = _retention_field()
    retention_audit_days: int | None = _retention_field()
    retention_backups_days: int | None = _retention_field()
    rate_messages_per_minute: int | None = Field(default=None, ge=1, le=1000)
    rate_logins_per_hour: int | None = Field(default=None, ge=1, le=1000)
    group_max_members: int | None = Field(default=None, ge=2, le=5000)
    invites_per_day: int | None = Field(default=None, ge=1, le=1000)

    def changes(self) -> dict:
        data = self.model_dump(exclude_none=True)
        explicit_null = data.pop("max_attachment_bytes_set", False)
        # "Use the server default" is expressed as an explicit NULL, which
        # exclude_none would otherwise drop as "field not supplied".
        if explicit_null and "max_attachment_bytes" not in data:
            data["max_attachment_bytes"] = None
        return data


class AdminUserView(StrictModel):
    username: str
    display_name: str
    calls_enabled: bool
    dnd: bool
    manual_away: bool
    status_text: str | None = None
    status_expires_at: datetime | None = None
    muted: bool = False
    auditor: bool = False
    is_active: bool = True
    is_admin: bool = False
    department: str = ""
    title: str = ""
    sessions: int = 0


class UpdateUserCalls(StrictModel):
    calls_enabled: bool


class UpdateUserFlag(StrictModel):
    value: bool
