from app.embeddings import chunk_text, embed_texts, cosine_similarity


def test_chunk_text_splits_long_text_into_overlapping_windows():
    text = " ".join(f"word{i}" for i in range(400))
    chunks = chunk_text(text)

    assert len(chunks) > 1
    overlap = set(chunks[0][0].split()) & set(chunks[1][0].split())
    assert len(overlap) > 0


def test_chunk_text_offsets_slice_the_original_text_exactly():
    text = " ".join(f"word{i}" for i in range(400))
    chunks = chunk_text(text)

    for chunk, char_start, char_end in chunks:
        assert text[char_start:char_end] == chunk


def test_chunk_text_handles_short_text_as_one_chunk():
    text = "just a few words here"
    assert chunk_text(text) == [(text, 0, len(text))]


def test_chunk_text_handles_empty_text():
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_embed_texts_handles_empty_list():
    assert embed_texts([]) == []


def test_embed_texts_returns_one_vector_per_text():
    vectors = embed_texts(["hello world", "goodbye world"])
    assert len(vectors) == 2
    assert len(vectors[0]) == len(vectors[1]) > 0


def test_cosine_similarity_of_identical_vectors_is_one():
    v = [1.0, 2.0, 3.0]
    assert round(cosine_similarity(v, v), 6) == 1.0


def test_cosine_similarity_of_orthogonal_vectors_is_zero():
    assert round(cosine_similarity([1.0, 0.0], [0.0, 1.0]), 6) == 0.0


def test_semantically_similar_sentences_score_higher_than_unrelated_ones():
    vectors = embed_texts(
        [
            "The cat sat on the mat.",
            "A feline rested on the rug.",
            "Stock prices fell sharply today.",
        ]
    )
    similar_score = cosine_similarity(vectors[0], vectors[1])
    unrelated_score = cosine_similarity(vectors[0], vectors[2])
    assert similar_score > unrelated_score
