import { useRef, useState } from 'react';
import { Button } from '@patternfly/react-core';

interface SvgExportButtonsProps {
  svgRef: { current: SVGSVGElement | null };
  filename: string;
  backgroundColor?: string;
  prepareSvg?: (svg: SVGSVGElement) => void;
}

type ExportFormat = 'png' | 'svg';

const STYLE_PROPERTIES = [
  'color',
  'fill',
  'fill-opacity',
  'fill-rule',
  'stroke',
  'stroke-dasharray',
  'stroke-dashoffset',
  'stroke-linecap',
  'stroke-linejoin',
  'stroke-opacity',
  'stroke-width',
  'font-family',
  'font-size',
  'font-style',
  'font-weight',
  'letter-spacing',
  'text-anchor',
  'text-decoration',
  'text-transform',
  'dominant-baseline',
  'opacity',
  'visibility',
  'display',
  'rx',
  'ry',
];

let logoDataUriPromise: Promise<string> | undefined;

function getLogoDataUri(): Promise<string | undefined> {
  if (!logoDataUriPromise) {
    const logoUrl = new URL('/apme.svg', window.location.origin);
    logoDataUriPromise = fetch(logoUrl)
      .then((response) => {
        if (!response.ok) throw new Error('Could not load the APME logo.');
        return response.text();
      })
      .then((svg) => `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`);
  }
  return logoDataUriPromise.catch(() => {
    logoDataUriPromise = undefined;
    return undefined;
  });
}

function findBackgroundColor(svg: SVGSVGElement, fallback = '#ffffff'): string {
  let element: Element | null = svg.parentElement;
  while (element) {
    const color = window.getComputedStyle(element).backgroundColor;
    if (color && color !== 'transparent' && color !== 'rgba(0, 0, 0, 0)') return color;
    element = element.parentElement;
  }
  return fallback;
}

function parseViewBox(svg: SVGSVGElement): [number, number, number, number] {
  const viewBox = svg.getAttribute('viewBox');
  if (viewBox) {
    const values = viewBox.trim().split(/[\s,]+/).map(Number);
    if (values.length === 4 && values.every(Number.isFinite) && values[2]! > 0 && values[3]! > 0) {
      return [values[0]!, values[1]!, values[2]!, values[3]!];
    }
  }
  const rect = svg.getBoundingClientRect();
  return [0, 0, Math.max(rect.width, 1), Math.max(rect.height, 1)];
}

async function createExportSvg(
  source: SVGSVGElement,
  backgroundColor: string,
  prepareSvg?: (svg: SVGSVGElement) => void,
): Promise<{ source: string; width: number; height: number }> {
  const clone = source.cloneNode(true) as SVGSVGElement;
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
  clone.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink');

  prepareSvg?.(clone);

  const [minX, minY, width, height] = parseViewBox(clone);
  const exportWidth = Math.max(width, 180);
  const headerHeight = 64;
  clone.setAttribute('viewBox', `${minX} ${minY - headerHeight} ${exportWidth} ${height + headerHeight}`);
  clone.setAttribute('width', String(exportWidth));
  clone.setAttribute('height', String(height + headerHeight));
  clone.removeAttribute('style');

  const originalElements = [source, ...Array.from(source.querySelectorAll('*'))];
  const clonedElements = [clone, ...Array.from(clone.querySelectorAll('*'))];
  originalElements.slice(1).forEach((element, index) => {
    const copiedElement = clonedElements[index + 1];
    if (!(element instanceof SVGElement) || !(copiedElement instanceof SVGElement)) return;
    const computed = window.getComputedStyle(element);
    const inlineStyles = STYLE_PROPERTIES
      .map((property) => {
        const value = computed.getPropertyValue(property);
        return value ? `${property}:${value}` : '';
      })
      .filter(Boolean)
      .join(';');
    if (inlineStyles) copiedElement.setAttribute('style', inlineStyles);
  });

  const logoDataUri = await getLogoDataUri();
  const background = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
  background.setAttribute('x', String(minX));
  background.setAttribute('y', String(minY - headerHeight));
  background.setAttribute('width', String(exportWidth));
  background.setAttribute('height', String(height + headerHeight));
  background.setAttribute('fill', backgroundColor);
  clone.insertBefore(background, clone.firstChild);

  const badgeX = minX + 8;
  const badgeY = minY - headerHeight + 8;
  const badge = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  badge.setAttribute('aria-label', 'APME');

  const panel = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
  panel.setAttribute('x', String(badgeX));
  panel.setAttribute('y', String(badgeY));
  panel.setAttribute('width', '156');
  panel.setAttribute('height', '48');
  panel.setAttribute('rx', '6');
  panel.setAttribute('fill', '#ffffff');
  panel.setAttribute('fill-opacity', '0.96');
  panel.setAttribute('stroke', '#c9d1d9');
  panel.setAttribute('stroke-width', '1');
  badge.appendChild(panel);

  if (logoDataUri) {
    const logo = document.createElementNS('http://www.w3.org/2000/svg', 'image');
    logo.setAttribute('x', String(badgeX + 7));
    logo.setAttribute('y', String(badgeY + 7));
    logo.setAttribute('width', '40');
    logo.setAttribute('height', '34');
    logo.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    logo.setAttribute('href', logoDataUri);
    badge.appendChild(logo);
  }

  const wordmark = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  wordmark.setAttribute('x', String(badgeX + 56));
  wordmark.setAttribute('y', String(badgeY + 30));
  wordmark.setAttribute('fill', '#151515');
  wordmark.setAttribute('font-family', 'Arial, sans-serif');
  wordmark.setAttribute('font-size', '17');
  wordmark.setAttribute('font-weight', '700');
  wordmark.setAttribute('letter-spacing', '1');
  wordmark.textContent = 'APME';
  badge.appendChild(wordmark);
  clone.insertBefore(badge, clone.firstChild?.nextSibling ?? null);

  const serialized = new XMLSerializer().serializeToString(clone);
  return { source: serialized, width: exportWidth, height: height + headerHeight };
}

