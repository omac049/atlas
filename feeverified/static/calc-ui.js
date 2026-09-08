// Renders a schedule's inputs as a form and shows the fee breakdown live.
(function () {
  "use strict";
  const FV = window.FeeVerified;
  function money(x) { return "$" + Number(x).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function el(tag, attrs, children) {
    const e = document.createElement(tag);
    for (const k in attrs || {}) { if (k === "text") e.textContent = attrs[k]; else if (k === "html") e.innerHTML = attrs[k]; else e.setAttribute(k, attrs[k]); }
    (children || []).forEach(c => e.appendChild(c));
    return e;
  }
  function optionsFor(spec, schedule) {
    if (spec.options) return spec.options;
    if (spec.options_from) return Object.entries(schedule[spec.options_from]).map(([k, v]) => [k, v.label || k]);
    return [];
  }
  function readInputs(form, schedule) {
    const out = {};
    for (const spec of schedule.inputs) {
      const node = form.elements[spec.id];
      if (!node) continue;
      if (spec.type === "bool") out[spec.id] = node.checked;
      else if (spec.type === "percent") out[spec.id] = Number(node.value || 0) / 100;
      else if (spec.type === "money") out[spec.id] = Number(node.value || 0);
      else out[spec.id] = node.value;
    }
    return out;
  }
  function render(result, target, schedule, inputs) {
    target.innerHTML = "";
    const table = el("table", { class: "fees" });
    const tbody = el("tbody");
    for (const line of result.lines) {
      const tr = el("tr");
      tr.appendChild(el("td", { text: line.label + (line.detail ? "" : "") }));
      const td = el("td", { class: "num", text: money(line.amount) });
      tr.appendChild(td);
      tbody.appendChild(tr);
      if (line.detail) {
        const dr = el("tr", { class: "detail" });
        dr.appendChild(el("td", { class: "muted", text: line.detail, colspan: "2" }));
        tbody.appendChild(dr);
      }
    }
    const total = el("tr", { class: "total" });
    total.appendChild(el("td", { text: "Total fees" })); total.appendChild(el("td", { class: "num", text: money(result.total_fees) }));
    tbody.appendChild(total);
    const net = el("tr", { class: "net" });
    net.appendChild(el("td", { text: "You keep" })); net.appendChild(el("td", { class: "num", text: money(result.net) }));
    tbody.appendChild(net);
    table.appendChild(tbody);
    target.appendChild(table);
    if (result.buyer_fees && result.buyer_fees.length) {
      const bt = el("table", { class: "fees buyer" }); const bb = el("tbody");
      for (const b of result.buyer_fees) { const tr = el("tr"); tr.appendChild(el("td", { text: b.label })); tr.appendChild(el("td", { class: "num", text: money(b.amount) })); bb.appendChild(tr); }
      bt.appendChild(bb); target.appendChild(el("p", { class: "muted", text: "Paid by the buyer on top — not deducted from you:" })); target.appendChild(bt);
    }
    if (result.note) target.appendChild(el("p", { class: "muted", text: result.note }));
    if (result.lines.length === 0) target.insertBefore(el("p", { text: "No seller fees." }), target.firstChild);
    const eff = el("p", { class: "muted", text: "Effective rate: " + result.effective_rate.toFixed(2) + "% of what you charged" + (result.total_sale !== undefined ? " · fee base (total sale incl. tax): " + money(result.total_sale) : "") });
    target.appendChild(eff);
  }
  function mount(schedule, formId, outId) {
    const form = document.getElementById(formId), out = document.getElementById(outId);
    for (const spec of schedule.inputs) {
      const wrap = el("div", { class: "field" });
      const label = el("label", { for: "in-" + spec.id });
      let input;
      if (spec.type === "select") {
        input = el("select", { name: spec.id, id: "in-" + spec.id });
        for (const [v, l] of optionsFor(spec, schedule)) {
          const o = el("option", { value: v, text: l }); if (v === spec.default) o.selected = true; input.appendChild(o);
        }
        label.appendChild(el("span", { text: spec.label })); label.appendChild(input);
      } else if (spec.type === "bool") {
        input = el("input", { type: "checkbox", name: spec.id, id: "in-" + spec.id }); if (spec.default) input.checked = true;
        label.appendChild(input); label.appendChild(el("span", { text: " " + spec.label }));
        wrap.classList.add("check");
      } else {
        input = el("input", { type: "number", name: spec.id, id: "in-" + spec.id, step: spec.type === "percent" ? "0.1" : "0.01", min: "0", inputmode: "decimal" });
        input.value = spec.type === "percent" ? (spec.default * 100) : spec.default;
        label.appendChild(el("span", { text: spec.label + (spec.type === "percent" ? " (%)" : " ($)") })); label.appendChild(input);
      }
      wrap.appendChild(label);
      if (spec.help) wrap.appendChild(el("div", { class: "muted small", text: spec.help }));
      form.appendChild(wrap);
    }
    const update = () => { try { render(FV.computeFees(schedule, readInputs(form, schedule)), out, schedule); } catch (e) { out.textContent = "Could not compute: " + e.message; } };
    form.addEventListener("input", update); form.addEventListener("change", update);
    form.addEventListener("submit", ev => { ev.preventDefault(); update(); });
    update();
  }
  window.FeeVerifiedUI = { mount };
})();
