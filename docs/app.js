"use strict";

const MARKET = "株価・市場";
const PRELOAD_PX = 800;
const state = { manifest: null, loaded: new Map(), tab: "ALL", q: "", market: false };
const $ = (id) => document.getElementById(id);

const TZ = "Asia/Tokyo";
const dayKeyFmt = new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" });
const dayFmt = new Intl.DateTimeFormat("ja-JP", { timeZone: TZ, month: "long", day: "numeric", weekday: "short" });
const dayYearFmt = new Intl.DateTimeFormat("ja-JP", { timeZone: TZ, year: "numeric", month: "long", day: "numeric", weekday: "short" });
const timeFmt = new Intl.DateTimeFormat("ja-JP", { timeZone: TZ, hour: "2-digit", minute: "2-digit" });
const updatedFmt = new Intl.DateTimeFormat("ja-JP", { timeZone: TZ, month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function safeUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

function companyOf(ticker) {
  return state.manifest.companies.find((c) => c.ticker === ticker);
}

function stamp(article) {
  return article.published || article.fetched;
}

function relevantMonths() {
  return state.manifest.months
    .filter((m) => state.tab === "ALL" || (m.counts[state.tab] || 0) > 0)
    .map((m) => m.month);
}

function nextUnloaded() {
  return relevantMonths().find((m) => !state.loaded.has(m));
}

async function loadMonth(month) {
  try {
    const res = await fetch(`data/${month}.json`, { cache: "no-cache" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state.loaded.set(month, await res.json());
  } catch (err) {
    console.error(`data/${month}.json`, err);
    state.loaded.set(month, []);
  }
}

function matches(article) {
  if (state.tab !== "ALL" && !article.companies.includes(state.tab)) return false;
  if (!state.market && article.category === MARKET) return false;
  if (!state.q) return true;
  const haystack = `${article.title_ja} ${article.title} ${article.summary} ${article.source}`.toLowerCase();
  return state.q.split(/\s+/).every((word) => haystack.includes(word));
}

function card(article, date) {
  const li = el("li", "card");
  const meta = el("div", "meta");
  meta.append(el("time", "time", article.published ? timeFmt.format(date) : "—"));
  for (const ticker of article.companies) {
    const company = companyOf(ticker);
    const badge = el("span", "badge", company ? company.name : ticker);
    if (company) badge.style.setProperty("--c", company.color);
    meta.append(badge);
  }
  if (article.category) meta.append(el("span", "cat", article.category));
  meta.append(el("span", "src", article.source));
  li.append(meta);

  const heading = el("h3", "title");
  const label = article.title_ja || article.title;
  const url = safeUrl(article.url);
  if (url) {
    const link = el("a", null, label);
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    heading.append(link);
  } else {
    heading.textContent = label;
  }
  li.append(heading);
  if (article.summary) li.append(el("p", "summary", article.summary));
  if (article.title_ja && article.title_ja !== article.title) li.append(el("p", "orig", article.title));
  return li;
}

function render() {
  const timeline = $("timeline");
  timeline.replaceChildren();
  const items = [];
  for (const month of relevantMonths()) {
    const articles = state.loaded.get(month);
    if (!articles) break;
    for (const article of articles) if (matches(article)) items.push(article);
  }
  items.sort((a, b) => stamp(b).localeCompare(stamp(a)));

  const thisYear = dayKeyFmt.format(new Date()).slice(0, 4);
  let currentDay = null;
  let list = null;
  for (const article of items) {
    const date = new Date(stamp(article));
    const dayKey = dayKeyFmt.format(date);
    if (dayKey !== currentDay) {
      currentDay = dayKey;
      const section = el("section", "day");
      section.append(el("h2", "day-label", (dayKey.startsWith(thisYear) ? dayFmt : dayYearFmt).format(date)));
      list = el("ol", "cards");
      section.append(list);
      timeline.append(section);
    }
    list.append(card(article, date));
  }

  const more = Boolean(nextUnloaded());
  let status = "";
  if (!items.length) status = more ? "読み込み中…" : "該当する記事はありません";
  else if (!more) status = "これより前の記事はありません";
  $("status").textContent = status;
}

function sentinelNear() {
  return $("sentinel").getBoundingClientRect().top < window.innerHeight + PRELOAD_PX;
}

let filling = false;
async function fill() {
  if (filling) return;
  filling = true;
  try {
    for (;;) {
      const month = nextUnloaded();
      if (!month) break;
      // 検索中は全期間を対象にするため、画面の位置に関係なく最後まで読み込む
      if (!state.q && !sentinelNear()) break;
      await loadMonth(month);
      render();
    }
  } finally {
    filling = false;
  }
  render();
}

function renderTabs() {
  const nav = $("tabs");
  nav.replaceChildren();
  const tabs = [{ ticker: "ALL", name: "すべて" }, ...state.manifest.companies];
  for (const tab of tabs) {
    const link = el("a", "tab", tab.name);
    link.href = `#${tab.ticker}`;
    if (tab.color) link.style.setProperty("--c", tab.color);
    if (tab.ticker === state.tab) link.setAttribute("aria-current", "page");
    nav.append(link);
  }
}

function readHash() {
  const wanted = decodeURIComponent(location.hash.slice(1)).toUpperCase();
  state.tab = state.manifest.companies.some((c) => c.ticker === wanted) ? wanted : "ALL";
}

async function init() {
  try {
    const res = await fetch("data/manifest.json", { cache: "no-cache" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state.manifest = await res.json();
  } catch (err) {
    console.error(err);
    $("status").textContent = "データを読み込めませんでした";
    return;
  }
  $("updated").textContent = `最終更新 ${updatedFmt.format(new Date(state.manifest.updated))}`;
  readHash();
  renderTabs();
  render();
  fill();

  window.addEventListener("hashchange", () => {
    readHash();
    renderTabs();
    render();
    fill();
  });
  let timer;
  $("q").addEventListener("input", (event) => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      state.q = event.target.value.trim().toLowerCase();
      render();
      fill();
    }, 250);
  });
  $("market").addEventListener("change", (event) => {
    state.market = event.target.checked;
    render();
    fill();
  });
  new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting)) fill();
  }, { rootMargin: `${PRELOAD_PX}px` }).observe($("sentinel"));
}

init();
