import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

function base(children: React.ReactNode, props: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      {children}
    </svg>
  );
}

export function IconActivity(props: IconProps) {
  return base(<polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />, props);
}

export function IconHeartPulse(props: IconProps) {
  return base(
    <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z" />,
    props,
  );
}

export function IconBattery(props: IconProps) {
  return base(
    <>
      <rect x="1" y="6" width="18" height="12" rx="2" />
      <line x1="23" y1="10" x2="23" y2="14" />
    </>,
    props,
  );
}

export function IconZap(props: IconProps) {
  return base(<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />, props);
}

export function IconCheckCircle(props: IconProps) {
  return base(
    <>
      <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
      <polyline points="22 4 12 14.01 9 11.01" />
    </>,
    props,
  );
}

export function IconAlertTriangle(props: IconProps) {
  return base(
    <>
      <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
      <line x1="12" y1="9" x2="12" y2="13" />
      <line x1="12" y1="17" x2="12.01" y2="17" />
    </>,
    props,
  );
}

export function IconLogOut(props: IconProps) {
  return base(
    <>
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <polyline points="16 17 21 12 16 7" />
      <line x1="21" y1="12" x2="9" y2="12" />
    </>,
    props,
  );
}

export function IconPlus(props: IconProps) {
  return base(
    <>
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </>,
    props,
  );
}

export function IconSignal(props: IconProps) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" {...props}>
      <rect x="2" y="14" width="4" height="8" rx="1" />
      <rect x="10" y="9" width="4" height="13" rx="1" opacity="0.85" />
      <rect x="18" y="3" width="4" height="19" rx="1" opacity="0.7" />
    </svg>
  );
}

export function IconBeaker(props: IconProps) {
  return base(
    <>
      <path d="M9 2h6" />
      <path d="M10 2v6.34a2 2 0 0 1-.4 1.2L5.06 15.4A3 3 0 0 0 7.4 20h9.2a3 3 0 0 0 2.34-4.6l-4.54-5.86a2 2 0 0 1-.4-1.2V2" />
    </>,
    props,
  );
}

export function IconShield(props: IconProps) {
  return base(<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />, props);
}

export function IconSun(props: IconProps) {
  return base(
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2" />
      <path d="M12 20v2" />
      <path d="M4.93 4.93l1.41 1.41" />
      <path d="M17.66 17.66l1.41 1.41" />
      <path d="M2 12h2" />
      <path d="M20 12h2" />
      <path d="M6.34 17.66l-1.41 1.41" />
      <path d="M19.07 4.93l-1.41 1.41" />
    </>,
    props,
  );
}

export function IconMoon(props: IconProps) {
  return base(<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />, props);
}

export function IconSparkles(props: IconProps) {
  return base(
    <>
      <path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M18.4 5.6l-2.8 2.8M8.4 15.6l-2.8 2.8" />
    </>,
    props,
  );
}

export function IconSend(props: IconProps) {
  return base(<path d="M22 2 11 13M22 2 15 22l-4-9-9-4 20-7z" />, props);
}

export function IconInfo(props: IconProps) {
  return base(
    <>
      <circle cx="12" cy="12" r="10" />
      <line x1="12" y1="16" x2="12" y2="11.5" />
      <line x1="12" y1="8" x2="12.01" y2="8" />
    </>,
    props,
  );
}
