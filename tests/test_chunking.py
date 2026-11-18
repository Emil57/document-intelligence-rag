from document_intelligence_rag.chunking import chunk_text


def test_empty_text_has_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   \n  ") == []


def test_short_text_is_one_chunk():
    assert chunk_text("Hello world.") == ["Hello world."]


def test_long_text_is_split_into_overlapping_chunks():
    text = " ".join(f"word{i}" for i in range(2000))
    chunks = chunk_text(text, size=500, overlap=100)

    assert len(chunks) > 1
    assert all(len(chunk) <= 500 for chunk in chunks)
    # Every word is in some chunk, so nothing is lost.
    assert set(text.split()) == {word for chunk in chunks for word in chunk.split()}
    # Neighbouring chunks share some words (the overlap).
    for first, second in zip(chunks, chunks[1:]):
        assert set(first.split()) & set(second.split())


def test_chunks_break_at_paragraphs_when_possible():
    paragraph = "A sentence about cats. " * 15
    chunks = chunk_text(f"{paragraph}\n\n{paragraph}", size=500, overlap=50)
    assert chunks[0].endswith("cats.")
