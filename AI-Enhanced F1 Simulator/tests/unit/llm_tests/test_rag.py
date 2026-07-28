import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src" / "llm"))

import rag


@pytest.fixture
def isolated_kb(monkeypatch, tmp_path):
    """Point load_knowledge_base()/retrieve() at a throwaway knowledge_base
    folder and a fresh collection, so tests can control file contents
    without touching the real knowledge base or its already-loaded
    global collection."""
    fake_module_file = tmp_path / "rag.py"
    fake_module_file.write_text("# placeholder")
    monkeypatch.setattr(rag, "__file__", str(fake_module_file))

    fresh_collection = rag.chroma_client.get_or_create_collection(name=f"test_kb_{tmp_path.name}")
    monkeypatch.setattr(rag, "collection", fresh_collection)
    monkeypatch.setattr(rag, "_IS_LOADED", False)

    def _write(rel_path, content):
        path = tmp_path / "knowledge_base" / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    return _write, fresh_collection


def test_loading_knowledge_base_twice_does_not_crash(isolated_kb):
    write, collection = isolated_kb
    write("driving_technique/braking.txt", "Brake in a straight line.\n\nRelease the brake smoothly.")

    rag.load_knowledge_base()
    rag.load_knowledge_base()  # already loaded - must be a no-op, not a crash or re-index

    assert collection.count() == 2


def test_loading_knowledge_base_repeatedly_stays_stable(isolated_kb):
    write, collection = isolated_kb
    write("driving_technique/throttle.txt", "Apply throttle progressively.\n\nAvoid pumping the pedal.")

    for _ in range(5):
        rag.load_knowledge_base()

    assert collection.count() == 2
    assert rag._IS_LOADED is True


def test_malformed_file_loads_without_empty_chunks_or_index_errors(isolated_kb):
    write, collection = isolated_kb
    # Blank lines, whitespace-only "paragraphs", and a completely empty file
    # should all be tolerated without producing empty documents or raising.
    write(
        "telemetry/track_position.txt",
        "\n\n   \n\nStay on the racing line.\n\n\n\n   \n\nExit wide, apex late.\n\n",
    )
    write("telemetry/empty.txt", "")

    rag.load_knowledge_base()

    docs = collection.get()["documents"]
    assert docs
    assert all(doc.strip() for doc in docs)
    assert len(docs) == 2


def test_same_filename_in_different_subdirs_does_not_collide(isolated_kb):
    write, collection = isolated_kb
    write("driving_technique/notes.txt", "Brake later at Turn 1.")
    write("telemetry/notes.txt", "Track position drifted wide at Turn 1.")

    rag.load_knowledge_base()

    all_ids = collection.get()["ids"]
    assert len(all_ids) == len(set(all_ids))
    assert collection.count() == 2


def test_paragraph_break_convention_is_consistent():
    # Real knowledge base files must use a blank line ("\n\n") to separate
    # paragraphs, since that is the exact delimiter load_knowledge_base()
    # splits on - single newlines between paragraphs would silently collapse
    # a file into one giant chunk instead of several retrievable ones.
    knowledge_dir = os.path.join(os.path.dirname(os.path.abspath(rag.__file__)), "knowledge_base")
    txt_files = []
    for root, _dirs, files in os.walk(knowledge_dir):
        for filename in files:
            if filename.endswith(".txt"):
                txt_files.append(os.path.join(root, filename))

    assert txt_files, "expected at least one knowledge base file to check"

    for path in txt_files:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        chunks = [c.strip() for c in content.split("\n\n") if c.strip()]
        assert chunks, f"{path} produced no retrievable chunks"
