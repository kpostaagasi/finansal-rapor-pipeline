#!/usr/bin/env python3
"""Emtia futures egrileri + ABD faiz egrisi -> tek dosya HTML rapor.
Kaynaklar: Yahoo Finance (vadeli kontratlar), US Treasury (getiri egrisi).
Cikti: emtia_futures.html (ayni dizine).
"""
import datetime, json, os, ssl, sys, urllib.error, urllib.request

import certifi

CTX = ssl.create_default_context(cafile=certifi.where())

def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, context=CTX, timeout=20).read().decode()

MONTH_CODE = {1:"F",2:"G",3:"H",4:"J",5:"K",6:"M",7:"N",8:"Q",9:"U",10:"V",11:"X",12:"Z"}
TR_AY = {1:"Oca",2:"Şub",3:"Mar",4:"Nis",5:"May",6:"Haz",7:"Tem",8:"Ağu",9:"Eyl",10:"Eki",11:"Kas",12:"Ara"}

COMMODITIES = [
    {"key":"petrol",   "title":"WTI Ham Petrol",   "unit":"$/varil", "root":"CL",  "suffix":"NYM", "cycle":list(range(1,13)), "count":14, "dec":2},
    {"key":"altin",    "title":"Altın",             "unit":"$/ons",   "root":"GC",  "suffix":"CMX", "cycle":[2,4,6,8,10,12],   "count":8,  "dec":1},
    {"key":"gumus",    "title":"Gümüş",             "unit":"$/ons",   "root":"SI",  "suffix":"CMX", "cycle":[3,5,7,9,12],      "count":7,  "dec":2},
    {"key":"platin",   "title":"Platin",            "unit":"$/ons",   "root":"PL",  "suffix":"NYM", "cycle":[1,4,7,10],        "count":6,  "dec":1},
    {"key":"bakir",    "title":"Bakır",             "unit":"$/lb",    "root":"HG",  "suffix":"CMX", "cycle":[3,5,7,9,12],      "count":7,  "dec":3},
    {"key":"aluminyum","title":"Alüminyum",         "unit":"$/ton",   "root":"ALI", "suffix":"CMX", "cycle":list(range(1,13)), "count":10, "dec":2},
]

def gen_contracts(root, suffix, cycle, count):
    today = datetime.date.today()
    y, m = today.year, today.month
    out = []
    for i in range(1, 40):
        mm = m + i
        yy = y + (mm - 1) // 12
        mo = (mm - 1) % 12 + 1
        if mo in cycle:
            out.append((f"{root}{MONTH_CODE[mo]}{yy%100:02d}.{suffix}", yy, mo))
        if len(out) >= count:
            break
    return out

