"use strict";

const CATEGORY_ORDER = {
  rennrad: ["RTF", "Radmarathon", "Rennen", "Brevet", "Gravel", "Etappenfahrt"],
  laufen: ["5 km", "10 km", "Halbmarathon", "Marathon", "Ultra", "Trail", "Sonstige"],
};
const DACH = new Set(["DE", "AT", "CH"]);
const PAGE = 40;
const STORE = "sep-filters-v1";

const $ = (s) => document.querySelector(s);
const state = {
  events: [],
  sport: "rennrad",
  cats: { rennrad: new Set(), laufen: new Set() },
  from: "",
  to: "",
  region: "ALL",
  radiusOn: false,
  radius: 100,
  origin: null, // {lat, lon, label}
  q: "",
  shown: PAGE,
};

// ------------------------------------------------------------ helpers

const iso = (d) => d.toISOString().slice(0, 10);
const fmtDate = (s) =>
  s ? new Date(s + "T12:00:00").toLocaleDateString("de-DE", { weekday: "short", day: "2-digit", month: "2-digit", year: "numeric" }) : "";
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtNum = (n) => Number(n).toLocaleString("de-DE", { maximumFractionDigits: 1 });

function distanceKm(a, b) {
  const R = 6371, rad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * rad, dLon = (b.lon - a.lon) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

function save() {
  try {
    localStorage.setItem(STORE, JSON.stringify({
      sport: state.sport,
      cats: { rennrad: [...state.cats.rennrad], laufen: [...state.cats.laufen] },
      region: state.region, radiusOn: state.radiusOn, radius: state.radius, origin: state.origin,
    }));
  } catch (_) {}
}

function load() {
  try {
    const s = JSON.parse(localStorage.getItem(STORE) || "null");
    if (!s) return;
    state.sport = s.sport || state.sport;
    state.cats.rennrad = new Set(s.cats?.rennrad || []);
    state.cats.laufen = new Set(s.cats?.laufen || []);
    state.region = s.region || state.region;
    state.radiusOn = !!s.radiusOn;
    state.radius = s.radius || state.radius;
    state.origin = s.origin || null;
  } catch (_) {}
}

// ------------------------------------------------------------ .ics

function icsEscape(s) {
  return String(s ?? "").replace(/\\/g, "\\\\").replace(/;/g, "\\;").replace(/,/g, "\\,").replace(/\r?\n/g, "\\n");
}

function fold(line) {
  // RFC 5545: Zeilen nach 75 Zeichen umbrechen
  const out = [];
  while (line.length > 74) { out.push(line.slice(0, 74)); line = " " + line.slice(74); }
  out.push(line);
  return out.join("\r\n");
}

function nextDay(s) {
  const d = new Date(s + "T12:00:00Z");
  d.setUTCDate(d.getUTCDate() + 1);
  return iso(d);
}

function buildIcs(ev, kind) {
  const isReg = kind === "registration";
  const start = isReg ? ev.registration_start : ev.date;
  const end = isReg ? start : ev.end_date || ev.date;
  const title = isReg ? `Anmeldestart: ${ev.name}` : ev.name;
  const desc = [
    isReg ? `Anmeldestart für ${ev.name} (Event am ${fmtDate(ev.date)}).` : null,
    routesText(ev) ? `Strecken: ${routesText(ev)}` : null,
    ev.fee ? `Gebühr: ${ev.fee}` : null,
    ev.registration_start || ev.registration_end ? `Anmeldung: ${regText(ev)}` : null,
    ev.notes || null,
    ev.website || ev.source_url || null,
  ].filter(Boolean).join("\n");
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d+/, "");
  const lines = [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//Sporteventplaner//DE",
    "CALSCALE:GREGORIAN",
    "METHOD:PUBLISH",
    "BEGIN:VEVENT",
    `UID:${ev.id}-${kind}@sporteventplaner`,
    `DTSTAMP:${stamp}`,
    `DTSTART;VALUE=DATE:${start.replace(/-/g, "")}`,
    `DTEND;VALUE=DATE:${nextDay(end).replace(/-/g, "")}`,
    `SUMMARY:${icsEscape(title)}`,
    ev.place ? `LOCATION:${icsEscape([ev.address || ev.place, ev.country].filter(Boolean).join(", "))}` : null,
    `DESCRIPTION:${icsEscape(desc)}`,
    ev.website || ev.source_url ? `URL:${ev.website || ev.source_url}` : null,
    "TRANSP:TRANSPARENT",
    "BEGIN:VALARM",
    "ACTION:DISPLAY",
    `DESCRIPTION:${icsEscape(title)}`,
    // Ganztägig: Erinnerung um 9 Uhr am Tag selbst bzw. eine Woche vorher beim Event
    isReg ? "TRIGGER:PT9H" : "TRIGGER:-P6DT15H",
    "END:VALARM",
    "END:VEVENT",
    "END:VCALENDAR",
  ].filter(Boolean);
  return lines.map(fold).join("\r\n") + "\r\n";
}

