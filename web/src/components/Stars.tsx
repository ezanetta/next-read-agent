const POSITIONS = [1, 2, 3, 4, 5] as const;

/** A real rating as stars (half stars round up to a full one visually), labelled for
 * screen readers. Only for real ratings: picked books show "Picked" instead. */
export function Stars({ rating }: { rating: number }) {
  const filled = Math.round(rating);
  return (
    <span
      role="img"
      aria-label={`Rated ${rating} out of 5`}
      className="whitespace-nowrap text-accent"
    >
      {POSITIONS.map((position) => (
        <span
          key={position}
          aria-hidden="true"
          className={position <= filled ? "" : "text-border-strong"}
        >
          ★
        </span>
      ))}
    </span>
  );
}

export function PickedBadge() {
  return (
    <span className="inline-block whitespace-nowrap rounded-full align-middle bg-accent/15 px-3 py-1 text-xs font-semibold text-accent">
      Picked
    </span>
  );
}
