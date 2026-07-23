from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from services.umil.app.evidence import EvidenceLedger


class EvidenceLedgerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = EvidenceLedger(Path(self.temp.name) / "umil.db")
        self.now = datetime(2026, 7, 22, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp.cleanup()

    def test_append_builds_verifiable_hash_chain(self):
        first = self.ledger.append(event_type="FIRST", payload={"value": 1}, observed_at=self.now)
        second = self.ledger.append(event_type="SECOND", payload={"value": 2}, observed_at=self.now)
        self.assertEqual(first.sequence, 1)
        self.assertEqual(second.previous_chain_hash, first.chain_hash)
        self.assertEqual(self.ledger.verify_chain(), (True, None))

    def test_database_triggers_reject_update_and_delete(self):
        record = self.ledger.append(event_type="IMMUTABLE", payload={"value": 1}, observed_at=self.now)
        connection = sqlite3.connect(self.ledger.database_path)
        try:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                connection.execute(
                    "UPDATE evidence_records SET payload_json = '{}' WHERE evidence_id = ?",
                    (record.evidence_id,),
                )
            connection.rollback()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                connection.execute(
                    "DELETE FROM evidence_records WHERE evidence_id = ?", (record.evidence_id,)
                )
        finally:
            connection.close()

    def test_correction_appends_lineage_instead_of_rewriting(self):
        original = self.ledger.append(event_type="OBSERVATION", payload={"value": 1}, observed_at=self.now)
        correction = self.ledger.append(
            event_type="OBSERVATION_CORRECTION",
            payload={"value": 2},
            observed_at=self.now,
            supersedes_evidence_id=original.evidence_id,
            correction_reason="late provider correction",
        )
        self.assertEqual(correction.supersedes_evidence_id, original.evidence_id)
        self.assertEqual(self.ledger.get(original.evidence_id).payload, {"value": 1})
        self.assertEqual(self.ledger.verify_chain(), (True, None))

    def test_chain_verification_detects_external_tampering(self):
        record = self.ledger.append(event_type="OBSERVATION", payload={"value": 1}, observed_at=self.now)
        connection = sqlite3.connect(self.ledger.database_path)
        try:
            connection.execute("DROP TRIGGER evidence_records_no_update")
            connection.execute(
                "UPDATE evidence_records SET payload_json = ? WHERE evidence_id = ?",
                ('{"value":999}', record.evidence_id),
            )
            connection.commit()
        finally:
            connection.close()
        valid, reason = self.ledger.verify_chain()
        self.assertFalse(valid)
        self.assertIn("payload hash mismatch", reason)


    def test_reopened_ledger_streams_full_verification_and_then_checks_only_new_rows(self):
        for index in range(75):
            self.ledger.append(
                event_type="BATCHED", payload={"index": index}, observed_at=self.now
            )

        reopened = EvidenceLedger(self.ledger.database_path)
        self.assertEqual(reopened.verification_status()["state"], "UNVERIFIED")
        self.assertEqual(reopened.verify_chain(full=True, batch_size=16), (True, None))
        self.assertEqual(reopened.verification_status()["verified_sequence"], 75)

        appended = reopened.append(
            event_type="INCREMENTAL", payload={"index": 75}, observed_at=self.now
        )
        self.assertEqual(appended.sequence, 76)
        self.assertEqual(reopened.verify_chain(full=False, batch_size=16), (True, None))
        status = reopened.verification_status()
        self.assertEqual(status["state"], "VALID")
        self.assertEqual(status["verified_sequence"], 76)

    def test_verification_status_is_constant_time_and_does_not_open_sqlite(self):
        self.ledger.append(event_type="STATUS", payload={"value": 1}, observed_at=self.now)
        with patch.object(self.ledger, "_connect", side_effect=AssertionError("SQLite opened")):
            status = self.ledger.verification_status()
        self.assertEqual(status["state"], "VALID")
        self.assertTrue(status["valid"])
        self.assertEqual(status["verified_sequence"], 1)

    def test_ledger_closes_every_connection_it_opens(self):
        original_connect = sqlite3.connect

        class TrackingConnection(sqlite3.Connection):
            opened = 0
            closed = 0

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                type(self).opened += 1

            def close(self):
                type(self).closed += 1
                super().close()

        def connect(*args, **kwargs):
            return original_connect(*args, factory=TrackingConnection, **kwargs)

        with tempfile.TemporaryDirectory() as temp, patch(
            "services.umil.app.evidence.sqlite3.connect", side_effect=connect
        ):
            ledger = EvidenceLedger(Path(temp) / "tracked.db")
            record = ledger.append(
                event_type="TRACKED", payload={"value": 1}, observed_at=self.now
            )
            self.assertIsNotNone(ledger.get(record.evidence_id))
            self.assertEqual(ledger.verify_chain(), (True, None))

        self.assertGreater(TrackingConnection.opened, 0)
        self.assertEqual(TrackingConnection.closed, TrackingConnection.opened)


if __name__ == "__main__":
    unittest.main()
