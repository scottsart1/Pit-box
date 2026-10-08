/* Observed comparisons only: every plotted value comes from the selected,
   accepted lap cohort or the server's sector result. No setup-causality model. */
const finite = value => typeof value === 'number' && Number.isFinite(value);
const el = (tag, text = '', cls = '') => {
  const node = document.createElement(tag); node.textContent = String(text);
  if (cls) node.className = cls;
  return node;
};
const signed = value => `${value > 0 ? '+' : value < 0 ? '−' : ''}${Math.abs(value).toFixed(3)} s`;
const time = seconds => { const ms = Math.round(seconds * 1000); return `${Math.floor(ms / 60000)}:${String(Math.floor(ms % 60000 / 1000)).padStart(2, '0')}.${String(ms % 1000).padStart(3, '0')}`; };

export function comparisonCohorts(result, selections) {
  return Object.fromEntries(['a', 'b'].map(side => {
    const item = selections[side], matched = result.mode === 'setup';
    const expected = matched ? (result.pairs || []).length : result.clean_lap_counts?.[side];
    const ids = new Set(matched ? (result.pairs || []).map(pair => pair[`${side}_lap_id`])
      : result.selections?.[side]?.lap_ids || item?.laps?.map(lap => lap.id));
    const excluded = new Set((result.excluded?.[side] || []).map(lap => lap.lap_id));
    const laps = (item?.laps || []).filter(lap => ids.has(lap.id) && !excluded.has(lap.id)
      && finite(lap.lap_time_ms) && lap.lap_time_ms > 0);
    // If the loaded report cannot reproduce the exact accepted cohort, do
    // not quietly plot a different set of laps under the server's conclusion.
    return [side, finite(expected) && expected === laps.length ? laps : null];
  }));
}

