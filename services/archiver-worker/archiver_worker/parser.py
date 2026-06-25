"""Parsing MIME d'un mail brut en une structure exploitable."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import getaddresses, parsedate_to_datetime


@dataclass
class ParsedAttachment:
    filename: str
    content_type: str
    content: bytes


@dataclass
class ParsedMail:
    message_id: str
    date: dt.datetime
    from_addr: str
    to_addrs: list[str]
    cc_addrs: list[str]
    subject: str
    text_body: str  # texte indexable (corps text/plain ou HTML aplati)
    raw: bytes  # message d'origine, conservé pour l'export EML fidèle
    headers_canonical: bytes
    attachments: list[ParsedAttachment] = field(default_factory=list)


def _addresses(value: str | None) -> list[str]:
    if not value:
        return []
    return [addr for _, addr in getaddresses([value]) if addr]


def parse(raw: bytes) -> ParsedMail:
    msg: EmailMessage = message_from_bytes(raw, policy=policy.default)  # type: ignore[assignment]

    try:
        date = parsedate_to_datetime(msg["Date"]) if msg["Date"] else dt.datetime.now(dt.timezone.utc)
    except (TypeError, ValueError):
        date = dt.datetime.now(dt.timezone.utc)
    if date.tzinfo is None:
        date = date.replace(tzinfo=dt.timezone.utc)

    attachments: list[ParsedAttachment] = []
    text_parts: list[str] = []

    for part in msg.walk():
        if part.is_multipart():
            continue
        disposition = part.get_content_disposition()
        ctype = part.get_content_type()
        if disposition == "attachment" or (part.get_filename() and disposition != "inline"):
            payload = part.get_payload(decode=True) or b""
            attachments.append(
                ParsedAttachment(
                    filename=part.get_filename() or "unnamed",
                    content_type=ctype,
                    content=payload,
                )
            )
        elif ctype == "text/plain":
            text_parts.append(part.get_content())
        elif ctype == "text/html" and not text_parts:
            text_parts.append(part.get_content())

    # En-têtes canoniques : base stable pour l'empreinte/signature de l'archive.
    canonical_headers = ["Message-ID", "Date", "From", "To", "Cc", "Subject"]
    canonical = "\n".join(f"{h}: {msg.get(h, '')}" for h in canonical_headers).encode()

    return ParsedMail(
        message_id=(msg["Message-ID"] or "").strip("<>"),
        date=date,
        from_addr=(_addresses(msg["From"]) or [""])[0],
        to_addrs=_addresses(msg["To"]),
        cc_addrs=_addresses(msg["Cc"]),
        subject=msg["Subject"] or "",
        text_body="\n".join(text_parts),
        raw=raw,
        headers_canonical=canonical,
        attachments=attachments,
    )