function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function exportSvg(
  source: SVGSVGElement,
  filename: string,
  format: ExportFormat,
  backgroundColor: string,
  prepareSvg?: (svg: SVGSVGElement) => void,
): Promise<void> {
  const prepared = await createExportSvg(source, backgroundColor, prepareSvg);
  if (format === 'svg') {
    saveBlob(new Blob([prepared.source], { type: 'image/svg+xml;charset=utf-8' }), filename);
    return;
  }

  const imageUrl = URL.createObjectURL(
    new Blob([prepared.source], { type: 'image/svg+xml;charset=utf-8' }),
  );
  const canvas = document.createElement('canvas');
  const image = new Image();
  try {
    await new Promise<void>((resolve, reject) => {
      image.onload = () => resolve();
      image.onerror = () => reject(new Error('The graph image could not be rendered.'));
      image.src = imageUrl;
    });

    const scale = Math.min(
      2,
      12000 / prepared.width,
      12000 / prepared.height,
      Math.sqrt(40_000_000 / (prepared.width * prepared.height)),
    );
    canvas.width = Math.max(1, Math.round(prepared.width * scale));
    canvas.height = Math.max(1, Math.round(prepared.height * scale));
    const context = canvas.getContext('2d');
    if (!context) throw new Error('PNG export is not supported by this browser.');
    context.drawImage(image, 0, 0, canvas.width, canvas.height);

    const png = await new Promise<Blob>((resolve, reject) => {
      canvas.toBlob((blob) => {
        if (blob) resolve(blob);
        else reject(new Error('The graph image could not be encoded.'));
      }, 'image/png');
    });
    saveBlob(png, filename);
  } finally {
    canvas.width = 0;
    canvas.height = 0;
    image.src = '';
    URL.revokeObjectURL(imageUrl);
  }
}

function safeFilename(filename: string): string {
  return filename.trim().toLowerCase().replace(/[^a-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '') || 'apme-graph';
}

export function SvgExportButtons({
  svgRef,
  filename,
  backgroundColor,
  prepareSvg,
}: SvgExportButtonsProps) {
  const [error, setError] = useState('');
  const [isExporting, setIsExporting] = useState(false);
  const isExportingRef = useRef(false);

  const download = async (format: ExportFormat) => {
    if (isExportingRef.current) return;
    const svg = svgRef.current;
    if (!svg) return;
    isExportingRef.current = true;
    setIsExporting(true);
    setError('');
    try {
      await exportSvg(
        svg,
        `${safeFilename(filename)}.${format}`,
        format,
        backgroundColor ?? findBackgroundColor(svg),
        prepareSvg,
      );
    } catch {
      setError('Export failed. Please try again.');
    } finally {
      isExportingRef.current = false;
      setIsExporting(false);
    }
  };

  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      <Button type="button" variant="secondary" size="sm" isDisabled={isExporting} onClick={() => void download('png')}>
        Download PNG
      </Button>
      <Button type="button" variant="secondary" size="sm" isDisabled={isExporting} onClick={() => void download('svg')}>
        Download SVG
      </Button>
      {error && <span role="status" style={{ color: '#c9190b', fontSize: 12 }}>{error}</span>}
    </span>
  );
}
