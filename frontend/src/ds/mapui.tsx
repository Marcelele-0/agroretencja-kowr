import type { CSSProperties, ReactNode } from "react";
import { Button, cx, Icon, type IconName } from "./core";

export function ParcelPopover({ title, sub, icon = "sprout", rows = [], note, action, muted, style, className }: {
  title: ReactNode; sub?: ReactNode; icon?: IconName; rows?: [ReactNode, ReactNode][]; note?: ReactNode; action?: { label: string; onClick: () => void; busy?: boolean };
  muted?: boolean; style?: CSSProperties; className?: string;
}) {
  return (
    <div className={cx("ar-pop", muted && "ar-pop--muted", className)} style={style} role="dialog" aria-label={typeof title === "string" ? title : "Działka"}>
      <div className="ar-pop__head">
        <span className="ar-pop__icon"><Icon name={icon} /></span>
        <div><div className="ar-pop__title">{title}</div>{sub ? <div className="ar-pop__sub">{sub}</div> : null}</div>
      </div>
      {rows.length ? (
        <div className="ar-pop__rows" style={{ gridTemplateColumns: `repeat(${rows.length}, auto)` }}>
          {rows.map((r, i) => <span key={`k${i}`} className="ar-pop__k">{r[0]}</span>)}
          {rows.map((r, i) => <span key={`v${i}`} className="ar-pop__v">{r[1]}</span>)}
        </div>
      ) : null}
      {note ? <p className="ar-pop__note">{note}</p> : null}
      {action ? <div className="ar-pop__action"><Button variant="lime" arrow onClick={action.onClick} disabled={action.busy}>{action.label}</Button></div> : null}
    </div>
  );
}

export function LayerSwitch({ options, value, onChange, label = "Widok mapy" }: { options: { id: string; label: string; icon?: IconName }[]; value: string; onChange: (id: string) => void; label?: string }) {
  return (
    <div className="ar-layer" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.id} type="button" className="ar-layer__opt" aria-pressed={o.id === value ? "true" : "false"} onClick={() => onChange(o.id)}>
          {o.icon ? <Icon name={o.icon} /> : null}{o.label}
        </button>
      ))}
    </div>
  );
}

export function FloatingNav({ items, active, onChange, label = "Kroki analizy", className }: { items: { id: string; label: string; icon: IconName }[]; active: string; onChange: (id: string) => void; label?: string; className?: string }) {
  return (
    <nav className={cx("ar-nav", className)} aria-label={label}>
      {items.map((it) => {
        const on = it.id === active;
        return (
          <button key={it.id} type="button" className="ar-nav__item" aria-current={on ? "page" : undefined} aria-label={it.label} title={it.label} onClick={() => onChange(it.id)}>
            <Icon name={it.icon} />{on ? <span>{it.label}</span> : null}
          </button>
        );
      })}
    </nav>
  );
}
