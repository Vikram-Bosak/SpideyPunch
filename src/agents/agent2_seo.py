"""Agent 2 - Master SEO Agent & Platform SEO Adapters.

Google Drive Video -> Video Analyzer -> SEO Agent -> SEO Validation Agent -> YouTube SEO / Facebook SEO.
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

_NOISE_TOKENS = re.compile(
    r"\b(clip|clips|scene|scenes|part|pt|episode|ep|s\d{1,2}e\d{1,2}"
    r"|season|trailer|official|hd|4k|1080p|720p|2160p|hdr|youtube|facebook"
    r"|shorts|reel|reels|webrip|bluray|h264|x264|copy|v\d+|remastered"
    r"|best|moment|moments|movie)\b",
    re.IGNORECASE,
)
_EXT_RE = re.compile(r"\.(mp4|mov|m4v|mkv|webm|avi|mpg|mpeg)$", re.IGNORECASE)


class YouTubeSeoAgent:
    """Refines Master SEO Package for YouTube Upload SEO Factors."""

    def optimize(self, master_package: dict[str, Any], movie_title: str) -> dict[str, Any]:
        primary_keyword = master_package["primary_keyword"]
        
        # 1. Short, unique, engaging title
        title_base = f"{movie_title} Action Scene Reaction"
        if len(title_base) > 60:
            title_base = f"{movie_title} Reaction"

        # 2. Relevant Emoji
        emoji = "🔥"
        if "spiderman" in movie_title.lower() or "spider-man" in movie_title.lower():
            emoji = "🕸️"
        elif "batman" in movie_title.lower():
            emoji = "🦇"
        elif "wick" in movie_title.lower() or "fight" in movie_title.lower() or "action" in movie_title.lower():
            emoji = "⚔️"

        # 3. 3 Relevant Trending Hashtags
        hashtags = ["#Shorts", "#MovieReaction", "#Hollywood"]

        # Combined YouTube title formatting
        final_title = f"{title_base} {emoji}"
        if len(final_title) > 100:
            final_title = f"{movie_title} Reaction {emoji}"

        # Description must be completely empty
        description = ""

        return {
            "title": final_title,
            "description": description,
            "primary_keyword": primary_keyword,
            "secondary_keywords": master_package["secondary_keywords"],
            "tags": [],
            "hashtags": hashtags,
        }


class FacebookSeoAgent:
    """Refines Master SEO Package for Facebook Discovery & Engagement Factors."""

    def optimize(self, master_package: dict[str, Any], movie_title: str) -> dict[str, Any]:
        primary_keyword = master_package["primary_keyword"]
        
        # Engaging, short, and searchable Facebook caption/header
        title = f"Insane reaction to this moment in {movie_title}! 😱"
        
        # Short but context-rich description + Call to Action (CTA)
        description = (
            f"This scene from {movie_title} never gets old! "
            f"Is this the ultimate action moment? Share your favorite scene in the comments! 👇"
        )
        
        # Limited relevant hashtags
        hashtags = ["#Reels", "#MovieReaction", f"#{re.sub(r'[^a-zA-Z0-9]', '', movie_title)}"]

        return {
            "title": title,
            "description": description,
            "primary_keyword": primary_keyword,
            "hashtags": hashtags,
        }


class SeoValidationAgent:
    """Verifies that the generated metadata is accurate, compliant, and optimized."""

    def validate(self, master_package: dict[str, Any], movie_title: str) -> tuple[int, list[str]]:
        errors = []
        score = 100

        primary_kw = master_package.get("primary_keyword", "")
        secondary_kws = master_package.get("secondary_keywords", [])
        tags = master_package.get("tags", [])
        hashtags = master_package.get("hashtags", [])

        # 1. Primary Keyword checks
        if not primary_kw:
            errors.append("No primary keyword selected.")
            score -= 20
        elif movie_title.lower() not in primary_kw.lower():
            errors.append("Primary keyword does not naturally include movie name.")
            score -= 15

        # 2. Keyword stuffing check
        # If there are too many unrelated actors or universes listed, flag it
        unrelated_tags_count = 0
        for tag in tags:
            # Simple heuristic: tags should relate to movie title elements
            words = movie_title.lower().split()
            if not any(w in tag.lower() for w in words) and tag.lower() not in ["action clip", "movie reaction", "shorts", "reels"]:
                unrelated_tags_count += 1
        if unrelated_tags_count > 6:
            errors.append("Potential keyword stuffing detected with unrelated tags.")
            score -= 15

        # 3. Unnecessary or excessive hashtags
        if len(hashtags) > 8:
            errors.append("Too many hashtags (excessive hashtag usage).")
            score -= 10

        return max(score, 0), errors

    def refine(self, master_package: dict[str, Any], movie_title: str, errors: list[str]) -> dict[str, Any]:
        """Automatically improves Master SEO Package parameters to correct issues."""
        logger.info("Validation errors found: %s. Improving metadata automatically...", errors)
        for err in errors:
            if "movie name" in err:
                master_package["primary_keyword"] = f"{movie_title} reaction"
            if "keyword stuffing" in err:
                # Keep only tags that contain title words or core actions
                words = movie_title.lower().split()
                master_package["tags"] = [
                    t for t in master_package["tags"]
                    if any(w in t.lower() for w in words) or t.lower() in ["action clip", "movie reaction"]
                ]
            if "Too many hashtags" in err:
                master_package["hashtags"] = master_package["hashtags"][:4]
        return master_package


class SeoAgent:
    def __init__(self) -> None:
        self.settings = load_settings()
        self._profiles = self._load_profiles()
        self.validator = SeoValidationAgent()
        self.youtube_seo = YouTubeSeoAgent()
        self.facebook_seo = FacebookSeoAgent()

    @staticmethod
    def _load_profiles() -> dict[str, Any]:
        path = CONFIG_DIR / "seo_profiles.json"
        if path.exists():
            with path.open(encoding="utf-8") as fh:
                return json.load(fh)
        logger.warning("seo_profiles.json not found; using empty profiles.")
        return {}

    def analyze(self, clip: dict[str, Any], used_keywords: list[str] | None = None) -> dict[str, Any]:
        """Runs the Master SEO Flow: Research -> Master Package -> Validate -> Platform Refinements."""
        file_name = clip.get("drive_file_name") or clip.get("name", "")
        movie_title = clip.get("movie_hint") or self._extract_movie_title(file_name)
        used_kws_set = set(k.lower() for k in (used_keywords or []))

        # 1. Master SEO Generation
        primary_keyword = f"{movie_title} reaction"
        if primary_keyword.lower() in used_kws_set:
            primary_keyword = f"{movie_title} action scene reaction"

        secondary_keywords = [
            f"{movie_title} action scene",
            f"movie reaction {movie_title}",
            f"Hollywood action reaction"
        ]
        # Filter duplicates
        secondary_keywords = [k for k in secondary_keywords if k.lower() not in used_kws_set]
        if not secondary_keywords:
            secondary_keywords = [f"{movie_title} action clip"]

        tags = [movie_title, "movie reaction", "action clip"]
        hashtags = ["#Shorts", "#MovieReaction", f"#{re.sub(r'[^a-zA-Z0-9]', '', movie_title)}"]

        master_package = {
            "primary_keyword": primary_keyword,
            "secondary_keywords": secondary_keywords,
            "tags": tags,
            "hashtags": hashtags,
        }

        # Optional LLM override for master package
        llm_master = self._llm_enrich(movie_title, file_name, used_kws_set)
        if llm_master:
            master_package.update(llm_master)

        # 2. Validation
        score, errors = self.validator.validate(master_package, movie_title)
        if score < 85:
            master_package = self.validator.refine(master_package, movie_title, errors)
            score, errors = self.validator.validate(master_package, movie_title)

        # 3. Platform SEO Generation
        yt_meta = self.youtube_seo.optimize(master_package, movie_title)
        fb_meta = self.facebook_seo.optimize(master_package, movie_title)

        return {
            "movie_title": movie_title,
            "source_file": file_name,
            "primary_keyword": master_package["primary_keyword"],
            "secondary_keywords": master_package["secondary_keywords"],
            "seo_score": score,
            "validation_errors": errors,
            "youtube": yt_meta,
            "facebook": fb_meta,
            # Backwards compatibility
            "youtube_title": yt_meta["title"],
            "youtube_description": yt_meta["description"],
            "facebook_caption": fb_meta["description"],
            "tags": yt_meta["tags"],
            "hashtags": yt_meta["hashtags"],
            "category_id": self.settings.get("youtube", {}).get("category_id", "24"),
            "locale": self.settings.get("seo", {}).get("locale", "en-US"),
        }

    def _extract_movie_title(self, file_name: str) -> str:
        stem = _EXT_RE.sub("", file_name)
        stem = stem.replace("_", " ").replace("-", " ").replace(".", " ")
        cleaned = _NOISE_TOKENS.sub(" ", stem)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        if cleaned:
            return " ".join(w.capitalize() for w in cleaned.split())
        return " ".join(w.capitalize() for w in stem.split())

    def _llm_enrich(self, movie_title: str, source_file: str, used_keywords: set[str]) -> dict[str, Any] | None:
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
            "You write highly relevant, non-spammy Master SEO metadata for a movie reaction clip. "
            "Target US audience (American English). DO NOT use keyword stuffing or false claims. "
            "Return JSON only:\n"
            "{\n"
            "  \"primary_keyword\": \"Targeted search term (includes movie name)\",\n"
            "  \"secondary_keywords\": [\"list of 3 unique long-tail terms\"],\n"
            "  \"tags\": [\"list of 4-6 highly relevant movie tags\"],\n"
            "  \"hashtags\": [\"list of 3 hashtags\"]\n"
            "}\n"
            f"Movie: {movie_title}\n"
            f"Filename: {source_file}\n"
            f"Avoid: {list(used_keywords)}\n"
        )
        try:
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "max_tokens": 800,
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
            content = body["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
            return json.loads(content)
        except Exception as exc:
            logger.warning("LLM Master enrichment failed (%s); using rule-based.", exc)
            return None


def analyze_clip(clip: dict[str, Any], used_keywords: list[str] | None = None) -> dict[str, Any]:
    return SeoAgent().analyze(clip, used_keywords)
