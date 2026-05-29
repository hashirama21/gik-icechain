/**
 * GIK-IceChain Calendar-Map interactive component.
 *
 * Loads pre-computed daily admin-1 risk summaries from data/ and renders:
 *   1. A calendar heat-map (country-level worst-case risk per day).
 *   2. A Leaflet choropleth map for the selected day.
 *
 * Risk levels:  0=Green, 1=Yellow, 2=Orange, 3=Red, -1=No_Data (grey).
 */

const RISK_COLORS = {
  "-1": "#cccccc",
  0: "#4caf50",
  1: "#ffeb3b",
  2: "#ff9800",
  3: "#f44336",
};

const RISK_LABELS = {
  "-1": "No Data",
  0: "Green",
  1: "Yellow",
  2: "Orange",
  3: "Red",
};

const DATA_DIR = "data";
const WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

let map;
let geoJsonLayer;
let currentData = null;
let calendarIndex = {};
let selectedDate = null;


document.addEventListener("DOMContentLoaded", () => {
  initMap();
  initDatePicker();
  loadCalendarIndex();
});

function initMap() {
  map = L.map("map-container").setView([2.0, 38.0], 5);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
    maxZoom: 12,
  }).addTo(map);
}

function initDatePicker() {
  const picker = document.getElementById("date-picker");
  const today = new Date().toISOString().slice(0, 10);
  picker.value = today;
  picker.addEventListener("change", () => selectDate(picker.value));
}


async function loadCalendarIndex() {
  try {
    const resp = await fetch(`${DATA_DIR}/index.json`);
    if (!resp.ok) {
      console.warn("No calendar index found");
      return;
    }
    calendarIndex = await resp.json();
    renderCalendar();

    // Load the most recent available date
    const dates = Object.keys(calendarIndex).sort();
    if (dates.length > 0) {
      const latest = dates[dates.length - 1];
      document.getElementById("date-picker").value = latest;
      selectDate(latest);
    }
  } catch (err) {
    console.error("Failed to load calendar index:", err);
  }
}


function renderCalendar() {
  const container = document.getElementById("calendar-container");
  container.innerHTML = "";

  const dates = Object.keys(calendarIndex).sort();
  if (dates.length === 0) {
    container.innerHTML = '<p class="calendar-empty">No data available yet.</p>';
    return;
  }

  // Determine months to render (last 3 months from most recent date)
  const latest = new Date(dates[dates.length - 1] + "T00:00:00");
  const months = [];
  for (let i = 2; i >= 0; i--) {
    const d = new Date(latest.getFullYear(), latest.getMonth() - i, 1);
    months.push({ year: d.getFullYear(), month: d.getMonth() });
  }

  for (const { year, month } of months) {
    container.appendChild(buildMonthGrid(year, month));
  }
}

function buildMonthGrid(year, month) {
  const monthEl = document.createElement("div");
  monthEl.className = "calendar-month";

  const title = document.createElement("h3");
  const monthName = new Date(year, month, 1).toLocaleString("en", {
    month: "long",
    year: "numeric",
  });
  title.textContent = monthName;
  monthEl.appendChild(title);

  // Weekday header
  const header = document.createElement("div");
  header.className = "calendar-grid calendar-header";
  for (const label of WEEKDAY_LABELS) {
    const cell = document.createElement("span");
    cell.textContent = label;
    header.appendChild(cell);
  }
  monthEl.appendChild(header);

  // Day cells
  const grid = document.createElement("div");
  grid.className = "calendar-grid";

  const firstDay = new Date(year, month, 1);
  // Monday=0 offset
  const startOffset = (firstDay.getDay() + 6) % 7;
  const daysInMonth = new Date(year, month + 1, 0).getDate();

  // Empty cells before first day
  for (let i = 0; i < startOffset; i++) {
    const empty = document.createElement("span");
    empty.className = "calendar-cell empty";
    grid.appendChild(empty);
  }

  for (let day = 1; day <= daysInMonth; day++) {
    const dateStr = `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    const cell = document.createElement("span");
    cell.className = "calendar-cell";
    cell.textContent = day;

    const entry = calendarIndex[dateStr];
    if (entry) {
      const risk = entry.worst_risk ?? -1;
      cell.style.backgroundColor = RISK_COLORS[risk] || RISK_COLORS["-1"];
      cell.title = `${dateStr}: ${entry.risk_label || "N/A"} (${entry.n_units} units)`;
      cell.classList.add("has-data");

      if (risk >= 2) {
        cell.style.color = "#fff";
      }

      cell.addEventListener("click", () => {
        document.getElementById("date-picker").value = dateStr;
        selectDate(dateStr);
      });
    } else {
      cell.classList.add("no-data");
    }

    if (dateStr === selectedDate) {
      cell.classList.add("selected");
    }

    grid.appendChild(cell);
  }

  monthEl.appendChild(grid);
  return monthEl;
}


function selectDate(dateStr) {
  selectedDate = dateStr;
  // Re-render calendar to update selection highlight
  renderCalendar();
  loadDay(dateStr);
}


async function loadDay(dateStr) {
  const url = `${DATA_DIR}/${dateStr}.json`;
  try {
    const resp = await fetch(url);
    if (!resp.ok) {
      console.warn(`No data for ${dateStr}`);
      clearMap();
      return;
    }
    currentData = await resp.json();
    renderMap(currentData);
  } catch (err) {
    console.error("Failed to load day data:", err);
  }
}


function clearMap() {
  if (geoJsonLayer) {
    map.removeLayer(geoJsonLayer);
    geoJsonLayer = null;
  }
}

function renderMap(geojson) {
  clearMap();

  geoJsonLayer = L.geoJSON(geojson, {
    style: (feature) => {
      const risk = feature.properties.risk_state ?? -1;
      return {
        fillColor: RISK_COLORS[risk] || RISK_COLORS["-1"],
        weight: 1,
        color: "#333",
        fillOpacity: 0.65,
      };
    },
    onEachFeature: (feature, layer) => {
      const p = feature.properties;
      const label = p.risk_label || RISK_LABELS[p.risk_state] || "N/A";
      layer.bindPopup(
        `<strong>${p.admin1_name || p.admin1_pcode}</strong><br/>` +
          `Risk: ${label}<br/>` +
          `P(Orange): ${(p.p_orange ?? 0).toFixed(2)} &bull; ` +
          `P(Red): ${(p.p_red ?? 0).toFixed(2)}`
      );
    },
  }).addTo(map);

  map.fitBounds(geoJsonLayer.getBounds());
}
