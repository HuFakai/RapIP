import { Globe2 } from "lucide-react";
import { useEffect, useState } from "react";

const regions = new Intl.DisplayNames(["zh-CN"], { type: "region" });
const flags = import.meta.glob<string>(
  "/node_modules/flag-icons/flags/4x3/*.svg",
  { query: "?url", import: "default" },
);
const cache = new Map<string, Promise<string>>();
export function CountryFlag({ code }: { code: string | null }) {
  const country = (code || "").toUpperCase();
  const [asset, setAsset] = useState<{ code: string; url: string } | null>(
    null,
  );
  useEffect(() => {
    const loader =
      flags[`/node_modules/flag-icons/flags/4x3/${country.toLowerCase()}.svg`];
    if (!loader) return;
    let active = true;
    if (!cache.has(country)) cache.set(country, loader());
    void cache
      .get(country)!
      .then((url) => {
        if (active) setAsset({ code: country, url });
      })
      .catch(() => {
        cache.delete(country);
      });
    return () => {
      active = false;
    };
  }, [country]);
  if (!/^[A-Z]{2}$/.test(country) || !asset || asset.code !== country)
    return <Globe2 size={17} aria-label="未知国家" />;
  const name = regions.of(country) || country;
  return (
    <img
      className="country-flag"
      src={asset.url}
      alt={`${name}国旗`}
      title={`${name} · ${country}`}
      width={23}
      height={17}
      loading="lazy"
      draggable={false}
    />
  );
}