def fetch_quote(sym, now_ts=None):
    try:
        d = json.loads(http_get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1d&interval=1d"))
        meta = d["chart"]["result"][0]["meta"]
        px, ts = meta.get("regularMarketPrice"), meta.get("regularMarketTime", 0)
        if px is None:
            return None
        now_ts = now_ts if now_ts is not None else datetime.datetime.now().timestamp()
        age_days = max(0.0, (now_ts - ts) / 86400) if ts else None
        return {
            "value": px,
            "timestamp": ts,
            "age_days": age_days,
            "stale": age_days is None or age_days > 5,
        }
    except Exception:
        return None

def fetch_treasury():
    yr = datetime.date.today().year
    url = (f"https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
           f"daily-treasury-rates.csv/{yr}/all?type=daily_treasury_yield_curve&field_tdr_date_value={yr}&page&_format=csv")
    lines = [l for l in http_get(url).splitlines() if l.strip()]
    hdr = [h.strip().strip('"') for h in lines[0].split(",")]
    row = lines[1].split(",")
    want = [("1 Mo","1A"),("2 Mo","2A"),("3 Mo","3A"),("6 Mo","6A"),("1 Yr","1Y"),("2 Yr","2Y"),
            ("3 Yr","3Y"),("5 Yr","5Y"),("7 Yr","7Y"),("10 Yr","10Y"),("20 Yr","20Y"),("30 Yr","30Y")]
    pts, date = [], row[0]
    for col, lab in want:
        if col in hdr:
            try:
                pts.append({"label": lab, "value": float(row[hdr.index(col)])})
            except ValueError:
                pass
    return {"date": date, "points": pts}

def build_data():
    data = {
        "generated": datetime.datetime.now().strftime("%d.%m.%Y %H:%M"),
        "curves": [],
        "rates": None,
        "warnings": [],
    }
    total_requested = 0
    total_found = 0
    all_missing = []
    all_stale = []
    data_dates = []
    for c in COMMODITIES:
        contracts = gen_contracts(c["root"], c["suffix"], c["cycle"], c["count"])
        total_requested += len(contracts)
        pts, quote_times, excluded_stale_quotes = [], [], []
        found_symbols = set()
        for sym, yy, mo in contracts:
            quote = fetch_quote(sym)
            if quote is not None:
                short_sym = sym.split(".")[0]
                found_symbols.add(short_sym)
                if quote.get("stale"):
                    stale_quote = {
                        "symbol": short_sym,
                        "age_days": round(quote.get("age_days") or 0, 1),
                    }
                    excluded_stale_quotes.append(stale_quote)
                    all_stale.append({"commodity": c["title"], **stale_quote})
                    continue
                if quote["timestamp"]:
                    quote_times.append(quote["timestamp"])
                    data_dates.append(datetime.datetime.fromtimestamp(
                        quote["timestamp"], tz=datetime.timezone.utc
                    ).date())
                pts.append({
                    "label": f"{TR_AY[mo]} {yy%100:02d}",
                    "value": round(quote["value"], 4),
                    "sym": short_sym,
                    "quote_time": quote["timestamp"],
                })
        requested_symbols = [sym.split(".")[0] for sym, _, _ in contracts]
        missing_symbols = [sym for sym in requested_symbols if sym not in found_symbols]
        total_found += len(pts)
        all_missing.extend(missing_symbols)
        coverage = {
            "candidate_count": len(contracts),
            "requested_count": len(contracts) - len(excluded_stale_quotes),
            "found_count": len(pts),
            "missing_symbols": missing_symbols,
            "excluded_stale_quotes": excluded_stale_quotes,
            "quote_time_min": min(quote_times) if quote_times else None,
            "quote_time_max": max(quote_times) if quote_times else None,
        }
        if missing_symbols:
            data["warnings"].append(f"{c['title']}: {len(pts)}/{len(contracts)}")
        if excluded_stale_quotes:
            data["warnings"].append(
                f"{c['title']}: {len(excluded_stale_quotes)} vade likidite filtresinde"
            )
        if len(pts) >= 3:
            data["curves"].append({
                "key": c["key"], "title": c["title"], "unit": c["unit"],
                "dec": c["dec"], "points": pts, **coverage,
            })
        else:
            print(f"UYARI: {c['title']} icin yeterli kontrat yok ({len(pts)})", file=sys.stderr)
    try:
        data["rates"] = fetch_treasury()
        try:
            data_dates.append(datetime.datetime.strptime(
                data["rates"]["date"].strip('"'), "%m/%d/%Y"
            ).date())
        except (KeyError, TypeError, ValueError):
            pass
    except Exception as e:
        print(f"UYARI: hazine getirileri alinamadi: {e}", file=sys.stderr)
    data["report_meta"] = {
        "last_successful_run": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "data_end_date": max(data_dates).isoformat() if data_dates else None,
        "candidate_count": total_requested,
        "expected_count": total_requested - len(all_stale),
        "found_count": total_found,
        "missing": all_missing,
        "excluded_stale_quotes": all_stale,
        "treasury_expected_count": 12,
        "treasury_found_count": len(data.get("rates", {}).get("points", [])),
        "count_label": "kontrat",
        "source": "Yahoo Finance + ABD Hazinesi",
    }
    return data

HTML = r"""<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Emtia Futures Eğrileri</title>
<style>
  .viz-root {
    color-scheme: light;
    --page:#f9f9f7; --surface-1:#fcfcfb; --ink-1:#0b0b0b; --ink-2:#52514e; --muted:#898781;
    --grid:#e1e0d9; --baseline:#c3c2b7; --border:rgba(11,11,11,0.10); --series-1:#2a78d6; --neg:#e34948;
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    background: var(--page); color: var(--ink-1); margin:0; padding:24px 16px; min-height:100vh;
  }
  @media (prefers-color-scheme: dark) {
    :root:where(:not([data-theme="light"])) .viz-root {
      color-scheme: dark;
      --page:#0d0d0d; --surface-1:#1a1a19; --ink-1:#ffffff; --ink-2:#c3c2b7; --muted:#898781;
      --grid:#2c2c2a; --baseline:#383835; --border:rgba(255,255,255,0.10); --series-1:#3987e5; --neg:#e66767;
    }
  }
  :root[data-theme="dark"] .viz-root {
    color-scheme: dark;
    --page:#0d0d0d; --surface-1:#1a1a19; --ink-1:#ffffff; --ink-2:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --baseline:#383835; --border:rgba(255,255,255,0.10); --series-1:#3987e5; --neg:#e66767;
  }
  .wrap { max-width: 1100px; margin: 0 auto; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .sub { color: var(--ink-2); font-size: 13px; margin-bottom: 20px; }
  .warnings { display:none; color:#8a3b00; background:#fff3e8; border:1px solid #f2c49f; border-radius:8px; padding:9px 12px; margin:0 0 16px; font-size:12px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(440px, 1fr)); gap: 16px; }
  @media (max-width: 520px) { .grid { grid-template-columns: 1fr; } }
  .card { background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px; padding: 16px; }
  .card.wide { grid-column: 1 / -1; }
  .card h2 { font-size: 15px; margin: 0; display:flex; align-items:baseline; gap:8px; flex-wrap:wrap; }
  .card h2 .unit { color: var(--muted); font-size: 12px; font-weight: 400; }
  .badge { font-size: 11px; color: var(--ink-2); border: 1px solid var(--border); border-radius: 99px; padding: 1px 8px; font-weight: 400; }
  .chart { position: relative; margin-top: 8px; }
  .spreads { font-size: 12px; color: var(--ink-2); margin-top: 8px; font-variant-numeric: tabular-nums; }
  .coverage { font-size: 11px; color: var(--muted); margin-top: 5px; }
  .chart svg { display: block; width: 100%; }
  .tip { position: absolute; pointer-events: none; background: var(--surface-1); border: 1px solid var(--border);
         border-radius: 6px; padding: 5px 8px; font-size: 12px; box-shadow: 0 2px 8px rgba(0,0,0,.12);
         display: none; white-space: nowrap; z-index: 2; }
  .tip b { font-variant-numeric: tabular-nums; }
  details { margin-top: 8px; }
  summary { font-size: 12px; color: var(--ink-2); cursor: pointer; }
  table { border-collapse: collapse; font-size: 12px; margin-top: 6px; width: 100%; }
  th, td { text-align: right; padding: 3px 8px; border-bottom: 1px solid var(--grid); font-variant-numeric: tabular-nums; }
  th:first-child, td:first-child { text-align: left; }
  th { color: var(--muted); font-weight: 500; }
  .src { color: var(--muted); font-size: 11px; margin-top: 20px; }
</style>
</head>
<body>
<div class="viz-root"><div class="wrap">
  <h1>Emtia Futures Eğrileri</h1>
  <div class="sub">Son güncelleme: <span id="gen"></span> (İstanbul) · Kaynak: Yahoo Finance gecikmeli/son piyasa fiyatları &amp; ABD Hazinesi</div>
  <div class="warnings" id="warnings"></div>
  <div class="grid" id="grid"></div>
  <div class="src">Emtia değerleri resmî borsa settlement verisi değildir. Yahoo Finance'ın son erişilebilir piyasa değerleri kullanılır; beş günden eski fiyatlar eğriden çıkarılır ve likidite filtresinde açıklanır.</div>
</div></div>
<script>
const REPORT_META = __REPORT_META__;
const DATA = __DATA__;
const css = v => getComputedStyle(document.querySelector('.viz-root')).getPropertyValue(v).trim();
const fmt = (v, d) => v.toLocaleString('tr-TR', {minimumFractionDigits: d, maximumFractionDigits: d});
const fmtSign = n => (n > 0 ? '+' : '') + n.toLocaleString('tr-TR');
const fmtSignD = (v, d) => (v > 0 ? '+' : '') + fmt(v, d);

function niceTicks(min, max, n) {
  const span = max - min || 1, step0 = span / n, mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const step = [1,2,2.5,5,10].map(m => m*mag).find(s => span/s <= n) || 10*mag;
  const lo = Math.floor(min/step)*step, ticks = [];
  for (let v = lo; v <= max + step*0.001; v += step) if (v >= min - step*0.001) ticks.push(v);
  return ticks;
}

function drawChart(host, curve) {
  const box = host.querySelector('.chart'); box.innerHTML = '';
  const W = Math.max(box.clientWidth, 320), H = 260, padL = 56, padR = 30, padT = 14, padB = 34;
  const pts = curve.points, vals = pts.map(p => p.value);
  let vmin = Math.min(...vals), vmax = Math.max(...vals);
  const pad = (vmax - vmin || vmax*0.05 || 1) * 0.12; vmin -= pad; vmax += pad;
  const ticks = niceTicks(vmin, vmax, 5);
  vmin = Math.min(vmin, ticks[0]); vmax = Math.max(vmax, ticks[ticks.length-1]);
  const X = i => padL + (pts.length === 1 ? 0.5 : i/(pts.length-1)) * (W - padL - padR);
  const Y = v => padT + (1 - (v - vmin)/(vmax - vmin)) * (H - padT - padB);
  const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`); svg.setAttribute('width', W); svg.setAttribute('height', H);
  let g = '';
  const tickDec = (ticks[1]-ticks[0]) < 1 ? (ticks[1]-ticks[0]) < 0.05 ? 3 : 2 : ((ticks[1]-ticks[0]) < 10 && curve.dec > 0 ? 1 : 0);
  for (const t of ticks) {
    g += `<line x1="${padL}" x2="${W-padR}" y1="${Y(t)}" y2="${Y(t)}" stroke="${css('--grid')}" stroke-width="1"/>`;
    g += `<text x="${padL-8}" y="${Y(t)+4}" text-anchor="end" font-size="11" fill="${css('--muted')}" style="font-variant-numeric:tabular-nums">${fmt(t, tickDec)}</text>`;
  }
  g += `<line x1="${padL}" x2="${W-padR}" y1="${H-padB}" y2="${H-padB}" stroke="${css('--baseline')}" stroke-width="1"/>`;
  const every = Math.ceil(pts.length / Math.floor((W - padL - padR) / 58));
  pts.forEach((p, i) => {
    if (i % every === 0 || i === pts.length-1)
      g += `<text x="${X(i)}" y="${H-padB+16}" text-anchor="middle" font-size="11" fill="${css('--muted')}">${p.label}</text>`;
  });
  const path = pts.map((p,i) => `${i?'L':'M'}${X(i)},${Y(p.value)}`).join('');
  g += `<path d="${path}" fill="none" stroke="${css('--series-1')}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
  pts.forEach((p,i) => {
    g += `<circle cx="${X(i)}" cy="${Y(p.value)}" r="6" fill="${css('--surface-1')}"/>`;
    g += `<circle cx="${X(i)}" cy="${Y(p.value)}" r="4" fill="${css('--series-1')}"/>`;
  });
  const lastI = pts.length - 1;
  g += `<text x="${X(0)}" y="${Y(pts[0].value)-12}" text-anchor="start" font-size="11" font-weight="600" fill="${css('--ink-2')}" style="font-variant-numeric:tabular-nums">${fmt(pts[0].value, curve.dec)}</text>`;
  g += `<text x="${X(lastI)}" y="${Y(pts[lastI].value)-12}" text-anchor="end" font-size="11" font-weight="600" fill="${css('--ink-2')}" style="font-variant-numeric:tabular-nums">${fmt(pts[lastI].value, curve.dec)}</text>`;
  g += `<line id="xh" y1="${padT}" y2="${H-padB}" stroke="${css('--baseline')}" stroke-width="1" visibility="hidden"/>`;
  svg.innerHTML = g;
  box.appendChild(svg);
  const tip = document.createElement('div'); tip.className = 'tip'; box.appendChild(tip);
  const xh = svg.querySelector('#xh');
  svg.addEventListener('mousemove', e => {
    const r = svg.getBoundingClientRect(), sx = W / r.width;
    const mx = (e.clientX - r.left) * sx;
    let best = 0, bd = 1e9;
    pts.forEach((p,i) => { const d = Math.abs(X(i)-mx); if (d < bd) { bd = d; best = i; } });
    xh.setAttribute('x1', X(best)); xh.setAttribute('x2', X(best)); xh.setAttribute('visibility','visible');
    tip.style.display = 'block';
    tip.innerHTML = `${pts[best].sym ? pts[best].sym + ' · ' : ''}${pts[best].label}<br><b>${fmt(pts[best].value, curve.dec)}</b> ${curve.unit}`;
    const tx = Math.min(Math.max(X(best)/sx + 12, 0), r.width - tip.offsetWidth - 4);
    tip.style.left = tx + 'px';
    tip.style.top = (Y(pts[best].value)/ (H / r.height) - 40) + 'px';
  });
  svg.addEventListener('mouseleave', () => { tip.style.display = 'none'; xh.setAttribute('visibility','hidden'); });
}

function drawBars(host, curve) {
  const box = host.querySelector('.chart'); box.innerHTML = '';
  const W = Math.max(box.clientWidth, 320), H = 230, padL = 56, padR = 30, padT = 18, padB = 34;
  const pts = curve.points, vals = pts.map(p => p.value);
  let vmin = Math.min(0, ...vals), vmax = Math.max(0, ...vals);
  const pad = (vmax - vmin || 1) * 0.15;
  if (vmax > 0) vmax += pad; if (vmin < 0) vmin -= pad;
  const ticks = niceTicks(vmin, vmax, 5);
  vmin = Math.min(vmin, ticks[0]); vmax = Math.max(vmax, ticks[ticks.length-1]);
  const slot = (W - padL - padR) / pts.length, bw = Math.min(24, slot - 8);
  const Xc = i => padL + slot * (i + 0.5);
  const Y = v => padT + (1 - (v - vmin)/(vmax - vmin)) * (H - padT - padB);
  const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`); svg.setAttribute('width', W); svg.setAttribute('height', H);
  let g = '';
  for (const t of ticks) {
    g += `<line x1="${padL}" x2="${W-padR}" y1="${Y(t)}" y2="${Y(t)}" stroke="${css('--grid')}" stroke-width="1"/>`;
    g += `<text x="${padL-8}" y="${Y(t)+4}" text-anchor="end" font-size="11" fill="${css('--muted')}" style="font-variant-numeric:tabular-nums">${fmt(t, 0)}</text>`;
  }
  const y0 = Y(0);
  g += `<line x1="${padL}" x2="${W-padR}" y1="${y0}" y2="${y0}" stroke="${css('--baseline')}" stroke-width="1"/>`;
  pts.forEach((p, i) => {
    const up = p.value >= 0, yv = Y(p.value), x = Xc(i) - bw/2;
    const r = Math.min(4, bw/2, Math.abs(y0 - yv));
    const d = up
      ? `M${x},${y0} L${x},${yv+r} Q${x},${yv} ${x+r},${yv} L${x+bw-r},${yv} Q${x+bw},${yv} ${x+bw},${yv+r} L${x+bw},${y0} Z`
      : `M${x},${y0} L${x},${yv-r} Q${x},${yv} ${x+r},${yv} L${x+bw-r},${yv} Q${x+bw},${yv} ${x+bw},${yv-r} L${x+bw},${y0} Z`;
    g += `<path d="${d}" fill="${up ? css('--series-1') : css('--neg')}"/>`;
    g += `<text x="${Xc(i)}" y="${H-padB+16}" text-anchor="middle" font-size="11" fill="${css('--muted')}">${p.label}</text>`;
  });
  const iMax = vals.indexOf(Math.max(...vals)), iMin = vals.indexOf(Math.min(...vals));
  [...new Set([iMax, iMin])].forEach(i => {
    const p = pts[i], up = p.value >= 0;
    g += `<text x="${Xc(i)}" y="${Y(p.value) + (up ? -7 : 15)}" text-anchor="middle" font-size="11" font-weight="600" fill="${css('--ink-2')}" style="font-variant-numeric:tabular-nums">${fmtSign(p.value)}</text>`;
  });
  svg.innerHTML = g;
  box.appendChild(svg);
  const tip = document.createElement('div'); tip.className = 'tip'; box.appendChild(tip);
  svg.addEventListener('mousemove', e => {
    const r = svg.getBoundingClientRect(), sx = W / r.width;
    const mx = (e.clientX - r.left) * sx;
    let best = 0, bd = 1e9;
    pts.forEach((p,i) => { const d = Math.abs(Xc(i)-mx); if (d < bd) { bd = d; best = i; } });
    tip.style.display = 'block';
    tip.innerHTML = `${pts[best].pair || pts[best].label}<br><b>${fmtSign(pts[best].value)}</b> ${curve.unit}`;
    const tx = Math.min(Math.max(Xc(best)/sx + 12, 0), r.width - tip.offsetWidth - 4);
    tip.style.left = tx + 'px';
    tip.style.top = (Y(pts[best].value) / (H / r.height) - 40) + 'px';
  });
  svg.addEventListener('mouseleave', () => { tip.style.display = 'none'; });
}

function card(curve, wide, badgeText) {
  const dif = curve.diffBp, difU = curve.diffUnit;
  const el = document.createElement('div');
  el.className = 'card' + (wide ? ' wide' : '');
  const coverage = curve.requested_count ? `${curve.found_count} güncel vade` : '';
  el.innerHTML = `<h2>${curve.title} <span class="unit">${curve.unit}</span>${badgeText ? ` <span class="badge">${badgeText}</span>` : ''}${coverage ? ` <span class="badge">${coverage}</span>` : ''}</h2>
    ${curve.missing_symbols && curve.missing_symbols.length ? `<div class="coverage">Kaynakta bulunamayan kontratlar: ${curve.missing_symbols.join(', ')}</div>` : ''}
    ${curve.excluded_stale_quotes && curve.excluded_stale_quotes.length ? `<div class="coverage">Filtrelenen seyrek vadeler: ${curve.excluded_stale_quotes.map(q => `${q.symbol} (${fmt(q.age_days, 1)} gün)`).join(', ')}</div>` : ''}
    <div class="chart"></div>${curve.spreads ? `<div class="spreads">${curve.spreads}</div>` : ''}
    <details><summary>Tablo</summary><table><thead><tr><th>Vade</th><th>${curve.unit}</th>${dif ? '<th>Δ bp</th>' : ''}${difU ? '<th>Δ</th><th>Δ %</th>' : ''}</tr></thead><tbody>
    ${curve.points.map((p, i) => {
      const prev = i ? curve.points[i-1].value : null;
      return `<tr><td>${p.tlabel || ((p.sym ? p.sym + ' — ' : '') + p.label)}</td><td>${fmt(p.value, curve.dec)}</td>${dif ? `<td>${i ? fmtSign(Math.round((p.value - prev) * 100)) : '—'}</td>` : ''}${difU ? `<td>${i ? fmtSignD(p.value - prev, curve.dec) : '—'}</td><td>${i ? fmtSignD((p.value/prev - 1) * 100, 1) : '—'}</td>` : ''}</tr>`;
    }).join('')}
    </tbody></table></details>`;
  document.getElementById('grid').appendChild(el);
  return el;
}

document.getElementById('gen').textContent = DATA.generated;
const missingWarnings = DATA.curves
  .filter(c => c.missing_symbols && c.missing_symbols.length)
  .map(c => `${c.title}: ${c.missing_symbols.join(', ')}`);
const filteredWarnings = DATA.curves
  .filter(c => c.excluded_stale_quotes && c.excluded_stale_quotes.length)
  .map(c => `${c.title}: ${c.excluded_stale_quotes.length} vade`);
if (missingWarnings.length || filteredWarnings.length) {
  const warnings = document.getElementById('warnings');
  const parts = [];
  if (missingWarnings.length) parts.push('Kaynakta bulunamayan kontratlar — ' + missingWarnings.join(' · '));
  if (filteredWarnings.length) parts.push('Likidite filtresi — ' + filteredWarnings.join(' · '));
  warnings.textContent = parts.join(' | ');
  warnings.style.display = 'block';
}
const rendered = [];
for (const c of DATA.curves) {
  const f = c.points[0], l = c.points[c.points.length-1];
  const complete = c.found_count === c.requested_count;
  const badge = !complete ? 'Kaynakta eksik' : (l.value > f.value * 1.002 ? 'Contango' : l.value < f.value * 0.998 ? 'Backwardation' : 'Yatay');
  c.diffUnit = true;
  c.spreads = `${f.label} → ${l.label}: <b>${fmtSignD(l.value - f.value, c.dec)} ${c.unit}</b> (${fmtSignD((l.value/f.value - 1) * 100, 1)}%)`;
  rendered.push([card(c, false, badge), c, drawChart]);
}
if (DATA.rates && DATA.rates.points.length) {
  const rp = DATA.rates.points;
  const get = l => { const p = rp.find(q => q.label === l); return p ? p.value : null; };
  const sp = (a, b) => { const va = get(a), vb = get(b); return va == null || vb == null ? null : Math.round((vb - va) * 100); };
  const spreads = [['2Y','10Y'],['3A','10Y'],['2Y','30Y'],['10Y','30Y']]
    .map(([a, b]) => { const s = sp(a, b); return s == null ? null : `${a}–${b}: <b>${fmtSign(s)} bp</b>`; })
    .filter(Boolean).join(' &nbsp;·&nbsp; ');
  const rc = {title: 'ABD Hazine Getiri Eğrisi', unit: '%', dec: 2, points: rp, diffBp: true, spreads};
  rendered.push([card(rc, true, DATA.rates.date), rc, drawChart]);
  const diffs = rp.slice(1).map((p, i) => ({label: p.label, pair: rp[i].label + ' → ' + p.label,
    tlabel: rp[i].label + ' → ' + p.label, value: Math.round((p.value - rp[i].value) * 100)}));
  const dc = {title: 'Vadeler Arası Faiz Diferansiyeli', unit: 'bp', dec: 0, points: diffs};
  rendered.push([card(dc, true, 'bir önceki vadeye göre fark'), dc, drawBars]);
}
const drawAll = () => rendered.forEach(([el, c, fn]) => fn(el, c));
drawAll();
let rt; window.addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(drawAll, 150); });
</script>
</body>
</html>
"""

if __name__ == "__main__":
    data = build_data()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "emtia_futures.html")
    with open(out, "w") as f:
        f.write(HTML.replace(
            "__REPORT_META__", json.dumps(data["report_meta"], ensure_ascii=False)
        ).replace("__DATA__", json.dumps(data, ensure_ascii=False)))
    print(f"OK: {out} — {len(data['curves'])} emtia, faiz: {'var' if data['rates'] else 'YOK'}")
