"""Lexical similarity utilities.

Deliberately dependency-free: a small BM25 implementation over tokenized issue
and file text. Retrieval must run offline, with no embedding model and no
network access (docs/architecture.md, invariant 4).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
        "has", "have", "if", "in", "into", "is", "it", "its", "of", "on", "or",
        "such", "that", "the", "their", "then", "there", "these", "they", "this",
        "to", "was", "were", "will", "with", "what", "when", "where", "which",
        "who", "why", "would", "should", "can", "could", "not", "no", "nor",
        "so", "than", "too", "very", "just", "about", "after", "before", "over",
        "under", "again", "further", "once", "here", "out", "up", "down", "do",
        "does", "did", "doing", "i", "you", "he", "she", "we", "me", "my",
        "your", "our", "us",
    }
)

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_CODE_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z]+")

TOKENISATION_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def split_identifier(name: str) -> list[str]:
    """Split ``apply_bulk_discount`` / ``HTTPError`` into lowercase sub-tokens."""
    parts = [p for p in _CAMEL_RE.sub(" ", name).replace("_", " ").split(" ") if p]
    return [p.lower() for p in parts if p]


def tokenize(text: str, *, drop_stopwords: bool = True) -> list[str]:
    """Tokenize prose and code into lowercase terms."""
    raw = TOKENISATION_RE.findall(text)
    out: list[str] = []
    for token in raw:
        lowered = token.lower()
        if drop_stopwords and lowered in STOPWORDS and len(lowered) <= 3:
            continue
        out.append(lowered)
        if "_" in token or _CAMEL_RE.search(token):
            out.extend(split_identifier(token))
    return out


def extract_issue_entities(text: str, *, limit: int = 40) -> list[str]:
    """Pull distinctive identifiers and code-like tokens out of an issue body.

    Mirrors the "keywords in the issue description" step of prior retrievers,
    but without any model call.
    """
    scored: Counter[str] = Counter()
    for match in _CODE_TOKEN_RE.finditer(text):
        token = match.group(0)
        if len(token) < 3 or token.lower() in STOPWORDS:
            continue
        if token[0].isdigit():
            continue
        scored[token] += 1
        for piece in split_identifier(token):
            if len(piece) >= 3 and piece not in STOPWORDS:
                scored[piece] += 1
    # Prefer multi-part and longer identifiers: they are more discriminative.
    ranked = sorted(
        scored.items(),
        key=lambda kv: (kv[1], "_" in kv[0], len(kv[0]), kv[0]),
        reverse=True,
    )
    seen: set[str] = set()
    out: list[str] = []
    for token, _ in ranked:
        lowered = token.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        out.append(token)
        if len(out) >= limit:
            break
    return out


@dataclass
class BM25Index:
    """A minimal BM25 index over (path, text) documents."""

    k1: float = 1.5
    b: float = 0.75

    def build(self, docs: dict[str, str]) -> None:
        self.doc_tokens = {path: tokenize(text) for path, text in docs.items()}
        self.doc_len = {path: len(toks) for path, toks in self.doc_tokens.items()}
        self.avg_len = (
            sum(self.doc_len.values()) / len(self.doc_len) if self.doc_len else 0.0
        )
        self.tf: dict[str, Counter[str]] = {
            path: Counter(toks) for path, toks in self.doc_tokens.items()
        }
        df: Counter[str] = Counter()
        for toks in self.doc_tokens.values():
            df.update(set(toks))
        n = len(self.doc_tokens) or 1
        self.idf = {
            term: math.log(1 + (n - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }

    def score(self, query_terms: list[str], path: str) -> float:
        if not hasattr(self, "tf") or path not in self.tf:
            return 0.0
        total = 0.0
        doc_len = self.doc_len[path] or 1
        norm = self.k1 * (1 - self.b + self.b * doc_len / (self.avg_len or 1))
        for term in query_terms:
            freq = self.tf[path].get(term, 0)
            if not freq:
                continue
            idf = self.idf.get(term, 0.0)
            total += idf * (freq * (self.k1 + 1)) / (freq + norm)
        return total

    def rank(self, query_terms: list[str]) -> list[tuple[str, float]]:
        if not hasattr(self, "tf"):
            return []
        scored = [(path, self.score(query_terms, path)) for path in self.tf]
        return sorted((p for p in scored if p[1] > 0.0), key=lambda kv: (-kv[1], kv[0]))


def contains_entity(text: str, entity: str) -> bool:
    """Case-insensitive whole-token containment check."""
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(entity)}(?![A-Za-z0-9_])", text) is not None
