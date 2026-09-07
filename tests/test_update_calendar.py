import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("update_calendar", ROOT / "scripts" / "update_calendar.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
FIXTURE = (ROOT / "tests" / "fixtures" / "source.ics").read_text(encoding="utf-8")


class CalendarTests(unittest.TestCase):
    def build(self, source=FIXTURE, existing=None, hour=12):
        now = datetime(2026, 9, 7, hour, tzinfo=timezone.utc)
        return MODULE.build_calendar(source, existing, now)

    def test_filters_friendlies_and_training_but_keeps_official_competitions(self):
        calendar, _, status = self.build()
        text = calendar.decode("utf-8")
        self.assertNotIn("Fiorentina", text)
        self.assertNotIn("Entrenamiento Real Madrid", text)
        for name in ("Betis", "Inter", "Sevilla", "Barcelona", "Boca Juniors"):
            self.assertIn(name, text)
        self.assertEqual(status["published_events"], 5)
        self.assertEqual(status["excluded_events"], 2)
        self.assertNotIn("VALARM", text)

    def test_provisional_is_all_day_and_confirmed_has_two_hour_duration(self):
        calendar, _, status = self.build()
        text = calendar.decode("utf-8")
        self.assertIn("DTSTART;VALUE=DATE:20261011", text)
        self.assertIn("DTEND;VALUE=DATE:20261012", text)
        self.assertIn("Horario por confirmar", text)
        self.assertIn("DTSTART:20260904T190000Z", text)
        self.assertIn("DTEND:20260904T210000Z", text)
        self.assertEqual(status["provisional_events"], 1)

    def test_uid_is_stable_and_changed_event_increments_sequence(self):
        first, _, _ = self.build()
        changed_source = FIXTURE.replace("20260904T190000Z", "20260904T200000Z")
        second, changes, _ = self.build(changed_source, first.decode("utf-8"), hour=13)
        text = second.decode("utf-8")
        self.assertIn("UID:liga-1@realmadrid.com", text)
        self.assertIn("SEQUENCE:1", text)
        self.assertEqual(changes.modified, ("liga-1@realmadrid.com",))

    def test_second_run_is_idempotent(self):
        first, _, _ = self.build(hour=12)
        second, changes, _ = self.build(existing=first.decode("utf-8"), hour=13)
        self.assertEqual(first, second)
        self.assertFalse(changes.has_changes)

    def test_reimport_and_unique_uids(self):
        calendar, _, _ = self.build()
        events = MODULE.parse_events(calendar.decode("utf-8"))
        indexed = MODULE.index_events(events)
        self.assertEqual(len(events), len(indexed))
        self.assertEqual(len(events), 5)

    def test_duplicate_uid_fails(self):
        duplicate = FIXTURE.replace("UID:champions-1@realmadrid.com", "UID:liga-1@realmadrid.com")
        with self.assertRaisesRegex(ValueError, "UID duplicado"):
            self.build(duplicate)

    def test_confirmed_utc_time_converts_for_madrid_and_buenos_aires(self):
        kickoff = datetime(2026, 9, 4, 19, tzinfo=timezone.utc)
        madrid_summer = timezone(timedelta(hours=2))
        buenos_aires = timezone(timedelta(hours=-3))
        self.assertEqual(kickoff.astimezone(madrid_summer).hour, 21)
        self.assertEqual(kickoff.astimezone(buenos_aires).hour, 16)

    def test_utf8_folding_respects_75_octets_and_roundtrips(self):
        logical = "DESCRIPTION:" + "á" * 80
        folded = MODULE.fold_line(logical)
        self.assertTrue(all(len(line.encode("utf-8")) <= 75 for line in folded))
        self.assertEqual(MODULE.unfold_ics("\r\n".join(folded)), [logical])


if __name__ == "__main__":
    unittest.main()
