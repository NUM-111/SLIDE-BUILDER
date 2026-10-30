const byId = (id) => document.getElementById(id);
const state = { blocks: [], profiles: [], brand: null, filter: "", slideCount: 0, slideImages: {} };

function showNotice(message, kind = "") {
  const notice = byId("notice-line");
  notice.textContent = message;
  notice.dataset.kind = kind;
}

async function request(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
}

function renderInventory() {
  const body = byId("inventory-body");
  const query = state.filter.trim().toLocaleLowerCase();
  const visible = state.blocks.filter((block) => !query || block.text.toLocaleLowerCase().includes(query));
  byId("block-count").textContent = String(state.blocks.length);
  byId("inventory-showing").textContent = state.blocks.length
    ? `Showing ${visible.length} of ${state.blocks.length} blocks`
    : "No source loaded";
  byId("generate-button").disabled = state.blocks.length === 0;
  body.replaceChildren();
  if (!visible.length) {
    const row = document.createElement("tr");
    row.className = "empty-row";
    const cell = document.createElement("td");
    cell.colSpan = 3;
    const mark = document.createElement("span");
    mark.className = "empty-mark";
    mark.textContent = state.blocks.length ? "⌕" : "—";
    const title = document.createElement("strong");
    title.textContent = state.blocks.length ? "No matching content" : "Your source, in full context";
    const hint = document.createElement("span");
    hint.textContent = state.blocks.length ? "Try a different search term." : "Import a document to inspect every extracted text block.";
    cell.append(mark, title, hint);
    row.append(cell);
    body.append(row);
    return;
  }
  const fragment = document.createDocumentFragment();
  for (const block of visible.slice(0, 500)) {
    const row = document.createElement("tr");
    if (block.uncertain) row.className = "uncertain-row";
    const content = document.createElement("td");
    content.textContent = block.text;
    const type = document.createElement("td");
    type.className = "type-label";
    type.textContent = block.uncertain ? "review glyphs" : block.type.replaceAll("_", " ");
    const location = document.createElement("td");
    location.textContent = block.uncertain ? `${block.location} · review` : block.location;
    if (block.uncertain) row.title = "Undecodable PDF glyphs were preserved as extracted. Verify this block against the source before export.";
    row.append(content, type, location);
    fragment.append(row);
  }
  body.append(fragment);
  if (visible.length > 500) byId("inventory-showing").textContent += " · first 500 shown";
}

function renderBrand(brand) {
  state.brand = brand;
  byId("profile-select").value = brand.id || "";
  byId("brand-name").value = brand.name || "";
  byId("company-name").value = brand.company || "";
  byId("primary-color").value = brand.primary_color || "#1a0f49";
  byId("accent-color").value = brand.accent_color || "#c00000";
  byId("secondary-color").value = brand.secondary_color || "#25c6e3";
  byId("primary-source").textContent = (brand.color_sources?.primary_color || "manually configured").replace("not detected", "not detected");
  byId("accent-source").textContent = brand.color_sources?.accent_color || "manually configured";
  byId("secondary-source").textContent = brand.color_sources?.secondary_color || "Reference cyan";
  const logo = brand.logo;
  byId("logo-name").textContent = logo?.filename || "Add a company logo";
  const preview = byId("logo-preview");
  preview.replaceChildren();
  if (logo?.stored_as) {
    const image = document.createElement("img");
    image.alt = "Uploaded company logo preview";
    image.src = `/local-image/${encodeURIComponent(logo.stored_as)}`;
    preview.append(image);
  } else {
    preview.textContent = "FL";
  }
  renderCandidates(brand.color_candidates || []);
  renderProfileOptions();
}

