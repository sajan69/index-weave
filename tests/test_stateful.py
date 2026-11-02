import tempfile
from pathlib import Path

from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from indexweave import Delete, Document, Put, Writer, read_snapshot


class DocumentMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "workspace"
        self.writer = Writer.create(self.path)
        self.model = {}
        self.revision = 0
        self.snapshots = []

    @rule(
        key=st.sampled_from(["a", "b", "c", "d"]),
        text=st.text(alphabet="abc XYZ\néन", min_size=1, max_size=100),
    )
    def put(self, key, text):
        self.snapshots.append((self.writer.snapshot(), dict(self.model)))
        document = Document(key, "Reference title", text)
        self.writer.write([Put(document)], expected_revision=self.revision)
        self.revision += 1
        self.model[key] = (document, self.revision)

    @precondition(lambda self: bool(self.model))
    @rule(data=st.data())
    def delete(self, data):
        key = data.draw(st.sampled_from(sorted(self.model)))
        self.snapshots.append((self.writer.snapshot(), dict(self.model)))
        self.writer.write([Delete(key)], expected_revision=self.revision)
        self.revision += 1
        del self.model[key]

    @rule()
    def reopen(self):
        self.writer.close()
        self.writer = Writer(self.path)

    @invariant()
    def agrees_with_dictionary_and_prior_snapshots(self):
        for snapshot in [self.writer.snapshot(), read_snapshot(self.path)]:
            assert snapshot.revision == self.revision
            assert {
                key: (record.document, record.revision)
                for key, record in snapshot.documents.items()
            } == self.model
        for snapshot, reference in self.snapshots:
            assert {
                key: (record.document, record.revision)
                for key, record in snapshot.documents.items()
            } == reference

    def teardown(self):
        self.writer.close()
        self.tmp.cleanup()


TestDocumentMachine = DocumentMachine.TestCase
TestDocumentMachine.settings = settings(
    max_examples=40,
    stateful_step_count=35,
    derandomize=True,
    database=None,
    deadline=None,
)
