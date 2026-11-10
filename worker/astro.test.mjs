import { test } from "node:test";
import assert from "node:assert/strict";
import "../aerorisk/assets/astro.js";

const A = globalThis.Astro;
const ist = (y, m, d, h, mi) => A.julianDay(y, m, d, h + mi / 60 - 5.5);

test("moon longitude matches Meeus example 47.a", () => {
  assert.ok(Math.abs(A.moonLongitude(2448724.5) - 133.162655) < 0.02);
});

test("Lahiri ayanamsa matches published January values", () => {
  for (const [y, deg] of [[2000, 23 + 51 / 60 + 12 / 3600], [2025, 24 + 12 / 60 + 23 / 3600], [2026, 24 + 13 / 60 + 19 / 3600]])
    assert.ok(Math.abs(A.lahiri(A.julianDay(y, 1, 1, 0)) - deg) < 0.01, String(y));
});

test("moon sign and nakshatra change times match a published panchang (Aug 2025, Lahiri)", () => {
  // Mesha from 9:06 AM 14 Aug, Ashwini ends 7:36 AM 15 Aug, Mesha ends 11:43 AM 16 Aug (IST)
  assert.equal(A.vedicMoonAt(ist(2025, 8, 14, 9, 20)).rashi, "Mesha");
  assert.equal(A.vedicMoonAt(ist(2025, 8, 14, 8, 50)).rashi, "Meena");
  assert.equal(A.vedicMoonAt(ist(2025, 8, 15, 7, 20)).nakshatra, "Ashwini");
  assert.equal(A.vedicMoonAt(ist(2025, 8, 15, 7, 50)).nakshatra, "Bharani");
  assert.equal(A.vedicMoonAt(ist(2025, 8, 16, 12, 0)).rashi, "Vrishabha");
});

test("without a birth time, a day when the moon changes sign is flagged", () => {
  const r = A.vedicMoon("2025-08-14", "", 330);
  assert.equal(r.exact, false);
  assert.deepEqual(r.rashiChanges, ["Meena", "Mesha"]);
  assert.equal(A.vedicMoon("2025-08-15", "12:00", 330).exact, true);
});

test("lunar new year dates", () => {
  const known = { 1950: "1950-02-17", 1966: "1966-01-21", 1985: "1985-02-20", 1990: "1990-01-27", 1996: "1996-02-19",
    2000: "2000-02-05", 2004: "2004-01-22", 2012: "2012-01-23", 2015: "2015-02-19", 2020: "2020-01-25", 2021: "2021-02-12",
    2022: "2022-02-01", 2023: "2023-01-22", 2024: "2024-02-10", 2025: "2025-01-29", 2026: "2026-02-17", 2033: "2033-01-31" };
  for (const [y, d] of Object.entries(known)) assert.equal(A.lunarNewYear(Number(y)), d, y);
});

test("Chinese zodiac uses the lunar new year boundary", () => {
  assert.equal(A.chineseZodiac("2026-03-01").animal, "Horse");
  assert.equal(A.chineseZodiac("2026-03-01").element, "Fire");
  const before = A.chineseZodiac("1984-01-15");
  assert.equal(before.animal, "Pig");
  assert.equal(before.beforeNewYear, true);
  assert.equal(A.chineseZodiac("2024-02-10").animal, "Dragon");
  assert.equal(A.chineseZodiac("2024-02-09").animal, "Rabbit");
});

test("Rahu Kalam standard periods by weekday", () => {
  assert.deepEqual([A.rahuKalam("2026-11-09").start, A.rahuKalam("2026-11-09").end], ["7:30 am", "9:00 am"]);   // Monday
  assert.equal(A.rahuKalam("2026-11-08").start, "4:30 pm");    // Sunday
  assert.equal(A.rahuKalam("2026-11-11", "12:30").inside, true);   // Wednesday 12:00-1:30
  assert.equal(A.rahuKalam("2026-11-11", "13:30").inside, false);
  assert.equal(A.rahuKalam("2026-11-11").inside, null);
});