function renderSlideImages() {
  const select = byId("slide-number");
  const previous = select.value;
  select.replaceChildren();
  select.disabled = state.slideCount === 0;
  byId("slide-picture-file").disabled = state.slideCount === 0;
  if (!state.slideCount) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "Import a source first";
    select.append(option);
  } else {
    for (let number = 1; number <= state.slideCount; number += 1) {
      const option = document.createElement("option");
      option.value = String(number);
      option.textContent = `Slide ${number}`;
      select.append(option);
    }
    select.value = previous && Number(previous) <= state.slideCount ? previous : "1";
  }
  const list = byId("picture-list");
  list.replaceChildren();
  for (const [slideNumber, picture] of Object.entries(state.slideImages).sort((a, b) => Number(a[0]) - Number(b[0]))) {
    const item = document.createElement("div");
    item.className = "picture-item";
    const image = document.createElement("img");
    image.src = `/local-image/${encodeURIComponent(picture.stored_as)}`;
    image.alt = `Picture assigned to slide ${slideNumber}`;
    const text = document.createElement("span");
    text.innerHTML = `<strong>Slide ${slideNumber}</strong><small></small>`;
    text.querySelector("small").textContent = picture.filename;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "remove-picture";
    remove.title = `Remove picture from slide ${slideNumber}`;
    remove.setAttribute("aria-label", `Remove picture from slide ${slideNumber}`);
    remove.textContent = "×";
    remove.addEventListener("click", () => removeSlideImage(Number(slideNumber)));
    item.append(image, text, remove);
    list.append(item);
  }
}

function renderCandidates(candidates) {
  const container = byId("color-candidates");
  container.replaceChildren();
  container.hidden = candidates.length === 0;
  for (const candidate of candidates) {
    const button = document.createElement("button");
    button.type = "button";
    button.title = `Use detected ${candidate.hex} as the primary color`;
    const swatch = document.createElement("span");
    swatch.className = "candidate-swatch";
    swatch.style.backgroundColor = candidate.hex;
    const label = document.createElement("span");
    label.textContent = candidate.hex;
    button.append(swatch, label);
    button.addEventListener("click", () => {
      byId("primary-color").value = candidate.hex.toLowerCase();
      if (state.brand) {
        state.brand.primary_color = candidate.hex;
        state.brand.color_sources = { ...state.brand.color_sources, primary_color: "detected from uploaded logo" };
        renderBrand(state.brand);
      }
    });
    container.append(button);
  }
}

function renderProfileOptions() {
  const select = byId("profile-select");
  const current = state.brand?.id || "";
  select.replaceChildren();
  const profiles = [...state.profiles];
  if (state.brand && !profiles.some((profile) => profile.id === state.brand.id)) profiles.unshift(state.brand);
  for (const profile of profiles) {
    const option = document.createElement("option");
    option.value = profile.id;
    option.textContent = profile.name || profile.company || "Brand profile";
    select.append(option);
  }
  select.value = current;
}

function renderState(data) {
  state.blocks = data.blocks || [];
  state.profiles = data.brand_profiles || [];
  state.slideCount = data.slide_count || 0;
  state.slideImages = data.slide_images || {};
  renderBrand(data.brand || {});
  renderInventory();
  renderSlideImages();
  const source = data.source;
  byId("source-strip").hidden = !source;
  byId("drop-zone").hidden = Boolean(source);
  if (source) {
    byId("source-filename").textContent = source.filename;
    byId("source-meta").textContent = `${state.blocks.length} blocks · SHA-256 ${source.sha256.slice(0, 12)}`;
  }
  if (data.warnings?.length) showNotice(data.warnings.join(" "), "error");
  if (data.report) showReport(data.report, data.presentation);
}

function showReport(report, filename) {
  const panel = byId("report-panel");
  panel.hidden = false;
  const issueCount = report.missing.length + report.duplicated.length + report.altered.length + report.uncertain.length;
  byId("report-title").textContent = issueCount ? "Review content comparison" : "Source comparison passed";
  byId("report-detail").textContent = `${report.inventory_blocks} source blocks checked across ${report.slide_count} slides`;
  byId("report-mark").classList.toggle("has-issues", issueCount > 0);
  byId("report-mark").textContent = issueCount ? "!" : "✓";
  const metrics = byId("report-metrics");
  metrics.replaceChildren();
  for (const [label, count] of [["Missing", report.missing.length], ["Duplicated", report.duplicated.length], ["Altered", report.altered.length], ["Uncertain", report.uncertain.length]]) {
    const item = document.createElement("span");
    const number = document.createElement("strong");
    number.textContent = String(count);
    item.append(number, document.createTextNode(` ${label}`));
    metrics.append(item);
  }
  const link = byId("download-link");
  link.href = filename ? `/download/${encodeURIComponent(filename)}` : "#";
  link.hidden = !filename;
}

