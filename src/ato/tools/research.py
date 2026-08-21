"""Bounded coordination of search results and public-source retrieval."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ato.exceptions import ToolError
from ato.tools.search import WebSearchClient

MAX_RESEARCH_SOURCES = 3
MAX_CANDIDATES = 10
MAX_SOURCES_PER_HOST = 2
MAX_SOURCE_TEXT_CHARS = 2_500
MAX_METADATA_CHARS = 300
MAX_FAILURE_CHARS = 300
MAX_PASSAGES_PER_SOURCE = 2
MAX_PASSAGE_CHARS = 300
MAX_DISAGREEMENT_HINTS = 5
WORD_PATTERN = re.compile(r"[a-z0-9]+", re.IGNORECASE)
NUMBER_PATTERN = re.compile(r"(?<!\w)(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?%?)(?!\w)")
SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+|\n+")
STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "for",
    "in",
    "is",
    "of",
    "on",
    "the",
    "to",
    "what",
    "when",
    "where",
    "which",
    "who",
}


class WebResearchCoordinator:
    """Search, diversify, and fetch a small set of untrusted public sources."""

    def __init__(self, searcher: WebSearchClient, fetcher: Callable[[str], str]) -> None:
        self._searcher = searcher
        self._fetcher = fetcher

    def research(self, query: str, max_sources: int = 3) -> str:
        if not 1 <= max_sources <= MAX_RESEARCH_SOURCES:
            raise ToolError(f"Research source count must be between 1 and {MAX_RESEARCH_SOURCES}.")
        search_payload = _parse_object(
            self._searcher.search(query, count=min(MAX_CANDIDATES, max_sources * 3)),
            "Web search returned invalid research data.",
        )
        raw_results = search_payload.get("results")
        if not isinstance(raw_results, list):
            raise ToolError("Web search returned invalid research results.")

        candidates = _select_candidates(raw_results, max_sources)
        sources: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        for rank, candidate in enumerate(candidates, start=1):
            url = candidate["url"]
            try:
                page = _parse_object(
                    self._fetcher(url), "Fetched source returned invalid research data."
                )
                source_url = str(page.get("source_url", ""))
                if source_url != url:
                    raise ToolError("Fetched source URL did not match the approved search result.")
                text = str(page.get("text", ""))
                if not text.strip():
                    raise ToolError("Fetched source contained no readable text.")
                sources.append(
                    {
                        "source_id": f"S{rank}",
                        "rank": rank,
                        "title": str(page.get("title") or candidate["title"])[
                            :MAX_METADATA_CHARS
                        ],
                        "source_url": source_url,
                        "search_snippet": candidate["description"],
                        "text": text[:MAX_SOURCE_TEXT_CHARS],
                        "text_truncated": bool(page.get("truncated"))
                        or len(text) > MAX_SOURCE_TEXT_CHARS,
                        "document_type": str(page.get("document_type", "webpage")),
                        "pages": page.get("pages"),
                    }
                )
            except ToolError as exc:
                failures.append({"source_url": url, "error": str(exc)[:MAX_FAILURE_CHARS]})

        evidence_map = _build_evidence_map(str(search_payload.get("query", query)), sources)
        disagreements = _find_numeric_disagreements(evidence_map)
        report_assessment = _build_report_assessment(
            sources=sources,
            evidence=evidence_map,
            failures=failures,
            disagreements=disagreements,
            searched_results=len(raw_results),
            selected_results=len(candidates),
            requested_sources=max_sources,
        )

        return json.dumps(
            {
                "query": str(search_payload.get("query", query)),
                "provider": str(search_payload.get("provider", "unknown")),
                "content_trust": "untrusted_external",
                "sources": sources,
                "evidence_map": evidence_map,
                "potential_disagreements": disagreements,
                "report_assessment": report_assessment,
                "analysis_notice": (
                    "Passage matching is lexical. Numeric disagreement hints require review "
                    "and do not by themselves prove a contradiction."
                ),
                "failures": failures,
                "searched_results": len(raw_results),
                "selected_results": len(candidates),
            }
        )


def _parse_object(value: str, error: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ToolError(error) from exc
    if not isinstance(payload, Mapping):
        raise ToolError(error)
    return payload


def _select_candidates(raw_results: list[Any], limit: int) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    host_counts: Counter[str] = Counter()
    for raw in raw_results[:MAX_CANDIDATES]:
        if not isinstance(raw, Mapping):
            continue
        canonical = _canonical_https_url(str(raw.get("url", "")))
        if canonical is None or canonical in seen_urls:
            continue
        host = urlsplit(canonical).hostname or ""
        if host_counts[host] >= MAX_SOURCES_PER_HOST:
            continue
        seen_urls.add(canonical)
        host_counts[host] += 1
        selected.append(
            {
                "url": canonical,
                "title": str(raw.get("title", ""))[:MAX_METADATA_CHARS],
                "description": str(raw.get("description", ""))[:MAX_METADATA_CHARS],
            }
        )
        if len(selected) >= limit:
            break
    return selected


def _canonical_https_url(value: str) -> str | None:
    parsed = urlsplit(value)
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        return None
    return urlunsplit(("https", parsed.netloc.casefold(), parsed.path or "/", parsed.query, ""))


def _build_evidence_map(query: str, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    query_terms = _terms(query)
    query_set = set(query_terms)
    query_pairs = set(zip(query_terms, query_terms[1:], strict=False))
    evidence: list[dict[str, Any]] = []
    for source in sources:
        ranked: list[tuple[float, int, str, list[str]]] = []
        sentences = SENTENCE_BOUNDARY.split(str(source["text"]))
        for ordinal, raw_sentence in enumerate(sentences):
            sentence = " ".join(raw_sentence.split())
            if not sentence:
                continue
            sentence_terms = _terms(sentence)
            overlap = sorted(query_set & set(sentence_terms))
            if not overlap:
                continue
            sentence_pairs = set(zip(sentence_terms, sentence_terms[1:], strict=False))
            score = len(overlap) / max(1, len(query_set))
            score += len(query_pairs & sentence_pairs) / max(1, len(query_pairs))
            ranked.append((score, -ordinal, sentence[:MAX_PASSAGE_CHARS], overlap))
        ranked.sort(reverse=True)
        for passage_index, (_, _, passage, overlap) in enumerate(
            ranked[:MAX_PASSAGES_PER_SOURCE], start=1
        ):
            evidence.append(
                {
                    "evidence_id": f"{source['source_id']}-E{passage_index}",
                    "source_id": source["source_id"],
                    "source_url": source["source_url"],
                    "passage": passage,
                    "matched_query_terms": overlap,
                    "numbers": NUMBER_PATTERN.findall(passage),
                }
            )
    return evidence


def _find_numeric_disagreements(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    for index, left in enumerate(evidence):
        left_numbers = set(left["numbers"])
        if not left_numbers:
            continue
        for right in evidence[index + 1 :]:
            if left["source_id"] == right["source_id"]:
                continue
            right_numbers = set(right["numbers"])
            shared_terms = sorted(
                set(left["matched_query_terms"]) & set(right["matched_query_terms"])
            )
            differing_left = left_numbers - right_numbers
            differing_right = right_numbers - left_numbers
            if not right_numbers or not shared_terms or not differing_left or not differing_right:
                continue
            hints.append(
                {
                    "type": "potential_numeric_disagreement",
                    "evidence_ids": [left["evidence_id"], right["evidence_id"]],
                    "shared_query_terms": shared_terms,
                    "values": [sorted(differing_left), sorted(differing_right)],
                }
            )
            if len(hints) >= MAX_DISAGREEMENT_HINTS:
                return hints
    return hints


def _terms(value: str) -> tuple[str, ...]:
    return tuple(
        term for term in WORD_PATTERN.findall(value.casefold()) if term not in STOP_WORDS
    )


def _build_report_assessment(
    *,
    sources: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    failures: list[dict[str, str]],
    disagreements: list[dict[str, Any]],
    searched_results: int,
    selected_results: int,
    requested_sources: int,
) -> dict[str, Any]:
    independent_hosts = {
        urlsplit(str(source["source_url"])).hostname for source in sources
    } - {None}
    evidence_source_ids = {str(item["source_id"]) for item in evidence}
    sources_without_evidence = [
        str(source["source_id"])
        for source in sources
        if str(source["source_id"]) not in evidence_source_ids
    ]
    if not evidence:
        coverage = "none"
    elif len(independent_hosts) >= 2 and len(evidence_source_ids) >= 2:
        coverage = "multi_source"
    else:
        coverage = "limited"

    uncertainty_flags = []
    if failures:
        uncertainty_flags.append("source_fetch_failures")
    if disagreements:
        uncertainty_flags.append("potential_numeric_disagreement")
    if any(bool(source["text_truncated"]) for source in sources):
        uncertainty_flags.append("truncated_source_text")
    if sources and len(independent_hosts) < 2:
        uncertainty_flags.append("single_independent_host")
    if sources_without_evidence:
        uncertainty_flags.append("sources_without_query_relevant_passages")
    if not evidence:
        uncertainty_flags.append("no_query_relevant_passages")

    return {
        "coverage": coverage,
        "successful_sources": len(sources),
        "independent_hosts": len(independent_hosts),
        "evidence_passages": len(evidence),
        "supported_evidence_ids": [str(item["evidence_id"]) for item in evidence],
        "uncertainty_flags": uncertainty_flags,
        "source_gaps": {
            "requested_sources": requested_sources,
            "searched_results": searched_results,
            "selected_results": selected_results,
            "failed_urls": [failure["source_url"] for failure in failures],
            "sources_without_evidence": sources_without_evidence,
        },
        "inference_boundary": (
            "Only statements directly grounded in listed evidence IDs are source-supported. "
            "Any synthesis beyond those passages must be labelled as inference."
        ),
    }
