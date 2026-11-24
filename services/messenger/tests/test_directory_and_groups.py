import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT))

from messenger.auth import Identity  # noqa: E402
from messenger.main import _ends_subscription  # noqa: E402
from messenger.repository import _like_pattern, add_group_member  # noqa: E402
from messenger.schemas import RenameChat  # noqa: E402


class DirectoryAndGroupTests(unittest.IsolatedAsyncioTestCase):
    def test_removed_member_subscription_ends_on_own_removal_only(self):
        removed_me = {"type": "membership.updated", "membership": {"username": "bob", "removed": True}}
        removed_other = {"type": "membership.updated", "membership": {"username": "eve", "removed": True}}
        self.assertTrue(_ends_subscription(removed_me, "bob"))
        self.assertFalse(_ends_subscription(removed_other, "bob"))
        self.assertTrue(_ends_subscription({"type": "chat.deleted"}, "bob"))
        self.assertFalse(_ends_subscription({"type": "message.created"}, "bob"))

    def test_directory_search_treats_wildcards_literally(self):
        self.assertEqual(_like_pattern("50%_a"), "%50\\%\\_a%")

    def test_group_title_is_trimmed_and_required(self):
        self.assertEqual(RenameChat(title="  Отдел ОЦ ").title, "Отдел ОЦ")
        with self.assertRaises(ValueError):
            RenameChat(title="   ")

    async def test_admin_cannot_add_self_to_foreign_group(self):
        admin = Identity("root", "Root", ("chat_admin", "system_admin", "user"), 1, "t")
        with patch("messenger.repository.transaction") as transaction:
            with self.assertRaises(HTTPException) as raised:
                await add_group_member(admin, uuid4(), "root")
        self.assertEqual(raised.exception.status_code, 403)
        transaction.assert_not_called()


if __name__ == "__main__":
    unittest.main()
