/** Small declarative charts: supplied numbers only, no scripts or remote resources. */
import { responseTable, type RichTextOptions } from './rich-text';

export interface ResponseChart { type: 'bar' | 'line'; title: string; labels: string[]; series: { name: string; values: number[] }[] }
export function parseChart(source: string): ResponseChart | null {
  if (source.length > 32000) return null;
  try {
    const value = JSON.parse(source);
    if (!value || !['bar', 'line'].includes(value.type) || !Array.isArray(value.labels) || !value.labels.length || value.labels.length > 60
      || !value.labels.every((label: unknown) => typeof label === 'string' && label.length <= 120)) return null;
    const series = Array.isArray(value.series) ? value.series : [{ name: 'Value', values: value.values }];
    if (!series.length || series.length > 4 || !series.every((row: { name?: unknown; values?: unknown }) => row && typeof row.name === 'string'
      && row.name.length <= 120 && Array.isArray(row.values) && row.values.length === value.labels.length
      && row.values.every((n: unknown) => typeof n === 'number' && Number.isFinite(n) && Math.abs(n) <= 1e12))) return null;
    return { type: value.type, title: typeof value.title === 'string' ? value.title.slice(0, 200) : 'Chart', labels: value.labels, series };
  } catch { return null; }
}
const NS = 'http://www.w3.org/2000/svg';
function svg<K extends keyof SVGElementTagNameMap>(tag: K, attributes: Record<string, string | number>, text?: string): SVGElementTagNameMap[K] {
  const element = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
  if (text !== undefined) element.textContent = text;
  return element;
}
export function chartCard(source: string, options: RichTextOptions): HTMLElement {
  const chart = parseChart(source);
  if (!chart) {
    const fallback = document.createElement('div'); fallback.className = 'chart-fallback';
    const note = document.createElement('p'); note.textContent = 'Chart not rendered: use type "bar" or "line", labels, and numeric values (or named series). Original data is below.';
    fallback.append(note, options.codeBlock(source, 'json')); return fallback;
  }
  const card = document.createElement('figure'); card.className = 'response-chart';
  const title = document.createElement('figcaption'); title.textContent = chart.title;
  const plot = svg('svg', { viewBox: '0 0 640 280', role: 'group', 'aria-label': chart.title, class: 'chart-plot' });
  const values = chart.series.flatMap(series => series.values), low = Math.min(0, ...values), high = Math.max(0, ...values);
  const range = high - low || 1, x = (index: number) => 64 + (index + .5) * 552 / chart.labels.length;
  const y = (value: number) => 232 - (value - low) / range * 208;
  for (let tick = 0; tick <= 4; tick++) {
    const value = low + range * tick / 4, height = y(value);
    plot.append(svg('line', { x1: 60, x2: 620, y1: height, y2: height, class: 'chart-grid' }),
      svg('text', { x: 52, y: height + 4, 'text-anchor': 'end', class: 'chart-tick' }, Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(value)));
  }
  const detail = document.createElement('p'); detail.className = 'chart-detail'; detail.setAttribute('aria-live', 'polite');
  detail.textContent = 'Hover or focus a data point for its value. Use arrow keys between points, or view the full data table below.';
  const points: SVGElement[] = [];
  chart.labels.forEach((label, index) => {
    if (index % Math.max(1, Math.ceil(chart.labels.length / 8)) === 0) plot.append(svg('text', { x: x(index), y: 258, 'text-anchor': 'middle', class: 'chart-tick' }, label.length > 14 ? label.slice(0, 12) + '…' : label));
  });
  chart.series.forEach((series, seriesIndex) => {
    const group = svg('g', { class: `chart-series chart-series-${seriesIndex}` });
    if (chart.type === 'line') group.append(svg('polyline', { points: series.values.map((value, index) => `${x(index)},${y(value)}`).join(' '), class: 'chart-line' }));
    series.values.forEach((value, index) => {
      const width = Math.min(52, 440 / chart.labels.length / chart.series.length);
      const point = chart.type === 'line' ? svg('circle', { cx: x(index), cy: y(value), r: 5 })
        : svg('rect', { x: x(index) + (seriesIndex - chart.series.length / 2) * width, y: Math.min(y(0), y(value)), width: Math.max(1, width - 2), height: Math.max(1, Math.abs(y(value) - y(0))), rx: 2 });
      const label = `${chart.labels[index]} · ${series.name}: ${value.toLocaleString()}`;
      point.setAttribute('tabindex', points.length ? '-1' : '0'); point.setAttribute('role', 'img'); point.setAttribute('aria-label', label);
      points.push(point);
      point.append(svg('title', {}, label));
      point.addEventListener('focus', () => {
        detail.textContent = label;
        for (const item of points) item.setAttribute('tabindex', item === point ? '0' : '-1');
      });
      point.addEventListener('keydown', event => {
        const key = (event as KeyboardEvent).key, index = points.indexOf(point);
        const target = key === 'Home' ? 0 : key === 'End' ? points.length - 1 : ['ArrowRight', 'ArrowDown'].includes(key)
          ? (index + 1) % points.length : ['ArrowLeft', 'ArrowUp'].includes(key) ? (index + points.length - 1) % points.length : -1;
        if (target >= 0) {
          event.preventDefault();
          const next = points[target]!;
          for (const item of points) item.setAttribute('tabindex', item === next ? '0' : '-1');
          detail.textContent = next.getAttribute('aria-label') ?? '';
          next.focus();
        }
      });
      point.addEventListener('mouseenter', () => { detail.textContent = label; });
      group.append(point);
    });
    plot.append(group);
  });
  const legend = document.createElement('ul'); legend.className = 'chart-legend'; legend.setAttribute('aria-label', 'Chart series');
  chart.series.forEach((series, index) => {
    const item = document.createElement('li'); item.className = `chart-series-${index}`; item.textContent = series.name; legend.append(item);
  });
  const data = document.createElement('details'); data.className = 'chart-data';
  const summary = document.createElement('summary'); summary.textContent = 'View and copy data';
  data.append(summary, responseTable({ header: ['Label', ...chart.series.map(row => row.name)], align: ['', ...chart.series.map(() => 'right' as const)],
    rows: chart.labels.map((label, index) => [label, ...chart.series.map(row => String(row.values[index]))]), end: 0 }, options, true));
  card.append(title, legend, plot, detail, data); return card;
}
