import json
import sys
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import vkqr


class CompleteLoginTests(TestCase):
    def setUp(self) -> None:
        self.session = vkqr.Session(uuid="test", anonymous_token="anon")

    def test_accepts_success_without_next_step_url(self) -> None:
        payload = {
            "type": "okay",
            "data": {"access_token": "secret", "auth_user_hash": "hash"},
        }
        client = Mock()
        client.post.return_value = (200, json.dumps(payload))

        result = vkqr.complete_login(client, self.session, "super-app-token")

        self.assertEqual(result, payload)
        client.get.assert_not_called()

    def test_follows_legacy_next_step_url(self) -> None:
        client = Mock()
        client.post.return_value = (
            200,
            json.dumps({"type": "success", "data": {"next_step_url": "/finish"}}),
        )
        client.get.return_value = (200, "")

        vkqr.complete_login(client, self.session, "super-app-token")

        client.get.assert_called_once_with("https://vk.ru/finish")

    def test_unexpected_response_reports_shape_without_values(self) -> None:
        client = Mock()
        client.post.return_value = (
            200,
            json.dumps({"type": "maybe", "data": {"info": "private-value"}}),
        )

        with self.assertRaises(vkqr.VkQrError) as context:
            vkqr.complete_login(client, self.session, "super-app-token")

        self.assertIn("type=maybe", str(context.exception))
        self.assertIn("info", str(context.exception))
        self.assertNotIn("private-value", str(context.exception))


class BuildEntryTests(TestCase):
    def test_build_entry_requires_session_cookies(self) -> None:
        with self.assertRaises(vkqr.VkQrError):
            vkqr.build_entry({"p": "p-value"}, None, now=123)

    def test_build_entry_does_not_require_user_id(self) -> None:
        entry = vkqr.build_entry({"p": "p-value", "remixsid": "sid-value"}, None, now=123)

        self.assertEqual(entry, {"created_at": 123, "p": "p-value", "remixsid": "sid-value"})

    def test_build_entry_includes_user_id_when_available(self) -> None:
        entry = vkqr.build_entry({"p": "p-value", "remixsid": "sid-value"}, 42, now=123)

        self.assertEqual(
            entry,
            {"created_at": 123, "p": "p-value", "remixsid": "sid-value", "user_id": 42},
        )
