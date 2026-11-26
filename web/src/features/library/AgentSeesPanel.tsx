import type { ProfileSummary } from "../../api/client";
import { Stars } from "../../components/Stars";
import { ui } from "../../components/ui";

export function AgentSeesPanel({ profile }: { profile: ProfileSummary }) {
  const implied = profile.ratings_implied;
  return (
    <aside className="space-y-6">
      <section aria-labelledby="sees-heading" className={`${ui.panel} p-7`}>
        <h2 id="sees-heading" className="text-xl font-bold">
          What the agent sees
        </h2>
        {implied ? (
          <>
            <p className="mt-3 text-muted">
              These {profile.read} books, all counted as books you liked. It won't recommend any of
              them, in any edition or translation, and every reason will cite one of them.
            </p>
            <p className="mt-3 text-muted">
              Picked books show no stars: you didn't rate them, so the agent never claims you did.
            </p>
          </>
        ) : (
          <>
            <p className="mt-3 text-muted">
              These {profile.books} books with your ratings: 4 or 5 stars count as liked, and lower
              ratings tell the agent what to avoid.
            </p>
            <p className="mt-3 text-muted">
              It won't recommend anything on your shelves, in any edition or translation, and every
              reason will cite a book you've read.
            </p>
          </>
        )}
      </section>
      {implied && (
        <section aria-labelledby="export-heading" className={`${ui.panel} p-7`}>
          <h2 id="export-heading" className="font-sans text-lg font-semibold">
            With a Goodreads export
          </h2>
          <p className="mt-2 text-muted">
            Real ratings show as stars, and low ratings tell the agent what to avoid.
          </p>
          <dl className="mt-4 space-y-2">
            {[
              ["East of Eden", 5],
              ["The Pearl", 4],
              ["A book you didn't love", 2],
            ].map(([title, rating]) => (
              <div key={title} className="flex items-center justify-between">
                <dt>{title}</dt>
                <dd>
                  <Stars rating={Number(rating)} />
                </dd>
              </div>
            ))}
          </dl>
        </section>
      )}
    </aside>
  );
}
