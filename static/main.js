// ==== BITPenny Exchange Simulation ====
(function () {
  let usdBalance = 0;
  let bitpBalance = 0;
  let bitpPrice = 0.01; // start at 1 cent
  const feeRate = 0.005; // 0.5%
  let exchangeRevenue = 0;

  // Cache DOM after DOMContentLoaded if this file were in <head>.
  // In our template we load it at the end, so DOM is ready.
  const $ = (id) => document.getElementById(id);
  const usdEl = $("usdBalance");
  const bitpEl = $("bitpBalance");
  const priceEl = $("bitpPrice");
  const logEl = $("log");
  const depLogEl = $("depositLog");

  function fmtUSD(v) {
    return Number(v).toFixed(2);
  }
  function fmtBITP(v) {
    return Number(v).toFixed(4);
  }

  function updateDisplay(message = "") {
    usdEl.textContent = fmtUSD(usdBalance);
    bitpEl.textContent = fmtBITP(bitpBalance);
    priceEl.textContent = fmtUSD(bitpPrice);
    if (message) {
      logEl.textContent = message;
    }
    $("exchangeRevenue").textContent = fmtUSD(exchangeRevenue);
  }

  function deposit() {
    const amount = parseFloat($("depositAmount").value);
    if (!amount || amount <= 0) {
      depLogEl.textContent = "⚠️ Enter a positive number.";
      return;
    }
    usdBalance += amount;
    depLogEl.textContent = `✅ Deposited $${fmtUSD(amount)}.`;
    updateDisplay();
  }

  function buy() {
    const amountUSD = parseFloat($("tradeAmount").value);
    if (!amountUSD || amountUSD <= 0) return updateDisplay("⚠️ Enter a valid USD amount to buy.");
    if (amountUSD > usdBalance) return updateDisplay("❌ Insufficient USD balance.");

    const fee = amountUSD * feeRate;
    const netUSD = amountUSD - fee;
    const bitpBought = netUSD / bitpPrice;

    usdBalance -= amountUSD;
    bitpBalance += bitpBought;
    exchangeRevenue += fee;

    // Simple market impact up when buying
    bitpPrice *= (1 + Math.min(0.05, bitpBought * 0.0001));

    updateDisplay(`✅ Bought ${fmtBITP(bitpBought)} BITP for $${fmtUSD(amountUSD)} (fee $${fmtUSD(fee)}).`);
  }

  function sell() {
    const amountUSD = parseFloat($("tradeAmount").value);
    if (!amountUSD || amountUSD <= 0) return updateDisplay("⚠️ Enter a valid USD amount to sell.");

    const fee = amountUSD * feeRate;
    const netUSD = amountUSD - fee;
    const bitpToSell = netUSD / bitpPrice;

    if (bitpToSell > bitpBalance) return updateDisplay("❌ Insufficient BITP balance.");

    bitpBalance -= bitpToSell;
    usdBalance += amountUSD;
    exchangeRevenue += fee;

    // Simple market impact down when selling
    bitpPrice *= (1 - Math.min(0.05, bitpToSell * 0.0001));

    updateDisplay(`✅ Sold ${fmtBITP(bitpToSell)} BITP for $${fmtUSD(amountUSD)} (fee $${fmtUSD(fee)}).`);
  }

  // Bind events
  $("depositBtn").addEventListener("click", deposit);
  $("buyBtn").addEventListener("click", buy);
  $("sellBtn").addEventListener("click", sell);

  // Initial draw
  updateDisplay();
})();