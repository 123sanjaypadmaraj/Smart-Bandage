interface SparklineProps {
  values: number[];
  width?: number;
  height?: number;
  stroke: string;
  fill?: string;
  className?: string;
}

/**
 * Tiny inline trend line for a stat tile -- last-N raw values, no axes/grid.
 * Purely decorative context ("is this climbing or flat?"), not a substitute
 * for the full signal trace in MeasurementChart.
 */
export function Sparkline({ values, width = 64, height = 24, stroke, fill, className = "" }: SparklineProps) {
  if (values.length < 2) {
    return <svg width={width} height={height} className={className} aria-hidden="true" />;
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const step = width / (values.length - 1);

  const points = values.map((v, i) => {
    const x = i * step;
    const y = height - ((v - min) / span) * (height - 4) - 2;
    return [x, y] as const;
  });

  const linePath = points.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const areaPath = `${linePath} L${width},${height} L0,${height} Z`;
  const [lastX, lastY] = points[points.length - 1];

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} className={className} aria-hidden="true">
      {fill && <path d={areaPath} fill={fill} stroke="none" />}
      <path d={linePath} fill="none" stroke={stroke} strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={lastX} cy={lastY} r={1.8} fill={stroke} />
    </svg>
  );
}