export function comparisonVisuals(result, selections = {}) {
  const wrapper = el('section', '', 'engineering-visuals');
  wrapper.setAttribute('aria-label', 'A/B comparison charts');
  const matched = result.mode === 'setup', pairCount = (result.pairs || []).length;
  const counts = matched ? { a: pairCount, b: pairCount } : result.clean_lap_counts || {};
  const quality = el('p', '', 'engineering-chart-quality');
  quality.textContent = `${matched ? `${pairCount} matched pairs` : 'Selected clean laps'} · A ${counts.a ?? '—'} / B ${counts.b ?? '—'} used · excluded A ${(result.excluded?.a || []).length} / B ${(result.excluded?.b || []).length}.`;
  wrapper.append(quality, el('p', `${result.enough_evidence ? 'Observed difference' : 'Preliminary evidence'} · ${matched ? 'Conditions matched within the stated limits.' : 'Conditions are not matched.'} This does not prove a setup or tyre caused the difference.${result.cross_session ? ' Different sessions; game and car equivalence are unverified.' : ''}`, 'small muted'));

  const sectors = el('figure', '', 'engineering-chart');
  sectors.append(el('figcaption', 'Where the time changed', 'engineering-chart-title'),
    el('p', 'Sector delta · B minus A · negative means B was quicker', 'small muted'));
  const values = Array.from({ length: 3 }, (_, i) => result.sector_deltas_s?.[i]);
  const limit = Math.max(.001, ...values.filter(finite).map(Math.abs));
  const legend = el('div', '', 'engineering-delta-legend');
  legend.append(el('span', '← B quicker'), el('span', '0'), el('span', 'B slower →')); sectors.append(legend);
  values.forEach((value, i) => {
    const row = el('div', '', 'engineering-sector-row');
    row.dataset.sector = String(i + 1);
    const track = el('div', '', 'engineering-delta-track'); track.setAttribute('aria-hidden', 'true');
    if (finite(value)) {
      const bar = el('span', '', 'engineering-sector-bar');
      bar.dataset.direction = value < 0 ? 'quicker' : value > 0 ? 'slower' : 'equal';
      bar.style.width = `${Math.abs(value) / limit * 50}%`;
      bar.style.left = `${value < 0 ? 50 - Math.abs(value) / limit * 50 : 50}%`;
      track.append(bar);
    }
    row.append(el('span', `S${i + 1}`), track, el('span', finite(value) ? signed(value) : 'Unavailable', 'engineering-chart-value'));
    sectors.append(row);
  });
  if (result.sector_lap_counts) sectors.append(el('p', `Complete-sector laps: A ${result.sector_lap_counts.a} / B ${result.sector_lap_counts.b}. This cohort may differ from overall pace.`, 'small muted'));
  sectors.append(el('p', `Bar scale: ${signed(-limit)} to ${signed(limit)}. Sector medians need not sum to the lap median.`, 'small muted'));
  wrapper.append(sectors);

  const distribution = el('figure', '', 'engineering-chart');
  distribution.append(el('figcaption', `${matched ? 'Matched' : 'Clean'}-lap pace and consistency`, 'engineering-chart-title'),
    el('p', 'Dots show recorded lap times; ×N marks identical times. Both runs share the same scale; a shorter range means less variation.', 'small muted'));
  const cohorts = comparisonCohorts(result, selections);
  const laps = ['a', 'b'].flatMap(side => cohorts[side] || []);
  if (cohorts.a === null || cohorts.b === null) {
    distribution.append(el('p', 'Lap distribution unavailable: the loaded laps do not reproduce the accepted comparison cohort.', 'small muted'));
  } else if (!laps.length) {
    distribution.append(el('p', 'No accepted laps are available to plot.', 'small muted'));
  } else {
    const seconds = laps.map(lap => lap.lap_time_ms / 1000), low = Math.min(...seconds), high = Math.max(...seconds);
    const pad = Math.max((high - low) * .1, .05), minimum = Math.max(0, low - pad), maximum = high + pad;
    const position = value => (value - minimum) / (maximum - minimum) * 100;
    for (const side of ['a', 'b']) {
      const points = cohorts[side], row = el('div', '', 'engineering-pace-row'); row.dataset.side = side;
      row.append(el('strong', `${side.toUpperCase()} · ${points.length}`));
      const track = el('div', '', 'engineering-pace-track');
      const times = points.map(lap => lap.lap_time_ms / 1000).sort((a, b) => a - b);
      if (times.length) {
        const band = el('span', '', 'engineering-pace-range');
        band.style.left = `${position(times[0])}%`; band.style.width = `${position(times.at(-1)) - position(times[0])}%`;
        band.setAttribute('aria-hidden', 'true'); track.append(band);
        const grouped = new Map();
        for (const lap of points) { const group = grouped.get(lap.lap_time_ms) || []; group.push(lap); grouped.set(lap.lap_time_ms, group); }
        [...grouped.values()].forEach((group, i) => {
          const lap = group[0];
          const dot = el('span', '', 'engineering-pace-dot');
          dot.dataset.count = String(group.length);
          dot.style.left = `${position(lap.lap_time_ms / 1000)}%`; dot.style.top = `${12 + (i % 3) * 8}px`;
          dot.title = `${side.toUpperCase()} lap${group.length === 1 ? '' : 's'} ${group.map(l => l.lap_num).join(', ')}: ${time(lap.lap_time_ms / 1000)}`;
          if (group.length > 1) dot.append(el('span', `×${group.length}`, 'engineering-pace-count'));
          dot.setAttribute('role', 'img'); dot.setAttribute('aria-label', dot.title); track.append(dot);
        });
      }
      row.append(track); distribution.append(row);
      distribution.append(el('p', times.length ? `${side.toUpperCase()} range ${time(times[0])}–${time(times.at(-1))} · spread ${(times.at(-1) - times[0]).toFixed(3)} s` : `${side.toUpperCase()}: no accepted laps.`, 'small muted'));
    }
    const axis = el('div', '', 'engineering-pace-axis'); axis.append(el('span', `${time(minimum)} · quicker`), el('span', `${time(maximum)} · slower`)); distribution.append(axis);
  }
  wrapper.append(distribution);
  return wrapper;
}
