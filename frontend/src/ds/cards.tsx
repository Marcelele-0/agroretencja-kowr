import { useId, type ReactNode } from "react";
import { Button, cx, Icon } from "./core";

export function ActionCard({ title, image, metrics = [], permit, permitHref, onOpen, selected, onToggle, className }: {
  title: string; image?: string | false; metrics?: string[]; permit?: boolean | string; permitHref?: string; onOpen?: () => void; selected?: boolean; onToggle?: (v: boolean) => void; className?: string;
}) {
  const id = useId();
  return (
    <article className={cx("ar-action", className)} aria-labelledby={`${id}t`}>
      {image !== false ? <div className="ar-action__img" style={image ? { backgroundImage: `url("${image}")` } : undefined} aria-hidden="true" /> : null}
      <div className="ar-action__foot">
        <div style={{ minWidth: 0 }}>
          {onToggle ? <label className="ar-action__check"><input type="checkbox" checked={!!selected} onChange={(e) => onToggle(e.target.checked)} />Dodaj do symulacji</label> : null}
          <h3 className="ar-action__title" id={`${id}t`}>{title}</h3>
          {metrics.length ? <p className="ar-action__effect">{metrics.join(" · ")}</p> : null}
          {permit ? (
            <p className="ar-action__permit"><Icon name="file" /><span>{typeof permit === "string" ? permit : "Wymaga zgody Wód Polskich."}{permitHref ? <> <a href={permitHref} target="_blank" rel="noreferrer">Jak to załatwić</a></> : null}</span></p>
          ) : null}
        </div>
        {onOpen ? <Button variant="circle" label={`Szczegóły: ${title}`} onClick={onOpen} /> : null}
      </div>
    </article>
  );
}

export function PhotoCard({ image, title, text, tone, children }: { image?: string; title?: ReactNode; text?: ReactNode; tone?: "surface" | "forest" | "night"; children?: ReactNode }) {
  return (
    <article className={cx("ar-card", tone === "forest" && "ar-card--forest", tone === "night" && "ar-card--night")}>
      {image ? <div className="ar-card__img" style={{ backgroundImage: `url("${image}")` }} aria-hidden="true" /> : null}
      {title ? <h3 className="ar-card__title">{title}</h3> : null}
      {text ? <p className="ar-card__text">{text}</p> : null}
      {children}
    </article>
  );
}
