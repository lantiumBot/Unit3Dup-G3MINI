"""Tests for i18n locale consistency.

Ensures that:
- All locale files are valid JSON
- Every key present in any locale exists in ALL locales (no missing translations)
- No duplicate keys within a single locale file
- Keys referenced via data-i18n attributes in HTML templates exist in all locales
"""
import json
import re
import sys
import os
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

LOCALES_DIR = Path(__file__).parent.parent / "static" / "locales"
TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
STATIC_JS_DIR = Path(__file__).parent.parent / "static" / "js"

LOCALE_FILES = sorted(LOCALES_DIR.glob("*.json"))
LOCALE_NAMES = [f.stem for f in LOCALE_FILES]


def _load_locale(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _all_locales() -> dict[str, dict]:
    return {f.stem: _load_locale(f) for f in LOCALE_FILES}


# ── Basic validity ────────────────────────────────────────────────────────────

class TestLocaleValidity:
    def test_locale_files_exist(self):
        assert len(LOCALE_FILES) >= 2, "At least 2 locale files expected"

    @pytest.mark.parametrize("locale_file", LOCALE_FILES, ids=lambda f: f.stem)
    def test_valid_json(self, locale_file):
        data = json.loads(locale_file.read_text(encoding="utf-8"))
        assert isinstance(data, dict), f"{locale_file.name} must be a JSON object"

    @pytest.mark.parametrize("locale_file", LOCALE_FILES, ids=lambda f: f.stem)
    def test_no_duplicate_keys(self, locale_file):
        """Python's json.loads silently keeps last key on duplicates; detect via raw parse."""
        seen: set[str] = set()
        duplicates: list[str] = []
        text = locale_file.read_text(encoding="utf-8")
        for m in re.finditer(r'"([^"]+)"\s*:', text):
            key = m.group(1)
            if key in seen:
                duplicates.append(key)
            seen.add(key)
        assert not duplicates, (
            f"{locale_file.name} contains duplicate keys: {duplicates}"
        )

    @pytest.mark.parametrize("locale_file", LOCALE_FILES, ids=lambda f: f.stem)
    def test_all_values_are_strings(self, locale_file):
        data = _load_locale(locale_file)
        bad = {k: v for k, v in data.items() if not isinstance(v, str)}
        assert not bad, (
            f"{locale_file.name} has non-string values: {list(bad.keys())}"
        )


# ── Key consistency across locales ────────────────────────────────────────────

class TestLocaleKeyConsistency:
    def test_all_locales_have_same_keys(self):
        locales = _all_locales()
        all_keys: set[str] = set()
        for keys in locales.values():
            all_keys |= set(keys)

        missing: dict[str, list[str]] = {}
        for locale_name, data in locales.items():
            absent = sorted(all_keys - set(data))
            if absent:
                missing[locale_name] = absent

        assert not missing, (
            "Some locales are missing keys:\n"
            + "\n".join(
                f"  [{locale}] missing: {keys}"
                for locale, keys in sorted(missing.items())
            )
        )

    def test_reference_locale_fr_exists(self):
        assert (LOCALES_DIR / "fr.json").exists(), "fr.json must exist as reference locale"

    def test_en_locale_exists(self):
        assert (LOCALES_DIR / "en.json").exists(), "en.json must exist"

    def test_no_empty_values(self):
        """Warn about keys with empty string values (usually forgotten translations)."""
        locales = _all_locales()
        empties: dict[str, list[str]] = {}
        for locale_name, data in locales.items():
            blank = [k for k, v in data.items() if v.strip() == "" and not k.startswith("_")]
            if blank:
                empties[locale_name] = blank
        assert not empties, (
            "Some locales have empty values:\n"
            + "\n".join(
                f"  [{locale}]: {keys}"
                for locale, keys in sorted(empties.items())
            )
        )


# ── Keys referenced in HTML / JS exist in all locales ─────────────────────────

def _extract_html_i18n_keys() -> set[str]:
    """Extract static keys from data-i18n="..." attributes in HTML templates.

    Keys that contain '{' are Jinja2-computed at render time (e.g. ``{{ i18n_key }}``)
    and cannot be audited statically — they are skipped.
    """
    keys: set[str] = set()
    for tmpl in TEMPLATES_DIR.glob("*.html"):
        text = tmpl.read_text(encoding="utf-8")
        for m in re.finditer(r'data-i18n(?:-placeholder)?="([^"]+)"', text):
            key = m.group(1)
            if "{" not in key:  # skip Jinja2-dynamic keys
                keys.add(key)
    return keys


def _extract_js_t_calls() -> set[str]:
    """Extract keys from t("key") / t('key') calls in JS source files."""
    keys: set[str] = set()
    pattern = re.compile(r'\bt\(\s*["\']([a-z0-9_.]+)["\']\s*[,)]', re.IGNORECASE)
    for js_file in STATIC_JS_DIR.glob("*.js"):
        text = js_file.read_text(encoding="utf-8")
        for m in pattern.finditer(text):
            keys.add(m.group(1))
    return keys


class TestLocaleKeysUsage:
    def test_html_data_i18n_keys_exist_in_all_locales(self):
        html_keys = _extract_html_i18n_keys()
        if not html_keys:
            pytest.skip("No data-i18n keys found in templates")

        locales = _all_locales()
        missing: dict[str, list[str]] = {}
        for locale_name, data in locales.items():
            absent = sorted(html_keys - set(data))
            if absent:
                missing[locale_name] = absent

        assert not missing, (
            "HTML data-i18n keys missing from locales:\n"
            + "\n".join(
                f"  [{locale}] missing: {keys}"
                for locale, keys in sorted(missing.items())
            )
        )

    def test_js_t_call_keys_exist_in_all_locales(self):
        js_keys = _extract_js_t_calls()
        if not js_keys:
            pytest.skip("No t() calls found in JS files")

        locales = _all_locales()
        missing: dict[str, list[str]] = {}
        for locale_name, data in locales.items():
            absent = sorted(js_keys - set(data))
            if absent:
                missing[locale_name] = absent

        assert not missing, (
            "JS t() keys missing from locales:\n"
            + "\n".join(
                f"  [{locale}] missing: {keys}"
                for locale, keys in sorted(missing.items())
            )
        )

    def test_no_orphan_locale_keys(self):
        """Keys that exist in locales but are never used in HTML or JS (informational)."""
        html_keys = _extract_html_i18n_keys()
        js_keys   = _extract_js_t_calls()
        used_keys = html_keys | js_keys

        locales = _all_locales()
        # Use fr as reference — check orphans that appear in ALL locales
        ref_keys = set(next(iter(locales.values())))
        # Exclude meta keys like _date_locale
        orphans = sorted(
            k for k in ref_keys
            if k not in used_keys and not k.startswith("_")
        )
        # This is informational — we don't fail on orphans (some keys may be used
        # dynamically via string interpolation), but we report them.
        if orphans:
            import warnings
            warnings.warn(
                f"Potentially unused locale keys ({len(orphans)}): {orphans[:10]}"
                + ("…" if len(orphans) > 10 else ""),
                UserWarning,
                stacklevel=1,
            )
