"""Agent 2 - Movie Clip Analysis & SEO Agent.

Analyzes a movie clip (from its filename + optional metadata) and generates,
in American English:

  * Movie title / information
  * Relevant movie context
  * Search keywords
  * YouTube SEO title
  * YouTube description
  * Facebook caption
  * Relevant hashtags
  * Metadata

The generator is rule-based and deterministic so the pipeline works fully
offline. If a user-supplied LLM API key is present (USER_LLM_API_KEY), the SEO
is additionally enriched via an OpenAI-compatible chat-completions endpoint.
"""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from typing import Any

from ..common.config import BASE_DIR, CONFIG_DIR, getenv, load_settings
from ..common.logger import get_logger

logger = get_logger("agent2_seo")

# Words/phrases that get stripped from a filename when deriving the movie title.
_NOISE_TOKENS = re.compile(
    r"\b(clip|clips|scene|scenes|part|pt|episode|ep|s\d{1,2}e\d{1,2}"
    r"|season|trailer|official|hd|4k|1080p|720p|2160p|hdr|youtube|facebook"
    r"|shorts|reel|reels|webrip|bluray|h264|x264|copy|v\d+|remastered"
    r"|best|moment|moments|movie)\b",
    re.IGNORECASE,
)
_EXT_RE = re.compile(r"\.(mp4|mov|m4v|mkv|webm|avi|mpg|mpeg)$", re.IGNORECASE)


