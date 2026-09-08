// Fee engine for Fee Verified. One function per platform; every number comes
// from the platform's schedule JSON (docs/fees/<platform>.json), never from
// this file. Runs in the browser (calculator pages) and in Node (tests pin
// each platform's own published examples). Money is rounded to the cent,
// half up, at the points the platforms state or demonstrate.
(function (root) {
  "use strict";

  function cents(x) { return Math.round(x * 100 + 1e-9) / 100; }
  function num(v, d) { const n = Number(v); return Number.isFinite(n) ? n : (d || 0); }

  function tieredFee(amount, tiers) {
    // tiers: [{upto: 7500, rate}, {upto: ..., rate}, {above: X, rate}] applied to portions.
    let fee = 0, lower = 0;
    for (const t of tiers) {
      if (t.upto !== undefined) {
        const portion = Math.max(0, Math.min(amount, t.upto) - lower);
        fee += portion * t.rate; lower = t.upto;
      } else {
        fee += Math.max(0, amount - (t.above || lower)) * t.rate;
      }
    }
    return fee;
  }
  function wholeRate(amount, bands) {
    // bands: [{upto: 2000, rate}, {above: 2000, rate}] -> single rate on the whole amount.
    for (const b of bands) {
      if (b.upto !== undefined && amount <= b.upto) return b.rate;
      if (b.above !== undefined && amount > b.above) return b.rate;
    }
    return bands[bands.length - 1].rate;
  }

  // ---------------------------------------------------------------- eBay
  function ebay(s, i) {
    const r = s.rates;
    const price = num(i.price), shipping = num(i.shipping);
    const taxRate = num(i.sales_tax_rate);
    const tax = cents((price + shipping) * taxRate);
    const total = cents(price + shipping + tax);
    const cat = s.categories[i.category] || s.categories.most;
    const format = i.format === "auction" ? "auction" : "fixed";
    const lines = [];

    // Insertion fee (only when the free allowance is used up).
    let insertion = 0;
    if (i.insertion_fee_applies) {
      insertion = cat.insertion_fee !== undefined ? cat.insertion_fee : r.insertion_fee;
      if (cat.insertion_free_at_or_above !== undefined && price >= cat.insertion_free_at_or_above) insertion = 0;
      if (insertion) lines.push({ id: "insertion_fee", label: "Insertion fee", amount: cents(insertion) });
    }

    // Final value fee: percentage (tiered on portions, or a whole-amount band) + per-order fee.
    let pct = cat.tiers ? tieredFee(total, cat.tiers) : total * wholeRate(total, cat.whole);
    let perOrder = total <= r.per_order_fee_small_threshold ? r.per_order_fee_small : r.per_order_fee;
    if (cat.no_per_order_fee_at_or_above !== undefined && total >= cat.no_per_order_fee_at_or_above) perOrder = 0;
    const fvf = cents(pct + perOrder);
    lines.push({ id: "final_value_fee", label: "Final value fee", amount: fvf,
      detail: `${(cat.tiers ? "tiered" : "single rate")} on $${total.toFixed(2)} total sale + $${perOrder.toFixed(2)} per order` });

    // Performance surcharges: Below Standard wins if both would apply.
    let surcharge = 0, surchargeLabel = "";
    if (i.seller_level === "below_standard") { surcharge = r.below_standard_surcharge; surchargeLabel = "Below Standard surcharge (6%)"; }
    else if (i.seller_level === "below_standard_4mo") { surcharge = r.below_standard_surcharge_4mo; surchargeLabel = "Below Standard surcharge (7%)"; }
    else if (i.inad === "very_high") { surcharge = r.very_high_inad_surcharge; surchargeLabel = "Very High 'not as described' surcharge (5%)"; }
    else if (i.inad === "very_high_4mo") { surcharge = r.very_high_inad_surcharge_4mo; surchargeLabel = "Very High 'not as described' surcharge (6%)"; }
    if (surcharge) lines.push({ id: "surcharge", label: surchargeLabel, amount: cents(total * surcharge) });

    if (i.international) lines.push({ id: "international_fee", label: "International fee (1.65%)", amount: cents(total * r.international_fee) });

    // Optional listing upgrades.
    if (i.bold) lines.push({ id: "bold_fee", label: "Bold", amount: format === "auction" ? r.bold_auction : r.bold_fixed });
    if (i.subtitle) {
      const high = price > r.subtitle_price_threshold;
      lines.push({ id: "subtitle_fee", label: "Subtitle", amount: format === "auction" ? (high ? r.subtitle_auction_high : r.subtitle_auction_low) : (high ? r.subtitle_fixed_high : r.subtitle_fixed_low) });
    }
    if (i.gallery_plus) lines.push({ id: "gallery_plus_fee", label: "Gallery Plus", amount: format === "auction" ? r.gallery_plus_auction : r.gallery_plus_fixed });
    if (format === "auction" && num(i.reserve) > 0) {
      lines.push({ id: "reserve_fee", label: "Reserve price fee", amount: cents(Math.min(r.reserve_max, Math.max(r.reserve_min, num(i.reserve) * r.reserve_rate))) });
    }

    const totalFees = cents(lines.reduce((a, l) => a + l.amount, 0));
    // Net: what the seller keeps of the item price + shipping collected (tax is remitted by eBay).
    const net = cents(price + shipping - totalFees);
    return { total_sale: total, sales_tax: tax, lines, total_fees: totalFees, net,
      effective_rate: price > 0 ? cents(totalFees / (price + shipping) * 100) : 0 };
  }

  // -------------------------------------------------------------- PayPal
  function paypal(s, i) {
    const r = s.rates;
    const amount = num(i.amount);
    const type = i.payment_type || "paypal_checkout";
    const lines = [];
    let rate, fixed;
    switch (type) {
      case "goods_and_services_p2p": rate = r.goods_and_services_p2p; fixed = 0; break;
      case "qr_code": rate = r.qr_code; fixed = r.qr_fixed_fee_usd; break;
      case "micropayments": rate = r.micropayments; fixed = r.micropayment_fixed_fee_usd; break;
      case "invoicing_ach": rate = r.invoicing_ach; fixed = 0; break;
      case "donations": rate = r.donations; fixed = r.fixed_fee_usd; break;
      case "charity_approved": rate = r.charity_approved; fixed = r.fixed_fee_usd; break;
      default: rate = r[type] !== undefined ? r[type] : r.all_other_commercial; fixed = r.fixed_fee_usd;
    }
    if (i.international && type !== "invoicing_ach") rate += r.international_surcharge;
    let fee = amount * rate + fixed;
    if (type === "invoicing_ach") fee = Math.min(fee, r.invoicing_ach_cap);
    fee = cents(fee);
    lines.push({ id: "transaction_fee", label: "Transaction fee", amount: fee,
      detail: `${(rate * 100).toFixed(2)}%${fixed ? " + $" + fixed.toFixed(2) : ""}${i.international ? " (incl. 1.50% international)" : ""}` });
    let net = cents(amount - fee);
    if (i.instant_withdrawal) {
      const iw = cents(Math.max(r.instant_withdrawal_min, net * r.instant_withdrawal));
      lines.push({ id: "instant_withdrawal", label: "Instant transfer to bank/card (1.50%, min $0.50)", amount: iw });
      net = cents(net - iw);
    }
    const totalFees = cents(lines.reduce((a, l) => a + l.amount, 0));
    return { lines, total_fees: totalFees, net, effective_rate: amount > 0 ? cents(totalFees / amount * 100) : 0 };
  }

  const engines = { ebay, paypal };

  function computeFees(schedule, inputs) {
    const fn = engines[schedule.platform];
    if (!fn) throw new Error("no engine for " + schedule.platform);
    return fn(schedule, inputs || {});
  }

  root.FeeVerified = { computeFees, engines, cents };
})(typeof module !== "undefined" && module.exports ? module.exports : (window.FeeVerified = window.FeeVerified || {}, window));
