import type { AnchorHTMLAttributes, ButtonHTMLAttributes, ReactNode } from "react";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

const ICONS = {
  arrow: "M2 8h11.2M9.2 4l4 4-4 4",
  down: "M4.5 6.5 8 10l3.5-3.5",
  back: "M10 3.5 5.5 8l4.5 4.5",
  more: "M3.5 8h.01M8 8h.01M12.5 8h.01",
  plus: "M8 3v10M3 8h10",
  minus: "M3 8h10",
  locate: "M8 1.5V4M8 12v2.5M1.5 8H4M12 8h2.5M11.2 8a3.2 3.2 0 1 1-6.4 0 3.2 3.2 0 0 1 6.4 0Z",
  layers: "M8 2 1.8 5.2 8 8.4l6.2-3.2L8 2ZM1.8 8.2 8 11.4l6.2-3.2M1.8 11.2 8 14.4l6.2-3.2",
  drop: "M8 1.8S3.4 7 3.4 10a4.6 4.6 0 0 0 9.2 0C12.6 7 8 1.8 8 1.8Z",
  leaf: "M13.5 2.5C6 2.5 2.5 6 2.5 13.5 10 13.5 13.5 10 13.5 2.5ZM2.5 13.5 9 7",
  sun: "M8 5.2a2.8 2.8 0 1 0 0 5.6 2.8 2.8 0 0 0 0-5.6ZM8 1v1.6M8 13.4V15M1 8h1.6M13.4 8H15M3 3l1.1 1.1M11.9 11.9 13 13M3 13l1.1-1.1M11.9 4.1 13 3",
  wind: "M2 6h8.5a2 2 0 1 0-2-2M2 9.5h11a2 2 0 1 1-2 2M2 13h5",
  thermo: "M9.5 9.3V3a1.5 1.5 0 0 0-3 0v6.3a3 3 0 1 0 3 0Z",
  map: "M1.8 3.6 5.6 2l4.8 1.8 3.8-1.6v10.2l-3.8 1.6-4.8-1.8-3.8 1.6V3.6ZM5.6 2v10.2M10.4 3.8V14",
  gauge: "M2.5 11.5a5.5 5.5 0 1 1 11 0M8 11.5l2.6-3.4",
  sprout: "M8 14V7.5M8 7.5C8 4.5 5.8 3 2.8 3c0 3 2.2 4.5 5.2 4.5ZM8 9.5c0-3 2.2-4.5 5.2-4.5 0 3-2.2 4.5-5.2 4.5Z",
  file: "M4 1.8h5.2L12.5 5v9.2H4V1.8ZM9 1.8V5h3.5M6 8.5h4.5M6 11h4.5",
  area: "M2.5 5.5v-3h3M10.5 2.5h3v3M13.5 10.5v3h-3M5.5 13.5h-3v-3",
  calendar: "M2.5 3.5h11v10h-11zM2.5 6.5h11M5.5 2v3M10.5 2v3",
  check: "M3.5 8.5 6.5 11.5 12.5 5",
  search: "M7 12.2a5.2 5.2 0 1 0 0-10.4 5.2 5.2 0 0 0 0 10.4ZM10.8 10.8l3.7 3.7",
  play: "M5 3.2 13 8 5 12.8Z",
  pause: "M5 3.5v9M11 3.5v9",
  close: "M4 4l8 8M12 4l-8 8",
} as const;
export type IconName = keyof typeof ICONS;

export function Icon({ name, className }: { name: IconName; className?: string }) {
  return (
    <svg className={cx("ar-ico", className)} viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path d={ICONS[name]} fill="none" stroke="currentColor" strokeWidth={name === "more" ? 2.6 : 1.4} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Wordmark({ size, tone, className }: { size?: "md" | "lg"; tone?: "default" | "photo"; className?: string }) {
  return (
    <span className={cx("ar-wordmark", size === "lg" && "ar-wordmark--lg", tone === "photo" && "ar-wordmark--photo", className)}>
      <svg className="ar-wordmark__mark" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
        <circle className="ring" cx={16} cy={16} r={15} strokeWidth={1.5} />
        <g className="ln" strokeWidth={1.5} strokeLinecap="round">
          <path d="M7 12.5c3-2.6 6-2.6 9 0s6 2.6 9 0" />
          <path d="M7 17.5c3-2.6 6-2.6 9 0s6 2.6 9 0" />
          <path d="M10.5 22.5c1.8-1.5 3.7-1.5 5.5 0s3.7 1.5 5.5 0" />
        </g>
      </svg>
      <span className="ar-wordmark__name">Rusałka</span>
    </span>
  );
}

type Variant = "primary" | "light" | "lime" | "outline" | "icon" | "circle";
interface BtnBase { variant?: Variant; arrow?: boolean | "down"; size?: "sm" | "md" | "lg"; icon?: IconName; label?: string; photo?: boolean; children?: ReactNode; className?: string }
export type ButtonProps = BtnBase & ({ href: string } & Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "children"> | { href?: undefined } & Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children">);

export function Button(props: ButtonProps) {
  const { variant = "primary", arrow, size, icon, label, photo, children, className, ...rest } = props;
  const iconOnly = variant === "icon" || variant === "circle";
  const tail = !iconOnly && arrow && variant !== "outline";
  const cls = cx("ar-btn", `ar-btn--${variant}`, tail && "ar-btn--tail", size === "lg" && "ar-btn--lg", size === "sm" && "ar-btn--sm", photo && "ar-btn--photo", className);
  const aria = iconOnly ? { "aria-label": label ?? (typeof children === "string" ? children : "Dalej"), title: label } : {};
  const kids = iconOnly ? <Icon name={icon ?? "arrow"} /> : (
    <>
      {icon ? <Icon name={icon} /> : null}
      <span>{children}</span>
      {tail ? <span className="ar-btn__sq"><Icon name={arrow === "down" ? "down" : "arrow"} /></span> : null}
    </>
  );
  if ("href" in rest && rest.href !== undefined) return <a className={cls} {...aria} {...(rest as AnchorHTMLAttributes<HTMLAnchorElement>)}>{kids}</a>;
  const b = rest as ButtonHTMLAttributes<HTMLButtonElement>;
  return <button className={cls} type={b.type ?? "button"} {...aria} {...b}>{kids}</button>;
}

/** A toggle only. Never a status or data-source tag. */
export function Pill({ tone, pressed, onClick, children, className }: { tone?: "outline" | "glass"; pressed?: boolean; onClick: () => void; children: ReactNode; className?: string }) {
  return (
    <button type="button" className={cx("ar-pill", tone === "glass" && "ar-pill--glass", "ar-pill--button", className)} aria-pressed={pressed ? "true" : "false"} onClick={onClick}>
      <span>{children}</span>
    </button>
  );
}