class SeoAgent:
    def __init__(self) -> None:
        self.settings = load_settings()
        self._profiles = self._load_profiles()

    @staticmethod
    def _load_profiles() -> dict[str, Any]:
        path = CONFIG_DIR / "seo_profiles.json"
        if path.exists():
            with path.open(encoding="utf-8") as fh:
                return json.load(fh)
        logger.warning("seo_profiles.json not found; using empty profiles.")
        return {}

    # -- analysis -----------------------------------------------------------
    def analyze(self, clip: dict[str, Any]) -> dict[str, Any]:
        """Build SEO metadata for a clip.

        clip expects keys: drive_file_name (or file name) and optional
        movie_hint. Returns a full metadata dict.
        """
        file_name = clip.get("drive_file_name") or clip.get("name", "")
        movie_title = clip.get("movie_hint") or self._extract_movie_title(file_name)

        base = self._base_keywords(movie_title)

        rule_seo = {
            "movie_title": movie_title,
            "source_file": file_name,
            "search_keywords": base["keywords"],
            "tags": base["tags"],
            "hashtags": base["hashtags"],
            "youtube_title": self._youtube_title(movie_title),
            "youtube_description": self._youtube_description(movie_title),
            "facebook_caption": self._facebook_caption(movie_title),
            "category_id": self.settings.get("youtube", {}).get("category_id", "24"),
            "locale": self.settings.get("seo", {}).get("locale", "en-US"),
        }

        llm_seo = self._llm_enrich(movie_title, file_name)
        if llm_seo:
            rule_seo["llm_enriched"] = True
            # LLM fields win for titles/descriptions; keywords/hashtags are merged.
            rule_seo["youtube_title"] = llm_seo.get("youtube_title") or rule_seo["youtube_title"]
            rule_seo["youtube_description"] = (
                llm_seo.get("youtube_description") or rule_seo["youtube_description"]
            )
            rule_seo["facebook_caption"] = llm_seo.get("facebook_caption") or rule_seo["facebook_caption"]
            rule_seo["search_keywords"] = self._merge_unique(
                rule_seo["search_keywords"], llm_seo.get("search_keywords", [])
            )
            rule_seo["hashtags"] = self._merge_unique(
                rule_seo["hashtags"], llm_seo.get("hashtags", [])
            )
        else:
            rule_seo["llm_enriched"] = False

        return rule_seo

    # -- movie title extraction ---------------------------------------------
    def _extract_movie_title(self, file_name: str) -> str:
        stem = _EXT_RE.sub("", file_name)
        stem = stem.replace("_", " ").replace("-", " ").replace(".", " ")
        cleaned = _NOISE_TOKENS.sub(" ", stem)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        if cleaned:
            # Title-case the cleaned name for a natural movie title.
            return " ".join(w.capitalize() for w in cleaned.split())
        # Fall back to a cleaned version of the raw filename.
        return " ".join(w.capitalize() for w in stem.split())

    # -- keyword builders ---------------------------------------------------
    def _base_keywords(self, movie_title: str) -> dict[str, list[str]]:
        profiles = self._profiles
        keywords: list[str] = []
        for group in (
            "hollywood_genres", "universes", "genres", "video_formats", "audience_intent",
        ):
            keywords.extend(profiles.get(group, []))

        keywords.extend([
            f"{movie_title} movie", f"{movie_title} movie clip",
            f"{movie_title} best scene", f"{movie_title} movie scene",
            f"{movie_title} movie moments", f"{movie_title} full movie scene",
        ])

        title_words = movie_title.split()
        if len(title_words) >= 2:
            keywords.append(" ".join(title_words))

        keywords = self._dedupe(keywords)

        # A curated tag set for YouTube (<=500 chars).
        tags = self._dedupe(
            [movie_title, f"{movie_title} movie", movie_title + " scene",
             movie_title + " clip", movie_title + " best scene"]
            + profiles.get("hollywood_genres", [])[:6]
        )

        # Hashtags (mix of base + movie-derived, alphanumeric only).
        movie_tag = re.sub(r"[^a-zA-Z0-9]", "", movie_title)
        hashtags = list(profiles.get("hashtag_base", []))
        if movie_tag:
            hashtags.append(movie_tag)
            hashtags.append(f"{movie_tag}Movie")
        hashtags = self._dedupe([f"#{h}" for h in hashtags])

        return {"keywords": keywords, "tags": tags, "hashtags": hashtags}

    # -- content builders ---------------------------------------------------
    def _youtube_title(self, movie_title: str) -> str:
        return f"{movie_title} - Best Scene | Hollywood Movie Moment #Shorts"

    def _youtube_description(self, movie_title: str) -> str:
        lines = [
            f"{movie_title} - one of the most memorable scenes from Hollywood.",
            "",
            "This short highlights an iconic moment from the movie. Watch the "
            "full picture for the complete story.",
            "",
            "Follow for daily Hollywood movie clips, epic scenes, and the best "
            "moments in cinema history.",
            "",
            "Tags:",
            "#Shorts #Movie #Hollywood #MovieScene #MovieMoment #Film #Cinema",
        ]
        return "\n".join(lines)

    def _facebook_caption(self, movie_title: str) -> str:
        return (
            f"{movie_title} - Best Scene | Hollywood Movie Moment\n\n"
            "An unforgettable moment from this iconic film. Comment below with "
            "your favorite movie scene of all time!\n\n"
            "#Shorts #Movie #Hollywood #MovieScene #MovieMoment"
        )

    # -- optional LLM enrichment ---------------------------------------------
    def _llm_enrich(self, movie_title: str, source_file: str) -> dict[str, Any] | None:
        api_key = getenv(self.settings.get("seo", {}).get("llm", {}).get("api_key_env", "USER_LLM_API_KEY"))
        if not api_key:
            return None
        base_url = getenv(
            self.settings.get("seo", {}).get("llm", {}).get("base_url_env", "USER_LLM_BASE_URL"),
            "https://api.deepseek.com/v1",
        )
        model = getenv(
            self.settings.get("seo", {}).get("llm", {}).get("model_env", "USER_LLM_MODEL"),
            self.settings.get("seo", {}).get("llm", {}).get("model_default", "deepseek-chat"),
        )
        prompt = (
            "You write SEO metadata for a YouTube Short / Facebook Reel that "
            "shows a scene from a Hollywood movie. The target audience is in the "
            "United States, so write in natural American English, not translated "
            "English. Return JSON only with these keys: youtube_title, "
            "youtube_description, facebook_caption, search_keywords (list), "
            "hashtags (list of 10-15).\n"
            f"Movie title hint: {movie_title}\n"
            f"Source file name: {source_file}\n"
            "Keep titles under 100 characters and mention the movie name. "
            "Add #Shorts for YouTube."
        )
        try:
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are an expert social media SEO writer."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.7,
                "max_tokens": 900,
            }
            url = base_url.rstrip("/") + "/chat/completions"
            request = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            content = body["choices"][0]["message"]["content"]
            content = content.strip()
            if content.startswith("```"):
                content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
            parsed = json.loads(content)
            parsed["search_keywords"] = [str(k) for k in parsed.get("search_keywords", [])]
            parsed["hashtags"] = [str(h) for h in parsed.get("hashtags", [])]
            logger.info("LLM SEO enrichment completed for %s", movie_title)
            return parsed
        except Exception as exc:  # noqa: BLE001 - fall back to rule-based
            logger.warning("LLM enrichment failed (%s); using rule-based SEO.", exc)
            return None

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _dedupe(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            key = item.lower()
            if key not in seen:
                seen.add(key)
                out.append(item)
        return out

    def _merge_unique(self, primary: list[str], secondary: list[str]) -> list[str]:
        return self._dedupe(list(primary) + list(secondary))


def analyze_clip(clip: dict[str, Any]) -> dict[str, Any]:
    """Convenience entry point used by the orchestrator."""
    return SeoAgent().analyze(clip)
