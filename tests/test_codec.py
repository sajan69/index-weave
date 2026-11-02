import hashlib
import struct
import unittest

from indexweave import Document, Put
from indexweave.codec import (
    COMMIT_MAGIC,
    FRAME_MAGIC,
    HEADER_BYTES,
    MAGIC,
    MAX_PAYLOAD_BYTES,
    PREFIX,
    encode_frame,
    operations_payload,
    scan,
)
from indexweave.errors import Corruption


class CodecTests(unittest.TestCase):
    def setUp(self):
        self.payload = (
            b'{"operations":[{"document":{"id":"a","tags":[],"text":"Body",'
            b'"title":"Title"},"op":"put"}],"version":1}'
        )
        self.prefix = struct.pack(">4sIQ", b"IWFR", len(self.payload), 1)
        self.header = self.prefix + hashlib.sha256(self.prefix).digest()
        self.frame = (
            self.header
            + self.payload
            + hashlib.sha256(self.header + self.payload).digest()
            + b"IWCOMMIT"
        )

    def test_hand_built_frame_matches_contract_and_document(self):
        self.assertEqual(
            operations_payload([Put(Document("a", "Title", "Body"))]), self.payload
        )
        self.assertEqual(encode_frame(self.payload, 1), self.frame)
        snapshot, report = scan(MAGIC + self.frame)
        self.assertEqual(snapshot.get("a").document.text, "Body")
        self.assertEqual(snapshot.get("a").revision, 1)
        self.assertEqual(report.valid_bytes, len(MAGIC + self.frame))
        self.assertEqual(report.tail_bytes, 0)

    def test_every_incomplete_cut_has_only_validated_prefix(self):
        for cut in range(1, len(self.frame)):
            with self.subTest(cut=cut):
                state, report = scan(MAGIC + self.frame[:cut])
                self.assertEqual(state.revision, 0)
                self.assertEqual(report.valid_bytes, len(MAGIC))
                self.assertEqual(report.tail_bytes, cut)
        next_frame = encode_frame(self.payload, 2)
        state, report = scan(MAGIC + self.frame + next_frame[:20])
        self.assertEqual(state.revision, 1)
        self.assertEqual(report.tail_bytes, 20)

    def test_complete_damage_is_not_treated_as_a_repairable_tail(self):
        for position in [
            0,
            5,
            12,
            20,
            HEADER_BYTES + 5,
            len(self.frame) - 10,
            len(self.frame) - 1,
        ]:
            broken = bytearray(self.frame)
            broken[position] ^= 1
            with self.subTest(position=position), self.assertRaises(Corruption):
                scan(MAGIC + broken)

    def test_sequence_reorder_duplicate_and_skip_are_refused(self):
        for raw in [
            MAGIC + encode_frame(self.payload, 2),
            MAGIC + self.frame + self.frame,
        ]:
            with self.assertRaises(Corruption):
                scan(raw)

    def test_checksum_valid_but_invalid_json_or_schema_is_refused(self):
        values = [
            b"\xff",
            b'{"operations":[],"version":1}',
            b'{"operations":[],"version":1,"version":1}',
            b'{"operations":[],"version":true}',
            b'{"operations":[],"version":NaN}',
            self.payload.replace(b'"version":1', b'"version":1.0'),
            b" " + self.payload,
            self.payload.replace(b'"tags":[]', b'"tags":[],"unknown":1'),
        ]
        for payload in values:
            with self.subTest(payload=payload[:30]), self.assertRaises(Corruption):
                scan(MAGIC + encode_frame(payload, 1))

    def test_valid_payload_with_impossible_delete_is_corruption(self):
        payload = b'{"operations":[{"id":"missing","op":"delete"}],"version":1}'
        with self.assertRaises(Corruption):
            scan(MAGIC + encode_frame(payload, 1))

    def test_claimed_length_is_checked_without_allocating_payload(self):
        prefix = PREFIX.pack(FRAME_MAGIC, MAX_PAYLOAD_BYTES + 1, 1)
        with self.assertRaises(Corruption):
            scan(MAGIC + prefix + hashlib.sha256(prefix).digest())

    def test_partial_header_corruption_and_missing_magic_refused(self):
        for raw in [
            b"",
            MAGIC[:-1],
            b"wronglog",
            MAGIC + b"X",
            MAGIC + self.frame[:30][:-1] + b"x",
        ]:
            with self.assertRaises(Corruption):
                scan(raw)
        self.assertEqual(COMMIT_MAGIC, b"IWCOMMIT")
