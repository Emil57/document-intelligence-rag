from document_intelligence_rag.loaders import Document
from document_intelligence_rag.search import SearchIndex, tokenize


def doc(source: str, text: str) -> Document:
    return Document(text=text, source=source, kind="text", added_by="tester")


def make_index() -> SearchIndex:
    index = SearchIndex()
    index.add(doc("cats.txt", "Cats sleep most of the day and love warm places."))
    index.add(doc("budget.txt", "The 2026 budget for the marketing team is 40,000 euros."))
    index.add(doc("dogs.txt", "Dogs need a walk every day."))
    return index


def test_tokenize_lowercases_and_drops_stop_words():
    assert tokenize("What is THE budget_plan?") == ["budget", "plan"]


def test_best_match_comes_first():
    results = make_index().search("marketing budget")
    assert results[0].chunk.source == "budget.txt"


def test_no_results_for_unknown_words_or_only_stop_words():
    index = make_index()
    assert index.search("submarine") == []
    assert index.search("what is the") == []


def test_remove_takes_a_document_out_of_search():
    index = make_index()
    assert index.remove("budget.txt") is True
    assert index.search("budget") == []
    assert "budget.txt" not in index.documents
    # The other documents are still found.
    assert index.search("cats")[0].chunk.source == "cats.txt"


def test_remove_unknown_document_returns_false():
    assert make_index().remove("nope.txt") is False


def test_save_and_load_keep_the_library(tmp_path):
    path = tmp_path / "library.json"
    make_index().save(path)
    loaded = SearchIndex.load(path)
    assert set(loaded.documents) == {"cats.txt", "budget.txt", "dogs.txt"}
    assert loaded.search("walk")[0].chunk.source == "dogs.txt"
