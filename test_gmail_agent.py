import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import gmail_agent


def make_message(message_id: str, sender: str = "sender@example.com") -> dict:
    return {
        "id": message_id,
        "threadId": f"thread-{message_id}",
        "snippet": "A short preview",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "To", "value": "reader@example.com"},
                {"name": "Subject", "value": f"Subject {message_id}"},
                {"name": "Date", "value": "Mon, 01 Jan 2024 10:00:00 +0000"},
            ],
            "body": {
                "data": base64.urlsafe_b64encode(b"First sentence. Second sentence.").decode()
            },
        },
    }


class FakeRequest:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class FakeMessageCollection:
    def __init__(self):
        self.trashed = []
        self.list_calls = []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        if kwargs.get("pageToken"):
            return FakeRequest({"messages": [{"id": "second"}]})
        return FakeRequest(
            {"messages": [{"id": "first"}], "nextPageToken": "next-page"}
        )

    def get(self, **kwargs):
        return FakeRequest(make_message(kwargs["id"]))

    def trash(self, **kwargs):
        self.trashed.append(kwargs["id"])
        return FakeRequest({})


class FakeUsers:
    def __init__(self, messages):
        self._messages = messages

    def messages(self):
        return self._messages


class FakeService:
    def __init__(self):
        self.message_collection = FakeMessageCollection()

    def users(self):
        return FakeUsers(self.message_collection)


class GmailAgentTests(unittest.TestCase):
    def test_parse_message_decodes_body_and_headers(self):
        message = gmail_agent.parse_message(make_message("abc"))

        self.assertEqual(message["subject"], "Subject abc")
        self.assertEqual(message["body"], "First sentence. Second sentence.")

    def test_fetch_messages_follows_all_pages(self):
        service = FakeService()

        messages = gmail_agent.fetch_messages(service, query="is:unread", limit=0)

        self.assertEqual([message["id"] for message in messages], ["first", "second"])
        self.assertEqual(service.message_collection.list_calls[1]["pageToken"], "next-page")
        self.assertEqual(service.message_collection.list_calls[0]["q"], "is:unread")

    def test_group_counts_uses_normalized_email_addresses(self):
        messages = [
            {"from": "Ada Example <ADA@example.com>"},
            {"from": "ada@example.com"},
        ]

        self.assertEqual(gmail_agent.group_counts(messages, "from")["ada@example.com"], 2)

    def test_summary_is_bounded_and_extractive(self):
        self.assertEqual(
            gmail_agent.summarize_text("First sentence. Second sentence. Third."),
            "First sentence. Second sentence.",
        )

    def test_report_combines_messages_under_their_recipient(self):
        messages = [
            gmail_agent.parse_message(make_message("first")),
            gmail_agent.parse_message(make_message("second")),
        ]

        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "report.md"
            gmail_agent.write_report(messages, output, "extractive", "unused")
            report = output.read_text(encoding="utf-8")

        self.assertIn("### reader@example.com (2)", report)
        self.assertEqual(report.count("**Subject first**"), 2)
        self.assertEqual(report.count("**Subject second**"), 2)

    def test_cleanup_requires_exact_confirmation(self):
        service = FakeService()
        messages = [gmail_agent.parse_message(make_message("first"))]

        with patch.object(gmail_agent, "fetch_messages", return_value=messages):
            with patch("builtins.input", return_value="no"):
                gmail_agent._run_cleanup(
                    service,
                    type("Args", (), {"query": "is:unread", "limit": 1, "yes": False})(),
                )

        self.assertEqual(service.message_collection.trashed, [])

    def test_cleanup_moves_confirmed_message_to_trash(self):
        service = FakeService()
        messages = [gmail_agent.parse_message(make_message("first"))]

        with patch.object(gmail_agent, "fetch_messages", return_value=messages):
            with patch("builtins.input", return_value="yes"):
                gmail_agent._run_cleanup(
                    service,
                    type("Args", (), {"query": "is:unread", "limit": 1, "yes": False})(),
                )

        self.assertEqual(service.message_collection.trashed, ["first"])


if __name__ == "__main__":
    unittest.main()