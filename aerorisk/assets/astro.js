// Astrology helpers for the just-for-fun lucky seat. Pure functions, no I/O; runs in the browser and in Node tests.
// Moon: Meeus, Astronomical Algorithms ch. 47 (main periodic terms, about 0.02 degrees).
// New moon: Meeus ch. 49 (a few minutes). Ayanamsa: Lahiri (Chitrapaksha), linear about J2000.
(function (root) {
  const rad = Math.PI / 180;
  const norm = (x) => ((x % 360) + 360) % 360;
  const sin = (d) => Math.sin(d * rad);

  // Julian Day for a UTC instant given as y, m, d, hours (fractional).
  function julianDay(y, m, d, h) {
    if (m <= 2) { y -= 1; m += 12; }
    const a = Math.floor(y / 100), b = 2 - a + Math.floor(a / 4);
    return Math.floor(365.25 * (y + 4716)) + Math.floor(30.6001 * (m + 1)) + d + (h || 0) / 24 + b - 1524.5;
  }

  function moonLongitude(jd) {          // tropical ecliptic longitude, degrees
    const T = (jd - 2451545) / 36525;
    const Lp = 218.3164477 + 481267.88123421 * T;
    const D = 297.8501921 + 445267.1114034 * T;
    const M = 357.5291092 + 35999.0502909 * T;
    const Mp = 134.9633964 + 477198.8675055 * T;
    const F = 93.272095 + 483202.0175233 * T;
    const E = 1 - 0.002516 * T - 0.0000074 * T * T;
    const terms = [ // [coef, D, M, M', F]
      [6.288774, 0, 0, 1, 0], [1.274027, 2, 0, -1, 0], [0.658314, 2, 0, 0, 0], [0.213618, 0, 0, 2, 0],
      [-0.185116, 0, 1, 0, 0], [-0.114332, 0, 0, 0, 2], [0.058793, 2, 0, -2, 0], [0.057066, 2, -1, -1, 0],
      [0.053322, 2, 0, 1, 0], [0.045758, 2, -1, 0, 0], [-0.040923, 0, 1, -1, 0], [-0.03472, 1, 0, 0, 0],
      [-0.030383, 0, 1, 1, 0], [0.015327, 2, 0, 0, -2], [-0.012528, 0, 0, 1, 2], [0.01098, 0, 0, 1, -2],
      [0.010675, 4, 0, -1, 0], [0.010034, 0, 0, 3, 0], [0.008548, 4, 0, -2, 0], [-0.007888, 2, 1, -1, 0],
      [-0.006766, 2, 1, 0, 0], [-0.005163, 1, 0, -1, 0], [0.004987, 1, 1, 0, 0], [0.004036, 2, -1, 1, 0],
      [0.003994, 2, 0, 2, 0], [0.003861, 4, 0, 0, 0], [0.003665, 2, 0, -3, 0], [-0.002689, 0, 1, -2, 0],
      [-0.002602, 2, 0, -1, 2], [0.00239, 2, -1, -2, 0], [-0.002348, 1, 0, 1, 0], [0.002236, 2, -2, 0, 0],
      [-0.00212, 0, 1, 2, 0], [-0.002069, 0, 2, 0, 0], [0.002048, 2, -2, -1, 0]];
    let sum = 0;
    for (const [c, d, m, mp, f] of terms) {
      const e = Math.abs(m) === 1 ? E : Math.abs(m) === 2 ? E * E : 1;
      sum += c * e * sin(d * D + m * M + mp * Mp + f * F);
    }
    const A1 = 119.75 + 131.849 * T, A2 = 53.09 + 479264.29 * T;
    sum += 0.003958 * sin(A1) + 0.001962 * sin(Lp - F) + 0.000318 * sin(A2);
    return norm(Lp + sum);
  }

  const lahiri = (jd) => 23.85333 + 0.0141244 * ((jd - 2451545) / 365.25);   // 23°51′12″ in 2000, 24°12′23″ in 2025

  const RASHI = [ // [Sanskrit name, Western name, element]
    ['Mesha', 'Aries', 'Fire'], ['Vrishabha', 'Taurus', 'Earth'], ['Mithuna', 'Gemini', 'Air'], ['Karka', 'Cancer', 'Water'],
    ['Simha', 'Leo', 'Fire'], ['Kanya', 'Virgo', 'Earth'], ['Tula', 'Libra', 'Air'], ['Vrischika', 'Scorpio', 'Water'],
    ['Dhanu', 'Sagittarius', 'Fire'], ['Makara', 'Capricorn', 'Earth'], ['Kumbha', 'Aquarius', 'Air'], ['Meena', 'Pisces', 'Water']];
  const NAKSHATRA = ['Ashwini', 'Bharani', 'Krittika', 'Rohini', 'Mrigashira', 'Ardra', 'Punarvasu', 'Pushya', 'Ashlesha',
    'Magha', 'Purva Phalguni', 'Uttara Phalguni', 'Hasta', 'Chitra', 'Swati', 'Vishakha', 'Anuradha', 'Jyeshtha', 'Mula',
    'Purva Ashadha', 'Uttara Ashadha', 'Shravana', 'Dhanishta', 'Shatabhisha', 'Purva Bhadrapada', 'Uttara Bhadrapada', 'Revati'];
  // Nakshatra lords repeat in this order; numbers follow the usual planet-number pairing in Indian numerology.
  const LORDS = [['Ketu', 7], ['Venus', 6], ['Sun', 1], ['Moon', 2], ['Mars', 9], ['Rahu', 4], ['Jupiter', 3], ['Saturn', 8], ['Mercury', 5]];

  function vedicMoonAt(jd) {
    const sid = norm(moonLongitude(jd) - lahiri(jd));
    const r = Math.floor(sid / 30), nIdx = Math.floor(sid / (360 / 27));
    const pada = Math.floor((sid % (360 / 27)) / (360 / 108)) + 1;
    const lord = LORDS[nIdx % 9];
    return { longitude: sid, rashi: RASHI[r][0], western: RASHI[r][1], element: RASHI[r][2],
             nakshatra: NAKSHATRA[nIdx], nakshatraIndex: nIdx, pada, lord: lord[0], number: lord[1] };
  }

  // dob "YYYY-MM-DD"; tob "HH:MM" or ""; tzMinutes east of UTC (IST = 330).
  // Without a birth time the moon may change sign during the day, so the whole day is checked.
  function vedicMoon(dob, tob, tzMinutes) {
    const [y, m, d] = dob.split('-').map(Number);
    const tz = Number.isFinite(tzMinutes) ? tzMinutes : 330;
    const at = (localHours) => vedicMoonAt(julianDay(y, m, d, localHours - tz / 60));
    if (tob) {
      const [hh, mm] = tob.split(':').map(Number);
      return { ...at(hh + mm / 60), exact: true };
    }
    const start = at(0), end = at(23.99), noon = at(12);
    return { ...noon, exact: false,
             rashiChanges: start.rashi !== end.rashi ? [start.rashi, end.rashi] : null,
             nakshatraChanges: start.nakshatra !== end.nakshatra ? [start.nakshatra, end.nakshatra] : null };
  }

  function newMoonJde(k) {
    const T = k / 1236.85;
    const jde = 2451550.09766 + 29.530588861 * k + 0.00015437 * T * T - 0.00000015 * T ** 3 + 0.00000000073 * T ** 4;
    const M = 2.5534 + 29.1053567 * k - 0.0000014 * T * T - 0.00000011 * T ** 3;
    const Mp = 201.5643 + 385.81693528 * k + 0.0107582 * T * T + 0.00001238 * T ** 3 - 0.000000058 * T ** 4;
    const F = 160.7108 + 390.67050284 * k - 0.0016118 * T * T - 0.00000227 * T ** 3 + 0.000000011 * T ** 4;
    const Om = 124.7746 - 1.56375588 * k + 0.0020672 * T * T + 0.00000215 * T ** 3;
    const E = 1 - 0.002516 * T - 0.0000074 * T * T;
    return jde - 0.4072 * sin(Mp) + 0.17241 * E * sin(M) + 0.01608 * sin(2 * Mp) + 0.01039 * sin(2 * F)
      + 0.00739 * E * sin(Mp - M) - 0.00514 * E * sin(Mp + M) + 0.00208 * E * E * sin(2 * M) - 0.00111 * sin(Mp - 2 * F)
      - 0.00057 * sin(Mp + 2 * F) + 0.00056 * E * sin(2 * Mp + M) - 0.00042 * sin(3 * Mp) + 0.00042 * E * sin(M + 2 * F)
      + 0.00038 * E * sin(M - 2 * F) - 0.00024 * E * sin(2 * Mp - M) - 0.00017 * sin(Om);
  }

  function jdToDate(jd) {               // returns {y, m, d} for the calendar date of a JD
    const z = Math.floor(jd + 0.5), f = jd + 0.5 - z;
    const alpha = Math.floor((z - 1867216.25) / 36524.25);
    const a = z + 1 + alpha - Math.floor(alpha / 4), b = a + 1524;
    const c = Math.floor((b - 122.1) / 365.25), dd = Math.floor(365.25 * c), e = Math.floor((b - dd) / 30.6001);
    const day = b - dd - Math.floor(30.6001 * e) + f;
    const m = e < 14 ? e - 1 : e - 13;
    return { y: m > 2 ? c - 4716 : c - 4715, m, d: Math.floor(day) };
  }

  // Apparent solar longitude (Meeus ch. 25, low precision, about 0.01 degrees).
  function sunLongitude(jd) {
    const T = (jd - 2451545) / 36525;
    const L0 = 280.46646 + 36000.76983 * T, M = 357.52911 + 35999.05029 * T;
    const C = (1.914602 - 0.004817 * T) * sin(M) + (0.019993 - 0.000101 * T) * sin(2 * M) + 0.000289 * sin(3 * M);
    return norm(L0 + C - 0.00569 - 0.00478 * sin(125.04 - 1934.136 * T));
  }
  function sunReaches(target, jdGuess) {   // JD when the sun's longitude equals target, near jdGuess
    let jd = jdGuess;
    for (let i = 0; i < 6; i++) jd += ((target - sunLongitude(jd) + 540) % 360 - 180) / 0.9856;
    return jd;
  }
  const DT = 69 / 86400;                    // TT minus UT, close enough for calendar dates
  const cnDay = (jd) => Math.floor(jd - DT + 8 / 24 + 0.5);   // calendar day number in China time (UTC+8)
  const kNear = (jd) => Math.round((jd - 2451550.09766) / 29.530588861);

  // Lunar New Year by the Chinese calendar rules: month 11 holds the winter solstice; in a 13-month
  // year the first month without a principal solar term is the leap month.
  function lunarNewYear(year) {
    const sol1 = sunReaches(270, julianDay(year - 1, 12, 21, 12)), sol2 = sunReaches(270, julianDay(year, 12, 21, 12));
    const month11 = (sol) => { let k = kNear(sol); while (cnDay(newMoonJde(k)) > cnDay(sol)) k--;
                               while (cnDay(newMoonJde(k + 1)) <= cnDay(sol)) k++; return k; };
    const kA = month11(sol1), kB = month11(sol2);
    let offset = 2;
    if (kB - kA === 13) {
      const hasTerm = (k) => { const a = cnDay(newMoonJde(k)), b = cnDay(newMoonJde(k + 1));
        const la = sunLongitude(a + 0.5 - 8 / 24 + DT), lb = sunLongitude(b + 0.5 - 8 / 24 + DT);   // China midnights
        return Math.floor(la / 30) !== Math.floor(lb / 30); };
      for (let i = 1; i <= 2; i++) if (!hasTerm(kA + i)) { offset = 3; break; }
    }
    const dt = jdToDate(cnDay(newMoonJde(kA + offset)));
    return `${dt.y}-${String(dt.m).padStart(2, '0')}-${String(dt.d).padStart(2, '0')}`;
  }

  const ANIMALS = ['Rat', 'Ox', 'Tiger', 'Rabbit', 'Dragon', 'Snake', 'Horse', 'Goat', 'Monkey', 'Rooster', 'Dog', 'Pig'];
  const STEM_ELEMENT = ['Wood', 'Wood', 'Fire', 'Fire', 'Earth', 'Earth', 'Metal', 'Metal', 'Water', 'Water'];

  function chineseZodiac(dob) {
    let y = Number(dob.slice(0, 4));
    const lny = lunarNewYear(y);
    const beforeNewYear = Boolean(lny && dob < lny);
    if (beforeNewYear) y -= 1;
    const i = ((y - 4) % 12 + 12) % 12, s = ((y - 4) % 10 + 10) % 10;
    return { animal: ANIMALS[i], element: STEM_ELEMENT[s], yang: s % 2 === 0, year: y, lunarNewYear: lny, beforeNewYear };
  }

  // Rahu Kalam with the common fixed day of 6:00 to 18:00 split into eight 90-minute parts.
  const RAHU_PART = [8, 2, 7, 5, 6, 4, 3];   // Sunday..Saturday
  const DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
  function rahuKalam(date, time) {
    const [y, m, d] = date.split('-').map(Number);
    const wd = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
    const startMin = 360 + (RAHU_PART[wd] - 1) * 90, endMin = startMin + 90;
    const fmt = (min) => { const h = Math.floor(min / 60), mm = min % 60, h12 = ((h + 11) % 12) + 1;
      return `${h12}:${String(mm).padStart(2, '0')} ${h < 12 ? 'am' : 'pm'}`; };
    let inside = null;
    if (time) { const [hh, mm] = time.split(':').map(Number); const t = hh * 60 + mm; inside = t >= startMin && t < endMin; }
    return { weekday: DAYS[wd], start: fmt(startMin), end: fmt(endMin), startMin, endMin, inside };
  }

  const api = { julianDay, moonLongitude, sunLongitude, lahiri, vedicMoonAt, vedicMoon, newMoonJde, jdToDate, lunarNewYear, chineseZodiac,
                rahuKalam, RASHI, NAKSHATRA };
  root.Astro = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
