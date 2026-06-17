from moonmind.workflows.temporal.title_search import tokenize_title


def test_tokenize_title_lowercases_and_splits_on_non_alphanumeric() -> None:
    assert tokenize_title("MM-823: Post-Merge Jira") == [
        "mm",
        "823",
        "post",
        "merge",
        "jira",
    ]


def test_tokenize_title_dedupes_preserving_order() -> None:
    assert tokenize_title("fix the fix") == ["fix", "the"]


def test_tokenize_title_handles_blank_and_punctuation_only() -> None:
    assert tokenize_title(None) == []
    assert tokenize_title("") == []
    assert tokenize_title("   ") == []
    assert tokenize_title("!!! ---") == []


def test_tokenize_title_caps_token_count() -> None:
    title = " ".join(f"word{i}" for i in range(120))
    tokens = tokenize_title(title)
    assert len(tokens) == 50
    assert tokens[0] == "word0"
