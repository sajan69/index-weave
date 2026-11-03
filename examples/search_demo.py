"""Search only fictional documents inside a newly created temporary workspace."""

import json
from tempfile import TemporaryDirectory

from indexweave import Delete, Document, LexicalIndex, Put, Writer, read_snapshot


def run():
    with TemporaryDirectory() as parent:
        root = parent + "/example"
        with Writer.create(root) as writer:
            first = writer.write(
                [
                    Put(
                        Document(
                            "journal",
                            "Lock notes",
                            "A write ahead log records each transaction. "
                            "Recovery replays committed records.",
                            ("storage",),
                        )
                    ),
                    Put(
                        Document(
                            "index",
                            "Search notes",
                            "A positional index supports phrase search. "
                            "Ranking uses document lengths.",
                            ("search",),
                        )
                    ),
                    Put(
                        Document(
                            "draft",
                            "Draft",
                            "Temporary recovery notes.",
                            ("storage", "draft"),
                        )
                    ),
                    Put(
                        Document(
                            "travel",
                            "Travel",
                            "Train timetable for the weekend.",
                            ("personal",),
                        )
                    ),
                ],
                expected_revision=0,
            )
            original = LexicalIndex.from_snapshot(first)
            cases = []
            for query, expected in [
                ('"write ahead" tag:storage', ["journal"]),
                ('"phrase search"', ["index"]),
                ("recovery", ["draft", "journal"]),
                ("tag:storage", ["draft", "journal"]),
            ]:
                result = original.search(query)
                assert sorted(hit["id"] for hit in result["hits"]) == expected
                cases.append(
                    {
                        "query": query,
                        "total_matches": result["total_matches"],
                        "hits": result["hits"],
                    }
                )
            writer.write(
                [
                    Put(
                        Document(
                            "journal",
                            "Lock notes",
                            "A durable journal stores committed batches.",
                            ("storage",),
                        )
                    ),
                    Delete("draft"),
                ],
                expected_revision=1,
            )
        current = LexicalIndex.from_snapshot(read_snapshot(root))
        assert current.search("recovery")["total_matches"] == 0
        assert original.search("recovery")["total_matches"] == 2
        assert [hit["id"] for hit in current.search("durable")["hits"]] == ["journal"]
        return {
            "completed": True,
            "workload": "synthetic-lexical-lifecycle-v1",
            "initial_index": original.metadata(),
            "current_index": current.metadata(),
            "queries": cases,
            "updated_query": current.search("durable"),
            "deleted_and_replaced_terms_absent": True,
            "old_index_unchanged": True,
        }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, sort_keys=True))