function openIcs(ev, kind) {
  const ics = buildIcs(ev, kind);
  const name = (ev.name.replace(/[^\wäöüÄÖÜß-]+/g, "_").slice(0, 40) || "event") + (kind === "registration" ? "_anmeldung" : "") + ".ics";
  // iOS Safari zeigt bei text/calendar direkt den Dialog "Zum Kalender hinzufügen"
  const blob = new Blob([ics], { type: "text/calendar;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}

// ------------------------------------------------------------ rendering

function routesText(ev) {
  const r = ev.routes || [];
  if (!r.length) return "";
  if (ev.distance_range_only) return `${fmtNum(r[0].km)} bis ${fmtNum(r[r.length - 1].km)} km`;
  return r.map((x) => `${x.name ? x.name + " " : ""}${fmtNum(x.km)} km${x.elevation_m ? ` / ${fmtNum(x.elevation_m)} hm` : ""}`).join(" · ");
}

function regText(ev) {
  const s = ev.registration_start, e = ev.registration_end;
  if (s && e) return `${fmtDate(s)} bis ${fmtDate(e)}`;
  if (s) return `ab ${fmtDate(s)}`;
  if (e) return `bis ${fmtDate(e)}`;
  return "";
}

const NA = '<span class="na">k. A. – siehe Webseite</span>';

function card(ev) {
  const dist = state.origin && ev.lat != null ? ` · ${Math.round(distanceKm(state.origin, ev))} km entfernt` : "";
  const hasElev = (ev.routes || []).some((r) => r.elevation_m);
  const regFuture = ev.registration_start && ev.registration_start >= iso(new Date());
  const link = ev.website || ev.source_url;
  return `<li class="card">
    <div>
      <div class="when">${fmtDate(ev.date)}${ev.end_date && ev.end_date !== ev.date ? " – " + fmtDate(ev.end_date) : ""}</div>
      <h2>${esc(ev.name)}</h2>
      <div class="where">${esc(ev.place || "")}${ev.country && ev.country !== "DE" ? " (" + esc(ev.country) + ")" : ""}${dist}</div>
    </div>
    <div class="tags">${(ev.categories || []).map((c) => `<span class="tag">${esc(c)}</span>`).join("")}</div>
    <dl class="facts">
      <dt>Distanz</dt><dd>${routesText(ev) ? esc(routesText(ev)) : NA}</dd>
      ${ev.sport === "rennrad" && !hasElev ? `<dt>Höhenmeter</dt><dd>${NA}</dd>` : ""}
      <dt>Anmeldung</dt><dd>${regText(ev) ? esc(regText(ev)) : NA}</dd>
      <dt>Gebühr</dt><dd>${ev.fee ? esc(ev.fee) : NA}</dd>
      <dt>Webseite</dt><dd>${link ? `<a href="${esc(link)}" target="_blank" rel="noopener">${esc(link.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "").slice(0, 45))}</a>` : NA}</dd>
    </dl>
    ${ev.notes ? `<p class="note">${esc(ev.notes)}</p>` : ""}
    <div class="actions">
      <button class="btn primary" data-ics="event" data-id="${esc(ev.id)}">📅 Event eintragen</button>
      <button class="btn" data-ics="registration" data-id="${esc(ev.id)}" ${ev.registration_start ? "" : "disabled title='Anmeldestart unbekannt'"}>⏰ Anmeldestart${regFuture ? "" : ev.registration_start ? " (vorbei)" : " unbekannt"}</button>
    </div>
  </li>`;
}

function filtered() {
  const cats = state.cats[state.sport];
  const q = state.q.trim().toLowerCase();
  let list = state.events.filter((ev) => {
    if (ev.sport !== state.sport) return false;
    if (cats.size && !(ev.categories || []).some((c) => cats.has(c))) return false;
    const endDate = ev.end_date || ev.date;
    if (state.from && endDate < state.from) return false;
    if (state.to && ev.date > state.to) return false;
    const country = ev.country || "DE";
    if (state.region === "DE" && country !== "DE") return false;
    if (state.region === "DACH" && !DACH.has(country)) return false;
    if (q && !(ev.name + " " + (ev.place || "")).toLowerCase().includes(q)) return false;
    if (state.radiusOn && state.origin) {
      if (ev.lat == null) return false;
      if (distanceKm(state.origin, ev) > state.radius) return false;
    }
    return true;
  });
  return list;
}

function renderCats() {
  const present = new Set(state.events.filter((e) => e.sport === state.sport).flatMap((e) => e.categories || []));
  const order = CATEGORY_ORDER[state.sport];
  const all = [...order.filter((c) => present.has(c)), ...[...present].filter((c) => !order.includes(c)).sort()];
  const sel = state.cats[state.sport];
  $("#cats").innerHTML = all.length
    ? all.map((c) => `<button type="button" class="chip" aria-pressed="${sel.has(c)}" data-cat="${esc(c)}">${esc(c)}</button>`).join("")
    : '<span class="muted small">Noch keine Daten</span>';
}

function render() {
  const list = filtered();
  $("#count").textContent = `${list.length} ${list.length === 1 ? "Event" : "Events"}`;
  $("#list").innerHTML =
    list.slice(0, state.shown).map(card).join("") +
    (list.length > state.shown ? `<li class="more"><button class="btn" id="more">Weitere ${Math.min(PAGE, list.length - state.shown)} anzeigen</button></li>` : "");
  $("#origin").textContent = state.origin ? `Ausgangspunkt: ${state.origin.label}` : "Gib einen Ort ein oder nutze deinen Standort.";
  save();
}

// ------------------------------------------------------------ location

async function geocode(text) {
  const url = `https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&q=${encodeURIComponent(text)}`;
  const r = await fetch(url, { headers: { "Accept-Language": "de" } });
  const hits = await r.json();
  if (!hits.length) throw new Error("Ort nicht gefunden");
  return { lat: +hits[0].lat, lon: +hits[0].lon, label: hits[0].display_name.split(",").slice(0, 3).join(",") };
}

async function setPlace() {
  const text = $("#place").value.trim();
  if (!text) return;
  $("#origin").textContent = "Suche Ort …";
  try {
    state.origin = await geocode(text);
    state.radiusOn = true;
    $("#radius-on").checked = true;
  } catch (e) {
    $("#origin").textContent = e.message;
    return;
  }
  state.shown = PAGE;
  render();
}

function locate() {
  if (!navigator.geolocation) return;
  $("#origin").textContent = "Bestimme Standort …";
  navigator.geolocation.getCurrentPosition(
    (p) => {
      state.origin = { lat: p.coords.latitude, lon: p.coords.longitude, label: "Mein Standort" };
      state.radiusOn = true;
      $("#radius-on").checked = true;
      $("#place").value = "";
      render();
    },
    () => ($("#origin").textContent = "Standort nicht verfügbar. Gib bitte einen Ort ein."),
    { maximumAge: 600000, timeout: 15000 }
  );
}

// ------------------------------------------------------------ wiring

function bind() {
  document.querySelectorAll("input[name=sport]").forEach((el) =>
    el.addEventListener("change", () => { state.sport = el.value; state.shown = PAGE; renderCats(); render(); }));
  document.querySelectorAll("input[name=region]").forEach((el) =>
    el.addEventListener("change", () => { state.region = el.value; state.shown = PAGE; render(); }));
  $("#cats").addEventListener("click", (e) => {
    const b = e.target.closest("[data-cat]");
    if (!b) return;
    const set = state.cats[state.sport];
    set.has(b.dataset.cat) ? set.delete(b.dataset.cat) : set.add(b.dataset.cat);
    b.setAttribute("aria-pressed", set.has(b.dataset.cat));
    state.shown = PAGE;
    render();
  });
  $("#from").addEventListener("change", (e) => { state.from = e.target.value; render(); });
  $("#to").addEventListener("change", (e) => { state.to = e.target.value; render(); });
  $("#q").addEventListener("input", (e) => { state.q = e.target.value; state.shown = PAGE; render(); });
  $("#radius").addEventListener("input", (e) => { state.radius = +e.target.value; $("#radius-out").textContent = `${state.radius} km`; render(); });
  $("#radius-on").addEventListener("change", (e) => { state.radiusOn = e.target.checked; render(); });
  $("#place").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); setPlace(); } });
  $("#place").addEventListener("change", setPlace);
  $("#locate").addEventListener("click", locate);
  $("#filters").addEventListener("submit", (e) => e.preventDefault());
  $("#list").addEventListener("click", (e) => {
    if (e.target.id === "more") { state.shown += PAGE; render(); return; }
    const b = e.target.closest("[data-ics]");
    if (!b || b.disabled) return;
    const ev = state.events.find((x) => x.id === b.dataset.id);
    if (ev) openIcs(ev, b.dataset.ics);
  });
}

function applyStateToForm() {
  document.querySelector(`input[name=sport][value="${state.sport}"]`).checked = true;
  document.querySelector(`input[name=region][value="${state.region}"]`).checked = true;
  $("#radius").value = state.radius;
  $("#radius-out").textContent = `${state.radius} km`;
  $("#radius-on").checked = state.radiusOn;
  $("#from").value = state.from;
  $("#to").value = state.to;
}

async function init() {
  const today = new Date();
  const inAYear = new Date(today);
  inAYear.setFullYear(today.getFullYear() + 1);
  state.from = iso(today);
  state.to = iso(inAYear);
  load();
  applyStateToForm();
  bind();
  try {
    const r = await fetch("events.json", { cache: "no-cache" });
    const data = await r.json();
    state.events = data.events || [];
    $("#meta").textContent = `${state.events.length} Events · Stand ${new Date(data.generated).toLocaleDateString("de-DE")}`;
  } catch (e) {
    $("#meta").textContent = "Events konnten nicht geladen werden.";
  }
  renderCats();
  render();
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
}

if (typeof module !== "undefined") module.exports = { buildIcs, distanceKm };
else init();