async function loadState() {
  renderState(await request("/api/state"));
}

async function importFile(file) {
  if (!file) return;
  const data = new FormData();
  data.append("document", file);
  byId("document-file").disabled = true;
  showNotice(`Reading ${file.name} locally…`);
  try {
    const result = await request("/api/import", { method: "POST", body: data });
    state.blocks = result.blocks;
    state.slideCount = result.slide_count;
    state.slideImages = {};
    state.filter = "";
    byId("inventory-search").value = "";
    byId("source-strip").hidden = false;
    byId("drop-zone").hidden = true;
    byId("source-filename").textContent = result.source.filename;
    byId("source-meta").textContent = `${result.blocks.length} blocks · SHA-256 ${result.source.sha256.slice(0, 12)}`;
    byId("report-panel").hidden = true;
    renderInventory();
    renderSlideImages();
    showNotice(result.warnings?.join(" ") || "Extraction complete. Review the inventory before building.", result.warnings?.length ? "error" : "success");
  } catch (error) {
    showNotice(error.message, "error");
  } finally {
    byId("document-file").disabled = false;
    byId("document-file").value = "";
  }
}

async function uploadSlideImage(file) {
  if (!file) return;
  if (!state.slideCount) {
    showNotice("Import a source document before assigning pictures.", "error");
    return;
  }
  const data = new FormData();
  data.append("slide_number", byId("slide-number").value);
  data.append("picture", file);
  showNotice(`Saving picture for slide ${byId("slide-number").value} locally…`);
  try {
    const result = await request("/api/slide-image", { method: "POST", body: data });
    state.slideImages = result.slide_images;
    renderSlideImages();
    showNotice(`Picture added to slide ${byId("slide-number").value}.`, "success");
  } catch (error) {
    showNotice(error.message, "error");
  } finally {
    byId("slide-picture-file").value = "";
  }
}

async function removeSlideImage(slideNumber) {
  try {
    const result = await request("/api/slide-image/remove", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slide_number: slideNumber }),
    });
    state.slideImages = result.slide_images;
    renderSlideImages();
    showNotice(`Picture removed from slide ${slideNumber}.`, "success");
  } catch (error) {
    showNotice(error.message, "error");
  }
}

async function uploadLogo(file) {
  if (!file) return;
  const data = new FormData();
  data.append("logo", file);
  showNotice("Saving logo and checking color candidates locally…");
  try {
    const result = await request("/api/logo", { method: "POST", body: data });
    renderBrand(result.brand);
    showNotice(result.brand.color_candidates.length ? "Logo saved. Detected colors are available as optional suggestions." : "Logo saved. No dominant PNG color candidates were detected.", "success");
  } catch (error) {
    showNotice(error.message, "error");
  } finally {
    byId("logo-file").value = "";
  }
}

function currentBrand() {
  return {
    id: state.brand?.id || crypto.randomUUID(),
    name: byId("brand-name").value.trim() || "Brand profile",
    company: byId("company-name").value.trim(),
    primary_color: byId("primary-color").value.toUpperCase(),
    accent_color: byId("accent-color").value.toUpperCase(),
    secondary_color: byId("secondary-color").value.toUpperCase(),
    color_sources: {
      ...(state.brand?.color_sources || {}),
      company: "inferred from source title; user editable",
      primary_color: state.brand?.primary_color === byId("primary-color").value.toUpperCase()
        ? (state.brand?.color_sources?.primary_color || "manually configured") : "manually configured",
      accent_color: state.brand?.accent_color === byId("accent-color").value.toUpperCase()
        ? (state.brand?.color_sources?.accent_color || "manually configured") : "manually configured",
      secondary_color: state.brand?.secondary_color === byId("secondary-color").value.toUpperCase()
        ? (state.brand?.color_sources?.secondary_color || "manually configured") : "manually configured",
    },
  };
}

