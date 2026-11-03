import { useRef, type CSSProperties } from 'react';
import { Card, CardBody } from '@patternfly/react-core';
import type { TrendPoint } from '../types/api';
import { SvgExportButtons } from './SvgExportButtons';

interface TrendChartProps {
  data: TrendPoint[];
  title?: string;
  style?: CSSProperties;
}

/**
 * A simple SVG trend line chart for violation counts over time.
 * Works without any charting library dependency.
 */
export function TrendChart({ data, title = 'Violation Trend', style }: TrendChartProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  if (data.length < 2) return null;

  const sorted = [...data].sort(
    (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
  );

  const maxViolations = Math.max(...sorted.map((d) => d.total_violations), 1);
  const maxFixable = Math.max(...sorted.map((d) => d.fixable), 1);
  const yMax = Math.max(maxViolations, maxFixable);

  const width = 600;
  const height = 200;
  const padX = 40;
  const padY = 24;
  const chartW = width - padX * 2;
  const chartH = height - padY * 2;

  const toX = (i: number) => padX + (i / (sorted.length - 1)) * chartW;
  const toY = (v: number) => padY + chartH - (v / yMax) * chartH;

  const violationLine = sorted.map((d, i) => `${i === 0 ? 'M' : 'L'}${toX(i)},${toY(d.total_violations)}`).join(' ');
  const fixableLine = sorted.map((d, i) => `${i === 0 ? 'M' : 'L'}${toX(i)},${toY(d.fixable)}`).join(' ');

  const gridLines = [0, 0.25, 0.5, 0.75, 1].map((frac) => {
    const val = Math.round(yMax * (1 - frac));
    const y = padY + frac * chartH;
    return { val, y };
  });

  const prepareTrendExport = (svg: SVGSVGElement) => {
    const source = svgRef.current;
    if (!source) return;
    const paths = source.querySelectorAll('path');
    const textColor = window.getComputedStyle(source).color;
    const violationColor = paths[0] ? window.getComputedStyle(paths[0]).stroke : '#c9190b';
    const fixableColor = paths[1] ? window.getComputedStyle(paths[1]).stroke : '#3e8635';
    const ns = 'http://www.w3.org/2000/svg';
    const addText = (text: string, x: number, y: number, fontSize: number, fill: string, weight?: string) => {
      const element = document.createElementNS(ns, 'text');
      element.setAttribute('x', String(x));
      element.setAttribute('y', String(y));
      element.setAttribute('font-size', String(fontSize));
      element.setAttribute('font-family', 'Arial, sans-serif');
      element.setAttribute('fill', fill);
      if (weight) element.setAttribute('font-weight', weight);
      element.textContent = text;
      svg.appendChild(element);
    };
    const addLegendLine = (x: number, color: string, dashed = false) => {
      const line = document.createElementNS(ns, 'line');
      line.setAttribute('x1', String(x));
      line.setAttribute('x2', String(x + 18));
      line.setAttribute('y1', String(height + 34));
      line.setAttribute('y2', String(height + 34));
      line.setAttribute('stroke', color);
      line.setAttribute('stroke-width', '2');
      if (dashed) line.setAttribute('stroke-dasharray', '4 2');
      svg.appendChild(line);
    };

    svg.setAttribute('viewBox', `0 -32 ${width} ${height + 76}`);
    addText(title, padX, -10, 15, textColor, '600');
    addLegendLine(padX, violationColor);
    addText('Violations', padX + 24, height + 38, 12, textColor);
    addLegendLine(padX + 112, fixableColor, true);
    addText('Fixable', padX + 136, height + 38, 12, textColor);
  };

  return (
    <Card style={style}>
      <CardBody>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 16, marginBottom: 8 }}>
          <h3 style={{ margin: 0 }}>{title}</h3>
          <SvgExportButtons svgRef={svgRef} filename={title} prepareSvg={prepareTrendExport} />
        </div>
        <svg ref={svgRef} viewBox={`0 0 ${width} ${height}`} style={{ width: '100%', maxHeight: 220 }}>
          {gridLines.map((g, i) => (
            <g key={i}>
              <line x1={padX} y1={g.y} x2={width - padX} y2={g.y} stroke="var(--pf-t--global--border--color--default)" strokeWidth={0.5} />
              <text x={padX - 6} y={g.y + 4} textAnchor="end" fontSize={10} fill="currentColor" opacity={0.5}>
                {g.val}
              </text>
            </g>
          ))}
          <path d={violationLine} fill="none" stroke="var(--pf-t--global--color--status--danger--default)" strokeWidth={2} />
          <path d={fixableLine} fill="none" stroke="var(--pf-t--global--color--status--success--default)" strokeWidth={2} strokeDasharray="4 2" />
          {sorted.map((d, i) => (
            <g key={d.scan_id}>
              <circle cx={toX(i)} cy={toY(d.total_violations)} r={3} fill="var(--pf-t--global--color--status--danger--default)" />
              <circle cx={toX(i)} cy={toY(d.fixable)} r={2.5} fill="var(--pf-t--global--color--status--success--default)" />
            </g>
          ))}
          <text x={width - padX} y={height - 4} textAnchor="end" fontSize={10} fill="currentColor" opacity={0.5}>
            {sorted.length} scans
          </text>
        </svg>
        <div style={{ display: 'flex', gap: 16, fontSize: 12, opacity: 0.7, marginTop: 4 }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{ width: 12, height: 2, background: 'var(--pf-t--global--color--status--danger--default)', display: 'inline-block' }} />
            Violations
          </span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{ width: 12, height: 2, background: 'var(--pf-t--global--color--status--success--default)', display: 'inline-block', borderTop: '1px dashed' }} />
            Fixable
          </span>
        </div>
      </CardBody>
    </Card>
  );
}
