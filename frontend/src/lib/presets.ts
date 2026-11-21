export interface DemoPreset {
  id: string;
  title: string;
  sub: string;
  href: string;
}

export const DEMO_PRESETS: DemoPreset[] = [
  {
    id: "wielkopolska-2019",
    title: "Wielkopolska · susza 2019",
    sub: "Goraniec (gm. Czerniejewo) · kukurydza · gleba kat. II · 15 VII 2019",
    href: "/analiza?dzialki=300302_5.0005.49~kukurydza_ziarno&aktywna=300302_5.0005.49&data=2019-07-15",
  },
  {
    id: "lubelskie-czarnoziem",
    title: "Lubelszczyzna · gleba kat. IV",
    sub: "Wilków (gm. Werbkowice) · zboża ozime · 15 VII 2026",
    href: "/analiza?dzialki=060408_2.0166.7~zboza_ozime&aktywna=060408_2.0166.7&data=2026-07-15",
  },
  {
    id: "porownanie-2",
    title: "Porównanie 2 działek",
    sub: "Goraniec (kukurydza) i Kosmowo (zboża ozime) · 15 VII 2019",
    href: "/analiza?dzialki=300302_5.0005.49~kukurydza_ziarno,300302_5.0008.12%2F3~zboza_ozime&aktywna=300302_5.0005.49&data=2019-07-15",
  },
];
