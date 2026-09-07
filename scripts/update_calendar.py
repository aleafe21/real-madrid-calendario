#!/usr/bin/env python3
"""Genera calendario iCalendar filtrado desde feed oficial Real Madrid."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen


DEFAULT_SOURCE = (
    "https://publish.realmadrid.com/content/sling/app-servlets/realmadrid/"
    "ical.3kq9cckrnlogidldtdie2fkbl.es-es.ics"
)
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "site" / "real-madrid.ics"
EXCLUDED_PATTERN = re.compile(
    r"\b(amistos[oa]s?|friendly|friendlies|amical(?:e|es)?|"
    r"freundschaftsspiel|amichevole|entrenamiento|training)\b",
    re.IGNORECASE,
)
VOLATILE_PROPERTIES = {"DTSTAMP", "LAST-MODIFIED", "SEQUENCE"}


@dataclass(frozen=True)
class ChangeSet:
    added: tuple[str, ...]
    modified: tuple[str, ...]
    removed: tuple[str, ...]

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.modified or self.removed)


def unfold_ics(text: str) -> list[str]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    unfolded: list[str] = []
    for line in lines:
        if line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    return unfolded


def property_name(line: str) -> str:
    return line.split(":", 1)[0].split(";", 1)[0].upper()


def property_value(lines: list[str], name: str) -> str | None:
    wanted = name.upper()
    for line in lines:
        if ":" in line and property_name(line) == wanted:
            return line.split(":", 1)[1]
    return None


def parse_events(text: str) -> list[list[str]]:
    events: list[list[str]] = []
    current: list[str] | None = None
    nested_depth = 0
    for raw_line in unfold_ics(text):
        line = raw_line.strip("\ufeff")
        if line == "BEGIN:VEVENT":
            if current is not None:
                raise ValueError("VEVENT anidado no válido")
            current = []
            nested_depth = 0
        elif line == "END:VEVENT":
            if current is None:
                raise ValueError("END:VEVENT sin BEGIN:VEVENT")
            if nested_depth:
                raise ValueError("Componente anidado sin cierre")
            events.append(current)
            current = None
        elif current is not None:
            if line.startswith("BEGIN:"):
                nested_depth += 1
            elif line.startswith("END:"):
                nested_depth -= 1
                if nested_depth < 0:
                    raise ValueError("Cierre de componente inesperado")
            elif nested_depth == 0 and line:
                current.append(line)
    if current is not None:
        raise ValueError("VEVENT sin cierre")
    return events


def event_label(lines: list[str]) -> str:
    return property_value(lines, "SUMMARY") or property_value(lines, "UID") or "Sin título"


def excluded_event(lines: list[str]) -> bool:
    classification = " ".join(
        value
        for name in ("DESCRIPTION", "CATEGORIES")
        if (value := property_value(lines, name))
    )
    return bool(EXCLUDED_PATTERN.search(classification.replace("\\n", " ")))


def remove_properties(lines: list[str], names: set[str]) -> list[str]:
    return [line for line in lines if property_name(line) not in names]


def replace_property(lines: list[str], name: str, replacements: list[str]) -> list[str]:
    wanted = name.upper()
    result: list[str] = []
    inserted = False
    for line in lines:
        if property_name(line) == wanted:
            if not inserted:
                result.extend(replacements)
                inserted = True
        else:
            result.append(line)
    if not inserted:
        result.extend(replacements)
    return result


def append_tbd_description(lines: list[str]) -> list[str]:
    marker = "Horario por confirmar"
    result: list[str] = []
    found = False
    for line in lines:
        if property_name(line) == "DESCRIPTION":
            found = True
            if marker.casefold() not in line.casefold():
                line = f"{line}\\n{marker}"
        result.append(line)
    if not found:
        result.append(f"DESCRIPTION:{marker}")
    return result


def normalize_event(lines: list[str]) -> tuple[list[str], bool]:
    uid = property_value(lines, "UID")
    start = property_value(lines, "DTSTART")
    if not uid or not start:
        raise ValueError(f"Evento sin UID o DTSTART: {event_label(lines)}")

    clean = remove_properties(lines, VOLATILE_PROPERTIES | {"DTEND", "X-RM-TIME-TBD"})
    provisional = bool(re.fullmatch(r"\d{8}T000000Z", start))
    if provisional:
        day = datetime.strptime(start[:8], "%Y%m%d").date()
        clean = replace_property(
            clean,
            "DTSTART",
            [
                f"DTSTART;VALUE=DATE:{day:%Y%m%d}",
                f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}",
            ],
        )
        clean = append_tbd_description(clean)
        clean.append("X-RM-TIME-TBD:TRUE")
    else:
        match = re.fullmatch(r"(\d{8}T\d{6})Z", start)
        if not match:
            raise ValueError(f"DTSTART no soportado en {event_label(lines)}: {start}")
        start_dt = datetime.strptime(match.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        end_dt = start_dt + timedelta(hours=2)
        clean = replace_property(
            clean,
            "DTSTART",
            [f"DTSTART:{start_dt:%Y%m%dT%H%M%SZ}", f"DTEND:{end_dt:%Y%m%dT%H%M%SZ}"],
        )
    return clean, provisional


def semantic_signature(lines: list[str]) -> tuple[str, ...]:
    return tuple(line for line in lines if property_name(line) not in VOLATILE_PROPERTIES)


def metadata_from(lines: list[str]) -> tuple[str | None, str | None, int]:
    dtstamp = property_value(lines, "DTSTAMP")
    modified = property_value(lines, "LAST-MODIFIED")
    sequence_raw = property_value(lines, "SEQUENCE") or "0"
    try:
        sequence = int(sequence_raw)
    except ValueError:
        sequence = 0
    return dtstamp, modified, sequence


def add_metadata(lines: list[str], dtstamp: str, modified: str, sequence: int) -> list[str]:
    result: list[str] = []
    inserted = False
    for line in lines:
        result.append(line)
        if not inserted and property_name(line) == "UID":
            result.extend(
                [
                    f"DTSTAMP:{dtstamp}",
                    f"LAST-MODIFIED:{modified}",
                    f"SEQUENCE:{sequence}",
                ]
            )
            inserted = True
    if not inserted:
        raise ValueError(f"Evento sin UID: {event_label(lines)}")
    return result


def index_events(events: list[list[str]]) -> dict[str, list[str]]:
    indexed: dict[str, list[str]] = {}
    for event in events:
        uid = property_value(event, "UID")
        if not uid:
            raise ValueError(f"Evento sin UID: {event_label(event)}")
        if uid in indexed:
            raise ValueError(f"UID duplicado: {uid}")
        indexed[uid] = event
    return indexed


def fold_line(line: str, limit: int = 75) -> list[str]:
    if len(line.encode("utf-8")) <= limit:
        return [line]
    folded: list[str] = []
    current = ""
    current_bytes = 0
    prefix = ""
    for char in line:
        encoded_length = len(char.encode("utf-8"))
        available = limit - (1 if prefix else 0)
        if current and current_bytes + encoded_length > available:
            folded.append(prefix + current)
            current = char
            current_bytes = encoded_length
            prefix = " "
        else:
            current += char
            current_bytes += encoded_length
    if current:
        folded.append(prefix + current)
    return folded


def render_calendar(events: list[list[str]]) -> bytes:
    logical_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//aleafe21//Calendario Real Madrid//ES",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "NAME:Real Madrid - Partidos oficiales",
        "X-WR-CALNAME:Real Madrid - Partidos oficiales",
        "X-WR-TIMEZONE:UTC",
    ]
    for event in events:
        logical_lines.append("BEGIN:VEVENT")
        logical_lines.extend(event)
        logical_lines.append("END:VEVENT")
    logical_lines.append("END:VCALENDAR")
    physical_lines: list[str] = []
    for line in logical_lines:
        physical_lines.extend(fold_line(line))
    return ("\r\n".join(physical_lines) + "\r\n").encode("utf-8")


def read_source(source: str) -> str:
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        request = Request(source, headers={"User-Agent": "real-madrid-calendario/1.0"})
        with urlopen(request, timeout=30) as response:
            if response.status != 200:
                raise RuntimeError(f"Fuente respondió HTTP {response.status}")
            return response.read().decode("utf-8-sig")
    return Path(source).read_text(encoding="utf-8-sig")


def read_existing(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    return parse_events(path.read_text(encoding="utf-8-sig"))


def start_sort_key(lines: list[str]) -> str:
    return property_value(lines, "DTSTART") or "99999999"


def build_calendar(
    source_text: str,
    existing_text: str | None = None,
    now: datetime | None = None,
) -> tuple[bytes, ChangeSet, dict[str, object]]:
    source_events = parse_events(source_text)
    excluded = [event for event in source_events if excluded_event(event)]
    included = [event for event in source_events if not excluded_event(event)]
    normalized = [normalize_event(event) for event in included]
    normalized_events = [event for event, _ in normalized]
    provisional_count = sum(1 for _, provisional in normalized if provisional)
    new_index = index_events(normalized_events)

    old_events = parse_events(existing_text) if existing_text else []
    old_index = index_events(old_events)
    old_semantic = {
        uid: semantic_signature(remove_properties(event, VOLATILE_PROPERTIES))
        for uid, event in old_index.items()
    }
    new_semantic = {uid: semantic_signature(event) for uid, event in new_index.items()}

    added = sorted(set(new_index) - set(old_index))
    removed = sorted(set(old_index) - set(new_index))
    modified = sorted(
        uid for uid in set(new_index) & set(old_index) if new_semantic[uid] != old_semantic[uid]
    )
    changes = ChangeSet(tuple(added), tuple(modified), tuple(removed))

    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final_events: list[list[str]] = []
    for uid, event in new_index.items():
        if uid in old_index and uid not in modified:
            old_dtstamp, old_modified, old_sequence = metadata_from(old_index[uid])
            final_events.append(
                add_metadata(event, old_dtstamp or timestamp, old_modified or timestamp, old_sequence)
            )
        else:
            old_sequence = metadata_from(old_index[uid])[2] if uid in old_index else -1
            final_events.append(add_metadata(event, timestamp, timestamp, old_sequence + 1))

    final_events.sort(key=start_sort_key)
    status: dict[str, object] = {
        "status": "ok",
        "source_events": len(source_events),
        "published_events": len(final_events),
        "excluded_events": len(excluded),
        "provisional_events": provisional_count,
        "changes": {
            "added": [event_label(new_index[uid]) for uid in added],
            "modified": [event_label(new_index[uid]) for uid in modified],
            "removed": [event_label(old_index[uid]) for uid in removed],
        },
    }
    return render_calendar(final_events), changes, status


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Compara sin escribir")
    mode.add_argument("--update", action="store_true", help="Actualiza calendario y estado")
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="URL o archivo ICS fuente")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Archivo ICS destino")
    parser.add_argument("--status", type=Path, help="Archivo JSON de estado")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    output = args.output.resolve()
    status_path = (args.status or output.with_name("status.json")).resolve()
    try:
        source_text = read_source(args.source)
        existing_text = output.read_text(encoding="utf-8-sig") if output.exists() else None
        calendar_bytes, changes, status = build_calendar(source_text, existing_text)
        status["source"] = args.source
        status["checked_at_utc"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        status["calendar"] = output.name
        if args.update:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(calendar_bytes)
            status_path.parent.mkdir(parents=True, exist_ok=True)
            status_path.write_text(
                json.dumps(status, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        print(json.dumps(status, ensure_ascii=False, indent=2))
        if args.check:
            print("Cambios detectados." if changes.has_changes else "Sin cambios.")
        return 0
    except Exception as exc:  # mensaje CLI controlado
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
