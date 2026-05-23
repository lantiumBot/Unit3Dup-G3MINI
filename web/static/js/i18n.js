/* ── i18n — internationalisation engine ─────────────────────────────────── */
"use strict";

(function () {
  const _LANG_KEY   = "u3d-lang";
  const _CACHE_PFX  = "u3d-locale-";
  const _FLAGS      = { fr: "🇫🇷", en: "🇬🇧", es: "🇪🇸", it: "🇮🇹", de: "🇩🇪" };

  let _lang = localStorage.getItem(_LANG_KEY) || "fr";
  let _loc  = {};

  // Restore from localStorage cache immediately (synchronous — t() ready before DOMContentLoaded)
  try {
    const raw = localStorage.getItem(_CACHE_PFX + _lang);
    if (raw) _loc = JSON.parse(raw);
  } catch (e) {}

  // ── Public API ───────────────────────────────────────────────────────────
  window.t = function (key) {
    let s = Object.prototype.hasOwnProperty.call(_loc, key) ? _loc[key] : key;
    for (let i = 1; i < arguments.length; i++)
      s = s.replace("{" + (i - 1) + "}", arguments[i]);
    return s;
  };

  window.i18n = {
    lang:       () => _lang,
    dateLocale: () => _loc["_date_locale"] || "fr-FR",
    setLang,
    applyLocale,
  };

  // ── DOM application ──────────────────────────────────────────────────────
  function applyLocale() {
    document.querySelectorAll("[data-i18n]").forEach(el => {
      el.textContent = t(el.dataset.i18n);
    });
    document.querySelectorAll("[data-i18n-html]").forEach(el => {
      el.innerHTML = t(el.dataset.i18nHtml);
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach(el => {
      el.placeholder = t(el.dataset.i18nPlaceholder);
    });
    document.querySelectorAll("[data-i18n-title]").forEach(el => {
      el.title = t(el.dataset.i18nTitle);
    });
    // Update flag button
    const flag = document.getElementById("lang-flag");
    if (flag) flag.textContent = _FLAGS[_lang] || "🌐";
    // Update <html lang>
    const root = document.getElementById("html-root");
    if (root) root.setAttribute("lang", _lang);
    // Notify page scripts
    document.dispatchEvent(new CustomEvent("locale-changed", { detail: { lang: _lang } }));
  }

  // ── Language switch ──────────────────────────────────────────────────────
  async function setLang(lang) {
    try {
      const r = await fetch("/static/locales/" + lang + ".json");
      if (!r.ok) throw new Error("HTTP " + r.status);
      const data = await r.json();
      _loc  = data;
      _lang = lang;
      localStorage.setItem(_LANG_KEY, lang);
      localStorage.setItem(_CACHE_PFX + lang, JSON.stringify(data));
      applyLocale();
    } catch (e) {
      console.error("[i18n] Failed to load locale:", lang, e);
    }
  }

  // ── Init on DOM ready ────────────────────────────────────────────────────
  document.addEventListener("DOMContentLoaded", () => setLang(_lang));
})();
