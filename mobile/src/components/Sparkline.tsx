import { View } from "react-native";

interface SparklineProps {
  values: number[];
  color: string;
  width?: number;
  height?: number;
}

/**
 * Tiny trend graph for the twin ground-truth overlay -- RN has no <svg>, and
 * this app deliberately carries no chart library (see TwinControlPanel.tsx),
 * so this is a row of flex-height bars rather than a stroked line path like
 * the dashboard's Sparkline.tsx. Same purpose though: "is this climbing or
 * flat?", not a substitute for a real chart.
 */
export function Sparkline({ values, color, width = 72, height = 28 }: SparklineProps) {
  if (values.length < 2) {
    return <View style={{ width, height }} />;
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const barWidth = width / values.length;

  return (
    <View style={{ width, height, flexDirection: "row", alignItems: "flex-end" }}>
      {values.map((v, i) => {
        const pct = (v - min) / span;
        return (
          <View
            key={i}
            style={{
              width: Math.max(1, barWidth - 1),
              marginRight: 1,
              height: Math.max(1, pct * height),
              borderRadius: 1,
              backgroundColor: color,
              opacity: 0.4 + 0.6 * pct,
            }}
          />
        );
      })}
    </View>
  );
}
