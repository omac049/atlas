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

  // ---------------------------------------------------------------- Etsy
  function etsy(s, i) {
    const r = s.rates;
    const price = num(i.price), shipping = num(i.shipping), gift = num(i.gift_wrap);
    const qty = Math.max(1, Math.round(num(i.quantity, 1)));
    const tax = cents((price + shipping + gift) * num(i.sales_tax_rate));
    const feeBase = price + shipping + gift;          // 6.5% and Offsite Ads base (US: no tax)
    const processingBase = feeBase + tax;             // Etsy Payments base includes tax
    const lines = [];
    lines.push({ id: "listing_fee", label: "Listing fee ($0.20 per unit sold)", amount: cents(r.listing_fee * qty) });
    lines.push({ id: "transaction_fee", label: "Transaction fee (6.5% of price + shipping + gift wrap)", amount: cents(feeBase * r.transaction_rate) });
    lines.push({ id: "processing_fee", label: "Etsy Payments processing (3% + $0.25 on order incl. tax)", amount: cents(processingBase * r.processing_rate_us + r.processing_fixed_us) });
    if (i.offsite_ad) {
      const rate = num(i.offsite_rate, r.offsite_ads_standard);
      lines.push({ id: "offsite_ads_fee", label: `Offsite Ads fee (${(rate * 100).toFixed(0)}%, capped at $100)`, amount: cents(Math.min(r.offsite_ads_cap, feeBase * rate)) });
    }
    const totalFees = cents(lines.reduce((a, l) => a + l.amount, 0));
    return { total_sale: cents(processingBase), sales_tax: tax, lines, total_fees: totalFees, net: cents(feeBase - totalFees),
      effective_rate: feeBase > 0 ? cents(totalFees / feeBase * 100) : 0 };
  }

  // -------------------------------------------------------------- Reverb
  function reverb(s, i) {
    const r = s.rates;
    const price = num(i.price), shipping = num(i.shipping);
    const tax = cents((price + shipping) * num(i.sales_tax_rate));
    const sellingBase = price + shipping;             // "inclusive of all costs except sales tax"
    const processingBase = sellingBase + tax;         // processing includes tax
    const lines = [];
    const selling = cents(Math.min(r.selling_max, Math.max(r.selling_min, sellingBase * r.selling_rate)));
    lines.push({ id: "selling_fee", label: "Selling fee (5%, min $0.50, max $500)", amount: selling });
    const pRate = i.preferred ? r.processing_rate_preferred : r.processing_rate;
    lines.push({ id: "processing_fee", label: `Payment processing (${(pRate * 100).toFixed(2)}% + $0.49 on order incl. tax)`, amount: cents(processingBase * pRate + r.processing_fixed) });
    if (i.international) lines.push({ id: "cross_border_fee", label: "Cross-border fee (1%)", amount: cents(sellingBase * r.cross_border_rate) });
    const bump = num(i.bump_rate);
    if (bump > 0) lines.push({ id: "bump_fee", label: `Bump (${(bump * 100).toFixed(1)}% of sale)`, amount: cents(sellingBase * Math.min(r.bump_max, Math.max(r.bump_min, bump))) });
    const totalFees = cents(lines.reduce((a, l) => a + l.amount, 0));
    return { total_sale: cents(processingBase), sales_tax: tax, lines, total_fees: totalFees, net: cents(sellingBase - totalFees),
      effective_rate: sellingBase > 0 ? cents(totalFees / sellingBase * 100) : 0 };
  }

  // -------------------------------------------------------------- Amazon
  function amazon(s, i) {
    const r = s.rates;
    const price = num(i.item_price), delivery = num(i.delivery_charges), gift = num(i.gift_wrap_charges);
    const total = cents(price + delivery + gift);      // "total sales price", taxes excluded
    const cat = s.categories[i.category] || s.categories[Object.keys(s.categories)[0]];
    const lines = [];
    let referral = 0;
    if (cat.tiers) {
      if (cat.tier_basis === "whole_price") {
        const band = cat.tiers.find(t => total >= t.from && (t.to === null || t.to === undefined || total <= t.to)) || cat.tiers[cat.tiers.length - 1];
        referral = total * band.rate;
      } else {
        referral = tieredFee(total, cat.tiers.map(t => (t.to === null || t.to === undefined) ? { above: t.from, rate: t.rate } : { upto: t.to, rate: t.rate }));
      }
    } else {
      referral = total * num(cat.rate);
    }
    const minimum = cat.minimum === null || cat.minimum === undefined ? 0 : num(cat.minimum);
    referral = cents(Math.max(referral, minimum));
    lines.push({ id: "referral_fee", label: `Referral fee (${cat.label})`, amount: referral });
    if (r.media_categories.indexOf(i.category) >= 0) lines.push({ id: "closing_fee", label: "Closing fee (media)", amount: r.media_closing_fee });
    if (i.plan !== "professional") lines.push({ id: "plan_fee", label: "Individual plan ($0.99 per item sold)", amount: r.individual_per_item });
    const totalFees = cents(lines.reduce((a, l) => a + l.amount, 0));
    return { total_sale: total, lines, total_fees: totalFees, net: cents(total - totalFees),
      effective_rate: total > 0 ? cents(totalFees / total * 100) : 0 };
  }

  function pctFixed(amount, pair, extraRate) {
    const rate = (pair[0] || 0) + (extraRate || 0);
    return { fee: cents(amount * rate + (pair[1] || 0)), rate, fixed: pair[1] || 0 };
  }
  function label(rate, fixed, extra) {
    return `${(rate * 100).toFixed(2).replace(/\.?0+$/, "")}%${fixed ? " + $" + fixed.toFixed(2) : ""}${extra || ""}`;
  }
  function finish(amountBase, lines, extra) {
    const totalFees = cents(lines.reduce((a, l) => a + l.amount, 0));
    return Object.assign({ lines, total_fees: totalFees, net: cents(amountBase - totalFees),
      effective_rate: amountBase > 0 ? cents(totalFees / amountBase * 100) : 0 }, extra || {});
  }

  // -------------------------------------------------------------- Square
  function square(s, i) {
    const r = s.rates, amount = num(i.amount), plan = i.plan || "free", ch = i.channel || "in_person";
    const lines = [];
    const intl = i.international ? r.international_surcharge : 0;
    if (ch === "ach_invoice" || ch === "ach_api") {
      const a = ch === "ach_invoice" ? r.ach_invoice : r.ach_api;
      const cap = ch === "ach_invoice" ? a.cap[plan] : a.cap;
      let fee = Math.max(a.min, amount * a.rate);
      if (cap !== null && cap !== undefined) fee = Math.min(fee, cap);
      lines.push({ id: "processing_fee", label: `ACH (1%, min $1${cap ? ", max $" + cap : ""})`, amount: cents(fee) });
    } else {
      const pair = ch === "in_person" ? r.in_person[plan] : ch === "online" ? r.online[plan] : ch === "api" ? r.api : ch === "keyed" ? r.keyed : r.afterpay;
      const f = pctFixed(amount, pair, intl);
      lines.push({ id: "processing_fee", label: `Processing (${label(f.rate, f.fixed, intl ? " incl. 1.5% international" : "")})`, amount: f.fee });
    }
    return finish(amount, lines);
  }

  // -------------------------------------------------------------- Stripe
  function stripe(s, i) {
    const r = s.rates, amount = num(i.amount), m = i.method || "card";
    const lines = [];
    if (m === "ach") {
      lines.push({ id: "processing_fee", label: "ACH Direct Debit (0.8%, max $5)", amount: cents(Math.min(r.ach.cap, amount * r.ach.rate)) });
    } else {
      let pair = m === "terminal" ? r.terminal : m === "klarna" ? r.klarna : m === "affirm" ? r.affirm : r.card;
      let extra = (m === "manual" ? r.manual_surcharge : 0) + (i.international && m !== "terminal" ? r.international_surcharge : 0) + (i.currency_conversion ? r.currency_conversion_surcharge : 0);
      const f = pctFixed(amount, pair, extra);
      lines.push({ id: "processing_fee", label: `Processing (${label(f.rate, f.fixed)})`, amount: f.fee });
    }
    let net = cents(amount - lines[0].amount);
    if (i.instant_payout) { const ip = cents(net * r.instant_payout); lines.push({ id: "instant_payout", label: "Instant Payout (1.5%)", amount: ip }); }
    return finish(amount, lines);
  }

  // ------------------------------------------------------------- Shopify
  function shopify(s, i) {
    const r = s.rates, amount = num(i.amount), plan = i.plan || "basic", ch = i.channel || "online_standard";
    const lines = [];
    if (ch === "third_party") {
      lines.push({ id: "transaction_fee", label: `Shopify transaction fee for using another provider (${(r.third_party_fee[plan] * 100).toFixed(1)}%)`, amount: cents(amount * r.third_party_fee[plan]),
        detail: "Your payment provider's own processing fee comes on top and is not shown here." });
    } else {
      const pair = ch === "manual" ? r.manual : r[ch][plan];
      const intl = i.international && ch.startsWith("online") ? r.international_surcharge : 0;
      const f = pctFixed(amount, pair, intl);
      lines.push({ id: "processing_fee", label: `Shopify Payments (${label(f.rate, f.fixed, intl ? " incl. 1% international" : "")})`, amount: f.fee });
    }
    return finish(amount, lines, { note: `Plan subscription: $${r.plan_monthly[plan]}/month (or $${r.plan_yearly_billed[plan]}/month billed yearly), not per sale.` });
  }

  // --------------------------------------------------------------- Venmo
  function venmo(s, i) {
    const r = s.rates, amount = num(i.amount), pair = r[i.profile] || r.business;
    const lines = [];
    const f = pctFixed(amount, pair);
    lines.push({ id: "seller_fee", label: `Venmo fee (${label(f.rate, f.fixed)})`, amount: f.fee });
    if (i.instant_transfer) {
      const net = amount - f.fee;
      lines.push({ id: "instant_transfer", label: "Instant transfer (1.75%, min $0.25, max $25)", amount: cents(Math.min(r.instant_transfer.max, Math.max(r.instant_transfer.min, net * r.instant_transfer.rate))) });
    }
    return finish(amount, lines);
  }

  // ------------------------------------------------------------ Cash App
  function cashapp(s, i) {
    const r = s.rates, amount = num(i.amount), pair = r[i.channel] || r.business;
    const lines = [];
    const f = pctFixed(amount, pair);
    lines.push({ id: "processing_fee", label: `Cash App for Business (${label(f.rate, f.fixed)})`, amount: f.fee });
    if (i.instant_transfer) {
      const it = r.instant_transfer;
      const rate = Math.min(it.rate_max, Math.max(it.rate_min, num(i.instant_rate, 0.0175)));
      const net = amount - f.fee;
      lines.push({ id: "instant_transfer", label: `Instant transfer (${(rate * 100).toFixed(2)}%; Cash App publishes 0.5%–2.5%, min $0.25–$1, max $75)`,
        amount: cents(Math.min(it.max_fee, Math.max(it.min_fee_low, net * rate))) });
    }
    return finish(amount, lines);
  }

  // ------------------------------------------------------------ GoFundMe
  function gofundme(s, i) {
    const r = s.rates, amount = num(i.amount), pair = r[i.fundraiser_type] || r.individual;
    const lines = [];
    const f = pctFixed(amount, pair);
    lines.push({ id: "transaction_fee", label: `Transaction fee (${label(f.rate, f.fixed)})`, amount: f.fee });
    const buyer = i.recurring ? [{ label: "Recurring-donation fee paid by the donor (5%)", amount: cents(amount * r.recurring_donor_fee) }] : [];
    return finish(amount, lines, { buyer_fees: buyer });
  }

  // --------------------------------------------------------------- Depop
  function depop(s, i) {
    const r = s.rates, price = num(i.price), shipping = num(i.shipping);
    const tax = cents((price + shipping) * num(i.sales_tax_rate));
    const lines = [];
    const f = pctFixed(price + shipping + tax, r.processing);
    lines.push({ id: "processing_fee", label: "Depop Payments processing (3.3% + $0.45 on item + shipping + tax)", amount: f.fee });
    if (i.boosted) lines.push({ id: "boost_fee", label: "Boosted listing fee (12%)", amount: cents((price + (i.own_shipping ? shipping : 0)) * r.boost) });
    return finish(price + shipping, lines, { total_sale: cents(price + shipping + tax), sales_tax: tax });
  }

  // ------------------------------------------------------------ Poshmark
  function poshmark(s, i) {
    const r = s.rates, price = num(i.price);
    const lines = [];
    const fee = price < r.threshold ? r.flat_under_threshold : cents(price * r.rate_at_or_above);
    lines.push({ id: "poshmark_fee", label: price < r.threshold ? "Poshmark fee (flat $2.95 under $15)" : "Poshmark fee (20% at $15 and above)", amount: fee });
    const upgrade = num(i.label_upgrade);
    if (upgrade > 0) lines.push({ id: "label_upgrade", label: `Heavier shipping label upgrade`, amount: upgrade });
    if (i.texas) lines.push({ id: "texas_fee_tax", label: "Texas Seller Fee Tax (sales tax on 80% of the fee)", amount: cents(fee * r.texas_tax_base_share * num(i.texas_rate, r.texas_state_rate)) });
    return finish(price, lines, { buyer_fees: [{ label: "Shipping label paid by the buyer", amount: r.buyer_shipping_label }] });
  }

  // ------------------------------------------------------------- Mercari
  function mercari(s, i) {
    const r = s.rates, price = num(i.price), shipping = num(i.shipping);
    const base = price + shipping;
    const lines = [{ id: "selling_fee", label: "Selling fee (10% of item + buyer-paid shipping)", amount: cents(base * r.selling_fee) }];
    if (i.instant_pay) lines.push({ id: "instant_pay", label: "Instant Pay cash-out", amount: r.instant_pay_per_cashout });
    return finish(base, lines, { buyer_fees: [{ label: "Buyer Protection fee paid by the buyer (3.6%)", amount: cents(base * r.buyer_protection_buyer_paid) }] });
  }

  // ------------------------------------------------------------- Whatnot
  function whatnot(s, i) {
    const r = s.rates, price = num(i.price), shipping = num(i.shipping), tax = num(i.sales_tax);
    const cat = i.category || "standard";
    let rate = r.tiers[i.tier || "standard"] || r.commission_standard;
    if (cat === "coins") rate = r.commission_coins; else if (cat === "pallets") rate = r.commission_pallets;
    let commission;
    if (cat === "high_value") commission = Math.min(price, r.high_value_threshold) * rate + Math.max(0, price - r.high_value_threshold) * r.high_value_rate_above;
    else commission = price * rate;
    const total = cents(price + shipping + tax);
    const lines = [{ id: "commission", label: `Commission (${(rate * 100).toFixed(2).replace(/\.?0+$/, "")}%${cat === "high_value" ? ", 0% above $1,500" : ""})`, amount: cents(commission) }];
    const f = pctFixed(total, r.processing);
    lines.push({ id: "processing_fee", label: "Payment processing (2.9% + $0.30 on checkout total)", amount: f.fee });
    return finish(price, lines, { total_sale: total });
  }

  // -------------------------------------------------------------- Vinted
  function vinted(s, i) {
    const r = s.rates, price = num(i.price);
    return finish(price, [], { buyer_fees: [{ label: "Buyer Protection fee paid by the buyer ($0.70 + 5%)", amount: cents(price * r.buyer_protection[0] + r.buyer_protection[1]) }] });
  }

  // -------------------------------------------------------- QuickBooks Payments
  // Standard QuickBooks Online chart: one percentage per payment method, +1% for
  // international cards/PayPal (not ACH), no fixed fee; instant deposit 1.75% of
  // the amount deposited. Intuit rounds each fee to the nearest cent.
  function quickbooks(s, i) {
    const r = s.rates, amount = num(i.amount);
    const names = { invoiced_card: "Invoice, card or wallet", invoiced_ach: "Invoice, ACH bank payment",
      card_reader: "Card reader / Tap to Pay", keyed: "Keyed-in card", pin_debit: "PIN debit" };
    const ch = names[i.channel] ? i.channel : "invoiced_card";
    const intl = i.international && ch !== "invoiced_ach" ? r.international_surcharge : 0;
    const f = pctFixed(amount, [r[ch], 0], intl);
    const lines = [{ id: "processing_fee", label: `${names[ch]} (${label(f.rate, 0, intl ? " incl. 1% international" : "")})`, amount: f.fee }];
    if (i.instant_deposit) {
      lines.push({ id: "instant_deposit", label: "Instant deposit (1.75% of the amount deposited)", amount: cents((amount - f.fee) * r.instant_deposit) });
    }
    return finish(amount, lines);
  }

  // ------------------------------------------------------------------ Grailed
  // Seller fee on the sale price (listing price, plus buyer-paid shipping unless a
  // Grailed Label is used): 9% at $120 and above, else 6% with a $1.99 minimum.
  // Payment processing is separate, by payout setup and domestic/international.
  function grailed(s, i) {
    const r = s.rates, c = r.commission, price = num(i.price), shipping = num(i.shipping);
    const base = price + (i.grailed_label ? 0 : shipping);
    const standard = base >= c.threshold;
    const fee = standard ? cents(base * c.standard) : cents(Math.max(c.reduced_min, base * c.reduced));
    const lines = [{ id: "commission", amount: fee, label: standard
      ? `Seller fee (${label(c.standard, 0)} on sales of $${c.threshold} and above)`
      : `Seller fee (${label(c.reduced, 0)} under $${c.threshold}, minimum $${c.reduced_min.toFixed(2)})` }];
    const setup = r.processing[i.processing] ? i.processing : "stripe_onboarded";
    const charged = price + shipping;
    const f = pctFixed(charged, r.processing[setup][i.international ? "international" : "domestic"]);
    lines.push({ id: "processing_fee", label: `Payment processing (${label(f.rate, f.fixed)})`, amount: f.fee });
    return finish(charged, lines);
  }

  // -------------------------------------------------------------- TikTok Shop
  // One referral fee on Buyer Paid + Platform Discount - Tax, by category group;
  // collectibles and pre-owned pay a lower rate on the portion above a threshold.
  function tiktokshop(s, i) {
    const g = s.rates.groups[i.category] || s.rates.groups.standard;
    const base = num(i.price) + num(i.shipping);
    let fee = base * g.rate, text = `Referral fee (${label(g.rate, 0)})`;
    if (g.over && base > g.over) {
      fee = g.over * g.rate + (base - g.over) * g.over_rate;
      text = `Referral fee (${label(g.rate, 0)} up to $${g.over.toLocaleString("en-US")}, ${label(g.over_rate, 0)} above)`;
    }
    return finish(base, [{ id: "referral_fee", label: text, amount: cents(fee) }]);
  }

  // ------------------------------------------------------------------ Patreon
  // Platform fee on the payment before tax; processing and currency conversion on
  // the payment including tax; legacy plans use micropayment rates at $3 or less.
  function patreon(s, i) {
    const r = s.rates, amount = num(i.amount), tax = num(i.tax), charged = amount + tax;
    const plan = r.platform[i.plan] !== undefined ? i.plan : "standard";
    const method = r.processing_standard[i.method] ? i.method : "card";
    const lines = [{ id: "platform_fee", label: `Platform fee (${label(r.platform[plan], 0)} of the payment before tax)`, amount: cents(amount * r.platform[plan]) }];
    let pair, why;
    if (plan === "founders") { pair = method === "card" ? r.processing_founders.card : r.processing_founders.paypal; why = "Founders rate"; }
    else if (plan !== "standard" && amount <= r.micropayment_max) { pair = r.processing_legacy_micro[method]; why = `micropayment rate, tier $${r.micropayment_max} or less`; }
    else { pair = r.processing_standard[method]; why = plan === "standard" ? "standard plan" : "standard rate"; }
    const f = pctFixed(charged, pair);
    lines.push({ id: "processing_fee", label: `Payment processing (${label(f.rate, f.fixed)}, ${why}${tax ? ", on payment plus tax" : ""})`, amount: f.fee });
    if (i.currency_conversion) {
      lines.push({ id: "currency_conversion", label: `Currency conversion (${label(r.currency_conversion, 0)} of payment plus tax)`, amount: cents(charged * r.currency_conversion) });
    }
    const left = amount - lines.reduce((a, l) => a + l.amount, 0);
    if (i.payout === "direct_deposit") {
      lines.push({ id: "payout_fee", label: `Payout by direct deposit ($${r.payout.direct_deposit.fixed.toFixed(2)} per payout)`, amount: r.payout.direct_deposit.fixed });
    } else if (i.payout === "paypal") {
      const p = r.payout.paypal;
      lines.push({ id: "payout_fee", label: `Payout to PayPal (${label(p.rate, 0)}, minimum $${p.min.toFixed(2)}, capped at $${p.max})`, amount: cents(Math.min(p.max, Math.max(p.min, left * p.rate))) });
    }
    return finish(amount, lines);
  }

  // ------------------------------------------------------------------- Upwork
  // Freelancer Service Fee: a per-contract rate between 0% and 15% (shown on the
  // offer), rounded to the nearest cent; plus a flat fee per withdrawal method.
  function upwork(s, i) {
    const r = s.rates, earnings = num(i.earnings);
    const rate = Math.min(r.service_fee_max, Math.max(r.service_fee_min, num(i.fee_rate, 0.10)));
    const lines = [{ id: "service_fee", label: `Freelancer Service Fee (${label(rate, 0)} of earnings)`, amount: cents(earnings * rate) }];
    const w = i.withdrawal && r.withdrawal[i.withdrawal] !== undefined ? i.withdrawal : "none";
    if (w !== "none") {
      const names = { us_bank_us: "Direct to U.S. Bank, US tax address", us_bank_intl: "Direct to U.S. Bank, international tax address",
        local_bank: "Direct to Local Bank", wire: "U.S. Dollar Wire Transfer", instant: "Instant Pay" };
      lines.push({ id: "withdrawal_fee", label: `Withdrawal: ${names[w]}`, amount: r.withdrawal[w] });
    }
    return finish(earnings, lines);
  }

  const engines = { ebay, paypal, etsy, reverb, amazon, square, stripe, shopify, venmo, cashapp, gofundme, depop, poshmark, mercari, whatnot, vinted, quickbooks, grailed, tiktokshop, patreon, upwork };

  function computeFees(schedule, inputs) {
    const fn = engines[schedule.platform];
    if (!fn) throw new Error("no engine for " + schedule.platform);
    return fn(schedule, inputs || {});
  }

  root.FeeVerified = { computeFees, engines, cents };
})(typeof module !== "undefined" && module.exports ? module.exports : (window.FeeVerified = window.FeeVerified || {}, window));
