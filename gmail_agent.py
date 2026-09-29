"""Local-first Gmail mailbox tools: summaries, grouped reports, and cleanup."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from collections import Counter, defaultdict
from email.utils import getaddresses
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from google_apis import google_service


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _decode_body(data: str) -> str:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding).decode("utf-8", errors="replace")


def _walk_parts(payload: dict[str, Any]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data")
    if body_data:
        result.append((mime_type, _decode_body(body_data)))
    for part in payload.get("parts", []):
        result.extend(_walk_parts(part))
    return result


def _body_text(payload: dict[str, Any]) -> str:
    parts = _walk_parts(payload)
    plain_text = next((text for mime, text in parts if mime == "text/plain"), "")
    if plain_text:
        return plain_text
    html_text = next((text for mime, text in parts if mime == "text/html"), "")
    if not html_text:
        return ""
    extractor = _TextExtractor()
    extractor.feed(html_text)
    return re.sub(r"\s+", " ", " ".join(extractor.parts)).strip()


def _headers(message: dict[str, Any]) -> dict[str, str]:
    return {
        header.get("name", "").lower(): header.get("value", "")
        for header in message.get("payload", {}).get("headers", [])
    }


def parse_message(message: dict[str, Any]) -> dict[str, str]:
    headers = _headers(message)
    return {
        "id": message.get("id", ""),
        "thread_id": message.get("threadId", ""),
        "from": headers.get("from", "Unknown sender"),
        "to": headers.get("to", ""),
        "subject": headers.get("subject", "(no subject)"),
        "date": headers.get("date", ""),
        "snippet": message.get("snippet", ""),
        "body": _body_text(message.get("payload", {})),
    }


def fetch_messages(service: Any, query: str = "", limit: int = 100) -> list[dict[str, str]]:
    """Fetch up to limit messages, or every matching message when limit is zero."""
    messages: list[dict[str, str]] = []
    page_token = None
    while True:
        page_size = 500 if limit == 0 else min(500, limit - len(messages))
        if page_size <= 0:
            break
        request_args: dict[str, Any] = {"userId": "me", "maxResults": page_size}
        if query:
            request_args["q"] = query
        if page_token:
            request_args["pageToken"] = page_token
        response = service.users().messages().list(**request_args).execute()
        for item in response.get("messages", []):
            message = (
                service.users()
                .messages()
                .get(userId="me", id=item["id"], format="full")
                .execute()
            )
            messages.append(parse_message(message))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return messages


def summarize_text(text: str, max_chars: int = 280) -> str:
    """Create a no-token extractive summary for a message."""
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return "No readable message body; use the Gmail snippet instead."
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    summary = " ".join(sentences[:2])
    if len(summary) > max_chars:
        summary = summary[: max_chars - 1].rsplit(" ", 1)[0] + "..."
    return summary


def summarize_with_ollama(text: str, model: str) -> str:
    """Ask a locally running Ollama server to summarize without a cloud API."""
    endpoint = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
    payload = json.dumps(
        {
            "model": model,
            "prompt": "Summarize this email in at most 3 concise sentences. "
            "Do not invent facts.\n\n"
            + text[:12000],
            "stream": False,
            "options": {"num_predict": 120},
        }
    ).encode("utf-8")
    request = Request(
        f"{endpoint}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))["response"].strip()


def group_counts(messages: list[dict[str, str]], field: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for message in messages:
        raw_values = message.get(field, "")
        addresses = getaddresses([raw_values])
        values = [address.lower() for _, address in addresses if address]
        if values:
            counts.update(values)
        elif field == "from":
            counts.update([raw_values or "Unknown sender"])
    return counts


def _message_summary(message: dict[str, str], engine: str, model: str) -> str:
    text = message.get("body") or message.get("snippet", "")
    if engine == "ollama" and text:
        try:
            return summarize_with_ollama(text, model)
        except (URLError, TimeoutError, KeyError, ValueError) as error:
            print(f"Ollama unavailable ({error}); using local extractive summary.", file=sys.stderr)
    return summarize_text(text)


def write_report(
    messages: list[dict[str, str]], output: Path, engine: str, model: str
) -> None:
    by_sender: dict[str, list[dict[str, str]]] = defaultdict(list)
    by_recipient: dict[str, list[dict[str, str]]] = defaultdict(list)
    summaries = {
        message["id"]: _message_summary(message, engine, model) for message in messages
    }
    for message in messages:
        sender = group_counts([message], "from").most_common(1)
        by_sender[sender[0][0] if sender else "Unknown sender"].append(message)
        recipients = group_counts([message], "to")
        if recipients:
            for recipient, _ in recipients.items():
                by_recipient[recipient].append(message)

    lines = ["# Gmail mailbox report", "", f"Messages analyzed: {len(messages)}", ""]
    lines.extend(["## Grouped by sender", ""])
    for address, group in sorted(by_sender.items(), key=lambda item: (-len(item[1]), item[0])):
        lines.extend([f"### {address} ({len(group)})", ""])
        for message in group:
            lines.append(f"- **{message['subject']}** ({message['date']})")
            lines.append(f"  {summaries[message['id']]}")
        lines.append("")

    lines.extend(["## Grouped by recipient", ""])
    for address, group in sorted(
        by_recipient.items(), key=lambda item: (-len(item[1]), item[0])
    ):
        lines.extend([f"### {address} ({len(group)})", ""])
        for message in group:
            lines.append(f"- **{message['subject']}** ({message['date']})")
            lines.append(f"  {summaries[message['id']]}")
        lines.append("")
    if not by_recipient:
        lines.append("No recipient headers were available.")
    lines.append("")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")


def _authenticate() -> Any:
    return google_service()


def _run_overview(service: Any) -> None:
    profile = service.users().getProfile(userId="me").execute()
    print(f"Account: {profile['emailAddress']}")
    print(f"Messages: {profile.get('messagesTotal', 0)}")
    print(f"Threads: {profile.get('threadsTotal', 0)}")


def _run_summarize(service: Any, args: argparse.Namespace) -> None:
    messages = fetch_messages(service, args.query, args.limit)
    if not messages:
        print("No matching messages.")
        return
    print(f"Summaries for {len(messages)} message(s):\n")
    for message in messages:
        summary = _message_summary(message, args.engine, args.model)
        print(f"{message['date']} | {message['from']} | {message['subject']}")
        print(f"  {summary}\n")


def _run_report(service: Any, args: argparse.Namespace) -> None:
    messages = fetch_messages(service, args.query, args.limit)
    write_report(messages, Path(args.output), args.engine, args.model)
    print(f"Wrote report for {len(messages)} message(s): {args.output}")


def _run_cleanup(service: Any, args: argparse.Namespace) -> None:
    messages = fetch_messages(service, args.query, args.limit)
    if not messages:
        print("No matching messages; nothing moved to Trash.")
        return
    print(f"Matched {len(messages)} message(s) for query: {args.query}")
    for message in messages:
        print(f"- {message['date']} | {message['from']} | {message['subject']}")
    if args.yes:
        answer = "yes"
    else:
        answer = input("Move these messages to Gmail Trash? Type 'yes' to confirm: ")
    if answer.strip().lower() != "yes":
        print("Cancelled; no messages were changed.")
        return
    for message in messages:
        service.users().messages().trash(userId="me", id=message["id"]).execute()
    print(f"Moved {len(messages)} message(s) to Trash. They were not permanently deleted.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Free, local-first Gmail mailbox agent")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("overview", help="Show account and mailbox totals")

    summarize = subparsers.add_parser("summarize", help="Summarize matching messages")
    summarize.add_argument("--query", default="in:anywhere -in:trash -in:spam")
    summarize.add_argument("--limit", type=int, default=100, help="Use 0 to scan all matches")
    summarize.add_argument("--engine", choices=["extractive", "ollama"], default="extractive")
    summarize.add_argument("--model", default=os.getenv("OLLAMA_MODEL", "llama3.2:3b"))

    report = subparsers.add_parser("report", help="Write a sender/recipient grouped report")
    report.add_argument("--query", default="in:anywhere -in:trash -in:spam")
    report.add_argument("--limit", type=int, default=100, help="Use 0 to scan all matches")
    report.add_argument("--output", default="reports/gmail-report.md")
    report.add_argument("--engine", choices=["extractive", "ollama"], default="extractive")
    report.add_argument("--model", default=os.getenv("OLLAMA_MODEL", "llama3.2:3b"))

    cleanup = subparsers.add_parser("cleanup", help="Review matches and move confirmed mail to Trash")
    cleanup.add_argument("--query", required=True, help="A Gmail search query to review")
    cleanup.add_argument("--limit", type=int, default=20, help="Maximum messages; use 0 for all")
    cleanup.add_argument("--yes", action="store_true", help="Skip confirmation (use carefully)")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command is None:
        args.command = "overview"
    if args.command != "cleanup" and getattr(args, "limit", 1) < 0:
        parser.error("--limit must be zero or greater")
    if args.command == "cleanup" and args.limit < 0:
        parser.error("--limit must be zero or greater")

    try:
        service = _authenticate()
        if args.command == "overview":
            _run_overview(service)
        elif args.command == "summarize":
            _run_summarize(service, args)
        elif args.command == "report":
            _run_report(service, args)
        else:
            _run_cleanup(service, args)
    except FileNotFoundError as error:
        parser.error(f"{error}. Configure a Google OAuth desktop client first.")


if __name__ == "__main__":
    main()