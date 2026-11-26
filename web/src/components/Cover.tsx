import { useState } from "react";

/** Cover colors from the mockups, used when a book has no cover or it fails to load. */
export const COVER_PALETTE = [
  "#2f5d50",
  "#3a4150",
  "#8a5a2e",
  "#2e4a7a",
  "#6b4e8c",
  "#7a2e2e",
  "#4e5a3a",
] as const;

/** A stable color per title, so a book keeps its color across screens. */
export function coverColor(title: string): string {
  let hash = 0;
  for (const char of title) hash = (hash * 31 + (char.codePointAt(0) ?? 0)) >>> 0;
  return COVER_PALETTE[hash % COVER_PALETTE.length] ?? COVER_PALETTE[0];
}

type Size = "strip" | "thumb" | "card" | "large";

const SIZES: Record<Size, string> = {
  strip: "h-12 w-6",
  thumb: "h-14 w-10",
  card: "aspect-[2/3] w-full",
  large: "h-44 w-28 sm:h-56 sm:w-36",
};

/** A book cover: the real one when it loads, else a colored block with the title (and
 * author on large covers). Decorative when `labelled` is false: the title is nearby. */
export function Cover({
  title,
  author,
  url,
  size = "card",
  showText = size !== "strip" && size !== "thumb",
}: {
  title: string;
  author?: string;
  url?: string | null;
  size?: Size;
  showText?: boolean;
}) {
  // The URL that failed, not a flag: when a cover arrives later, the new URL gets a chance.
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const failed = url != null && failedUrl === url;
  const base = `${SIZES[size]} shrink-0 overflow-hidden rounded-md`;
  if (url && !failed) {
    return (
      <img
        src={url}
        alt=""
        loading="lazy"
        onError={() => setFailedUrl(url)}
        className={`${base} bg-surface-2 object-cover`}
      />
    );
  }
  return (
    <div
      aria-hidden="true"
      className={`${base} flex flex-col justify-end p-3 text-left`}
      style={{ backgroundColor: coverColor(title) }}
    >
      {showText && (
        <>
          <span className="line-clamp-3 wrap-break-word hyphens-auto font-display text-sm leading-tight font-bold text-text">
            {title}
          </span>
          {author && (
            <span className="mt-1 line-clamp-2 wrap-break-word text-xs text-text">{author}</span>
          )}
        </>
      )}
    </div>
  );
}