async function saveBrand() {
  try {
    const result = await request("/api/brand", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(currentBrand()),
    });
    state.profiles = result.brand_profiles;
    renderBrand(result.brand);
    showNotice("Brand profile saved on this device.", "success");
  } catch (error) {
    showNotice(error.message, "error");
  }
}

async function generatePresentation() {
  const button = byId("generate-button");
  button.disabled = true;
  button.lastChild.textContent = " Building and checking…";
  showNotice("Building editable slides and comparing source blocks…");
  try {
    const result = await request("/api/generate", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    showReport(result.report, result.download.split("/").at(-1));
    showNotice("Presentation built. Source text comparison is complete.", "success");
  } catch (error) {
    showNotice(error.message, "error");
  } finally {
    button.disabled = state.blocks.length === 0;
    button.lastChild.textContent = " Build presentation";
  }
}

byId("document-file").addEventListener("change", (event) => importFile(event.target.files[0]));
byId("logo-file").addEventListener("change", (event) => uploadLogo(event.target.files[0]));
byId("slide-picture-file").addEventListener("change", (event) => uploadSlideImage(event.target.files[0]));
byId("inventory-search").addEventListener("input", (event) => { state.filter = event.target.value; renderInventory(); });
byId("generate-button").addEventListener("click", generatePresentation);
byId("save-profile").addEventListener("click", saveBrand);
byId("primary-color").addEventListener("input", (event) => {
  if (state.brand) state.brand = { ...state.brand, primary_color: event.target.value.toUpperCase(), color_sources: { ...state.brand.color_sources, primary_color: "manually configured" } };
});
byId("accent-color").addEventListener("input", (event) => {
  if (state.brand) state.brand = { ...state.brand, accent_color: event.target.value.toUpperCase(), color_sources: { ...state.brand.color_sources, accent_color: "manually configured" } };
});
byId("secondary-color").addEventListener("input", (event) => {
  if (state.brand) state.brand = { ...state.brand, secondary_color: event.target.value.toUpperCase(), color_sources: { ...state.brand.color_sources, secondary_color: "manually configured" } };
});
byId("profile-select").addEventListener("change", async (event) => {
  const profile = state.profiles.find((item) => item.id === event.target.value);
  if (!profile) return;
  try {
    const result = await request("/api/brand/select", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: profile.id }),
    });
    renderBrand(result.brand);
    showNotice("Brand profile selected for this project.", "success");
  } catch (error) {
    showNotice(error.message, "error");
  }
});
byId("new-profile").addEventListener("click", () => renderBrand({
  id: crypto.randomUUID(), name: "New brand profile", company: "", primary_color: "#1A0F49", accent_color: "#C00000", secondary_color: "#25C6E3",
  color_sources: { primary_color: "manually configured", accent_color: "manually configured", secondary_color: "reference cyan" },
  logo: null, color_candidates: [],
}));
byId("replace-source").addEventListener("click", () => {
  byId("drop-zone").hidden = false;
  byId("source-strip").hidden = true;
  byId("document-file").click();
});
for (const eventName of ["dragenter", "dragover"]) byId("drop-zone").addEventListener(eventName, (event) => {
  event.preventDefault();
  byId("drop-zone").classList.add("dragging");
});
for (const eventName of ["dragleave", "drop"]) byId("drop-zone").addEventListener(eventName, (event) => {
  event.preventDefault();
  byId("drop-zone").classList.remove("dragging");
});
byId("drop-zone").addEventListener("drop", (event) => importFile(event.dataTransfer.files[0]));

loadState().catch((error) => showNotice(error.message, "error"));