// ==== BITPenny Exchange Simulation ====

let usdBalance = 0;
let bitpBalance = 0;
let bitpPrice = 0.01; // Start at 1 cent
let feeRate = 0.005; // 0.5% fee
let exchangeRevenue = 0; // Tracks all collected fees

// DOM elements
const usdEl = document.getElementById("usdBalance");
const bitpEl = document.getElementById("bitpBalance");
const priceEl = document.getElementById("bitpPrice");
const logEl = document.getElementById("log");

// Update the displayed balances and price
function updateDisplay(message = "") {
  usdEl.textContent = usdBalance.toFixed(2);
  bitpEl.textContent = bitpBalance.toFixed(2);
  priceEl.textContent = bitpPrice.toFixed(4);
  if (message) logEl.textContent = message;
}

// Deposit funds (simulated)
function deposit() {
  const amount = parseFloat(document.getElementById("depositAmount").value);
  if (!amount || amount <= 0) return updateDisplay("⚠️ Invalid deposit amount.");
  
  usdBalance += amount;
  updateDisplay(`✅ Deposited $${amount.toFixed(2)} USD`);
}

// Buy BITP with USD
function buy() {
  const amountUSD = parseFloat(document.getElementById("tradeAmount").value);
  if (!amountUSD || amountUSD <= 0) return updateDisplay("⚠️ Enter a valid USD amount to buy.");
  if (amountUSD > usdBalance) return updateDisplay("❌ Insufficient USD balance.");

  // Calculate fee and net purchase
  const fee = amountUSD * feeRate;
  const netUSD = amountUSD - fee;
  const bitpBought = netUSD / bitpPrice;

  // Update balances
  usdBalance -= amountUSD;
  bitpBalance += bitpBought;
  exchangeRevenue += fee;

  // Simulate price increase (basic market impact)
  bitpPrice *= 1 + (bitpBought / 10000); // more buys → price rises

  updateDisplay(`✅ Bought ${bitpBought.toFixed(4)} BITP for $${amountUSD.toFixed(2)} (Fee: $${fee.toFixed(2)})`);
}

// Sell BITP for USD
function sell() {
  const amountUSD = parseFloat(document.getElementById("tradeAmount").value);
  if (!amountUSD || amountUSD <= 0) return updateDisplay("⚠️ Enter a valid USD amount to sell.");

  const bitpToSell = amountUSD / bitpPrice;
  if (bitpToSell > bitpBalance) return updateDisplay("❌ Insufficient BITP balance.");

  // Calculate fee and net sale
  const fee = amountUSD * feeRate;
  const netUSD = amountUSD - fee;

  // Update balances
  bitpBalance -= bitpToSell;
  usdBalance += netUSD;
  exchangeRevenue += fee;

  // Simulate price drop (basic market impact)
  bitpPrice *= 1 - (bitpToSell / 10000); // more sells → price drops

  updateDisplay(`✅ Sold ${bitpToSell.toFixed(4)} BITP for $${netUSD.toFixed(2)} (Fee: $${fee.toFixed(2)})`);
}

// Initialize display
updateDisplay("💹 Welcome to BITPenny Exchange");
